"""Request-local authoritative evidence bindings for continuous planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, ClassVar, Iterable

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, text_sha256

from .contracts import RichPlannerSequenceV1, ValidatorFinalizationPackageV1


class EvidenceBindingKind(StrEnum):
    CURRENT_USER_SOURCE = "current_user_source"
    WORLD_RECORD = "world_record"


class EvidenceVisibility(StrEnum):
    PUBLIC = "public"
    CHARACTER_PRIVATE = "character_private"
    CREATOR_PRIVATE = "creator_private"


@dataclass(frozen=True, slots=True)
class RequestEvidenceBindingV1:
    """One Python-allocated handle valid only for one request."""

    SCHEMA_VERSION: ClassVar[str] = "cera.request_evidence_binding.v1"

    schema_version: str
    binding_key: str
    kind: EvidenceBindingKind
    world_id: str
    branch_id: str
    turn_id: str
    source_identity: str | None
    source_sha256: str
    protected_user_allowance_scope: str | None
    relative_path: str | None
    record_revision: int | None
    record_type: str | None
    visibility: EvidenceVisibility
    knowledge_owner_id: str | None
    exact_read_operation_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("evidence binding schema changed")
        if not self.binding_key.startswith("binding_") or not self.binding_key.replace("_", "").isalnum():
            raise ContractValidationError("evidence binding key is invalid")
        if not all(isinstance(value, str) and value.strip() for value in (self.world_id, self.branch_id, self.turn_id)):
            raise ContractValidationError("evidence binding scope is incomplete")
        if not re_is_sha256(self.source_sha256):
            raise ContractValidationError("evidence source hash is invalid")
        if self.kind is EvidenceBindingKind.CURRENT_USER_SOURCE:
            if not self.source_identity or not self.protected_user_allowance_scope:
                raise ContractValidationError("current source binding is incomplete")
            if any(value is not None for value in (
                self.relative_path,
                self.record_revision,
                self.record_type,
                self.knowledge_owner_id,
                self.exact_read_operation_sha256,
            )):
                raise ContractValidationError("current source binding carries world-record fields")
            if self.visibility is not EvidenceVisibility.PUBLIC:
                raise ContractValidationError("current source binding visibility changed")
        else:
            if self.source_identity is not None or self.protected_user_allowance_scope is not None:
                raise ContractValidationError("world binding carries current-source fields")
            if not self.relative_path or not self.record_type or not self.exact_read_operation_sha256:
                raise ContractValidationError("world binding is not an exact fetched record")
            if not re_is_sha256(self.exact_read_operation_sha256):
                raise ContractValidationError("world binding read operation hash is invalid")
            if self.record_revision is not None and (
                type(self.record_revision) is not int or self.record_revision < 1
            ):
                raise ContractValidationError("world binding revision is invalid")
            if self.visibility is EvidenceVisibility.CHARACTER_PRIVATE and not self.knowledge_owner_id:
                raise ContractValidationError("private evidence requires its knowledge owner")
            if self.visibility is EvidenceVisibility.PUBLIC and self.knowledge_owner_id is not None:
                raise ContractValidationError("public evidence cannot carry a private owner")

    @property
    def binding_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


class RequestEvidenceBindingRegistry:
    """Mutable request ledger whose exported bindings are immutable records."""

    SCHEMA_VERSION = "cera.request_evidence_binding_registry.v1"

    def __init__(self, *, world_id: str, branch_id: str, turn_id: str) -> None:
        if not all(isinstance(value, str) and value.strip() for value in (world_id, branch_id, turn_id)):
            raise ContractValidationError("evidence registry scope is incomplete")
        self.world_id = world_id
        self.branch_id = branch_id
        self.turn_id = turn_id
        self._bindings: dict[str, RequestEvidenceBindingV1] = {}

    @staticmethod
    def current_source_key(*, world_id: str, branch_id: str, turn_id: str, source_sha256: str) -> str:
        return "binding_source_" + canonical_sha256(
            {"world_id": world_id, "branch_id": branch_id, "turn_id": turn_id, "source_sha256": source_sha256}
        )[:20]

    @staticmethod
    def world_record_key(*, world_id: str, branch_id: str, turn_id: str, relative_path: str, source_sha256: str) -> str:
        return "binding_record_" + canonical_sha256(
            {
                "world_id": world_id,
                "branch_id": branch_id,
                "turn_id": turn_id,
                "relative_path": relative_path,
                "source_sha256": source_sha256,
            }
        )[:20]

    def allocate_current_source(
        self,
        *,
        source_identity: str,
        source_text: str,
        protected_user_allowance_scope: str,
    ) -> RequestEvidenceBindingV1:
        source_sha256 = text_sha256(source_text)
        binding = RequestEvidenceBindingV1(
            schema_version=RequestEvidenceBindingV1.SCHEMA_VERSION,
            binding_key=self.current_source_key(
                world_id=self.world_id,
                branch_id=self.branch_id,
                turn_id=self.turn_id,
                source_sha256=source_sha256,
            ),
            kind=EvidenceBindingKind.CURRENT_USER_SOURCE,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            source_identity=source_identity,
            source_sha256=source_sha256,
            protected_user_allowance_scope=protected_user_allowance_scope,
            relative_path=None,
            record_revision=None,
            record_type=None,
            visibility=EvidenceVisibility.PUBLIC,
            knowledge_owner_id=None,
            exact_read_operation_sha256=None,
        )
        return self._add(binding)

    def allocate_world_record(
        self,
        *,
        relative_path: str,
        source_sha256: str,
        record_revision: int | None,
        record_type: str,
        visibility: EvidenceVisibility,
        knowledge_owner_id: str | None,
        exact_read_operation_sha256: str,
    ) -> RequestEvidenceBindingV1:
        binding = RequestEvidenceBindingV1(
            schema_version=RequestEvidenceBindingV1.SCHEMA_VERSION,
            binding_key=self.world_record_key(
                world_id=self.world_id,
                branch_id=self.branch_id,
                turn_id=self.turn_id,
                relative_path=relative_path,
                source_sha256=source_sha256,
            ),
            kind=EvidenceBindingKind.WORLD_RECORD,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            source_identity=None,
            source_sha256=source_sha256,
            protected_user_allowance_scope=None,
            relative_path=relative_path,
            record_revision=record_revision,
            record_type=record_type,
            visibility=visibility,
            knowledge_owner_id=knowledge_owner_id,
            exact_read_operation_sha256=exact_read_operation_sha256,
        )
        return self._add(binding)

    def allocate_initial_projection(
        self,
        *,
        branch_root: Path,
        relative_path: str,
        record_type: str,
        visibility: EvidenceVisibility,
        knowledge_owner_id: str | None,
    ) -> RequestEvidenceBindingV1:
        target = (branch_root / relative_path).resolve()
        if branch_root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
            raise StateConflictError("initial evidence projection is unavailable or escaped branch")
        text = target.read_text(encoding="utf-8")
        revision = None
        if target.suffix.casefold() == ".json":
            payload = json.loads(text)
            revision = payload.get("_cera_revision") if isinstance(payload, dict) else None
        return self.allocate_world_record(
            relative_path=target.relative_to(branch_root).as_posix(),
            source_sha256=text_sha256(text),
            record_revision=revision,
            record_type=record_type,
            visibility=visibility,
            knowledge_owner_id=knowledge_owner_id,
            exact_read_operation_sha256=canonical_sha256(
                {
                    "operation": "deterministic_initial_projection",
                    "relative_path": target.relative_to(branch_root).as_posix(),
                    "source_sha256": text_sha256(text),
                    "turn_id": self.turn_id,
                }
            ),
        )

    def _add(self, binding: RequestEvidenceBindingV1) -> RequestEvidenceBindingV1:
        prior = self._bindings.get(binding.binding_key)
        if prior is not None and prior != binding:
            raise StateConflictError("evidence binding key collision")
        self._bindings[binding.binding_key] = binding
        return binding

    def import_bindings(self, values: Iterable[RequestEvidenceBindingV1]) -> None:
        for value in values:
            if (value.world_id, value.branch_id, value.turn_id) != (
                self.world_id,
                self.branch_id,
                self.turn_id,
            ):
                raise StateConflictError("evidence binding belongs to another request")
            self._add(value)

    def import_provider_debug(self, value: Any) -> None:
        if not isinstance(value, dict):
            return
        for raw in value.get("evidence_bindings", ()):
            if not isinstance(raw, dict):
                raise ContractValidationError("provider evidence binding descriptor is invalid")
            self.import_bindings(
                (
                    RequestEvidenceBindingV1(
                        schema_version=raw["schema_version"],
                        binding_key=raw["binding_key"],
                        kind=EvidenceBindingKind(raw["kind"]),
                        world_id=raw["world_id"],
                        branch_id=raw["branch_id"],
                        turn_id=raw["turn_id"],
                        source_identity=raw.get("source_identity"),
                        source_sha256=raw["source_sha256"],
                        protected_user_allowance_scope=raw.get("protected_user_allowance_scope"),
                        relative_path=raw.get("relative_path"),
                        record_revision=raw.get("record_revision"),
                        record_type=raw.get("record_type"),
                        visibility=EvidenceVisibility(raw["visibility"]),
                        knowledge_owner_id=raw.get("knowledge_owner_id"),
                        exact_read_operation_sha256=raw.get("exact_read_operation_sha256"),
                    ),
                )
            )

    @property
    def bindings(self) -> tuple[RequestEvidenceBindingV1, ...]:
        return tuple(self._bindings[key] for key in sorted(self._bindings))

    def prompt_manifest(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {
                "binding_key": value.binding_key,
                "kind": value.kind.value,
                "source_identity": value.source_identity,
                "source_sha256": value.source_sha256,
                "relative_path": value.relative_path,
                "record_revision": value.record_revision,
                "record_type": value.record_type,
                "visibility": value.visibility.value,
                "knowledge_owner_id": value.knowledge_owner_id,
            }
            for value in self.bindings
        )

    def validate_sequence(self, sequence: RichPlannerSequenceV1, *, branch_root: Path) -> None:
        if (sequence.world_id, sequence.branch_id) != (self.world_id, self.branch_id):
            raise StateConflictError("Planner sequence changed evidence scope")
        for beat in sequence.beats:
            resolved = []
            for key in beat.source_evidence_bindings:
                try:
                    binding = self._bindings[key]
                except KeyError as exc:
                    raise StateConflictError("Planner cited an unallocated evidence binding") from exc
                if binding.kind is EvidenceBindingKind.WORLD_RECORD:
                    self._validate_world_binding(binding, branch_root)
                    if (
                        binding.visibility is EvidenceVisibility.CHARACTER_PRIVATE
                        and binding.knowledge_owner_id not in beat.actor_ids
                    ):
                        raise PermissionError("private evidence transferred to a non-owner actor")
                resolved.append(binding)
            if not resolved:
                raise StateConflictError("Planner beat has no resolved evidence")
            npc_actors = {value for value in beat.actor_ids if value != "character:ted"}
            world_bindings = [
                value for value in resolved if value.kind is EvidenceBindingKind.WORLD_RECORD
            ]
            if npc_actors and not world_bindings:
                raise StateConflictError(
                    "hard character decision lacks an exact record or deterministic projection"
                )
            if beat.protected_user_allowance.source_binding_keys:
                for key in beat.protected_user_allowance.source_binding_keys:
                    binding = self._bindings.get(key)
                    if binding is None or binding.kind is not EvidenceBindingKind.CURRENT_USER_SOURCE:
                        raise PermissionError("protected-user allowance lacks current-source authority")

    def validate_traceability(
        self,
        sequence: RichPlannerSequenceV1,
        package: ValidatorFinalizationPackageV1,
    ) -> None:
        if package.complete_final_sequence is None:
            return
        beats = {value.beat_key: value for value in sequence.beats}
        items = {value.item_key: value for value in package.complete_final_sequence.items}
        for item in items.values():
            for beat_key in item.planner_beat_keys:
                if beat_key not in beats:
                    raise StateConflictError("final sequence cites an unknown Planner beat")
                if not beats[beat_key].source_evidence_bindings:
                    raise StateConflictError("final sequence traces to an ungrounded Planner beat")
        for operation in package.world_edit_operations:
            item = items.get(operation.source_final_sequence_item)
            if item is None or not item.planner_beat_keys:
                raise StateConflictError("world edit has no evidence-grounded final-sequence source")

    def _validate_world_binding(self, binding: RequestEvidenceBindingV1, branch_root: Path) -> None:
        assert binding.relative_path is not None
        target = (branch_root / binding.relative_path).resolve()
        if branch_root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
            raise StateConflictError("evidence binding record is unavailable or escaped branch")
        text = target.read_text(encoding="utf-8")
        if text_sha256(text) != binding.source_sha256:
            raise StateConflictError("evidence binding content is stale")
        if target.suffix.casefold() == ".json":
            payload = json.loads(text)
            revision = payload.get("_cera_revision") if isinstance(payload, dict) else None
            if revision != binding.record_revision:
                raise StateConflictError("evidence binding revision is stale")

    @property
    def registry_sha256(self) -> str:
        return domain_sha256(
            self.SCHEMA_VERSION,
            {
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "turn_id": self.turn_id,
                "bindings": self.bindings,
            },
        )
