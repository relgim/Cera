"""Typed accepted-world assembly and responder-scoped voice lookup.

The active route reads one compact, accepted authority projection.  It never
accepts a caller-composed semantic projection and never selects responders.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import ClassVar, Protocol

from cera.continuous.world import ContinuousWorldStore
from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.serialization import canonical_json, canonical_sha256, to_primitive

from .contracts import (
    ApprovedTargetV1,
    CharacterDeltaV1,
    EvidenceRecordV1,
    PersistenceTargetCustodyV1,
    SequenceFirstTurnRequestV1,
    VoiceCueV1,
)


@dataclass(frozen=True, slots=True)
class SequenceFirstAcceptedAuthorityV1:
    """Compact accepted projection generated with the ACTIVE world view."""

    SCHEMA_VERSION: ClassVar[str] = "cera.sequence_first.accepted_authority.v1"

    schema_version: str
    world_id: str
    branch_id: str
    known_character_ids: tuple[str, ...]
    explicitly_authorized_remote_character_ids: tuple[str, ...] = ()
    character_deltas: tuple[CharacterDeltaV1, ...] = ()
    evidence_records: tuple[EvidenceRecordV1, ...] = ()
    approved_targets: tuple[ApprovedTargetV1, ...] = ()
    persistence_targets: tuple[PersistenceTargetCustodyV1, ...] = ()
    voice_cues: tuple[VoiceCueV1, ...] = ()
    hard_boundaries: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("accepted authority schema changed")
        if not self.world_id.strip() or not self.branch_id.strip():
            raise ContractValidationError("accepted authority scope is invalid")
        if len(self.known_character_ids) != len(set(self.known_character_ids)):
            raise ContractValidationError("accepted authority character IDs repeat")
        known = set(self.known_character_ids)
        if not set(self.explicitly_authorized_remote_character_ids).issubset(known):
            raise ContractValidationError("accepted remote authority is unknown")
        if {value.character_id for value in self.character_deltas} - known:
            raise ContractValidationError("accepted character delta is unknown")
        if {value.character_id for value in self.voice_cues} - known:
            raise ContractValidationError("accepted voice cue is unknown")
        semantic_keys = {value.target_key for value in self.approved_targets}
        custody_keys = {value.target_key for value in self.persistence_targets}
        if semantic_keys != custody_keys:
            raise ContractValidationError("accepted target projection lost private custody")
        if len(self.voice_cues) != len({value.character_id for value in self.voice_cues}):
            raise ContractValidationError("accepted voice cues repeat a character")


class AcceptedWorldAuthorityAssemblerPort(Protocol):
    def assemble(self, *, world_id: str, branch_id: str) -> SequenceFirstAcceptedAuthorityV1: ...


class ActiveWorldAuthorityAssembler:
    """Load one hash-bound compact projection from the accepted ACTIVE tree."""

    RELATIVE_PATH = "SEQUENCE_FIRST_AUTHORITY.json"

    def __init__(self, store: ContinuousWorldStore) -> None:
        self._store = store

    def assemble(self, *, world_id: str, branch_id: str) -> SequenceFirstAcceptedAuthorityV1:
        root = self._store.initialize(world_id, branch_id)
        active = root / "ACTIVE"
        path = active / self.RELATIVE_PATH
        if not path.is_file():
            raise StateConflictError(
                "accepted sequence-first authority projection is unavailable"
            )
        raw = path.read_text(encoding="utf-8")
        value = from_mapping(SequenceFirstAcceptedAuthorityV1, json.loads(raw))
        if value.world_id != world_id or value.branch_id != branch_id:
            raise StateConflictError("accepted authority projection changed scope")
        indexed_characters = self._known_characters_from_index(active)
        if set(value.known_character_ids) != indexed_characters:
            raise StateConflictError(
                "accepted authority character index changed"
            )
        refreshed_targets = tuple(
            self._refresh_target(active, target)
            for target in value.persistence_targets
        )
        return replace(value, persistence_targets=refreshed_targets)

    def publish_initial_projection(
        self,
        authority: SequenceFirstAcceptedAuthorityV1,
    ) -> Path:
        """Seed one validated compact projection before any accepted story turn."""

        root = self._store.initialize(authority.world_id, authority.branch_id)
        active = root / "ACTIVE"
        state = json.loads(
            (active / "WORLD_STATE.json").read_text(encoding="utf-8")
        )
        if state.get("accepted_turn_ids") != []:
            raise StateConflictError(
                "accepted authority projection can only be seeded before story turns"
            )
        path = active / self.RELATIVE_PATH
        if path.exists():
            raise StateConflictError("accepted authority projection already exists")
        if set(authority.known_character_ids) != self._known_characters_from_index(active):
            raise StateConflictError("accepted authority character index changed")
        for target in authority.persistence_targets:
            self._refresh_target(active, target)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            canonical_json(to_primitive(authority)) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        temporary.replace(path)
        self._store._rebuild_index(active)
        return path

    @staticmethod
    def _known_characters_from_index(active: Path) -> set[str]:
        index = active / "WORLD_INDEX.jsonl"
        if not index.is_file():
            raise StateConflictError("accepted world index is unavailable")
        result: set[str] = set()
        for line in index.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("record_type") != "characters":
                continue
            result.update(
                value
                for value in row.get("subjects", ())
                if isinstance(value, str) and value.startswith("character:")
            )
        return result

    def resolve(
        self,
        *,
        responder_ids: tuple[str, ...],
        request: SequenceFirstTurnRequestV1,
    ) -> tuple[VoiceCueV1, ...]:
        authority = self.assemble(
            world_id=request.custody.world_id,
            branch_id=request.custody.branch_id,
        )
        by_character = {value.character_id: value for value in authority.voice_cues}
        missing = tuple(value for value in responder_ids if value not in by_character)
        if missing:
            raise StateConflictError("accepted voice cue is unavailable for a responder")
        return tuple(by_character[value] for value in responder_ids)

    @staticmethod
    def _refresh_target(
        active: Path,
        target: PersistenceTargetCustodyV1,
    ) -> PersistenceTargetCustodyV1:
        path = active / target.target_file.replace("\\", "/")
        if not path.is_file():
            raise StateConflictError("accepted persistence target is unavailable")
        document = json.loads(path.read_text(encoding="utf-8"))
        revision = document.get("_cera_revision")
        if type(revision) is not int or revision < 1:
            raise StateConflictError("accepted persistence target revision is unavailable")
        expected_prior = None
        if target.operation.value == "replace":
            current = document
            for raw in target.field_path.split("/")[1:]:
                key = raw.replace("~1", "/").replace("~0", "~")
                if not isinstance(current, dict) or key not in current:
                    raise StateConflictError("accepted replace target field is unavailable")
                current = current[key]
            expected_prior = canonical_sha256(current)
        return replace(
            target,
            expected_file_revision=revision,
            expected_prior_value_sha256=expected_prior,
        )


class StaticAcceptedWorldAuthorityAssembler:
    """Provider-free fixture adapter with the same closed assembly interface."""

    def __init__(self, authority: SequenceFirstAcceptedAuthorityV1) -> None:
        self._authority = authority

    def assemble(self, *, world_id: str, branch_id: str) -> SequenceFirstAcceptedAuthorityV1:
        if (world_id, branch_id) != (
            self._authority.world_id,
            self._authority.branch_id,
        ):
            raise StateConflictError("static accepted authority changed scope")
        return self._authority

    def resolve(
        self,
        *,
        responder_ids: tuple[str, ...],
        request: SequenceFirstTurnRequestV1,
    ) -> tuple[VoiceCueV1, ...]:
        authority = self.assemble(
            world_id=request.custody.world_id,
            branch_id=request.custody.branch_id,
        )
        by_character = {value.character_id: value for value in authority.voice_cues}
        missing = tuple(value for value in responder_ids if value not in by_character)
        if missing:
            raise StateConflictError("accepted voice cue is unavailable for a responder")
        return tuple(by_character[value] for value in responder_ids)
