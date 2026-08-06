"""Machine-readable provider-call boundary authority for governed live runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping, Protocol

from cera.errors import ContractValidationError, StateConflictError


_ROLE_NAMES = frozenset({"planner", "writer", "validator", "reader"})


def _required_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractValidationError(f"{label} must be non-empty text")
    return value


def _sha256_text(value: str, label: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ContractValidationError(f"{label} must be a lowercase SHA-256")
    return value


def _git_oid(value: str, label: str) -> str:
    if len(value) not in (40, 64) or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ContractValidationError(f"{label} must be a lowercase Git object ID")
    return value


@dataclass(frozen=True, slots=True)
class ProviderRoleAuthorityV1:
    role: str
    route: str
    model: str
    effort: str
    adapter: str
    prompt: str
    schema: str
    instructions_sha256: str

    def __post_init__(self) -> None:
        if self.role not in _ROLE_NAMES:
            raise ContractValidationError("run authority role is not closed")
        for label in ("route", "model", "effort", "adapter", "prompt", "schema"):
            _required_text(getattr(self, label), f"run_authority.role.{label}")
        _sha256_text(
            self.instructions_sha256,
            "run_authority.role.instructions_sha256",
        )


@dataclass(frozen=True, slots=True)
class RunAuthorityV1:
    queue_revision: str
    active_goal: int
    run_id: str
    source_commit: str
    source_tree: str
    world_id: str
    branch_id: str
    session_id: str
    turn_source_sha256: str
    profile_sha256: str
    roles: tuple[ProviderRoleAuthorityV1, ...]
    maximum_writer_attempts: int
    call_ceilings: tuple[tuple[str, int], ...]
    excluded_effects: tuple[str, ...]

    SCHEMA_VERSION = "cera.run_authority.v1"

    def __post_init__(self) -> None:
        for label in (
            "queue_revision",
            "run_id",
            "world_id",
            "branch_id",
            "session_id",
        ):
            _required_text(getattr(self, label), f"run_authority.{label}")
        if self.active_goal < 0:
            raise ContractValidationError("run authority goal is invalid")
        for label in ("source_commit", "source_tree"):
            _git_oid(getattr(self, label), f"run_authority.{label}")
        for label in ("turn_source_sha256", "profile_sha256"):
            _sha256_text(getattr(self, label), f"run_authority.{label}")
        role_names = tuple(role.role for role in self.roles)
        if set(role_names) != _ROLE_NAMES or len(role_names) != len(_ROLE_NAMES):
            raise ContractValidationError("run authority must bind each provider role once")
        if not 1 <= self.maximum_writer_attempts <= 3:
            raise ContractValidationError("run authority Writer attempt bound is invalid")
        ceiling_names = tuple(name for name, _value in self.call_ceilings)
        if len(set(ceiling_names)) != len(ceiling_names):
            raise ContractValidationError("run authority call ceilings are duplicated")
        if any(not name or value < 0 for name, value in self.call_ceilings):
            raise ContractValidationError("run authority call ceiling is invalid")
        if not self.excluded_effects or len(set(self.excluded_effects)) != len(
            self.excluded_effects
        ):
            raise ContractValidationError("run authority excluded effects are invalid")

    def payload(self) -> dict[str, object]:
        value = asdict(self)
        value["schema_version"] = self.SCHEMA_VERSION
        return value

    @property
    def sha256(self) -> str:
        encoded = json.dumps(
            self.payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class _EvidenceSnapshot(Protocol):
    def snapshot(self) -> dict[str, object]: ...


class _DispatchedLedger(Protocol):
    @property
    def dispatched_call_count(self) -> int: ...


class ProviderBoundaryAuthorityGuardV1:
    """Fail closed before calls while permitting exact higher-roadmap continuity."""

    def __init__(
        self,
        *,
        pointer_path: Path,
        initial_roadmap_revision: str,
        authority: RunAuthorityV1,
        evidence: _EvidenceSnapshot,
        ledger: _DispatchedLedger,
    ) -> None:
        self.pointer_path = pointer_path.resolve()
        self.initial_roadmap_revision = _required_text(
            initial_roadmap_revision,
            "initial_roadmap_revision",
        )
        self.authority = authority
        self.evidence = evidence
        self.ledger = ledger

    def prove_before_provider_call(
        self,
        *,
        submitted_calls_by_family: Mapping[str, int] | None = None,
    ) -> None:
        snapshot = self.evidence.snapshot()
        calls = snapshot.get("calls")
        if not isinstance(calls, list):
            raise StateConflictError("operation evidence snapshot lacks calls")
        if any(not isinstance(call, dict) or not call.get("terminal") for call in calls):
            raise StateConflictError("an operation-evidence directory is not terminal")
        if len(calls) != self.ledger.dispatched_call_count:
            raise StateConflictError("operation evidence and provider ledger counts differ")

        pointer = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        if pointer.get("status") != "active" or pointer.get("pause_lifted") is not True:
            raise StateConflictError("manager authority is paused or inactive")
        if pointer.get("queue_revision") != self.authority.queue_revision:
            raise StateConflictError("manager queue changed before provider operation")
        if pointer.get("active_goal") != self.authority.active_goal:
            raise StateConflictError("manager goal changed before provider operation")
        authorized_identity = pointer.get(
            "authorized_identity",
            pointer.get("authorized_identity_after_gates"),
        )
        if authorized_identity != self.authority.run_id:
            raise StateConflictError("manager run identity changed before provider operation")

        current_revision = str(pointer.get("roadmap_revision", ""))
        try:
            revision_order = int(current_revision) - int(self.initial_roadmap_revision)
        except ValueError as exc:
            raise StateConflictError("manager roadmap revision is invalid") from exc
        if revision_order < 0:
            raise StateConflictError("manager roadmap regressed before provider operation")
        if revision_order > 0 and (
            pointer.get("continue_active_identity_at_safe_boundary") is not True
            or pointer.get("run_authority_sha256") != self.authority.sha256
        ):
            raise StateConflictError(
                "higher manager roadmap lacks identical explicit run authority"
            )

        if submitted_calls_by_family is not None:
            ceilings = pointer.get("provider_calls_authorized_now")
            if isinstance(ceilings, dict):
                for family, submitted in submitted_calls_by_family.items():
                    ceiling = ceilings.get(family)
                    if not isinstance(ceiling, int) or submitted > ceiling:
                        raise StateConflictError(
                            "manager provider ceiling is below submitted calls"
                        )
