"""Closed terminal-effect and postcondition evidence for continuous Job 4."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping

from cera.serialization import canonical_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _closed(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    actual = set(value)
    if actual != expected:
        extra = sorted(actual - expected)
        missing = sorted(expected - actual)
        raise ValueError(f"{label} fields differ: missing={missing}, extra={extra}")
    return value


def _count(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _flag(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def _optional_sha256(value: object, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be null or a lowercase SHA-256")
    return value


@dataclass(frozen=True, slots=True)
class ContinuousJob4OperationalCountersV1:
    live_story_writes: int
    production_database_writes: int
    deployment_operations: int
    remote_operations: int
    merge_operations: int
    push_operations: int
    service_changes: int
    installed_sillytavern_changes: int

    SCHEMA_VERSION = "cera.continuous_job4_operational_counters.v1"

    def __post_init__(self) -> None:
        for name in (
            "live_story_writes",
            "production_database_writes",
            "deployment_operations",
            "remote_operations",
            "merge_operations",
            "push_operations",
            "service_changes",
            "installed_sillytavern_changes",
        ):
            _count(getattr(self, name), name)

    @property
    def story_database_writes(self) -> int:
        return self.live_story_writes + self.production_database_writes

    @property
    def deployment_remote_or_push_effects(self) -> int:
        return sum(
            (
                self.deployment_operations,
                self.remote_operations,
                self.merge_operations,
                self.push_operations,
                self.service_changes,
                self.installed_sillytavern_changes,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "live_story_writes": self.live_story_writes,
            "production_database_writes": self.production_database_writes,
            "deployment_operations": self.deployment_operations,
            "remote_operations": self.remote_operations,
            "merge_operations": self.merge_operations,
            "push_operations": self.push_operations,
            "service_changes": self.service_changes,
            "installed_sillytavern_changes": self.installed_sillytavern_changes,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4OperationalCountersV1":
        keys = {
            "schema_version",
            "live_story_writes",
            "production_database_writes",
            "deployment_operations",
            "remote_operations",
            "merge_operations",
            "push_operations",
            "service_changes",
            "installed_sillytavern_changes",
        }
        value = _closed(raw, keys, "operational counters")
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("operational counters schema version changed")
        return cls(
            **{
                name: _count(value[name], name)
                for name in keys
                if name != "schema_version"
            }
        )


@dataclass(frozen=True, slots=True)
class ContinuousJob4PostconditionsV1:
    execution_mode: str
    source_database_sha256_before: str | None
    source_database_sha256_after: str | None
    disposable_database_sha256_before: str | None
    disposable_database_sha256_after: str | None
    database_integrity_check: str | None
    database_foreign_key_findings: int | None
    active_profile_sha256_before: str | None
    active_profile_sha256_after: str | None
    active_profile_inspection_status: str
    thread_archival: Mapping[str, bool]
    accepted_session_synchronized: bool
    accepted_final_sequences_injected: bool
    call_ledger_dispatches: int
    scripted_transport_invocations: int

    SCHEMA_VERSION = "cera.continuous_job4_postconditions.v1"

    def __post_init__(self) -> None:
        if self.execution_mode not in {
            "live_one_shot",
            "provider_free_scripted_v7",
            "provider_free_scripted_v8",
        }:
            raise ValueError("terminal execution mode is unsupported")
        for name in (
            "source_database_sha256_before",
            "source_database_sha256_after",
            "disposable_database_sha256_before",
            "disposable_database_sha256_after",
            "active_profile_sha256_before",
            "active_profile_sha256_after",
        ):
            _optional_sha256(getattr(self, name), name)
        if self.database_integrity_check is not None and not isinstance(
            self.database_integrity_check, str
        ):
            raise ValueError("database_integrity_check must be null or a string")
        if self.database_foreign_key_findings is not None:
            _count(
                self.database_foreign_key_findings,
                "database_foreign_key_findings",
            )
        if self.active_profile_inspection_status not in {"verified", "failed"}:
            raise ValueError("active profile inspection status is invalid")
        archival = _closed(
            self.thread_archival,
            {"planner", "validator"},
            "thread archival",
        )
        for role in ("planner", "validator"):
            _flag(archival[role], f"thread_archival.{role}")
        _flag(self.accepted_session_synchronized, "accepted_session_synchronized")
        _flag(
            self.accepted_final_sequences_injected,
            "accepted_final_sequences_injected",
        )
        _count(self.call_ledger_dispatches, "call_ledger_dispatches")
        _count(
            self.scripted_transport_invocations,
            "scripted_transport_invocations",
        )

    @property
    def active_route_changes(self) -> int:
        if (
            self.active_profile_inspection_status == "verified"
            and self.active_profile_sha256_before is not None
            and self.active_profile_sha256_after is not None
            and self.active_profile_sha256_before
            == self.active_profile_sha256_after
        ):
            return 0
        return 1

    def call_ledger_reconciled(self, provider_calls: int) -> bool:
        if self.execution_mode.startswith("provider_free_scripted_"):
            return (
                provider_calls == 0
                and self.call_ledger_dispatches
                == self.scripted_transport_invocations
            )
        return (
            self.scripted_transport_invocations == 0
            and self.call_ledger_dispatches == provider_calls
        )

    def failure_codes(self, provider_calls: int) -> tuple[str, ...]:
        failures: list[str] = []
        if (
            self.source_database_sha256_before is None
            or self.source_database_sha256_after is None
            or self.source_database_sha256_before
            != self.source_database_sha256_after
        ):
            failures.append("source_database_unverified_or_changed")
        if (
            self.disposable_database_sha256_before is None
            or self.disposable_database_sha256_after is None
            or self.disposable_database_sha256_before
            != self.disposable_database_sha256_after
        ):
            failures.append("disposable_database_unverified_or_changed")
        if self.database_integrity_check != "ok":
            failures.append("database_integrity_failed")
        if self.database_foreign_key_findings != 0:
            failures.append("database_foreign_keys_failed")
        if self.active_profile_inspection_status != "verified":
            failures.append("active_profile_inspection_failed")
        elif self.active_route_changes:
            failures.append("active_profile_changed")
        if not all(self.thread_archival.values()):
            failures.append("thread_archival_incomplete")
        if not self.accepted_session_synchronized:
            failures.append("accepted_session_unsynchronized")
        if not self.accepted_final_sequences_injected:
            failures.append("accepted_sequence_injection_incomplete")
        if not self.call_ledger_reconciled(provider_calls):
            failures.append("provider_call_ledger_mismatch")
        return tuple(failures)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "execution_mode": self.execution_mode,
            "source_database_sha256_before": self.source_database_sha256_before,
            "source_database_sha256_after": self.source_database_sha256_after,
            "disposable_database_sha256_before": self.disposable_database_sha256_before,
            "disposable_database_sha256_after": self.disposable_database_sha256_after,
            "database_integrity_check": self.database_integrity_check,
            "database_foreign_key_findings": self.database_foreign_key_findings,
            "active_profile_sha256_before": self.active_profile_sha256_before,
            "active_profile_sha256_after": self.active_profile_sha256_after,
            "active_profile_inspection_status": self.active_profile_inspection_status,
            "thread_archival": dict(self.thread_archival),
            "accepted_session_synchronized": self.accepted_session_synchronized,
            "accepted_final_sequences_injected": self.accepted_final_sequences_injected,
            "call_ledger_dispatches": self.call_ledger_dispatches,
            "scripted_transport_invocations": self.scripted_transport_invocations,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4PostconditionsV1":
        keys = {
            "schema_version",
            "execution_mode",
            "source_database_sha256_before",
            "source_database_sha256_after",
            "disposable_database_sha256_before",
            "disposable_database_sha256_after",
            "database_integrity_check",
            "database_foreign_key_findings",
            "active_profile_sha256_before",
            "active_profile_sha256_after",
            "active_profile_inspection_status",
            "thread_archival",
            "accepted_session_synchronized",
            "accepted_final_sequences_injected",
            "call_ledger_dispatches",
            "scripted_transport_invocations",
        }
        value = _closed(raw, keys, "terminal postconditions")
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("terminal postconditions schema version changed")
        return cls(
            execution_mode=value["execution_mode"],
            source_database_sha256_before=value["source_database_sha256_before"],
            source_database_sha256_after=value["source_database_sha256_after"],
            disposable_database_sha256_before=value[
                "disposable_database_sha256_before"
            ],
            disposable_database_sha256_after=value[
                "disposable_database_sha256_after"
            ],
            database_integrity_check=value["database_integrity_check"],
            database_foreign_key_findings=value["database_foreign_key_findings"],
            active_profile_sha256_before=value["active_profile_sha256_before"],
            active_profile_sha256_after=value["active_profile_sha256_after"],
            active_profile_inspection_status=value[
                "active_profile_inspection_status"
            ],
            thread_archival=value["thread_archival"],
            accepted_session_synchronized=value[
                "accepted_session_synchronized"
            ],
            accepted_final_sequences_injected=value[
                "accepted_final_sequences_injected"
            ],
            call_ledger_dispatches=value["call_ledger_dispatches"],
            scripted_transport_invocations=value[
                "scripted_transport_invocations"
            ],
        )


@dataclass(frozen=True, slots=True)
class ContinuousJob4EffectEvidenceV1:
    provider_calls: int
    active_route_changes: int
    operational_counters: ContinuousJob4OperationalCountersV1

    SCHEMA_VERSION = "cera.continuous_job4_effect_evidence.v1"

    def __post_init__(self) -> None:
        _count(self.provider_calls, "provider_calls")
        _count(self.active_route_changes, "active_route_changes")

    @property
    def canonical_effects(self) -> dict[str, int]:
        return {
            "provider_calls": self.provider_calls,
            "story_database_writes": self.operational_counters.story_database_writes,
            "active_route_changes": self.active_route_changes,
            "deployment_remote_or_push_effects": (
                self.operational_counters.deployment_remote_or_push_effects
            ),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "provider_calls": self.provider_calls,
            "active_route_changes": self.active_route_changes,
            "operational_counters": self.operational_counters.to_dict(),
            "canonical_effects": self.canonical_effects,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4EffectEvidenceV1":
        value = _closed(
            raw,
            {
                "schema_version",
                "provider_calls",
                "active_route_changes",
                "operational_counters",
                "canonical_effects",
            },
            "effect evidence",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("effect evidence schema version changed")
        instance = cls(
            provider_calls=_count(value["provider_calls"], "provider_calls"),
            active_route_changes=_count(
                value["active_route_changes"], "active_route_changes"
            ),
            operational_counters=ContinuousJob4OperationalCountersV1.from_dict(
                value["operational_counters"]
            ),
        )
        canonical = _closed(
            value["canonical_effects"],
            {
                "provider_calls",
                "story_database_writes",
                "active_route_changes",
                "deployment_remote_or_push_effects",
            },
            "canonical effects",
        )
        decoded = {
            name: _count(canonical[name], f"canonical_effects.{name}")
            for name in canonical
        }
        if decoded != instance.canonical_effects:
            raise ValueError("canonical effects contradict typed effect evidence")
        return instance


@dataclass(frozen=True, slots=True)
class ContinuousJob4TerminalEvidenceV1:
    execution_status: str
    effect_evidence: ContinuousJob4EffectEvidenceV1
    postconditions: ContinuousJob4PostconditionsV1
    status: str
    failure_codes: tuple[str, ...]

    SCHEMA_VERSION = "cera.continuous_job4_terminal_evidence.v1"

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    @classmethod
    def build(
        cls,
        *,
        execution_status: str,
        provider_calls: int,
        operational_counters: ContinuousJob4OperationalCountersV1,
        postconditions: ContinuousJob4PostconditionsV1,
    ) -> "ContinuousJob4TerminalEvidenceV1":
        if execution_status not in {"completed", "failed"}:
            raise ValueError("execution status must be completed or failed")
        effects = ContinuousJob4EffectEvidenceV1(
            provider_calls=provider_calls,
            active_route_changes=postconditions.active_route_changes,
            operational_counters=operational_counters,
        )
        failures = list(postconditions.failure_codes(provider_calls))
        if execution_status != "completed":
            failures.insert(0, "execution_not_completed")
        if effects.canonical_effects["story_database_writes"]:
            failures.append("story_database_effect_detected")
        if effects.active_route_changes:
            failures.append("active_route_effect_detected")
        if effects.canonical_effects["deployment_remote_or_push_effects"]:
            failures.append("prohibited_operational_effect_detected")
        unique_failures = tuple(dict.fromkeys(failures))
        return cls(
            execution_status=execution_status,
            effect_evidence=effects,
            postconditions=postconditions,
            status="completed" if not unique_failures else "failed",
            failure_codes=unique_failures,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "execution_status": self.execution_status,
            "effect_evidence": self.effect_evidence.to_dict(),
            "postconditions": self.postconditions.to_dict(),
            "status": self.status,
            "failure_codes": list(self.failure_codes),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4TerminalEvidenceV1":
        value = _closed(
            raw,
            {
                "schema_version",
                "execution_status",
                "effect_evidence",
                "postconditions",
                "status",
                "failure_codes",
            },
            "terminal evidence",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("terminal evidence schema version changed")
        if not isinstance(value["failure_codes"], list) or not all(
            isinstance(item, str) and item for item in value["failure_codes"]
        ):
            raise ValueError("terminal failure_codes must be a string array")
        supplied_effects = ContinuousJob4EffectEvidenceV1.from_dict(
            value["effect_evidence"]
        )
        rebuilt = cls.build(
            execution_status=value["execution_status"],
            provider_calls=supplied_effects.provider_calls,
            operational_counters=supplied_effects.operational_counters,
            postconditions=ContinuousJob4PostconditionsV1.from_dict(
                value["postconditions"]
            ),
        )
        if supplied_effects != rebuilt.effect_evidence:
            raise ValueError("effect evidence contradicts terminal postconditions")
        if value["status"] != rebuilt.status:
            raise ValueError("terminal status contradicts mandatory evidence")
        if tuple(value["failure_codes"]) != rebuilt.failure_codes:
            raise ValueError("terminal failure codes contradict mandatory evidence")
        return rebuilt
