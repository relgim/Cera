"""Persistent, non-production Hanezawa world for local human testing.

The human-test world is deliberately separate from disposable qualification
databases and from any future production world.  Resetting it recompiles the
immutable V1.2 creator package into a fresh SQLite file and leaves the root
branch at generation zero, which is the doorway state encoded by Genesis and
the SillyTavern card greeting.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from typing import ClassVar

from cera.evidence import (
    EvidenceAccessScope,
    EvidenceRequesterRole,
    EvidenceService,
)
from cera.contracts import Visibility
from cera.genesis import CreatorAuthorization, GenesisCompiler, GenesisRepository
from cera.genesis.hanezawa_builder import (
    CHARACTER_IDS,
    default_repository_paths,
    default_v1_2_repository_paths,
)
from cera.genesis.models import CompiledGenesisRevision, GenesisRecordType
from cera.ids import IdKind, TypedId
from cera.schema import from_mapping
from cera.serialization import canonical_json, text_sha256
from cera.storage import SQLiteAuthorityStore


HUMAN_TEST_WORLD_ID = TypedId(IdKind.WORLD, "hanezawa-human-test-v1-2")
HUMAN_TEST_BRANCH_ID = TypedId(IdKind.BRANCH, "main")
HUMAN_TEST_DATABASE_RELATIVE_PATH = Path(
    "runtime/development/hanezawa_human_test_v1_2.sqlite3"
)
CONTINUOUS_MANUAL_WORLD_ID = TypedId(
    IdKind.WORLD, "hanezawa-continuous-manual-v1-2"
)
CONTINUOUS_MANUAL_BRANCH_ID = TypedId(IdKind.BRANCH, "manual-main")
CONTINUOUS_MANUAL_DATABASE_RELATIVE_PATH = Path(
    "runtime/manual/continuous_v3/hanezawa_continuous_manual_v1_2.sqlite3"
)

_SCENARIO_OWNER_CLAUSES = {
    "Mia has prepared tea": CHARACTER_IDS["Mia"],
    "Hana has cut fruit": CHARACTER_IDS["Hana"],
    "Tomi is privately curious whether living with a boy could become romantic": (
        CHARACTER_IDS["Tomi"]
    ),
    "Enne is aware that Ted's room is beside hers": CHARACTER_IDS["Enne"],
    "Aoi is outwardly cooperative despite private opposition": CHARACTER_IDS["Aoi"],
    "Yuuni is eager to test the unfamiliar man with bratty questions": (
        CHARACTER_IDS["Yuuni"]
    ),
}


@dataclass(frozen=True, slots=True)
class HanezawaHumanTestWorld:
    WORLD_ID: ClassVar[TypedId] = HUMAN_TEST_WORLD_ID
    BRANCH_ID: ClassVar[TypedId] = HUMAN_TEST_BRANCH_ID
    DATABASE_RELATIVE_PATH: ClassVar[Path] = HUMAN_TEST_DATABASE_RELATIVE_PATH
    TRANSACTION_NAMESPACE: ClassVar[str] = "human-test"

    project_root: Path
    database_path: Path
    store: SQLiteAuthorityStore
    service: EvidenceService
    compiled: CompiledGenesisRevision
    revision_id: TypedId
    authorization_id: TypedId

    @property
    def world_id(self) -> TypedId:
        return type(self).WORLD_ID

    @property
    def branch_id(self) -> TypedId:
        return type(self).BRANCH_ID

    @classmethod
    def open(
        cls,
        project_root: str | Path,
        database_path: str | Path | None = None,
    ) -> "HanezawaHumanTestWorld":
        root = Path(project_root).resolve()
        target = (
            Path(database_path).resolve()
            if database_path is not None
            else (root / cls.DATABASE_RELATIVE_PATH).resolve()
        )
        if not target.is_file():
            raise FileNotFoundError(
                f"human-test world has not been initialized: {target}"
            )
        compiled, authorization = _compile_v1_2(root)
        store = SQLiteAuthorityStore(target)
        branch = store.get_branch(cls.BRANCH_ID)
        if branch.world_id != cls.WORLD_ID:
            raise RuntimeError("human-test branch belongs to another world")
        revision_id = store.get_world_genesis_revision(cls.WORLD_ID)
        if revision_id != compiled.manifest.revision_id:
            raise RuntimeError("human-test world is not bound to Genesis V1.2")
        if store.integrity_check() != ("ok",) or store.foreign_key_check():
            raise RuntimeError("human-test SQLite integrity validation failed")
        return cls(
            project_root=root,
            database_path=target,
            store=store,
            service=EvidenceService(store),
            compiled=compiled,
            revision_id=revision_id,
            authorization_id=authorization.authorization_id,
        )

    @classmethod
    def initialize(
        cls,
        project_root: str | Path,
        database_path: str | Path | None = None,
        *,
        replace: bool = False,
    ) -> "HanezawaHumanTestWorld":
        root = Path(project_root).resolve()
        target = (
            Path(database_path).resolve()
            if database_path is not None
            else (root / cls.DATABASE_RELATIVE_PATH).resolve()
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not replace:
            return cls.open(root, target)

        with NamedTemporaryFile(
            prefix=f"{target.stem}.building-",
            suffix=".sqlite3",
            dir=target.parent,
            delete=False,
        ) as temporary:
            staging = Path(temporary.name).resolve()
        try:
            staging.unlink()
            _build_v1_2_database(
                root,
                staging,
                world_id=cls.WORLD_ID,
                branch_id=cls.BRANCH_ID,
                transaction_namespace=cls.TRANSACTION_NAMESPACE,
            )
            built = cls.open(root, staging)
            branch = built.store.get_branch(built.branch_id)
            if (
                branch.generation != 0
                or branch.head_artifact_id is not None
                or built.store.visible_artifact_ids(built.branch_id)
            ):
                raise RuntimeError("fresh human-test world is not at the doorway")
            os.replace(staging, target)
        finally:
            if staging.exists():
                staging.unlink()
        return cls.open(root, target)

    @property
    def protected_user_id(self) -> TypedId:
        return TypedId(IdKind.CHARACTER, "ted")

    @property
    def relationship_record_ids(self) -> dict[TypedId, TypedId]:
        return {
            character_id: next(
                record.record_id
                for record in self.compiled.records
                if record.record_type is GenesisRecordType.RELATIONSHIP_EDGE
                and record.relationship_from_id == character_id
                and record.relationship_to_id == self.protected_user_id
            )
            for character_id in CHARACTER_IDS.values()
        }

    @property
    def character_baseline_record_ids(
        self,
    ) -> dict[TypedId, tuple[TypedId, ...]]:
        """Stable minimal psychology needed before runtime Codex chooses a move."""

        required_tags = {"core_premise"}
        result: dict[TypedId, tuple[TypedId, ...]] = {}
        for character_id in CHARACTER_IDS.values():
            records = tuple(
                record.record_id
                for record in self.compiled.records
                if character_id in record.subject_ids
                and required_tags.intersection(record.tags)
            )
            found = {
                tag
                for record in self.compiled.records
                if record.record_id in records
                for tag in required_tags.intersection(record.tags)
            }
            if len(records) != 1 or found != required_tags:
                raise RuntimeError(
                    "Hanezawa V1.2 character baseline records are incomplete"
                )
            result[character_id] = records
        return result

    @property
    def public_household_rule_record_ids(self) -> tuple[TypedId, ...]:
        """Active creator-authorized rules safe for ordinary household scenes."""

        records = tuple(
            record.record_id
            for record in self.compiled.records
            if record.record_type is GenesisRecordType.HOUSEHOLD_RULE
            and record.visibility is Visibility.PUBLIC
        )
        if len(records) != 2:
            raise RuntimeError(
                "Hanezawa V1.2 public household rule records are incomplete"
            )
        return records

    @property
    def initial_scenario_record_id(self) -> TypedId:
        matches = tuple(
            record.record_id
            for record in self.compiled.records
            if "14_2_scenario_projection" in record.tags
        )
        if len(matches) != 1:
            raise RuntimeError(
                "Hanezawa V1.2 must contain exactly one initial scenario projection"
            )
        return matches[0]

    def initial_scenario_projection(
        self,
        aware_character_ids: tuple[TypedId, ...],
    ) -> str:
        """Build a hash-bound provider view without cross-owner private state."""

        record = next(
            value
            for value in self.compiled.records
            if value.record_id == self.initial_scenario_record_id
        )
        aware = set(aware_character_ids)
        sentences = tuple(
            value.strip()
            for value in re.split(r"(?<=\.)\s+", record.claim.strip())
            if value.strip()
        )
        included: list[dict[str, str]] = []
        observed_owner_clauses: set[str] = set()
        for sentence in sentences:
            if sentence.startswith("Mia has prepared tea"):
                for clause, owner_id in _SCENARIO_OWNER_CLAUSES.items():
                    if clause not in sentence:
                        raise RuntimeError(
                            "initial scenario owner clause no longer matches Genesis"
                        )
                    observed_owner_clauses.add(clause)
                    if owner_id in aware:
                        included.append(
                            {
                                "scope": f"owner_private:{owner_id}",
                                "text": clause + ".",
                            }
                        )
                continue
            included.append({"scope": "shared_scene_fact", "text": sentence})
        if observed_owner_clauses != set(_SCENARIO_OWNER_CLAUSES):
            raise RuntimeError(
                "initial scenario ownership projection no longer matches Genesis"
            )
        return (
            "Python privacy-filtered creator scenario projection: "
            + canonical_json(
                {
                    "projection_policy_version": (
                        "cera.hanezawa_initial_scenario_projection.v1"
                    ),
                    "source_record_id": str(record.record_id),
                    "source_record_version": record.record_version,
                    "source_claim_sha256": text_sha256(record.claim),
                    "facts": included,
                }
            )
        )

    @property
    def system_scope(self) -> EvidenceAccessScope:
        return EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            permitted_private_owner_ids=tuple(CHARACTER_IDS.values()),
            allow_system_private=True,
        )


def _compile_v1_2(
    project_root: Path,
) -> tuple[CompiledGenesisRevision, CreatorAuthorization]:
    paths = default_v1_2_repository_paths(project_root)
    authorization = from_mapping(
        CreatorAuthorization,
        json.loads(paths["authorization_path"].read_text(encoding="utf-8")),
    )
    return (
        GenesisCompiler().compile(paths["package_root"], authorization),
        authorization,
    )


def _build_v1_2_database(
    project_root: Path,
    database_path: Path,
    *,
    world_id: TypedId = HUMAN_TEST_WORLD_ID,
    branch_id: TypedId = HUMAN_TEST_BRANCH_ID,
    transaction_namespace: str = "human-test",
) -> None:
    parent_paths = default_repository_paths(project_root)
    child_paths = default_v1_2_repository_paths(project_root)
    parent_authorization = from_mapping(
        CreatorAuthorization,
        json.loads(
            parent_paths["authorization_path"].read_text(encoding="utf-8")
        ),
    )
    child_authorization = from_mapping(
        CreatorAuthorization,
        json.loads(
            child_paths["authorization_path"].read_text(encoding="utf-8")
        ),
    )
    store = SQLiteAuthorityStore(database_path)
    repository = GenesisRepository(store)
    store.create_world(world_id)
    store.create_root_branch(world_id, branch_id)
    repository.compile_and_install(
        parent_paths["package_root"],
        parent_authorization,
        transaction_id=TypedId(
            IdKind.TRANSACTION,
            f"{transaction_namespace}-genesis-parent-install",
        ),
        idempotency_key=f"{transaction_namespace}-genesis-parent-install",
    )
    installed = repository.compile_and_install(
        child_paths["package_root"],
        child_authorization,
        transaction_id=TypedId(
            IdKind.TRANSACTION,
            f"{transaction_namespace}-genesis-v1-2-install",
        ),
        idempotency_key=f"{transaction_namespace}-genesis-v1-2-install",
    )
    store.bind_world_to_genesis(
        world_id,
        installed.receipt.revision_id,
        child_authorization.authorization_id,
    )
    store.rebuild_evidence_search_index()
    if store.integrity_check() != ("ok",) or store.foreign_key_check():
        raise RuntimeError("new human-test SQLite database failed integrity checks")


class HanezawaContinuousManualWorld(HanezawaHumanTestWorld):
    """Resettable V1.2 authority database owned only by the manual V3 route."""

    WORLD_ID = CONTINUOUS_MANUAL_WORLD_ID
    BRANCH_ID = CONTINUOUS_MANUAL_BRANCH_ID
    DATABASE_RELATIVE_PATH = CONTINUOUS_MANUAL_DATABASE_RELATIVE_PATH
    TRANSACTION_NAMESPACE = "continuous-manual"
