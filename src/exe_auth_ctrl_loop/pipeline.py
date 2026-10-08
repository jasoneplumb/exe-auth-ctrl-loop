"""
Intent: Join the proposal and execution stages and record what happened at each step
Context: The composition root -- the only place that knows about both providers.py and
        executor.py, and the only writer to the ledger
Pattern: Orchestration without authority. Nothing here decides anything; it sequences the
        stages and appends the evidence of what they decided.
Future: Ledger writes are best-effort in-process. A production loop needs the append to
        succeed before the effect is considered recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .authority import Decision, Proposal, ProposalBundle, Route
from .executor import ClaudeExecutionAgent, ExecutionRun
from .ledger import EventLedger
from .providers import OpenAIProposalGenerator
from .shadow import Provenance, ShadowEvidenceLog


@dataclass
class CrossModelAuthorityLoop:
    proposer: OpenAIProposalGenerator
    executor: ClaudeExecutionAgent
    ledger: EventLedger
    policy_version: str
    environment_version: str
    shadow: ShadowEvidenceLog | None = None

    def __post_init__(self) -> None:
        """
        intent: Guarantee that every decision this loop acts on is committed to its ledger
                first, by wiring the executor's commit hook to it
        constraint: An executor that already has a commit hook keeps it; the loop cannot
                    tell whether that hook writes to this ledger, so a caller who sets one
                    takes responsibility for the ordering contract.
        """
        if self.executor.commit is None:
            self.executor.commit = self.commit

    def commit(self, decision: Decision, proposal: Proposal) -> None:
        """
        intent: Put the decision on the record and, for an audit, freeze what will be judged
        effect: An AUDIT-routed proposal is frozen into the shadow log as AUDIT provenance
                under its decision id, before the token exists. The independent label
                arrives later through shadow.adjudicate(decision_id, ...); an audit that
                is never labelled expires to INCONCLUSIVE and counts against the record.
                AUTONOMOUS executions are not frozen here: their labels are optional, and
                an optional slot that expires to a failure would punish not auditing.
        constraint: A freeze the log refuses raises, so the executor refuses the step --
                    an audit without a frozen target is not an audit.
        """
        self.ledger.commit_decision(decision, proposal)
        if self.shadow is not None and decision.route == Route.AUDIT:
            result = self.shadow.freeze(decision.decision_id, proposal, Provenance.AUDIT)
            if not result.accepted:
                raise RuntimeError(f"audit trial not frozen: {result.reason}")

    def propose(
        self,
        intent: str,
        context: Mapping[str, Any] | None = None,
    ) -> ProposalBundle:
        """
        intent: Generate a bundle and record its digest before anything acts on it
        effect: The bundle digest is in the ledger ahead of execution, so what was proposed
                cannot be reconstructed differently after the fact
        """
        bundle = self.proposer.generate(
            intent,
            execution_model=self.executor.model,
            execution_prompt_version=self.executor.prompt_version,
            policy_version=self.policy_version,
            environment_version=self.environment_version,
            context=context,
        )
        self.ledger.append(
            "proposal.bundle.created",
            bundle.bundle_id,
            "openai",
            {
                "bundle_digest": bundle.bundle_digest,
                "readiness": bundle.readiness.value,
                "proposal_digests": [p.proposal_digest for p in bundle.proposals],
            },
        )
        return bundle

    def execute(
        self,
        bundle: ProposalBundle,
        approved_proposal_ids: Iterable[str] = (),
    ) -> ExecutionRun:
        """
        intent: Run the bundle and chain one ledger event per step, refused steps included
        constraint: Refusals are recorded, not just successes. A history containing only the
                    operations that ran would answer "what happened" but not "what was
                    stopped", and the second question is the one an incident review asks.
        """
        run = self.executor.run(
            bundle,
            approved_proposal_ids=approved_proposal_ids,
        )
        for step in run.steps:
            self.ledger.append(
                "execution.step.recorded",
                step.proposal_id or bundle.bundle_id,
                "execution-gateway",
                {
                    "tool": step.tool_name,
                    "route": step.route.value,
                    "decision_id": step.decision.decision_id if step.decision else None,
                    "executed": step.executed,
                    "receipt": step.receipt,
                    "error": step.error,
                },
            )
        self.ledger.append(
            "execution.run.completed",
            bundle.bundle_id,
            "claude-execution-loop",
            {"status": run.status.value, "step_count": len(run.steps)},
        )
        return run
