"""Cross-model execution-authority control loop.

OpenAI proposes, Claude requests execution, a deterministic host-owned
authority controller authorizes, and the execution gateway is the only
path to a registered side-effect handler.
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
    Route,
    digest,
    utcnow,
    wilson_lower_bound,
)
from .executor import ClaudeExecutionAgent, ExecutionRun, ExecutionStatus, ExecutionStep
from .ledger import EventLedger, LedgerEvent
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
from .tools import ToolDefinition, ToolRegistry, ToolValidationError

__version__ = "0.1.0"

__all__ = [
    "EXTENSION_PREFIX",
    "EXTENSION_VERSION",
    "AuthorityController",
    "AuthorizationToken",
    "ClaudeExecutionAgent",
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
    "MetaKeyError",
    "MetaVerificationError",
    "OpenAIProposalGenerator",
    "Outcome",
    "OutcomeStatus",
    "PartitionKey",
    "Policy",
    "Proposal",
    "ProposalBundle",
    "ProposalDraftModel",
    "ProposalGenerationError",
    "ProposalReadiness",
    "ProposedActionModel",
    "Route",
    "ToolDefinition",
    "ToolRegistry",
    "ToolValidationError",
    "VerifiedAuthority",
    "attach_meta",
    "build_call_meta",
    "call_digest",
    "confidence_bin",
    "digest",
    "evidence_snapshot_hash",
    "meta_key",
    "utcnow",
    "validate_meta_prefix",
    "verify_call_meta",
    "wilson_lower_bound",
]
