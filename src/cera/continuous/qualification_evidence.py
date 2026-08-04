"""Immutable, qualification-only custody for benign raw provider results."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import bytes_sha256, canonical_bytes


_RAW_RESULT_ROLES = frozenset({"validator", "reader"})


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


class QualificationRawProviderJsonCaptureRegistry:
    """Own capture paths beneath one explicit qualification evidence root."""

    def __init__(self, evidence_root: Path) -> None:
        if not evidence_root.is_absolute():
            raise ContractValidationError(
                "qualification raw-result registry root must be absolute"
            )
        self.evidence_root = evidence_root.resolve()
        self.stage: str | None = None
        self._counters = {role: 0 for role in _RAW_RESULT_ROLES}
        self._by_session: dict[str, dict[str, str]] = {}

    def configure(self, stage: str) -> None:
        if self.stage is not None:
            raise StateConflictError(
                "qualification raw-result registry stage is already configured"
            )
        if (
            not isinstance(stage, str)
            or not stage.strip()
            or Path(stage).name != stage
            or stage in {".", ".."}
        ):
            raise ContractValidationError(
                "qualification raw-result registry stage is not a local name"
            )
        self.stage = stage

    def allocate(self, role: str) -> QualificationRawProviderJsonCapture:
        if self.stage is None:
            raise StateConflictError(
                "qualification raw-result registry stage is not configured"
            )
        if role not in _RAW_RESULT_ROLES:
            raise ContractValidationError(
                "qualification raw-result registry role is unsupported"
            )
        self._counters[role] += 1
        artifact = (
            self.evidence_root
            / self.stage
            / "raw_provider_results"
            / f"{role}_call_{self._counters[role]:04d}"
            / "RAW_PROVIDER_RESULT.json"
        )
        return QualificationRawProviderJsonCapture(artifact)

    def bind(
        self,
        role: str,
        physical_session_sha256: str | None,
        capture: QualificationRawProviderJsonCapture,
    ) -> None:
        if role not in _RAW_RESULT_ROLES:
            raise ContractValidationError(
                "qualification raw-result registry role is unsupported"
            )
        if not isinstance(physical_session_sha256, str) or not physical_session_sha256:
            raise ContractValidationError(
                f"{role} result omitted physical-session identity"
            )
        evidence = capture.evidence(evidence_root=self.evidence_root)
        key = f"{role}:{physical_session_sha256}"
        if key in self._by_session:
            raise StateConflictError(
                f"{role} physical-session capture identity was reused"
            )
        self._by_session[key] = evidence

    def for_session(
        self, role: str, physical_session_sha256: str | None
    ) -> dict[str, str] | None:
        if physical_session_sha256 is None:
            return None
        return self._by_session.get(f"{role}:{physical_session_sha256}")

    def inventory(self) -> list[dict[str, str]]:
        records: list[dict[str, str]] = []
        for path in sorted(
            self.evidence_root.glob(
                "*/raw_provider_results/*/RAW_PROVIDER_RESULT.json"
            )
        ):
            parent = path.parent.name
            role = parent.split("_call_", 1)[0]
            records.append(
                {
                    "role": role,
                    "raw_provider_result_path": path.relative_to(
                        self.evidence_root
                    ).as_posix(),
                    "raw_provider_result_sha256": bytes_sha256(path.read_bytes()),
                }
            )
        return records
