"""
Intent: Let the execution model ask for operations while ensuring asking is all it can do
Context: Second stage. Receives a bundle from providers.py and drives authority.py and the
        gateway; the model never reaches a handler itself.
Pattern: Verify-then-authorize -- six structural checks run before authority is consulted
        at all, so a malformed request never reaches the evidence lookup
Future: Cross-run replay is out of scope here. The executed set lives inside one run, so
        re-running the same bundle authorizes and executes again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping

from .authority import (
    AuthorityController,
    Decision,
    ExecutionGateway,
    Proposal,
    ProposalBundle,
    ProposalReadiness,
    Route,
)
from .tools import ToolRegistry, ToolValidationError


class ExecutionStatus(str, Enum):
    COMPLETED = "completed"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_AUDIT = "awaiting_audit"
    NEEDS_CLARIFICATION = "needs_clarification"
    NEEDS_REVISION = "needs_revision"
    DENIED = "denied"
    FAILED = "failed"


@dataclass(frozen=True)
class ExecutionStep:
    proposal_id: str
    tool_name: str
    route: Route
    decision: Decision | None
    executed: bool
    receipt: Any | None
    error: str | None


@dataclass(frozen=True)
class ExecutionRun:
    bundle_id: str
    status: ExecutionStatus
    final_text: str
    steps: tuple[ExecutionStep, ...]


class ClaudeExecutionAgent:
    """Claude requests operations; host-owned controller and gateway execute them."""

    SYSTEM_PROMPT = """You are an execution planner operating under explicit authority.
