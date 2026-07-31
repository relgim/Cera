"""CERA's deterministic, provider-neutral foundation."""

from .config import FoundationConfig
from .errors import (
    CanonicalizationError,
    CeraError,
    ConfigurationError,
    ContractValidationError,
    ErrorCode,
    IdentityError,
    RetryMode,
    StateConflictError,
    TransactionError,
)
from .ids import IdKind, TypedId, deterministic_id, new_id, require_kind
from .serialization import (
    canonical_bytes,
    canonical_json,
    canonical_sha256,
    domain_sha256,
    text_sha256,
    verify_sha256,
    verify_domain_sha256,
)

__all__ = [
    "CanonicalizationError",
    "CeraError",
    "ConfigurationError",
    "ContractValidationError",
    "ErrorCode",
    "FoundationConfig",
    "IdKind",
    "IdentityError",
    "RetryMode",
    "StateConflictError",
    "TransactionError",
    "TypedId",
    "canonical_bytes",
    "canonical_json",
    "canonical_sha256",
    "deterministic_id",
    "domain_sha256",
    "new_id",
    "require_kind",
    "text_sha256",
    "verify_sha256",
    "verify_domain_sha256",
]
