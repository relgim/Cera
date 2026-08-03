"""Immutable, qualification-only custody for benign raw provider results."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import bytes_sha256, canonical_bytes


@dataclass(frozen=True, slots=True)
class QualificationRawProviderJsonCapture:
    """Persist one parsed JSON result before DTO decoding, without overwrite."""

    artifact_path: Path
    _artifact_sha256: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.artifact_path.is_absolute():
            raise ContractValidationError(
                "qualification raw-result artifact path must be absolute"
            )
        if self.artifact_path.name != "RAW_PROVIDER_RESULT.json":
            raise ContractValidationError(
                "qualification raw-result artifact name is not canonical"
            )

    def __call__(self, value: dict[str, Any]) -> None:
        if not isinstance(value, dict):
            raise ContractValidationError(
                "qualification raw provider result must be a JSON object"
            )
        if self._artifact_sha256 is not None or self.artifact_path.exists():
            raise StateConflictError(
                "qualification raw provider result is already immutable"
            )
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = canonical_bytes(value) + b"\n"
        try:
            with self.artifact_path.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise StateConflictError(
                "qualification raw provider result is already immutable"
            ) from exc
        object.__setattr__(self, "_artifact_sha256", bytes_sha256(payload))

    @property
    def artifact_sha256(self) -> str:
        if self._artifact_sha256 is None:
            raise StateConflictError(
                "qualification raw provider result has not been captured"
            )
        return self._artifact_sha256

    def evidence(self, *, evidence_root: Path) -> dict[str, str]:
        root = evidence_root.resolve()
        artifact = self.artifact_path.resolve()
        try:
            relative = artifact.relative_to(root)
        except ValueError as exc:
            raise ContractValidationError(
                "qualification raw-result artifact escaped its evidence root"
            ) from exc
        return {
            "raw_provider_result_path": relative.as_posix(),
            "raw_provider_result_sha256": self.artifact_sha256,
        }
