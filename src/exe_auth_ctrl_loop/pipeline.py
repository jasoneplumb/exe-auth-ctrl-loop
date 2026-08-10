from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .authority import ProposalBundle
from .executor import ClaudeExecutionAgent, ExecutionRun
from .ledger import EventLedger
from .providers import OpenAIProposalGenerator


@dataclass
class CrossModelAuthorityLoop:
    proposer: OpenAIProposalGenerator
    executor: ClaudeExecutionAgent
    ledger: EventLedger
    policy_version: str
    environment_version: str

    def propose(
        self,
        intent: str,
        context: Mapping[str, Any] | None = None,
    ) -> ProposalBundle:
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
