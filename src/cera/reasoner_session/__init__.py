"""Branch-bound provider-neutral Reasoner session architecture."""

from .models import (
    AcceptedTurnReceipt,
    CheckpointStatus,
    CompactionState,
    ConstraintBinding,
    ConstraintScope,
    ConstraintStatus,
    ContextAuthorityDelta,
    ContextEvidenceBinding,
    CreatorConstraintRecord,
    EvidenceDelivery,
    ProviderSessionHandle,
    ProviderThreadCustodyDescriptor,
    ProviderThreadCustodyEvent,
    ProviderThreadCustodyEventKind,
    ProviderThreadRole,
    ProviderThreadStorageMode,
    ReasonerSessionCheckpoint,
    ReasonerSessionCompatibility,
    ReasonerSessionLedger,
    RejectedCandidateReceipt,
    SessionReconstructionBundle,
    SessionRole,
    SessionStatus,
    SessionTurnMode,
    SessionUsageReceiptV2,
)
from .ports import ReasonerSessionPort
from .fake import InMemoryReasonerSessionPort
from .coordinator import BranchBoundReasonerSessionCoordinator
from .codex_stored import (
    CodexStoredThreadSessionPort,
    OpenAICodexStoredThreadBackend,
    StoredThreadBackend,
)
from .materialization import AcceptedEvidenceMaterialization, reuse_accepted_evidence

__all__ = [
    "AcceptedTurnReceipt",
    "CheckpointStatus",
    "CompactionState",
    "ConstraintBinding",
    "ConstraintScope",
    "ConstraintStatus",
    "ContextAuthorityDelta",
    "ContextEvidenceBinding",
    "CreatorConstraintRecord",
    "EvidenceDelivery",
    "ProviderSessionHandle",
    "ProviderThreadCustodyDescriptor",
    "ProviderThreadCustodyEvent",
    "ProviderThreadCustodyEventKind",
    "ProviderThreadRole",
    "ProviderThreadStorageMode",
    "ReasonerSessionCheckpoint",
    "ReasonerSessionCompatibility",
    "ReasonerSessionLedger",
    "ReasonerSessionPort",
    "InMemoryReasonerSessionPort",
    "BranchBoundReasonerSessionCoordinator",
    "CodexStoredThreadSessionPort",
    "OpenAICodexStoredThreadBackend",
    "StoredThreadBackend",
    "AcceptedEvidenceMaterialization",
    "reuse_accepted_evidence",
    "NativeStoredReasonerSessionRuntime",
    "StoredReasonerCandidateBinding",
    "ReasonerSessionPromptCompilation",
    "compile_shadow_reasoner_session_prompt",
    "project_session_usage_receipt",
    "RejectedCandidateReceipt",
    "SessionReconstructionBundle",
    "SessionRole",
    "SessionStatus",
    "SessionTurnMode",
    "SessionUsageReceiptV2",
]


def __getattr__(name: str):
    """Keep storage-safe contracts free of provider/reasoner import cycles."""

    if name in {
        "ReasonerSessionPromptCompilation",
        "compile_shadow_reasoner_session_prompt",
    }:
        from .prompting import (
            ReasonerSessionPromptCompilation,
            compile_shadow_reasoner_session_prompt,
        )

        return {
            "ReasonerSessionPromptCompilation": ReasonerSessionPromptCompilation,
            "compile_shadow_reasoner_session_prompt": (
                compile_shadow_reasoner_session_prompt
            ),
        }[name]
    if name == "project_session_usage_receipt":
        from .observability import project_session_usage_receipt

        return project_session_usage_receipt
    if name in {
        "NativeStoredReasonerSessionRuntime",
        "StoredReasonerCandidateBinding",
    }:
        from .runtime import (
            NativeStoredReasonerSessionRuntime,
            StoredReasonerCandidateBinding,
        )

        return {
            "NativeStoredReasonerSessionRuntime": NativeStoredReasonerSessionRuntime,
            "StoredReasonerCandidateBinding": StoredReasonerCandidateBinding,
        }[name]
    raise AttributeError(name)