Use only the supplied client tools and only for the proposal_id bound to each
operation. Do not alter arguments, substitute tools, repeat an operation, or infer
additional authority. If a required action is absent, stop and explain that a new
proposal is required. A tool result reporting blocked authority is final for this
run; do not route around it."""

    def __init__(
        self,
        model: str,
        prompt_version: str,
        authority: AuthorityController,
        gateway: ExecutionGateway,
        registry: ToolRegistry,
        client: Any | None = None,
        max_turns: int = 12,
    ) -> None:
        if not model or not prompt_version:
            raise ValueError("pin an execution model and prompt version")
        if client is None:
            try:
                from anthropic import Anthropic
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("install the anthropic package") from exc
            client = Anthropic()
        # Any, not Anthropic: tests inject duck-typed fake clients by design.
        self.client: Any = client
        self.model = model
        self.prompt_version = prompt_version
        self.authority = authority
        self.gateway = gateway
        self.registry = registry
        self.max_turns = max_turns

    def run(
        self,
        bundle: ProposalBundle,
        *,
        approved_proposal_ids: Iterable[str] = (),
    ) -> ExecutionRun:
        """
        intent: Drive one bundle to completion, or stop at the first thing not authorized
        method: Turn loop over the model's tool requests, each handled and either executed
                or refused; the first refusal ends the run
        effect: No skip-and-continue. A blocked operation cannot be stepped over to reach a
                later one, so a partial plan cannot be assembled out of the allowed parts.
        constraint: Draft and clarification-needed bundles return before the model is called
                    at all -- an incomplete proposal is never shown an execution surface
        """
        if bundle.readiness == ProposalReadiness.DRAFT:
            return ExecutionRun(bundle.bundle_id, ExecutionStatus.NEEDS_REVISION, "", ())
        if bundle.readiness == ProposalReadiness.NEEDS_CLARIFICATION:
            return ExecutionRun(
                bundle.bundle_id, ExecutionStatus.NEEDS_CLARIFICATION, "", ()
            )

        proposals = {proposal.proposal_id: proposal for proposal in bundle.proposals}
        approved = frozenset(approved_proposal_ids)
        executed: set[str] = set()
        steps: list[ExecutionStep] = []
        proposal_ids_by_tool: dict[str, list[str]] = {}
        for proposal in bundle.proposals:
            proposal_ids_by_tool.setdefault(proposal.tool_name, []).append(proposal.proposal_id)
        tools = self.registry.anthropic_tools(proposal_ids_by_tool)
        messages: list[dict[str, Any]] = [{
            "role": "user",
            "content": json.dumps({
                "bundle_id": bundle.bundle_id,
                "intent": bundle.intent,
                "summary": bundle.summary,
                "actions": [self._public_proposal(p) for p in bundle.proposals],
            }, sort_keys=True),
        }]

        for _turn in range(self.max_turns):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=self.SYSTEM_PROMPT,
                tools=tools,
                # constraint: parallel tool use is off so effects are strictly serialized.
                # Authority is re-evaluated between every operation, and an outcome that
                # suspends a partition must be able to change the answer for the next call
                # in the same run -- concurrent calls would race that re-evaluation.
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                messages=messages,
            )
            tool_blocks = [block for block in response.content if block.type == "tool_use"]
            text = "\n".join(
                block.text for block in response.content if block.type == "text"
            )
            if not tool_blocks:
                return ExecutionRun(
                    bundle.bundle_id,
                    ExecutionStatus.COMPLETED,
                    text,
                    tuple(steps),
                )
            if len(tool_blocks) != 1:
                steps.append(ExecutionStep(
                    proposal_id="",
                    tool_name="",
                    route=Route.DENY,
                    decision=None,
                    executed=False,
                    receipt=None,
                    error="parallel tool requests are disabled",
                ))
                return ExecutionRun(
                    bundle.bundle_id,
                    ExecutionStatus.DENIED,
                    text,
                    tuple(steps),
                )

            block = tool_blocks[0]
            outcome = self._handle_tool_call(
                block.name,
                dict(block.input),
                proposals,
                approved,
                executed,
            )
            steps.append(outcome)
            messages += [
                {"role": "assistant", "content": response.content},
                {
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(
                            outcome.receipt if outcome.executed else {"error": outcome.error},
                            sort_keys=True,
                            default=str,
                        ),
                        "is_error": not outcome.executed,
                    }],
                },
            ]
            if not outcome.executed:
                return ExecutionRun(
                    bundle.bundle_id,
                    self._status_for_route(outcome.route),
                    text,
                    tuple(steps),
                )

        return ExecutionRun(
            bundle.bundle_id,
            ExecutionStatus.FAILED,
            "Claude exceeded the configured turn limit.",
            tuple(steps),
        )

    def _handle_tool_call(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        proposals: Mapping[str, Proposal],
        approved: frozenset[str],
        executed: set[str],
    ) -> ExecutionStep:
        """
        intent: Decide whether one request from the model may become an effect
        method: Six structural checks -- known id, not already run, tool matches, arguments
                identical, schema still valid, effects unchanged -- then authority, then a
                token, then the handler
        effect: Structure is checked before evidence is consulted, so a tampered request is
                refused without ever touching the track record
        constraint: Argument comparison is whole-dictionary equality, not a subset test. An
                    extra field is a mismatch, not an addition to be tolerated.
        future: Python equality means 24 and 24.0 compare as identical, and the executed set
                is per-run, so cross-run replay protection belongs to the deployment
        """
        proposal_id = str(tool_input.pop("proposal_id", ""))
        proposal = proposals.get(proposal_id)
        if proposal is None:
            return self._blocked(proposal_id, tool_name, "unknown proposal_id")
        if proposal_id in executed:
            return self._blocked(proposal_id, tool_name, "proposal already executed")
        if tool_name != proposal.tool_name:
            return self._blocked(proposal_id, tool_name, "tool does not match proposal")
        if tool_input != dict(proposal.parameters):
            return self._blocked(proposal_id, tool_name, "arguments changed after proposal")
        try:
            registered = self.registry.get(tool_name)
            self.registry.validate(tool_name, tool_input)
        except ToolValidationError as exc:
            return self._blocked(proposal_id, tool_name, str(exc))
        if registered.effects != proposal.requested_effects:
            return self._blocked(proposal_id, tool_name, "registered effects changed")

        decision = self.authority.evaluate(proposal)
        human_approved = proposal_id in approved
        try:
            token = self.gateway.issue(
                decision,
                proposal,
                self.authority.policy,
                human_approved=human_approved,
            )
        except PermissionError:
            return ExecutionStep(
                proposal_id=proposal_id,
                tool_name=tool_name,
                route=decision.route,
                decision=decision,
                executed=False,
                receipt=None,
                error=f"authority route: {decision.route.value}",
            )

        receipt = self.gateway.execute(
            token.token_id,
            proposal,
            lambda p: self.registry.execute(p.tool_name, p.parameters),
        )
        executed.add(proposal_id)
        return ExecutionStep(
            proposal_id=proposal_id,
            tool_name=tool_name,
            route=decision.route,
            decision=decision,
            executed=True,
            receipt=receipt,
            error=None,
        )

    @staticmethod
    def _blocked(proposal_id: str, tool_name: str, error: str) -> ExecutionStep:
        return ExecutionStep(
            proposal_id=proposal_id,
            tool_name=tool_name,
            route=Route.DENY,
            decision=None,
            executed=False,
            receipt=None,
            error=error,
        )

    @staticmethod
    def _status_for_route(route: Route) -> ExecutionStatus:
        return {
            Route.HUMAN_APPROVAL: ExecutionStatus.AWAITING_APPROVAL,
            Route.AUDIT: ExecutionStatus.AWAITING_AUDIT,
            Route.CLARIFICATION: ExecutionStatus.NEEDS_CLARIFICATION,
            Route.REVISION: ExecutionStatus.NEEDS_REVISION,
            Route.DENY: ExecutionStatus.DENIED,
        }.get(route, ExecutionStatus.FAILED)

    @staticmethod
    def _public_proposal(proposal: Proposal) -> dict[str, Any]:
        """
        intent: Show the execution model what it may select, and nothing that would help it
                argue for more
        constraint: Omits the partition, confidence, effects, assumptions, and unresolved
                    questions. The model cannot see the evidence key it would be judged
                    against, so it cannot tailor a request toward a well-stocked partition.
        """
        return {
            "proposal_id": proposal.proposal_id,
            "intent": proposal.intent,
            "tool_name": proposal.tool_name,
            "parameters": dict(proposal.parameters),
            "preconditions": proposal.preconditions,
            "success_criteria": proposal.success_criteria,
        }
