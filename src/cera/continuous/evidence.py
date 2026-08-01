"""Request-local authoritative evidence bindings for continuous planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, ClassVar, Iterable

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, text_sha256

from .contracts import (
    CharacterSummaryEnvelopeV1,
    ProtectedUserAllowanceMode,
    RichPlannerSequenceV1,
    ValidatorFinalizationPackageV1,
)


class EvidenceBindingKind(StrEnum):
    CURRENT_USER_SOURCE = "current_user_source"
    MECHANICAL_CONNECTIVE_ALLOWANCE = "mechanical_connective_allowance"
    WORLD_RECORD = "world_record"


class EvidenceVisibility(StrEnum):
    PUBLIC = "public"
    CHARACTER_PRIVATE = "character_private"
    CREATOR_PRIVATE = "creator_private"


class EvidenceAuthorityClass(StrEnum):
    CURRENT_SOURCE = "current_source"
    PYTHON_MECHANICAL_ALLOWANCE = "python_mechanical_allowance"
    ACTIVE_AUTHORITY = "active_authority"
    DERIVED_RETRIEVAL_CONTEXT = "derived_retrieval_context"


@dataclass(frozen=True, slots=True)
class RequestEvidenceBindingV1:
    """One Python-allocated handle valid only for one request."""

    SCHEMA_VERSION: ClassVar[str] = "cera.request_evidence_binding.v2"

    schema_version: str
    binding_key: str
    kind: EvidenceBindingKind
    world_id: str
    branch_id: str
    turn_id: str
    source_identity: str | None
    source_sha256: str
    protected_user_allowance_scope: str | None
    authority_classification: EvidenceAuthorityClass
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
        if self.kind in {
            EvidenceBindingKind.CURRENT_USER_SOURCE,
            EvidenceBindingKind.MECHANICAL_CONNECTIVE_ALLOWANCE,
        }:
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
            expected_authority = (
                EvidenceAuthorityClass.CURRENT_SOURCE
                if self.kind is EvidenceBindingKind.CURRENT_USER_SOURCE
                else EvidenceAuthorityClass.PYTHON_MECHANICAL_ALLOWANCE
            )
            if self.authority_classification is not expected_authority:
                raise ContractValidationError(
                    "source or mechanical binding authority changed"
                )
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
            relative = self.relative_path.replace("\\", "/")
            expected_authority = (
                EvidenceAuthorityClass.ACTIVE_AUTHORITY
                if relative.startswith("ACTIVE/")
                else EvidenceAuthorityClass.DERIVED_RETRIEVAL_CONTEXT
            )
            if self.authority_classification is not expected_authority:
                raise ContractValidationError(
                    "world binding authority does not match its exact path"
                )

    @property
    def binding_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


class RequestEvidenceBindingRegistry:
    """Mutable request ledger whose exported bindings are immutable records."""

    SCHEMA_VERSION = "cera.request_evidence_binding_registry.v2"

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
            authority_classification=EvidenceAuthorityClass.CURRENT_SOURCE,
            relative_path=None,
            record_revision=None,
            record_type=None,
            visibility=EvidenceVisibility.PUBLIC,
            knowledge_owner_id=None,
            exact_read_operation_sha256=None,
        )
        return self._add(binding)

    def allocate_mechanical_connective_allowance(
        self,
        *,
        allowance_scope: str = (
            "nonmeaningful continuity only; no action, dialogue, thought, decision, "
            "movement, consent, or new story fact for the protected user"
        ),
    ) -> RequestEvidenceBindingV1:
        source_identity = f"python_mechanical_allowance:{self.turn_id}"
        source_sha256 = canonical_sha256(
            {
                "source_identity": source_identity,
                "allowance_scope": allowance_scope,
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "turn_id": self.turn_id,
            }
        )
        binding = RequestEvidenceBindingV1(
            schema_version=RequestEvidenceBindingV1.SCHEMA_VERSION,
            binding_key="binding_mechanical_"
            + canonical_sha256(
                {
                    "world_id": self.world_id,
                    "branch_id": self.branch_id,
                    "turn_id": self.turn_id,
                    "scope": allowance_scope,
                }
            )[:20],
            kind=EvidenceBindingKind.MECHANICAL_CONNECTIVE_ALLOWANCE,
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=self.turn_id,
            source_identity=source_identity,
            source_sha256=source_sha256,
            protected_user_allowance_scope=allowance_scope,
            authority_classification=EvidenceAuthorityClass.PYTHON_MECHANICAL_ALLOWANCE,
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
            authority_classification=(
                EvidenceAuthorityClass.ACTIVE_AUTHORITY
                if relative_path.replace("\\", "/").startswith("ACTIVE/")
                else EvidenceAuthorityClass.DERIVED_RETRIEVAL_CONTEXT
            ),
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
                        authority_classification=EvidenceAuthorityClass(
                            raw["authority_classification"]
                        ),
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
                "authority_classification": value.authority_classification.value,
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
            active_bindings = [
                value
                for value in resolved
                if value.kind is EvidenceBindingKind.WORLD_RECORD
                and value.authority_classification
                is EvidenceAuthorityClass.ACTIVE_AUTHORITY
            ]
            if npc_actors and not active_bindings:
                raise StateConflictError(
                    "hard character decision lacks exact ACTIVE authority"
                )
            private_bindings = [
                value
                for value in resolved
                if value.visibility is EvidenceVisibility.CHARACTER_PRIVATE
            ]
            if private_bindings:
                if len(npc_actors) != 1:
                    raise PermissionError(
                        "private evidence beat must have exactly one NPC actor"
                    )
                actor = next(iter(npc_actors))
                if any(value.knowledge_owner_id != actor for value in private_bindings):
                    raise PermissionError(
                        "private evidence transferred outside its exact actor owner"
                    )
            if beat.protected_user_allowance.source_binding_keys:
                allowance_bindings = []
                for key in beat.protected_user_allowance.source_binding_keys:
                    binding = self._bindings.get(key)
                    if binding is None or key not in beat.source_evidence_bindings:
                        raise PermissionError(
                            "protected-user allowance lacks a cited Python binding"
                        )
                    allowance_bindings.append(binding)
                if (
                    beat.protected_user_allowance.mode
                    is ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY
                    and any(
                        value.kind is not EvidenceBindingKind.CURRENT_USER_SOURCE
                        for value in allowance_bindings
                    )
                ):
                    raise PermissionError(
                        "exact protected-user action lacks current-source authority"
                    )
                if (
                    beat.protected_user_allowance.mode
                    is ProtectedUserAllowanceMode.MINIMAL_NONBRANCHING_CONNECTIVE
                    and any(
                        value.kind
                        is not EvidenceBindingKind.MECHANICAL_CONNECTIVE_ALLOWANCE
                        for value in allowance_bindings
                    )
                ):
                    raise PermissionError(
                        "minimal connective lacks Python mechanical authority"
                    )
            if "character:ted" in beat.actor_ids and (
                beat.protected_user_allowance.mode
                is not ProtectedUserAllowanceMode.EXACT_SOURCE_ONLY
            ):
                raise PermissionError(
                    "protected-user actor requires exact current-source authority"
                )

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


def build_character_summary_envelope(
    *,
    branch_root: Path,
    source_path: str,
    character_id: str,
) -> CharacterSummaryEnvelopeV1:
    """Build, rather than trust, a summary envelope from one exact record."""

    normalized = source_path.replace("\\", "/")
    if not normalized.startswith(("ACTIVE/", "DERIVED/CharacterSummaries/")):
        raise ContractValidationError(
            "character summary source must be ACTIVE or a Validator-derived summary"
        )
    target = (branch_root / normalized).resolve()
    if branch_root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
        raise StateConflictError("character summary source is unavailable or escaped branch")
    text = target.read_text(encoding="utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ContractValidationError("character summary source must be a JSON object")
    if payload.get("character_id") != character_id:
        raise StateConflictError("character summary source belongs to another character")
    revision = payload.get("_cera_revision")
    if type(revision) is not int or revision < 1:
        raise ContractValidationError("character summary source revision is invalid")
    if normalized.startswith("ACTIVE/"):
        authority = "active_authoritative_record_fields"
        summary_pointer = "/reasoning_summary"
        changes_pointer = "/latest_accepted_changes"
    else:
        authority = "validator_derived_summary_record"
        if payload.get("authority_classification") != authority:
            raise ContractValidationError(
                "Validator-derived character summary classification changed"
            )
        _validate_derived_character_summary_source(branch_root, payload)
        summary_pointer = "/summary"
        changes_pointer = "/latest_accepted_changes"
    summary = _json_pointer_value(payload, summary_pointer)
    changes = _json_pointer_value(payload, changes_pointer)
    if not isinstance(summary, str) or not summary.strip():
        raise ContractValidationError("character summary field is absent")
    if not isinstance(changes, list) or not all(
        isinstance(value, str) and value.strip() for value in changes
    ):
        raise ContractValidationError("character latest-change field is invalid")
    source_sha256 = text_sha256(text)
    receipt = _character_summary_receipt(
        character_id=character_id,
        source_path=normalized,
        source_revision=revision,
        source_sha256=source_sha256,
        authority=authority,
        summary_pointer=summary_pointer,
        changes_pointer=changes_pointer,
        summary=summary,
        changes=tuple(changes),
    )
    envelope = CharacterSummaryEnvelopeV1(
        schema_version=CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
        character_id=character_id,
        source_path_or_record_id=normalized,
        source_revision=revision,
        source_sha256=source_sha256,
        source_authority_classification=authority,
        summary_field_path=summary_pointer,
        latest_changes_field_path=changes_pointer,
        latest_accepted_changes=tuple(changes),
        summary=summary,
        derivation_receipt_sha256=receipt,
    )
    validate_character_summary_envelope(branch_root=branch_root, envelope=envelope)
    return envelope


def validate_character_summary_envelope(
    *,
    branch_root: Path,
    envelope: CharacterSummaryEnvelopeV1,
) -> Path:
    normalized = envelope.source_path_or_record_id.replace("\\", "/")
    target = (branch_root / normalized).resolve()
    if branch_root.resolve() not in target.parents or not target.is_file() or target.is_symlink():
        raise StateConflictError("character summary source is unavailable or escaped branch")
    text = target.read_text(encoding="utf-8")
    if text_sha256(text) != envelope.source_sha256:
        raise StateConflictError("character summary source hash is stale")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ContractValidationError("character summary source must be an object")
    if payload.get("_cera_revision") != envelope.source_revision:
        raise StateConflictError("character summary source revision is stale")
    if payload.get("character_id") != envelope.character_id:
        raise StateConflictError("character summary identity does not match its source")
    expected_authority = (
        "active_authoritative_record_fields"
        if normalized.startswith("ACTIVE/")
        else "validator_derived_summary_record"
        if normalized.startswith("DERIVED/CharacterSummaries/")
        else None
    )
    if expected_authority != envelope.source_authority_classification:
        raise StateConflictError("character summary authority/path classification changed")
    if expected_authority == "validator_derived_summary_record":
        if payload.get("authority_classification") != expected_authority:
            raise StateConflictError("Validator-derived summary classification changed")
        _validate_derived_character_summary_source(branch_root, payload)
    if _json_pointer_value(payload, envelope.summary_field_path) != envelope.summary:
        raise StateConflictError("character summary text was not derived from its exact source")
    changes = _json_pointer_value(payload, envelope.latest_changes_field_path)
    if not isinstance(changes, list) or tuple(changes) != envelope.latest_accepted_changes:
        raise StateConflictError(
            "character latest changes were not derived from their exact source"
        )
    expected_receipt = _character_summary_receipt(
        character_id=envelope.character_id,
        source_path=normalized,
        source_revision=envelope.source_revision,
        source_sha256=envelope.source_sha256,
        authority=envelope.source_authority_classification,
        summary_pointer=envelope.summary_field_path,
        changes_pointer=envelope.latest_changes_field_path,
        summary=envelope.summary,
        changes=envelope.latest_accepted_changes,
    )
    if expected_receipt != envelope.derivation_receipt_sha256:
        raise StateConflictError("character summary derivation receipt changed")
    return target


def _validate_derived_character_summary_source(
    branch_root: Path, payload: dict[str, Any]
) -> None:
    source_path = payload.get("source_record_path")
    source_revision = payload.get("source_record_revision")
    source_sha256 = payload.get("source_record_sha256")
    if not isinstance(source_path, str) or not source_path.startswith("ACTIVE/"):
        raise ContractValidationError(
            "derived character summary lacks an ACTIVE source record"
        )
    source = (branch_root / source_path).resolve()
    if branch_root.resolve() not in source.parents or not source.is_file() or source.is_symlink():
        raise StateConflictError("derived character summary ACTIVE source is unavailable")
    text = source.read_text(encoding="utf-8")
    record = json.loads(text)
    if (
        text_sha256(text) != source_sha256
        or not isinstance(record, dict)
        or record.get("_cera_revision") != source_revision
        or record.get("character_id") != payload.get("character_id")
    ):
        raise StateConflictError("derived character summary ACTIVE source is stale")
    validator_path = payload.get("validator_package_path")
    validator_sha256 = payload.get("validator_package_sha256")
    if (
        not isinstance(validator_path, str)
        or not validator_path.startswith("CANDIDATES/")
        or not validator_path.endswith("/VALIDATOR_PACKAGE.json")
        or not re_is_sha256(validator_sha256)
    ):
        raise ContractValidationError(
            "derived character summary lacks a bound Validator package"
        )
    validator_source = (branch_root / validator_path).resolve()
    if (
        branch_root.resolve() not in validator_source.parents
        or not validator_source.is_file()
        or validator_source.is_symlink()
        or text_sha256(validator_source.read_text(encoding="utf-8"))
        != validator_sha256
    ):
        raise StateConflictError("derived character summary Validator package is stale")
    expected_receipt = canonical_sha256(
        {
            "authority_classification": "validator_derived_summary_record",
            "character_id": payload.get("character_id"),
            "source_record_path": source_path,
            "source_record_revision": source_revision,
            "source_record_sha256": source_sha256,
            "validator_package_path": validator_path,
            "validator_package_sha256": validator_sha256,
            "summary": payload.get("summary"),
            "latest_accepted_changes": tuple(
                payload.get("latest_accepted_changes", ())
            ),
        }
    )
    if payload.get("validator_derivation_receipt_sha256") != expected_receipt:
        raise StateConflictError(
            "derived character summary Validator derivation receipt changed"
        )


def _character_summary_receipt(
    *,
    character_id: str,
    source_path: str,
    source_revision: int,
    source_sha256: str,
    authority: str,
    summary_pointer: str,
    changes_pointer: str,
    summary: str,
    changes: tuple[str, ...],
) -> str:
    return canonical_sha256(
        {
            "schema_version": CharacterSummaryEnvelopeV1.SCHEMA_VERSION,
            "character_id": character_id,
            "source_path_or_record_id": source_path,
            "source_revision": source_revision,
            "source_sha256": source_sha256,
            "source_authority_classification": authority,
            "summary_field_path": summary_pointer,
            "latest_changes_field_path": changes_pointer,
            "summary": summary,
            "latest_accepted_changes": changes,
        }
    )


def _json_pointer_value(payload: Any, pointer: str) -> Any:
    if not pointer.startswith("/") or pointer == "/":
        raise ContractValidationError("character summary field pointer is invalid")
    current = payload
    for encoded in pointer[1:].split("/"):
        key = encoded.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise StateConflictError("character summary field pointer is unavailable")
        current = current[key]
    return current
