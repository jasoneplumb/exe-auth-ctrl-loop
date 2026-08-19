"""
Intent: Turn a user's intent into typed proposals, while giving the proposing model no way
        to cause an effect or to assert anything the host will believe
Context: First stage of the loop. Feeds executor.py, which may only select from what this
        produced; the host resolves every authority-relevant field itself.
Pattern: Structured Output as a boundary -- the model fills a schema, and the host rebuilds
        the real objects from the registry rather than trusting the parsed fields
Future: The proposal stage has no evidence stream of its own; proposal quality is currently
        observable only through end-to-end outcomes.
"""

from __future__ import annotations

import json
import secrets
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .authority import (
    PartitionKey,
    Proposal,
    ProposalBundle,
    ProposalReadiness,
)
from .tools import ToolRegistry, ToolValidationError


class ProposedActionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(description="Stable identifier within this proposal")
    intent: str
    tool_name: str
    arguments_json: str = Field(description="A JSON object containing exact tool arguments")
    declared_effects: list[str]
    declared_confidence: float = Field(ge=0.0, le=1.0)
    preconditions: list[str]
    success_criteria: list[str]
    assumptions: list[str]
    unresolved_questions: list[str]


class ProposalDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    readiness: Literal["draft", "needs_clarification", "executable"]
    summary: str
    missing_information: list[str]
    actions: list[ProposedActionModel]


class ProposalGenerationError(RuntimeError):
    pass


def confidence_bin(value: float) -> str:
    """
    intent: Make self-reported confidence usable as a partition field without treating it
            as a measurement
    method: Five coarse bands rather than the raw float
    tradeoff: Binning loses resolution but keeps partitions populated -- keying evidence on
              a raw float would give almost every operation its own empty partition, so
              nothing would ever accumulate a track record
    constraint: The boundaries are cliffs: 0.949 and 0.950 are different partitions and
                share no evidence
    """
    if value < 0.50:
        return "0.00-0.49"
    if value < 0.75:
        return "0.50-0.74"
    if value < 0.90:
        return "0.75-0.89"
    if value < 0.95:
        return "0.90-0.94"
    return "0.95-1.00"


class OpenAIProposalGenerator:
    """Read-only OpenAI builder that returns typed proposals, never tool calls."""

    SYSTEM_PROMPT = """You are a proposal builder, not an execution agent.
Produce a typed proposal describing what could be done. You have no permission to
execute tools. Use only tools from the supplied catalog. If information required
for safe execution is missing, choose draft or needs_clarification. Mark a proposal
executable only when every argument, precondition, success criterion, effect, and
unresolved question is explicit. arguments_json must be a JSON object. Never invent
tool names, permissions, observations, approvals, or evidence."""

    def __init__(
        self,
        model: str,
        prompt_version: str,
        registry: ToolRegistry,
        client: Any | None = None,
    ) -> None:
        if not model or not prompt_version:
            raise ValueError("pin a proposal model and prompt version")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError("install the openai package") from exc
            client = OpenAI()
        self.client = client
        self.model = model
        self.prompt_version = prompt_version
        self.registry = registry

    def generate(
        self,
        user_intent: str,
        *,
        execution_model: str,
        execution_prompt_version: str,
        policy_version: str,
        environment_version: str,
        context: Mapping[str, Any] | None = None,
    ) -> ProposalBundle:
        request = {
            "intent": user_intent,
            "context": dict(context or {}),
            "available_tools": self.registry.public_catalog(),
        }
        response = self.client.responses.parse(
            model=self.model,
            input=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(request, sort_keys=True)},
            ],
            text_format=ProposalDraftModel,
        )
        draft = response.output_parsed
        if draft is None:
            raise ProposalGenerationError("OpenAI returned no parsed proposal")
        return self._to_bundle(
            user_intent,
            draft,
            execution_model=execution_model,
            execution_prompt_version=execution_prompt_version,
            policy_version=policy_version,
            environment_version=environment_version,
        )

    def _to_bundle(
        self,
        user_intent: str,
        draft: ProposalDraftModel,
        *,
        execution_model: str,
        execution_prompt_version: str,
        policy_version: str,
        environment_version: str,
    ) -> ProposalBundle:
        """
        intent: Rebuild host-owned objects from a model's draft, keeping only what the model
                is entitled to decide
        method: The model supplies intent, tool name, arguments, and confidence. Effects,
                tool version, task category, and risk class are read from the registry --
                the model's `declared_effects` is compared and used to reject, never adopted.
        effect: A model that overstates its permissions fails here rather than at the
                gateway, and a mismatch raises instead of being reconciled
        constraint: proposal_id is host-generated with random entropy, so a model cannot
                    choose an id and cannot collide with one it saw earlier
        """
        readiness = ProposalReadiness(draft.readiness)
        proposals: list[Proposal] = []
        for position, action in enumerate(draft.actions):
            try:
                parameters = json.loads(action.arguments_json)
            except json.JSONDecodeError as exc:
                raise ProposalGenerationError("arguments_json is not valid JSON") from exc
            if not isinstance(parameters, dict):
                raise ProposalGenerationError("arguments_json must contain an object")

            try:
                registered = self.registry.get(action.tool_name)
                self.registry.validate(action.tool_name, parameters)
            except ToolValidationError as exc:
                raise ProposalGenerationError(str(exc)) from exc
            # constraint: exact set equality, not a subset test. A proposal that declares
            # fewer effects than the tool actually has is as wrong as one that declares
            # more -- it means the model and the host disagree about what this call does.
            if frozenset(action.declared_effects) != registered.effects:
                raise ProposalGenerationError(
                    f"declared effects do not match registered effects for {action.tool_name}"
                )

            key = PartitionKey(
                proposal_provider="openai",
                proposal_model_version=self.model,
                proposal_prompt_version=self.prompt_version,
                execution_provider="anthropic",
                execution_model_version=execution_model,
                execution_prompt_version=execution_prompt_version,
                tool_version=registered.version,
                policy_version=policy_version,
                environment_version=environment_version,
                task_category=registered.task_category,
                confidence_bin=confidence_bin(action.declared_confidence),
                risk_class=registered.classify_risk(parameters),
            )
            proposals.append(Proposal(
                proposal_id=f"{action.action_id}-{position}-{secrets.token_hex(4)}",
                intent=action.intent,
                tool_name=action.tool_name,
                parameters=parameters,
                confidence=action.declared_confidence,
                requested_effects=registered.effects,
                partition=key,
                proposer="openai",
                readiness=readiness,
                preconditions=tuple(action.preconditions),
                success_criteria=tuple(action.success_criteria),
                assumptions=tuple(action.assumptions),
                unresolved_questions=tuple(action.unresolved_questions),
            ))

        if readiness == ProposalReadiness.EXECUTABLE and not proposals:
            raise ProposalGenerationError("an executable bundle must contain an action")
        return ProposalBundle(
            bundle_id=secrets.token_hex(12),
            intent=user_intent,
            readiness=readiness,
            proposals=tuple(proposals),
            summary=draft.summary,
            missing_information=tuple(draft.missing_information),
        )
