"""Disposable real-Genesis sandbox for provider-free integration certification."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.evidence import (
    EvidenceAccessScope,
    EvidenceRequesterRole,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.genesis import CreatorAuthorization, GenesisCompiler, GenesisRepository
from cera.genesis.hanezawa_builder import (
    CHARACTER_IDS,
    default_repository_paths,
    default_v1_2_repository_paths,
)
from cera.genesis.models import CompiledGenesisRevision, StoredGenesisRevision
from cera.ids import IdKind, TypedId
from cera.schema import from_mapping
from cera.storage import SQLiteAuthorityStore


@dataclass(slots=True)
class RealGenesisSandbox:
    """An auto-deleting SQLite world bound to the installed creator package.

    The database path is allocated by ``TemporaryDirectory`` and is not caller
    configurable. This keeps calibration incapable of becoming a production
    world binding by accident.
    """

    project_root: Path
    temporary: TemporaryDirectory[str]
    database_path: Path
    store: SQLiteAuthorityStore
    repository: GenesisRepository
    service: EvidenceService
    authorization: CreatorAuthorization
    compiled: CompiledGenesisRevision
    installed: StoredGenesisRevision
    world_id: TypedId
    branch_id: TypedId

    @classmethod
    def create(
        cls,
        project_root: str | Path,
        *,
        revision: str = "v1_1",
    ) -> "RealGenesisSandbox":
        root = Path(project_root).resolve()
        if revision not in {"v1_1", "v1_2"}:
            raise ValueError("real Genesis sandbox revision must be v1_1 or v1_2")
        parent_paths = default_repository_paths(root)
        paths = (
            parent_paths
            if revision == "v1_1"
            else default_v1_2_repository_paths(root)
        )
        authorization = from_mapping(
            CreatorAuthorization,
            json.loads(paths["authorization_path"].read_text(encoding="utf-8")),
        )
        compiled = GenesisCompiler().compile(paths["package_root"], authorization)
        temporary = TemporaryDirectory(prefix="cera-real-genesis-")
        database_path = Path(temporary.name) / "offline-authority.sqlite3"
        store = SQLiteAuthorityStore(database_path)
        repository = GenesisRepository(store)
        world_id = TypedId(
            IdKind.WORLD,
            f"offline-hanezawa-{revision.replace('_', '-')}",
        )
        branch_id = TypedId(IdKind.BRANCH, "offline-main")
        store.create_world(world_id)
        store.create_root_branch(world_id, branch_id)
        if revision == "v1_2":
            parent_authorization = from_mapping(
                CreatorAuthorization,
                json.loads(
                    parent_paths["authorization_path"].read_text(
                        encoding="utf-8"
                    )
                ),
            )
            repository.compile_and_install(
                parent_paths["package_root"],
                parent_authorization,
                transaction_id=TypedId(
                    IdKind.TRANSACTION,
                    "offline-genesis-parent-install",
                ),
                idempotency_key="offline-genesis-parent-install",
            )
        installed = repository.compile_and_install(
            paths["package_root"],
            authorization,
            transaction_id=TypedId(IdKind.TRANSACTION, "offline-genesis-install"),
            idempotency_key="offline-genesis-install",
        )
        store.bind_world_to_genesis(
            world_id,
            installed.receipt.revision_id,
            authorization.authorization_id,
        )
        store.rebuild_evidence_search_index()
        return cls(
            project_root=root,
            temporary=temporary,
            database_path=database_path,
            store=store,
            repository=repository,
            service=EvidenceService(store),
            authorization=authorization,
            compiled=compiled,
            installed=installed,
            world_id=world_id,
            branch_id=branch_id,
        )

    def close(self) -> None:
        self.temporary.cleanup()

    def __enter__(self) -> "RealGenesisSandbox":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @property
    def revision_id(self) -> TypedId:
        return self.installed.receipt.revision_id

    @staticmethod
    def character_id(name: str) -> TypedId:
        return CHARACTER_IDS[name]

    @staticmethod
    def protected_user_id() -> TypedId:
        return TypedId(IdKind.CHARACTER, "ted")

    @staticmethod
    def session_id() -> TypedId:
        return TypedId(IdKind.SESSION, "offline-calibration")

    @staticmethod
    def system_scope() -> EvidenceAccessScope:
        return EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            permitted_private_owner_ids=tuple(CHARACTER_IDS.values()),
            allow_system_private=True,
        )

    @staticmethod
    def character_scope(name: str) -> EvidenceAccessScope:
        character_id = CHARACTER_IDS[name]
        return EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.CHARACTER,
            perspective_id=character_id,
            permitted_private_owner_ids=(character_id,),
        )

    def open_snapshot(
        self,
        request_suffix: str,
        *,
        scope: EvidenceAccessScope | None = None,
        branch_id: TypedId | None = None,
    ):
        return self.service.open_snapshot(
            request_id=TypedId(IdKind.REQUEST, f"offline-{request_suffix}"),
            world_id=self.world_id,
            branch_id=branch_id or self.branch_id,
            access_scope=scope or self.system_scope(),
            world_mode=EvidenceWorldMode.REAL,
        )

    def record_with_payload_value(self, key: str, value: object):
        for record in self.compiled.records:
            if json.loads(record.payload_json).get(key) == value:
                return record
        raise LookupError(f"real Genesis record not found: {key}={value!r}")

    def evidence_id(self, snapshot, record_id: TypedId) -> TypedId:
        return EvidenceService._evidence_id(snapshot, record_id)
