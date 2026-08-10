"""Trusted-local protected byte custody for provider-stage retry checkpoints.

Only privacy-safe hashes and sizes cross this port.  Exact prompt/result bytes
remain beneath the configured protected root and never enter SQLite, terminal
DTOs, logs, or UI projections.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import bytes_sha256, canonical_sha256, domain_sha256, re_is_sha256

from .provider_stage_retry import (
    MAXIMUM_PROVIDER_STAGE_ATTEMPTS,
    MAXIMUM_SAFE_INTEGER,
    ProviderStageCheckpointKind,
)


@dataclass(frozen=True, slots=True)
class ProtectedStageBlobReceiptV1:
    """Opaque, path-free receipt for one exact protected byte checkpoint."""

    SCHEMA_VERSION: ClassVar[str] = "cera.provider_stage_protected_blob_receipt.v1"

    schema_version: str
    blob_id_sha256: str
    chain_id: str
    checkpoint_kind: ProviderStageCheckpointKind
    attempt_number: int | None
    content_sha256: str
    size_bytes: int
    custody_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("protected stage blob schema changed")
        if type(self.checkpoint_kind) is not ProviderStageCheckpointKind:
            raise ContractValidationError("protected stage blob kind is not closed")
        if not self.chain_id.startswith("stage-retry-") or not re_is_sha256(
            self.chain_id.removeprefix("stage-retry-")
        ):
            raise ContractValidationError("protected stage blob chain is invalid")
        if self.checkpoint_kind is ProviderStageCheckpointKind.INPUT:
            if self.attempt_number is not None:
                raise ContractValidationError("protected input blob has an attempt")
        elif (
            type(self.attempt_number) is not int
            or not 1 <= self.attempt_number <= MAXIMUM_PROVIDER_STAGE_ATTEMPTS
        ):
            raise ContractValidationError("protected result blob attempt is invalid")
        if not re_is_sha256(self.blob_id_sha256) or not re_is_sha256(self.content_sha256):
            raise ContractValidationError("protected stage blob hash is invalid")
        if type(self.size_bytes) is not int or not 1 <= self.size_bytes <= MAXIMUM_SAFE_INTEGER:
            raise ContractValidationError("protected stage blob size is invalid")
        body = {
            "schema_version": self.SCHEMA_VERSION,
            "blob_id_sha256": self.blob_id_sha256,
            "chain_id": self.chain_id,
            "checkpoint_kind": self.checkpoint_kind.value,
            "attempt_number": self.attempt_number,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
        }
        if self.custody_sha256 != canonical_sha256(body):
            raise ContractValidationError("protected stage blob custody changed")

    @classmethod
    def create(
        cls,
        *,
        chain_id: str,
        checkpoint_kind: ProviderStageCheckpointKind,
        attempt_number: int | None,
        exact_bytes: bytes,
    ) -> ProtectedStageBlobReceiptV1:
        if not isinstance(exact_bytes, bytes) or not exact_bytes:
            raise ContractValidationError("protected stage bytes must be non-empty bytes")
        content_sha256 = bytes_sha256(exact_bytes)
        blob_id_sha256 = domain_sha256(
            "cera.provider_stage_protected_blob_id.v1",
            {
                "chain_id": chain_id,
                "checkpoint_kind": checkpoint_kind.value,
                "attempt_number": attempt_number,
                "content_sha256": content_sha256,
            },
        )
        body = {
            "schema_version": cls.SCHEMA_VERSION,
            "blob_id_sha256": blob_id_sha256,
            "chain_id": chain_id,
            "checkpoint_kind": checkpoint_kind.value,
            "attempt_number": attempt_number,
            "content_sha256": content_sha256,
            "size_bytes": len(exact_bytes),
        }
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            blob_id_sha256=blob_id_sha256,
            chain_id=chain_id,
            checkpoint_kind=checkpoint_kind,
            attempt_number=attempt_number,
            content_sha256=content_sha256,
            size_bytes=len(exact_bytes),
            custody_sha256=canonical_sha256(body),
        )


class ProtectedStageBlobPort(Protocol):
    def freeze(
        self,
        *,
        chain_id: str,
        checkpoint_kind: ProviderStageCheckpointKind,
        attempt_number: int | None,
        exact_bytes: bytes,
    ) -> ProtectedStageBlobReceiptV1: ...

    def load(self, receipt: ProtectedStageBlobReceiptV1) -> bytes: ...


class TrustedLocalProtectedStageBlobStore:
    """Small immutable blob store for one trusted local backend instance."""

    def __init__(self, root: Path) -> None:
        if not isinstance(root, Path) or not root.is_absolute() or root.parent == root:
            raise ContractValidationError("protected stage blob root must be bounded and absolute")
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.root.mkdir(parents=True, exist_ok=True)

    def freeze(
        self,
        *,
        chain_id: str,
        checkpoint_kind: ProviderStageCheckpointKind,
        attempt_number: int | None,
        exact_bytes: bytes,
    ) -> ProtectedStageBlobReceiptV1:
        receipt = ProtectedStageBlobReceiptV1.create(
            chain_id=chain_id,
            checkpoint_kind=checkpoint_kind,
            attempt_number=attempt_number,
            exact_bytes=exact_bytes,
        )
        target = self._path(receipt)
        if target.exists():
            self._require_exact(target.read_bytes(), receipt)
            return receipt
        temporary = self.root / f".{receipt.blob_id_sha256}.{uuid4().hex}.pending"
        try:
            with temporary.open("xb") as stream:
                stream.write(exact_bytes)
                stream.flush()
                os.fsync(stream.fileno())
            self._require_exact(temporary.read_bytes(), receipt)
            os.replace(temporary, target)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        self._require_exact(target.read_bytes(), receipt)
        return receipt

    def load(self, receipt: ProtectedStageBlobReceiptV1) -> bytes:
        if type(receipt) is not ProtectedStageBlobReceiptV1:
            raise ContractValidationError("protected stage blob receipt contract changed")
        try:
            exact_bytes = self._path(receipt).read_bytes()
        except OSError as exc:
            raise StateConflictError("protected stage blob is unavailable") from exc
        self._require_exact(exact_bytes, receipt)
        return exact_bytes

    def _path(self, receipt: ProtectedStageBlobReceiptV1) -> Path:
        return self.root / f"{receipt.blob_id_sha256}.bin"

    @staticmethod
    def _require_exact(
        exact_bytes: bytes,
        receipt: ProtectedStageBlobReceiptV1,
    ) -> None:
        if (
            len(exact_bytes) != receipt.size_bytes
            or bytes_sha256(exact_bytes) != receipt.content_sha256
        ):
            raise StateConflictError("protected stage blob bytes changed")
