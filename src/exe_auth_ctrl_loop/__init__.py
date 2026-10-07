"""
Intent: Grant an agent execution authority only where a track record earned under that
        exact configuration says it has been earned
Context: OpenAI proposes, Claude requests execution, a deterministic host-owned controller
        authorizes, and the gateway is the only path to a registered side-effect handler
Pattern: Model output is never authority. Every model claim is a request the host verifies
        against its own registry, evidence, and policy before anything runs.
Future: Research prototype, single process. See the production trust boundary in README.md
        before reusing any of it -- the gateway, the ledger, and token consumption all need
        different implementations outside one process.

Module map:
  authority.py  decision, evidence, capability issuance, gateway
  tools.py      host-owned tool registry and schema validation
  providers.py  OpenAI proposal generation
  executor.py   Claude execution requests, verified per operation
  pipeline.py   composition and ledger recording
  ledger.py     tamper-evident hash chain
  lifecycle.py  explicit UNESTABLISHED/QUALIFYING/AUTONOMOUS/SUSPENDED state machine
  sequential.py anytime-valid Beta-mixture lower bound for the evidence gate
  shadow.py     frozen shadow trials, oracle labels, provenance-separated counts
  mcp.py        signed authority metadata for MCP tools/call
"""

from __future__ import annotations

from .authority import (
    AuthorityController,
    AuthorizationToken,
    Decision,
    EvidenceSnapshot,
    EvidenceStore,
    ExecutionGateway,
    Outcome,
    OutcomeStatus,
    PartitionKey,
    Policy,
    Proposal,
    ProposalBundle,
    ProposalReadiness,
    RedemptionGuard,
    Route,
    digest,
    utcnow,
    wilson_lower_bound,
)
from .executor import ClaudeExecutionAgent, ExecutionRun, ExecutionStatus, ExecutionStep
from .ledger import EventLedger, LedgerEvent
from .lifecycle import LifecycleEvent, LifecycleManager, LifecycleState, PartitionRecord
from .mcp import (
    EXTENSION_PREFIX,
    EXTENSION_VERSION,
    MetaKeyError,
    MetaVerificationError,
    VerifiedAuthority,
    attach_meta,
    build_call_meta,
    call_digest,
    evidence_snapshot_hash,
    meta_key,
    validate_meta_prefix,
    verify_call_meta,
)
from .pipeline import CrossModelAuthorityLoop
from .providers import (
    OpenAIProposalGenerator,
    ProposalDraftModel,
    ProposalGenerationError,
    ProposedActionModel,
    confidence_bin,
)
from .sequential import beta_mixture_lower_bound, crosses, log_mixture_martingale
from .shadow import (
    COUNTING_PROVENANCE,
    Counts,
    Oracle,
    OracleKind,
    Provenance,
    Reason,
    Result,
    ReviewAction,
    ShadowEvidenceLog,
    ShadowTrial,
    TrialLabel,
    TrialStatus,
)
from .tools import ToolDefinition, ToolRegistry, ToolValidationError

__version__ = "0.2.0"

__all__ = [
    "COUNTING_PROVENANCE",
    "EXTENSION_PREFIX",
    "EXTENSION_VERSION",
    "AuthorityController",
    "AuthorizationToken",
    "ClaudeExecutionAgent",
    "Counts",
    "CrossModelAuthorityLoop",
    "Decision",
    "EventLedger",
    "EvidenceSnapshot",
    "EvidenceStore",
    "ExecutionGateway",
    "ExecutionRun",
    "ExecutionStatus",
    "ExecutionStep",
    "LedgerEvent",
    "LifecycleState",
    "LifecycleManager",
    "LifecycleEvent",
    "MetaKeyError",
    "MetaVerificationError",
    "OpenAIProposalGenerator",
    "Oracle",
    "OracleKind",
    "Outcome",
    "OutcomeStatus",
    "PartitionKey",
    "PartitionRecord",
    "Policy",
    "Proposal",
    "ProposalBundle",
    "ProposalDraftModel",
    "ProposalGenerationError",
    "ProposalReadiness",
    "ProposedActionModel",
    "Provenance",
    "Reason",
    "RedemptionGuard",
    "Result",
    "ReviewAction",
    "Route",
    "ShadowEvidenceLog",
    "ShadowTrial",
    "ToolDefinition",
    "ToolRegistry",
    "ToolValidationError",
    "TrialLabel",
    "TrialStatus",
    "VerifiedAuthority",
    "attach_meta",
    "beta_mixture_lower_bound",
    "build_call_meta",
    "call_digest",
    "confidence_bin",
    "crosses",
    "digest",
    "evidence_snapshot_hash",
    "log_mixture_martingale",
    "meta_key",
    "utcnow",
    "validate_meta_prefix",
    "verify_call_meta",
    "wilson_lower_bound",
]
