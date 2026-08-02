"""Closed terminal-effect and postcondition evidence for continuous Job 4."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Callable, Mapping, TypeVar

from cera.serialization import canonical_sha256

from .sessions import ContinuousThreadArchiveEvidenceV1


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_T = TypeVar("_T")


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


_CAPABILITY_COUNTER_FIELDS = {
    "live_story_write": "live_story_writes",
    "production_database_write": "production_database_writes",
    "deployment": "deployment_operations",
    "remote_operation": "remote_operations",
    "merge": "merge_operations",
    "push": "push_operations",
    "service_change": "service_changes",
    "installed_sillytavern_change": "installed_sillytavern_changes",
}
_ACTIVE_ROUTE_CAPABILITY = "active_route_mutation"
_ALL_EFFECT_CAPABILITIES = frozenset(
    (*_CAPABILITY_COUNTER_FIELDS, _ACTIVE_ROUTE_CAPABILITY)
)


@dataclass(frozen=True, slots=True)
class ContinuousJob4CapabilityLedgerV1:
    """Closed proof of custody for every non-provider effect capability."""

    capabilities: Mapping[str, Mapping[str, Any]]

    SCHEMA_VERSION = "cera.continuous_job4_capability_ledger.v1"
    DENIAL_CODE = "structurally_unavailable_to_job4_process"

    def __post_init__(self) -> None:
        capability_map = _closed(
            self.capabilities,
            set(_ALL_EFFECT_CAPABILITIES),
            "Job 4 capability ledger",
        )
        for name in sorted(_ALL_EFFECT_CAPABILITIES):
            entry = _closed(
                capability_map[name],
                {"mode", "count", "port_id_sha256", "denial_code", "sealed"},
                f"Job 4 capability {name}",
            )
            if entry["mode"] not in {"counted_port", "structurally_unavailable"}:
                raise ValueError(f"Job 4 capability mode is invalid: {name}")
            _count(entry["count"], f"Job 4 capability count {name}")
            _flag(entry["sealed"], f"Job 4 capability sealed {name}")
            if entry["sealed"] is not True:
                raise ValueError(f"Job 4 capability is not sealed: {name}")
            if entry["mode"] == "structurally_unavailable":
                if (
                    entry["count"] != 0
                    or entry["port_id_sha256"] is not None
                    or entry["denial_code"] != self.DENIAL_CODE
                ):
                    raise ValueError(
                        f"Job 4 denied capability evidence is contradictory: {name}"
                    )
            else:
                _optional_sha256(
                    entry["port_id_sha256"],
                    f"Job 4 capability port_id_sha256 {name}",
                )
                if entry["port_id_sha256"] is None or entry["denial_code"] is not None:
                    raise ValueError(
                        f"Job 4 counted capability evidence is incomplete: {name}"
                    )

    @classmethod
    def structurally_unavailable(cls) -> "ContinuousJob4CapabilityLedgerV1":
        return cls(
            capabilities={
                name: {
                    "mode": "structurally_unavailable",
                    "count": 0,
                    "port_id_sha256": None,
                    "denial_code": cls.DENIAL_CODE,
                    "sealed": True,
                }
                for name in sorted(_ALL_EFFECT_CAPABILITIES)
            }
        )

    @property
    def operational_counters(self) -> ContinuousJob4OperationalCountersV1:
        values = {
            counter_field: self.capabilities[capability]["count"]
            for capability, counter_field in _CAPABILITY_COUNTER_FIELDS.items()
        }
        return ContinuousJob4OperationalCountersV1(**values)

    @property
    def active_route_mutations(self) -> int:
        return self.capabilities[_ACTIVE_ROUTE_CAPABILITY]["count"]

    @property
    def all_zero_effects_structurally_denied(self) -> bool:
        return all(
            value["mode"] == "structurally_unavailable" and value["count"] == 0
            for value in self.capabilities.values()
        )

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "capabilities": {
                name: dict(self.capabilities[name])
                for name in sorted(self.capabilities)
            },
            "operational_counters": self.operational_counters.to_dict(),
            "active_route_mutations": self.active_route_mutations,
            "all_zero_effects_structurally_denied": (
                self.all_zero_effects_structurally_denied
            ),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4CapabilityLedgerV1":
        value = _closed(
            raw,
            {
                "schema_version",
                "capabilities",
                "operational_counters",
                "active_route_mutations",
                "all_zero_effects_structurally_denied",
            },
            "Job 4 capability ledger",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("Job 4 capability ledger schema changed")
        instance = cls(capabilities=value["capabilities"])
        if (
            ContinuousJob4OperationalCountersV1.from_dict(
                value["operational_counters"]
            )
            != instance.operational_counters
            or _count(
                value["active_route_mutations"], "active_route_mutations"
            )
            != instance.active_route_mutations
            or _flag(
                value["all_zero_effects_structurally_denied"],
                "all_zero_effects_structurally_denied",
            )
            != instance.all_zero_effects_structurally_denied
        ):
            raise ValueError("Job 4 capability summary contradicts capability custody")
        return instance


class ContinuousJob4CapabilityCustody:
    """Process-owned sealed port registry for otherwise excluded effects."""

    def __init__(self, *, counted_capabilities: tuple[str, ...] = ()) -> None:
        selected = set(counted_capabilities)
        if len(selected) != len(counted_capabilities) or not selected.issubset(
            _ALL_EFFECT_CAPABILITIES
        ):
            raise ValueError("Job 4 counted capability selection is invalid")
        self._counted = frozenset(selected)
        self._counts = {name: 0 for name in _ALL_EFFECT_CAPABILITIES}

    def record(self, capability: str) -> None:
        if capability not in _ALL_EFFECT_CAPABILITIES:
            raise ValueError("unknown Job 4 effect capability")
        if capability not in self._counted:
            raise PermissionError(
                f"Job 4 effect capability is structurally unavailable: {capability}"
            )
        self._counts[capability] += 1

    @property
    def evidence(self) -> ContinuousJob4CapabilityLedgerV1:
        capabilities: dict[str, dict[str, Any]] = {}
        for name in sorted(_ALL_EFFECT_CAPABILITIES):
            if name in self._counted:
                capabilities[name] = {
                    "mode": "counted_port",
                    "count": self._counts[name],
                    "port_id_sha256": canonical_sha256(
                        {
                            "schema_version": (
                                "cera.continuous_job4_counted_effect_port.v1"
                            ),
                            "capability": name,
                        }
                    ),
                    "denial_code": None,
                    "sealed": True,
                }
            else:
                capabilities[name] = {
                    "mode": "structurally_unavailable",
                    "count": 0,
                    "port_id_sha256": None,
                    "denial_code": ContinuousJob4CapabilityLedgerV1.DENIAL_CODE,
                    "sealed": True,
                }
        return ContinuousJob4CapabilityLedgerV1(capabilities=capabilities)


_CAPABILITY_SURFACE_LABELS = {
    "live_story_write": "live story publication",
    "production_database_write": "production database mutation",
    "active_route_mutation": "active runtime route mutation",
    "deployment": "deployment operation",
    "remote_operation": "repository remote operation",
    "merge": "repository merge",
    "push": "repository push",
    "service_change": "service state change",
    "installed_sillytavern_change": "installed SillyTavern mutation",
}
_JOB4_ENTRYPOINT_PATHS = {
    "continuous_planner_validator_job4": (
        "scripts/run_continuous_planner_validator_job4.py"
    ),
    "continuous_corrections_v12_job4": (
        "scripts/run_continuous_corrections_v12_job4.py"
    ),
}
_FORBIDDEN_DIRECT_IMPORTS = {
    "live_story_write": (
        "cera.runtime.application",
        "cera.runtime.commit",
        "cera.runtime.post_publication",
    ),
    "production_database_write": (
        "cera.storage.sqlite_store",
        "cera.storage.blocked_store",
        "cera.storage.consolidation_store",
        "cera.storage.creator_review_store",
        "cera.storage.genesis_store",
        "cera.storage.post_publication_store",
        "cera.storage.reasoner_session_store",
        "cera.storage.stage_audit_store",
    ),
    "active_route_mutation": (
        "cera.active_runtime_profile",
        "cera.runtime.active_route",
    ),
    "deployment": ("cera.deployment",),
    "remote_operation": ("git", "dulwich"),
    "merge": ("git", "dulwich"),
    "push": ("git", "dulwich"),
    "service_change": (
        "cera.sillytavern.server",
        "scripts.run_cera_sillytavern_server",
    ),
    "installed_sillytavern_change": (
        "scripts.install_sillytavern",
        "scripts.update_sillytavern",
    ),
}
_MUTATING_COMMAND_TOKENS = {
    "deploy": "deployment",
    "deployment": "deployment",
    "fetch": "remote_operation",
    "pull": "remote_operation",
    "remote": "remote_operation",
    "merge": "merge",
    "push": "push",
    "restart-service": "service_change",
    "start-service": "service_change",
    "stop-service": "service_change",
}
_ALLOWED_ENTRYPOINT_HELPER_IMPORTS = {
    "continuous_corrections_v12_job4": (
        "scripts.run_continuous_corrections_v10_job4:RecordingResult",
        "scripts.run_continuous_corrections_v10_job4:inspect_active_profile",
        "scripts.run_continuous_corrections_v10_job4:sqlite_check",
        "scripts.run_continuous_planner_validator_job4:_apply_terminal_evidence",
        "scripts.run_continuous_planner_validator_job4:_record_terminal_failure",
        "scripts.run_continuous_planner_validator_job4:build_report",
        "scripts.run_continuous_planner_validator_job4:freeze_terminal_publication",
        "scripts.run_continuous_planner_validator_job4:git_head",
        "scripts.run_continuous_planner_validator_job4:validate_declared_unittest_ids",
    ),
    "continuous_planner_validator_job4": (),
}
_ENTRYPOINT_INVENTORY_POLICY = {
    "schema_version": "cera.continuous_job4_entrypoint_inventory_policy.v1",
    "scope": "cera_product_mutation_surfaces_not_os_sandbox",
    "entrypoints": _JOB4_ENTRYPOINT_PATHS,
    "forbidden_direct_imports": _FORBIDDEN_DIRECT_IMPORTS,
    "mutating_command_tokens": _MUTATING_COMMAND_TOKENS,
    "allowed_entrypoint_helper_imports": _ALLOWED_ENTRYPOINT_HELPER_IMPORTS,
}


def _job4_entrypoint_findings(
    *, entrypoint_id: str, entrypoint_path: Path
) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    """Inventory direct product-mutation surfaces before semantic work starts.

    This is intentionally an entrypoint and CERA-product boundary, not a claim
    that an in-process Python object is an operating-system sandbox.  The
    published runner identity, source hash, and complete findings are retained
    so a different or newly mutation-capable entrypoint cannot reuse a denial
    receipt.
    """

    expected = _JOB4_ENTRYPOINT_PATHS.get(entrypoint_id)
    resolved = entrypoint_path.resolve()
    relative = "/".join(resolved.parts[-2:])
    findings: list[str] = []
    validated_helpers: list[str] = []
    if expected is None or relative.lower() != expected.lower():
        findings.append(f"entrypoint_identity:{entrypoint_id}:{relative}")
    try:
        source = resolved.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(resolved))
    except (OSError, SyntaxError, UnicodeError) as exc:
        source = ""
        tree = ast.Module(body=[], type_ignores=[])
        findings.append(f"entrypoint_source:{type(exc).__name__}")

    qualified_bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                qualified_bindings[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                qualified_bindings[alias.asname or alias.name] = (
                    f"{node.module}.{alias.name}"
                )

    for node in ast.walk(tree):
        imported: tuple[str, ...] = ()
        if isinstance(node, ast.Import):
            imported = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported = (
                node.module,
                *(f"{node.module}.{alias.name}" for alias in node.names),
            )
            if node.module.startswith("scripts.run_continuous_"):
                allowed = _ALLOWED_ENTRYPOINT_HELPER_IMPORTS.get(
                    entrypoint_id, ()
                )
                for alias in node.names:
                    binding = f"{node.module}:{alias.name}"
                    if binding in allowed:
                        validated_helpers.append(binding)
                    else:
                        findings.append(
                            "entrypoint_control:unvalidated_alternate_entrypoint_"
                            f"binding:{binding}:L{node.lineno}"
                        )
        for module_name in imported:
            for capability, prefixes in _FORBIDDEN_DIRECT_IMPORTS.items():
                if any(
                    module_name == prefix or module_name.startswith(prefix + ".")
                    for prefix in prefixes
                ):
                    findings.append(
                        f"{capability}:direct_import:{module_name}:L{node.lineno}"
                    )

        if not isinstance(node, ast.Call):
            continue
        qualified = ""
        if isinstance(node.func, ast.Name):
            qualified = qualified_bindings.get(node.func.id, node.func.id)
        elif isinstance(node.func, ast.Attribute) and isinstance(
            node.func.value, ast.Name
        ):
            owner = qualified_bindings.get(
                node.func.value.id, node.func.value.id
            )
            qualified = f"{owner}.{node.func.attr}"
        if qualified in {
            "os.system",
            "os.startfile",
            "subprocess.Popen",
            "subprocess.call",
            "subprocess.check_call",
            "subprocess.check_output",
        }:
            findings.append(
                f"remote_operation:unwrapped_call:{qualified}:L{node.lineno}"
            )
        if qualified != "subprocess.run" or not node.args:
            continue
        command = node.args[0]
        if not isinstance(command, (ast.List, ast.Tuple)):
            findings.append(
                f"remote_operation:dynamic_subprocess_command:L{node.lineno}"
            )
            continue
        tokens = [
            value.value.lower()
            for value in command.elts
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        ]
        for token in tokens:
            capability = _MUTATING_COMMAND_TOKENS.get(token)
            if capability is not None:
                findings.append(
                    f"{capability}:subprocess_command:{token}:L{node.lineno}"
                )

    return (
        relative,
        canonical_sha256({"text": source}),
        tuple(sorted(set(findings))),
        tuple(sorted(set(validated_helpers))),
    )


@dataclass(frozen=True, slots=True)
class ContinuousJob4CapabilityBoundaryEvidenceV1:
    """Immutable proof that one inventoried entrypoint used one sealed container."""

    entrypoint_id: str
    entrypoint_relative_path: str
    entrypoint_source_sha256: str
    inventory_policy_sha256: str
    scope: str
    capability_ports: Mapping[str, Mapping[str, Any]]
    unwrapped_surfaces: tuple[str, ...]
    validated_helper_imports: tuple[str, ...]
    capability_ledger_sha256: str
    status: str

    SCHEMA_VERSION = "cera.continuous_job4_capability_boundary_evidence.v1"
    SCOPE = "cera_product_mutation_surfaces_not_os_sandbox"

    def __post_init__(self) -> None:
        if self.entrypoint_id not in _JOB4_ENTRYPOINT_PATHS:
            raise ValueError("Job 4 capability boundary entrypoint is unsupported")
        if self.entrypoint_relative_path != _JOB4_ENTRYPOINT_PATHS[self.entrypoint_id]:
            raise ValueError("Job 4 capability boundary entrypoint path changed")
        for label, value in (
            ("entrypoint_source_sha256", self.entrypoint_source_sha256),
            ("inventory_policy_sha256", self.inventory_policy_sha256),
            ("capability_ledger_sha256", self.capability_ledger_sha256),
        ):
            if _optional_sha256(value, label) is None:
                raise ValueError(f"Job 4 capability boundary {label} is missing")
        if self.inventory_policy_sha256 != canonical_sha256(
            _ENTRYPOINT_INVENTORY_POLICY
        ):
            raise ValueError("Job 4 capability boundary inventory policy changed")
        if self.scope != self.SCOPE:
            raise ValueError("Job 4 capability boundary scope changed")
        if self.status not in {"verified", "failed"}:
            raise ValueError("Job 4 capability boundary status is invalid")
        if not isinstance(self.unwrapped_surfaces, tuple) or not all(
            isinstance(value, str) and value for value in self.unwrapped_surfaces
        ):
            raise ValueError("Job 4 unwrapped surface inventory is invalid")
        if tuple(sorted(set(self.unwrapped_surfaces))) != self.unwrapped_surfaces:
            raise ValueError("Job 4 unwrapped surface inventory is not canonical")
        if not isinstance(self.validated_helper_imports, tuple) or not all(
            isinstance(value, str) and value
            for value in self.validated_helper_imports
        ):
            raise ValueError("Job 4 helper import inventory is invalid")
        if (
            tuple(sorted(set(self.validated_helper_imports)))
            != self.validated_helper_imports
            or not set(self.validated_helper_imports).issubset(
                _ALLOWED_ENTRYPOINT_HELPER_IMPORTS[self.entrypoint_id]
            )
        ):
            raise ValueError("Job 4 helper import inventory is not authorized")
        ports = _closed(
            self.capability_ports,
            set(_ALL_EFFECT_CAPABILITIES),
            "Job 4 capability boundary ports",
        )
        identities: set[str] = set()
        derived_unwrapped_counts = {name: 0 for name in _ALL_EFFECT_CAPABILITIES}
        for finding in self.unwrapped_surfaces:
            capability = finding.split(":", 1)[0]
            if capability in derived_unwrapped_counts:
                derived_unwrapped_counts[capability] += 1
        for capability in sorted(_ALL_EFFECT_CAPABILITIES):
            entry = _closed(
                ports[capability],
                {
                    "surface_label",
                    "port_id_sha256",
                    "access_mode",
                    "sealed",
                    "unwrapped_surface_count",
                },
                f"Job 4 capability boundary port {capability}",
            )
            if entry["surface_label"] != _CAPABILITY_SURFACE_LABELS[capability]:
                raise ValueError(f"Job 4 capability surface label changed: {capability}")
            identity = _optional_sha256(
                entry["port_id_sha256"], f"Job 4 boundary port identity {capability}"
            )
            if identity is None or identity in identities:
                raise ValueError("Job 4 boundary port identities are missing or duplicated")
            if identity != canonical_sha256(
                {
                    "schema_version": "cera.continuous_job4_sealed_capability_port.v1",
                    "entrypoint_id": self.entrypoint_id,
                    "capability": capability,
                }
            ):
                raise ValueError(f"Job 4 boundary port identity changed: {capability}")
            identities.add(identity)
            if entry["access_mode"] not in {
                "structurally_unavailable",
                "counted_port",
            }:
                raise ValueError(f"Job 4 boundary port mode is invalid: {capability}")
            if _flag(entry["sealed"], f"Job 4 boundary sealed {capability}") is not True:
                raise ValueError(f"Job 4 boundary port is not sealed: {capability}")
            count = _count(
                entry["unwrapped_surface_count"],
                f"Job 4 boundary surface count {capability}",
            )
            if count != derived_unwrapped_counts[capability]:
                raise ValueError(
                    f"Job 4 boundary surface count contradicts inventory: {capability}"
                )
        enforced = not self.unwrapped_surfaces and all(
            value["access_mode"] == "structurally_unavailable"
            and value["unwrapped_surface_count"] == 0
            for value in ports.values()
        )
        if (self.status == "verified") is not enforced:
            raise ValueError("Job 4 capability boundary status contradicts inventory")

    @property
    def enforced(self) -> bool:
        return self.status == "verified"

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "entrypoint_id": self.entrypoint_id,
            "entrypoint_relative_path": self.entrypoint_relative_path,
            "entrypoint_source_sha256": self.entrypoint_source_sha256,
            "inventory_policy_sha256": self.inventory_policy_sha256,
            "scope": self.scope,
            "capability_ports": {
                name: dict(self.capability_ports[name])
                for name in sorted(self.capability_ports)
            },
            "unwrapped_surfaces": list(self.unwrapped_surfaces),
            "validated_helper_imports": list(self.validated_helper_imports),
            "capability_ledger_sha256": self.capability_ledger_sha256,
            "status": self.status,
        }

    @classmethod
    def from_dict(
        cls, raw: object
    ) -> "ContinuousJob4CapabilityBoundaryEvidenceV1":
        value = _closed(
            raw,
            {
                "schema_version",
                "entrypoint_id",
                "entrypoint_relative_path",
                "entrypoint_source_sha256",
                "inventory_policy_sha256",
                "scope",
                "capability_ports",
                "unwrapped_surfaces",
                "validated_helper_imports",
                "capability_ledger_sha256",
                "status",
            },
            "Job 4 capability boundary evidence",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("Job 4 capability boundary evidence schema changed")
        if not isinstance(value["unwrapped_surfaces"], list) or not isinstance(
            value["validated_helper_imports"], list
        ):
            raise ValueError("Job 4 boundary inventories must be arrays")
        return cls(
            entrypoint_id=value["entrypoint_id"],
            entrypoint_relative_path=value["entrypoint_relative_path"],
            entrypoint_source_sha256=value["entrypoint_source_sha256"],
            inventory_policy_sha256=value["inventory_policy_sha256"],
            scope=value["scope"],
            capability_ports=value["capability_ports"],
            unwrapped_surfaces=tuple(value["unwrapped_surfaces"]),
            validated_helper_imports=tuple(value["validated_helper_imports"]),
            capability_ledger_sha256=value["capability_ledger_sha256"],
            status=value["status"],
        )


class _ContinuousJob4SealedCapabilityPort:
    def __init__(
        self, custody: ContinuousJob4CapabilityCustody, capability: str
    ) -> None:
        self._custody = custody
        self._capability = capability

    def invoke(
        self, operation: Callable[..., _T], /, *args: Any, **kwargs: Any
    ) -> _T:
        """Count before effect, or reject before the callback can run."""

        self._custody.record(self._capability)
        return operation(*args, **kwargs)


class ContinuousJob4CapabilityContainerV1:
    """The sole sealed product-mutation boundary for a Job 4 entrypoint."""

    SCHEMA_VERSION = "cera.continuous_job4_capability_container.v1"

    def __init__(
        self,
        *,
        entrypoint_id: str,
        entrypoint_path: str | Path,
        counted_capabilities: tuple[str, ...] = (),
        additional_unwrapped_surfaces: tuple[str, ...] = (),
    ) -> None:
        self._custody = ContinuousJob4CapabilityCustody(
            counted_capabilities=counted_capabilities
        )
        relative, source_sha256, findings, validated_helpers = (
            _job4_entrypoint_findings(
                entrypoint_id=entrypoint_id,
                entrypoint_path=Path(entrypoint_path),
            )
        )
        self._entrypoint_id = entrypoint_id
        self._entrypoint_relative_path = relative
        self._entrypoint_source_sha256 = source_sha256
        self._findings = tuple(
            sorted(set((*findings, *additional_unwrapped_surfaces)))
        )
        self._validated_helpers = validated_helpers
        self._ports = {
            capability: _ContinuousJob4SealedCapabilityPort(
                self._custody, capability
            )
            for capability in sorted(_ALL_EFFECT_CAPABILITIES)
        }

    @classmethod
    def restricted(
        cls, *, entrypoint_id: str, entrypoint_path: str | Path
    ) -> "ContinuousJob4CapabilityContainerV1":
        return cls(entrypoint_id=entrypoint_id, entrypoint_path=entrypoint_path)

    def port(self, capability: str) -> _ContinuousJob4SealedCapabilityPort:
        if capability not in self._ports:
            raise ValueError("unknown Job 4 effect capability")
        return self._ports[capability]

    def observe_unwrapped_effect(
        self,
        capability: str,
        *,
        probe: Callable[[], object],
        operation: Callable[[], _T],
    ) -> _T:
        """Adversarial monitor: preserve a detected bypass as a counted effect.

        A restricted production container rejects before ``operation``.  A test
        or future explicitly authorized monitor must declare the capability as
        counted, which itself makes the boundary non-enforced.  If the probe
        changes, custody is incremented in ``finally`` even when the operation
        raises after creating its effect.
        """

        if capability not in self._ports:
            raise ValueError("unknown Job 4 effect capability")
        if self.evidence.capabilities[capability]["mode"] != "counted_port":
            raise PermissionError(
                f"Job 4 effect capability is structurally unavailable: {capability}"
            )
        before = probe()
        try:
            return operation()
        finally:
            if probe() != before:
                self._custody.record(capability)

    def require_enforced(self) -> None:
        if not self.boundary_evidence.enforced:
            raise PermissionError(
                "Job 4 capability boundary inventory is not enforced"
            )

    @property
    def evidence(self) -> ContinuousJob4CapabilityLedgerV1:
        return self._custody.evidence

    @property
    def boundary_evidence(self) -> ContinuousJob4CapabilityBoundaryEvidenceV1:
        ledger = self.evidence
        unwrapped_counts = {name: 0 for name in _ALL_EFFECT_CAPABILITIES}
        for finding in self._findings:
            capability = finding.split(":", 1)[0]
            if capability in unwrapped_counts:
                unwrapped_counts[capability] += 1
        ports = {
            capability: {
                "surface_label": _CAPABILITY_SURFACE_LABELS[capability],
                "port_id_sha256": canonical_sha256(
                    {
                        "schema_version": (
                            "cera.continuous_job4_sealed_capability_port.v1"
                        ),
                        "entrypoint_id": self._entrypoint_id,
                        "capability": capability,
                    }
                ),
                "access_mode": ledger.capabilities[capability]["mode"],
                "sealed": True,
                "unwrapped_surface_count": unwrapped_counts[capability],
            }
            for capability in sorted(_ALL_EFFECT_CAPABILITIES)
        }
        enforced = not self._findings and ledger.all_zero_effects_structurally_denied
        return ContinuousJob4CapabilityBoundaryEvidenceV1(
            entrypoint_id=self._entrypoint_id,
            entrypoint_relative_path=self._entrypoint_relative_path,
            entrypoint_source_sha256=self._entrypoint_source_sha256,
            inventory_policy_sha256=canonical_sha256(_ENTRYPOINT_INVENTORY_POLICY),
            scope=ContinuousJob4CapabilityBoundaryEvidenceV1.SCOPE,
            capability_ports=ports,
            unwrapped_surfaces=self._findings,
            validated_helper_imports=self._validated_helpers,
            capability_ledger_sha256=ledger.sha256,
            status="verified" if enforced else "failed",
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
            "provider_free_scripted_v9",
            "provider_free_scripted_v10",
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


@dataclass(frozen=True, slots=True)
class ContinuousJob4TerminalEvidenceV2:
    execution_status: str
    effect_evidence: ContinuousJob4EffectEvidenceV1
    postconditions: ContinuousJob4PostconditionsV1
    capability_ledger: ContinuousJob4CapabilityLedgerV1
    status: str
    failure_codes: tuple[str, ...]

    SCHEMA_VERSION = "cera.continuous_job4_terminal_evidence.v2"

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    @classmethod
    def build(
        cls,
        *,
        execution_status: str,
        provider_calls: int,
        capability_ledger: ContinuousJob4CapabilityLedgerV1,
        postconditions: ContinuousJob4PostconditionsV1,
    ) -> "ContinuousJob4TerminalEvidenceV2":
        if execution_status not in {"completed", "failed"}:
            raise ValueError("execution status must be completed or failed")
        effects = ContinuousJob4EffectEvidenceV1(
            provider_calls=provider_calls,
            active_route_changes=max(
                postconditions.active_route_changes,
                capability_ledger.active_route_mutations,
            ),
            operational_counters=capability_ledger.operational_counters,
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
            capability_ledger=capability_ledger,
            status="completed" if not unique_failures else "failed",
            failure_codes=unique_failures,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "execution_status": self.execution_status,
            "effect_evidence": self.effect_evidence.to_dict(),
            "postconditions": self.postconditions.to_dict(),
            "capability_ledger": self.capability_ledger.to_dict(),
            "status": self.status,
            "failure_codes": list(self.failure_codes),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4TerminalEvidenceV2":
        value = _closed(
            raw,
            {
                "schema_version",
                "execution_status",
                "effect_evidence",
                "postconditions",
                "capability_ledger",
                "status",
                "failure_codes",
            },
            "terminal evidence v2",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("terminal evidence v2 schema version changed")
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
            capability_ledger=ContinuousJob4CapabilityLedgerV1.from_dict(
                value["capability_ledger"]
            ),
            postconditions=ContinuousJob4PostconditionsV1.from_dict(
                value["postconditions"]
            ),
        )
        if supplied_effects != rebuilt.effect_evidence:
            raise ValueError("effect evidence contradicts capability custody")
        if value["status"] != rebuilt.status:
            raise ValueError("terminal status contradicts mandatory evidence")
        if tuple(value["failure_codes"]) != rebuilt.failure_codes:
            raise ValueError("terminal failure codes contradict mandatory evidence")
        return rebuilt


@dataclass(frozen=True, slots=True)
class ContinuousJob4TerminalEvidenceV3:
    """Terminal custody including complete per-role archival evidence."""

    execution_status: str
    effect_evidence: ContinuousJob4EffectEvidenceV1
    postconditions: ContinuousJob4PostconditionsV1
    capability_ledger: ContinuousJob4CapabilityLedgerV1
    thread_archival_evidence: Mapping[
        str, ContinuousThreadArchiveEvidenceV1
    ]
    status: str
    failure_codes: tuple[str, ...]

    SCHEMA_VERSION = "cera.continuous_job4_terminal_evidence.v3"

    def __post_init__(self) -> None:
        archival = _closed(
            self.thread_archival_evidence,
            {"planner", "validator"},
            "terminal thread archival evidence",
        )
        derived: dict[str, bool] = {}
        for role in ("planner", "validator"):
            evidence = archival[role]
            if not isinstance(evidence, ContinuousThreadArchiveEvidenceV1):
                raise ValueError(
                    f"terminal thread archival evidence is invalid: {role}"
                )
            if evidence.role.value != role:
                raise ValueError(
                    f"terminal thread archival evidence role changed: {role}"
                )
            derived[role] = evidence.verified
        if dict(self.postconditions.thread_archival) != derived:
            raise ValueError(
                "thread archival postconditions are not derived from evidence"
            )

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    @classmethod
    def build(
        cls,
        *,
        execution_status: str,
        provider_calls: int,
        capability_ledger: ContinuousJob4CapabilityLedgerV1,
        postconditions: ContinuousJob4PostconditionsV1,
        thread_archival_evidence: Mapping[
            str, ContinuousThreadArchiveEvidenceV1
        ],
    ) -> "ContinuousJob4TerminalEvidenceV3":
        if execution_status not in {"completed", "failed"}:
            raise ValueError("execution status must be completed or failed")
        effects = ContinuousJob4EffectEvidenceV1(
            provider_calls=provider_calls,
            active_route_changes=max(
                postconditions.active_route_changes,
                capability_ledger.active_route_mutations,
            ),
            operational_counters=capability_ledger.operational_counters,
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
            capability_ledger=capability_ledger,
            thread_archival_evidence=thread_archival_evidence,
            status="completed" if not unique_failures else "failed",
            failure_codes=unique_failures,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "execution_status": self.execution_status,
            "effect_evidence": self.effect_evidence.to_dict(),
            "postconditions": self.postconditions.to_dict(),
            "capability_ledger": self.capability_ledger.to_dict(),
            "thread_archival_evidence": {
                role: self.thread_archival_evidence[role].to_dict()
                for role in ("planner", "validator")
            },
            "status": self.status,
            "failure_codes": list(self.failure_codes),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4TerminalEvidenceV3":
        value = _closed(
            raw,
            {
                "schema_version",
                "execution_status",
                "effect_evidence",
                "postconditions",
                "capability_ledger",
                "thread_archival_evidence",
                "status",
                "failure_codes",
            },
            "terminal evidence v3",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("terminal evidence v3 schema version changed")
        if not isinstance(value["failure_codes"], list) or not all(
            isinstance(item, str) and item for item in value["failure_codes"]
        ):
            raise ValueError("terminal failure_codes must be a string array")
        archival_raw = _closed(
            value["thread_archival_evidence"],
            {"planner", "validator"},
            "terminal thread archival evidence",
        )
        try:
            archival = {
                role: ContinuousThreadArchiveEvidenceV1.from_dict(
                    archival_raw[role]
                )
                for role in ("planner", "validator")
            }
        except Exception as exc:
            raise ValueError("terminal thread archival evidence is invalid") from exc
        supplied_effects = ContinuousJob4EffectEvidenceV1.from_dict(
            value["effect_evidence"]
        )
        rebuilt = cls.build(
            execution_status=value["execution_status"],
            provider_calls=supplied_effects.provider_calls,
            capability_ledger=ContinuousJob4CapabilityLedgerV1.from_dict(
                value["capability_ledger"]
            ),
            postconditions=ContinuousJob4PostconditionsV1.from_dict(
                value["postconditions"]
            ),
            thread_archival_evidence=archival,
        )
        if supplied_effects != rebuilt.effect_evidence:
            raise ValueError("effect evidence contradicts capability custody")
        if value["status"] != rebuilt.status:
            raise ValueError("terminal status contradicts mandatory evidence")
        if tuple(value["failure_codes"]) != rebuilt.failure_codes:
            raise ValueError("terminal failure codes contradict mandatory evidence")
        return rebuilt


@dataclass(frozen=True, slots=True)
class ContinuousJob4TerminalEvidenceV4:
    """Terminal custody with archival DTOs and an enforced process boundary."""

    execution_status: str
    effect_evidence: ContinuousJob4EffectEvidenceV1
    postconditions: ContinuousJob4PostconditionsV1
    capability_ledger: ContinuousJob4CapabilityLedgerV1
    capability_boundary_evidence: ContinuousJob4CapabilityBoundaryEvidenceV1
    thread_archival_evidence: Mapping[
        str, ContinuousThreadArchiveEvidenceV1
    ]
    status: str
    failure_codes: tuple[str, ...]

    SCHEMA_VERSION = "cera.continuous_job4_terminal_evidence.v4"

    def __post_init__(self) -> None:
        archival = _closed(
            self.thread_archival_evidence,
            {"planner", "validator"},
            "terminal thread archival evidence",
        )
        derived: dict[str, bool] = {}
        for role in ("planner", "validator"):
            evidence = archival[role]
            if not isinstance(evidence, ContinuousThreadArchiveEvidenceV1):
                raise ValueError(
                    f"terminal thread archival evidence is invalid: {role}"
                )
            if evidence.role.value != role:
                raise ValueError(
                    f"terminal thread archival evidence role changed: {role}"
                )
            derived[role] = evidence.verified
        if dict(self.postconditions.thread_archival) != derived:
            raise ValueError(
                "thread archival postconditions are not derived from evidence"
            )
        if (
            self.capability_boundary_evidence.capability_ledger_sha256
            != self.capability_ledger.sha256
        ):
            raise ValueError(
                "capability boundary evidence contradicts capability custody"
            )
        for capability in sorted(_ALL_EFFECT_CAPABILITIES):
            if (
                self.capability_boundary_evidence.capability_ports[capability][
                    "access_mode"
                ]
                != self.capability_ledger.capabilities[capability]["mode"]
            ):
                raise ValueError(
                    f"capability boundary port contradicts custody: {capability}"
                )
        if (
            self.capability_boundary_evidence.enforced
            and not self.capability_ledger.all_zero_effects_structurally_denied
        ):
            raise ValueError(
                "enforced capability boundary contradicts available effect ports"
            )

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    @classmethod
    def build(
        cls,
        *,
        execution_status: str,
        provider_calls: int,
        capability_ledger: ContinuousJob4CapabilityLedgerV1,
        capability_boundary_evidence: ContinuousJob4CapabilityBoundaryEvidenceV1,
        postconditions: ContinuousJob4PostconditionsV1,
        thread_archival_evidence: Mapping[
            str, ContinuousThreadArchiveEvidenceV1
        ],
    ) -> "ContinuousJob4TerminalEvidenceV4":
        if execution_status not in {"completed", "failed"}:
            raise ValueError("execution status must be completed or failed")
        effects = ContinuousJob4EffectEvidenceV1(
            provider_calls=provider_calls,
            active_route_changes=max(
                postconditions.active_route_changes,
                capability_ledger.active_route_mutations,
            ),
            operational_counters=capability_ledger.operational_counters,
        )
        failures = list(postconditions.failure_codes(provider_calls))
        if execution_status != "completed":
            failures.insert(0, "execution_not_completed")
        if not capability_boundary_evidence.enforced:
            failures.append("capability_boundary_not_enforced")
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
            capability_ledger=capability_ledger,
            capability_boundary_evidence=capability_boundary_evidence,
            thread_archival_evidence=thread_archival_evidence,
            status="completed" if not unique_failures else "failed",
            failure_codes=unique_failures,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "execution_status": self.execution_status,
            "effect_evidence": self.effect_evidence.to_dict(),
            "postconditions": self.postconditions.to_dict(),
            "capability_ledger": self.capability_ledger.to_dict(),
            "capability_boundary_evidence": (
                self.capability_boundary_evidence.to_dict()
            ),
            "thread_archival_evidence": {
                role: self.thread_archival_evidence[role].to_dict()
                for role in ("planner", "validator")
            },
            "status": self.status,
            "failure_codes": list(self.failure_codes),
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4TerminalEvidenceV4":
        value = _closed(
            raw,
            {
                "schema_version",
                "execution_status",
                "effect_evidence",
                "postconditions",
                "capability_ledger",
                "capability_boundary_evidence",
                "thread_archival_evidence",
                "status",
                "failure_codes",
            },
            "terminal evidence v4",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("terminal evidence v4 schema version changed")
        if not isinstance(value["failure_codes"], list) or not all(
            isinstance(item, str) and item for item in value["failure_codes"]
        ):
            raise ValueError("terminal failure_codes must be a string array")
        archival_raw = _closed(
            value["thread_archival_evidence"],
            {"planner", "validator"},
            "terminal thread archival evidence",
        )
        try:
            archival = {
                role: ContinuousThreadArchiveEvidenceV1.from_dict(
                    archival_raw[role]
                )
                for role in ("planner", "validator")
            }
        except Exception as exc:
            raise ValueError("terminal thread archival evidence is invalid") from exc
        supplied_effects = ContinuousJob4EffectEvidenceV1.from_dict(
            value["effect_evidence"]
        )
        rebuilt = cls.build(
            execution_status=value["execution_status"],
            provider_calls=supplied_effects.provider_calls,
            capability_ledger=ContinuousJob4CapabilityLedgerV1.from_dict(
                value["capability_ledger"]
            ),
            capability_boundary_evidence=(
                ContinuousJob4CapabilityBoundaryEvidenceV1.from_dict(
                    value["capability_boundary_evidence"]
                )
            ),
            postconditions=ContinuousJob4PostconditionsV1.from_dict(
                value["postconditions"]
            ),
            thread_archival_evidence=archival,
        )
        if supplied_effects != rebuilt.effect_evidence:
            raise ValueError("effect evidence contradicts capability custody")
        if value["status"] != rebuilt.status:
            raise ValueError("terminal status contradicts mandatory evidence")
        if tuple(value["failure_codes"]) != rebuilt.failure_codes:
            raise ValueError("terminal failure codes contradict mandatory evidence")
        return rebuilt


ContinuousJob4TerminalEvidence = (
    ContinuousJob4TerminalEvidenceV1
    | ContinuousJob4TerminalEvidenceV2
    | ContinuousJob4TerminalEvidenceV3
    | ContinuousJob4TerminalEvidenceV4
)


def decode_continuous_job4_terminal_evidence(
    raw: object,
) -> ContinuousJob4TerminalEvidence:
    if not isinstance(raw, Mapping):
        raise ValueError("terminal evidence must be an object")
    version = raw.get("schema_version")
    if version == ContinuousJob4TerminalEvidenceV1.SCHEMA_VERSION:
        return ContinuousJob4TerminalEvidenceV1.from_dict(raw)
    if version == ContinuousJob4TerminalEvidenceV2.SCHEMA_VERSION:
        return ContinuousJob4TerminalEvidenceV2.from_dict(raw)
    if version == ContinuousJob4TerminalEvidenceV3.SCHEMA_VERSION:
        return ContinuousJob4TerminalEvidenceV3.from_dict(raw)
    if version == ContinuousJob4TerminalEvidenceV4.SCHEMA_VERSION:
        return ContinuousJob4TerminalEvidenceV4.from_dict(raw)
    raise ValueError("terminal evidence schema version is unsupported")


def rebuild_failed_continuous_job4_terminal_evidence(
    terminal: ContinuousJob4TerminalEvidence,
) -> ContinuousJob4TerminalEvidence:
    if isinstance(terminal, ContinuousJob4TerminalEvidenceV4):
        return ContinuousJob4TerminalEvidenceV4.build(
            execution_status="failed",
            provider_calls=terminal.effect_evidence.provider_calls,
            capability_ledger=terminal.capability_ledger,
            capability_boundary_evidence=terminal.capability_boundary_evidence,
            postconditions=terminal.postconditions,
            thread_archival_evidence=terminal.thread_archival_evidence,
        )
    if isinstance(terminal, ContinuousJob4TerminalEvidenceV3):
        return ContinuousJob4TerminalEvidenceV3.build(
            execution_status="failed",
            provider_calls=terminal.effect_evidence.provider_calls,
            capability_ledger=terminal.capability_ledger,
            postconditions=terminal.postconditions,
            thread_archival_evidence=terminal.thread_archival_evidence,
        )
    if isinstance(terminal, ContinuousJob4TerminalEvidenceV2):
        return ContinuousJob4TerminalEvidenceV2.build(
            execution_status="failed",
            provider_calls=terminal.effect_evidence.provider_calls,
            capability_ledger=terminal.capability_ledger,
            postconditions=terminal.postconditions,
        )
    return ContinuousJob4TerminalEvidenceV1.build(
        execution_status="failed",
        provider_calls=terminal.effect_evidence.provider_calls,
        operational_counters=terminal.effect_evidence.operational_counters,
        postconditions=terminal.postconditions,
    )
