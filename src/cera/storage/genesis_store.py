"""Genesis revision operations implemented inside the authority-store boundary."""

from __future__ import annotations

import json
import sqlite3

from cera.errors import (
    ErrorCode,
    GenesisImportBlockedError,
    StateConflictError,
    TransactionError,
)
from cera.genesis.models import (
    GenesisInstallBundle,
    GenesisJournalEntry,
    GenesisJournalStatus,
    GenesisPackageClass,
    GenesisRecord,
    GenesisRecoveryReport,
    GenesisRevisionReceipt,
    StoredGenesisRevision,
)
from cera.ids import IdKind, TypedId, deterministic_id, require_kind
from cera.schema import from_mapping
from cera.serialization import canonical_json


class GenesisStoreMixin:
    """Public storage API used by the Phase 3 compiler/repository layer."""

    def _prepare_genesis_revision(
        self, bundle: GenesisInstallBundle
    ) -> GenesisJournalEntry:
        with self._connect() as connection:
            self._begin(connection)
            existing = connection.execute(
                "SELECT * FROM genesis_transaction_journal WHERE idempotency_key = ? "
                "OR transaction_id = ? OR revision_id = ?",
                (
                    bundle.idempotency_key,
                    str(bundle.transaction_id),
                    str(bundle.compiled.manifest.revision_id),
                ),
            ).fetchone()
            if existing is not None:
                entry = self._genesis_journal_from_row(existing)
                if (
                    existing["idempotency_key"] != bundle.idempotency_key
                    or existing["transaction_id"] != str(bundle.transaction_id)
                    or existing["revision_id"]
                    != str(bundle.compiled.manifest.revision_id)
                    or existing["transaction_sha256"] != bundle.transaction_sha256
                ):
                    connection.rollback()
                    raise TransactionError(
                        "Genesis idempotency, transaction, or revision identity was reused differently"
                    )
                connection.commit()
                return entry
            self._assert_genesis_parent(connection, bundle)
            connection.execute(
                "INSERT INTO genesis_transaction_journal("
                "transaction_id, idempotency_key, revision_id, transaction_sha256, "
                "status, receipt_id, rollback_reason, prepared_at, completed_at"
                ") VALUES (?, ?, ?, ?, 'prepared', NULL, NULL, ?, NULL)",
                (
                    str(bundle.transaction_id),
                    bundle.idempotency_key,
                    str(bundle.compiled.manifest.revision_id),
                    bundle.transaction_sha256,
                    self._now(),
                ),
            )
            connection.commit()
        return self.get_genesis_journal(bundle.transaction_id)

    def _finalize_genesis_revision(
        self, bundle: GenesisInstallBundle
    ) -> StoredGenesisRevision:
        with self._connect() as connection:
            self._begin(connection)
            journal = connection.execute(
                "SELECT * FROM genesis_transaction_journal WHERE transaction_id = ?",
                (str(bundle.transaction_id),),
            ).fetchone()
            if journal is None:
                connection.rollback()
                raise TransactionError("Genesis revision must be prepared before finalization")
            if (
                journal["idempotency_key"] != bundle.idempotency_key
                or journal["revision_id"] != str(bundle.compiled.manifest.revision_id)
                or journal["transaction_sha256"] != bundle.transaction_sha256
            ):
                connection.rollback()
                raise TransactionError("prepared Genesis transaction does not match bundle")
            if journal["status"] == GenesisJournalStatus.COMMITTED.value:
                receipt = self._genesis_receipt_in_connection(
                    connection, journal["receipt_id"]
                )
                connection.commit()
                return StoredGenesisRevision(receipt=receipt, exact_replay=True)
            if journal["status"] == GenesisJournalStatus.ROLLED_BACK.value:
                connection.rollback()
                raise TransactionError("rolled-back Genesis transaction cannot auto-retry")
            try:
                self._assert_genesis_parent(connection, bundle)
                self._assert_genesis_supersession(connection, bundle)
            except StateConflictError as exc:
                connection.execute(
                    "UPDATE genesis_transaction_journal SET status = 'rolled_back', "
                    "rollback_reason = ?, completed_at = ? WHERE transaction_id = ?",
                    (str(exc), self._now(), str(bundle.transaction_id)),
                )
                connection.commit()
                raise
            try:
                receipt = self._apply_genesis_bundle(connection, bundle)
                self._after_genesis_records_insert(connection, bundle)
                connection.execute(
                    "INSERT INTO genesis_revision_receipts("
                    "receipt_id, transaction_id, revision_id, receipt_json, "
                    "transaction_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(receipt.receipt_id),
                        str(receipt.transaction_id),
                        str(receipt.revision_id),
                        canonical_json(receipt),
                        receipt.transaction_sha256,
                        self._now(),
                    ),
                )
                connection.execute(
                    "UPDATE genesis_transaction_journal SET status = 'committed', "
                    "receipt_id = ?, completed_at = ? WHERE transaction_id = ?",
                    (
                        str(receipt.receipt_id),
                        self._now(),
                        str(bundle.transaction_id),
                    ),
                )
                connection.commit()
                return StoredGenesisRevision(receipt=receipt, exact_replay=False)
            except Exception as exc:
                connection.rollback()
                if isinstance(exc, (TransactionError, StateConflictError)):
                    raise
                raise TransactionError(
                    f"atomic Genesis commit failed and was rolled back: {exc}"
                ) from exc

    def install_genesis_revision(
        self, bundle: GenesisInstallBundle
    ) -> StoredGenesisRevision:
        self._require_creator_package(bundle)
        prepared = self._prepare_genesis_revision(bundle)
        if prepared.status is GenesisJournalStatus.ROLLED_BACK:
            raise TransactionError("rolled-back Genesis transaction requires a new key")
        return self._finalize_genesis_revision(bundle)

    def prepare_genesis_revision(
        self, bundle: GenesisInstallBundle
    ) -> GenesisJournalEntry:
        self._require_creator_package(bundle)
        return self._prepare_genesis_revision(bundle)

    def finalize_genesis_revision(
        self, bundle: GenesisInstallBundle
    ) -> StoredGenesisRevision:
        self._require_creator_package(bundle)
        return self._finalize_genesis_revision(bundle)

    def install_synthetic_genesis_fixture(
        self, bundle: GenesisInstallBundle
    ) -> StoredGenesisRevision:
        self._require_synthetic_package(bundle)
        prepared = self._prepare_genesis_revision(bundle)
        if prepared.status is GenesisJournalStatus.ROLLED_BACK:
            raise TransactionError("rolled-back synthetic transaction requires a new key")
        return self._finalize_genesis_revision(bundle)

    def prepare_synthetic_genesis_fixture(
        self, bundle: GenesisInstallBundle
    ) -> GenesisJournalEntry:
        self._require_synthetic_package(bundle)
        return self._prepare_genesis_revision(bundle)

    def finalize_synthetic_genesis_fixture(
        self, bundle: GenesisInstallBundle
    ) -> StoredGenesisRevision:
        self._require_synthetic_package(bundle)
        return self._finalize_genesis_revision(bundle)

    @staticmethod
    def _require_creator_package(bundle: GenesisInstallBundle) -> None:
        if bundle.compiled.manifest.package_class is not GenesisPackageClass.CREATOR_CANON:
            raise GenesisImportBlockedError(
                ErrorCode.SYNTHETIC_GENESIS_REJECTED,
                "production Genesis import rejects synthetic fixtures",
            )

    def _require_synthetic_package(self, bundle: GenesisInstallBundle) -> None:
        if not getattr(self, "allow_synthetic_genesis", False):
            raise GenesisImportBlockedError(
                ErrorCode.SYNTHETIC_GENESIS_REJECTED,
                "synthetic Genesis is disabled for this authority store",
            )
        if bundle.compiled.manifest.package_class is not GenesisPackageClass.SYNTHETIC_FIXTURE:
            raise GenesisImportBlockedError(
                ErrorCode.SYNTHETIC_GENESIS_REJECTED,
                "fixture install accepts only synthetic Genesis packages",
            )

    def _assert_genesis_parent(
        self, connection: sqlite3.Connection, bundle: GenesisInstallBundle
    ) -> None:
        manifest = bundle.compiled.manifest
        latest = connection.execute(
            "SELECT revision_id, revision_number, package_id, package_class "
            "FROM genesis_revisions "
            "ORDER BY revision_number DESC LIMIT 1"
        ).fetchone()
        if latest is None:
            if manifest.revision_number != 1 or manifest.parent_revision_id is not None:
                raise StateConflictError("first installed Genesis revision must be root revision 1")
            return
        if manifest.parent_revision_id is None:
            raise StateConflictError("Genesis already has a root revision")
        if latest["revision_id"] != str(manifest.parent_revision_id):
            raise StateConflictError("Genesis parent is not the current revision")
        if manifest.revision_number != int(latest["revision_number"]) + 1:
            raise StateConflictError("Genesis revision number is not sequential")
        if (
            latest["package_id"] != str(manifest.package_id)
            or latest["package_class"] != manifest.package_class.value
        ):
            raise StateConflictError("Genesis revision changed package identity or class")

    def _assert_genesis_supersession(
        self, connection: sqlite3.Connection, bundle: GenesisInstallBundle
    ) -> None:
        records = bundle.compiled.records
        new_by_id = {str(record.record_id): record for record in records}
        targets = [str(target) for record in records for target in record.supersedes]
        if len(targets) != len(set(targets)):
            raise StateConflictError("two Genesis records cannot supersede the same record")
        parent_id = bundle.compiled.manifest.parent_revision_id
        active_parent = (
            {
                str(record.record_id): record
                for record in self._active_genesis_records_in_connection(
                    connection, parent_id
                )
            }
            if parent_id is not None
            else {}
        )
        for record in records:
            for target in record.supersedes:
                target_record = new_by_id.get(str(target)) or active_parent.get(str(target))
                if target_record is None:
                    raise StateConflictError(
                        "Genesis supersession target is not current in this revision lineage"
                    )
                if record.record_version <= target_record.record_version:
                    raise StateConflictError(
                        "superseding Genesis record version must increase"
                    )

    def _apply_genesis_bundle(
        self, connection: sqlite3.Connection, bundle: GenesisInstallBundle
    ) -> GenesisRevisionReceipt:
        compiled = bundle.compiled
        manifest = compiled.manifest
        now = self._now()
        connection.execute(
            "INSERT INTO genesis_revisions("
            "revision_id, revision_number, parent_revision_id, schema_version, "
            "revision_label, manifest_sha256, bundle_sha256, authorization_id, "
            "transaction_id, created_at, package_id, package_class, "
            "compiler_contract_version, world_scope"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(manifest.revision_id),
                manifest.revision_number,
                str(manifest.parent_revision_id)
                if manifest.parent_revision_id is not None
                else None,
                manifest.schema_version,
                manifest.revision_label,
                compiled.manifest_sha256,
                compiled.bundle_sha256,
                str(bundle.authorization.authorization_id),
                str(bundle.transaction_id),
                now,
                str(manifest.package_id),
                manifest.package_class.value,
                manifest.compiler_contract_version,
                manifest.world_scope,
            ),
        )
        module_by_source = {module.source_id: module for module in compiled.modules}
        for module_ref in manifest.modules:
            connection.execute(
                "INSERT INTO genesis_sources("
                "revision_id, source_id, relative_path, content_sha256"
                ") VALUES (?, ?, ?, ?)",
                (
                    str(manifest.revision_id),
                    str(module_ref.source_id),
                    module_ref.relative_path,
                    module_ref.content_sha256,
                ),
            )
            if module_ref.source_id not in module_by_source:
                raise TransactionError("compiled Genesis module is missing")
        for record in compiled.records:
            connection.execute(
                "INSERT INTO genesis_records("
                "revision_id, record_id, record_version, record_type, epistemic_layer, "
                "truth_status, claim, owner_id, visibility, certainty, record_json, "
                "record_sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(manifest.revision_id),
                    str(record.record_id),
                    record.record_version,
                    record.record_type.value,
                    record.epistemic_layer.value,
                    record.truth_status.value,
                    record.claim,
                    str(record.owner_id) if record.owner_id is not None else None,
                    record.visibility.value,
                    record.certainty.value,
                    canonical_json(record),
                    record.record_sha256,
                ),
            )
        for record in compiled.records:
            for old_record_id in record.supersedes:
                connection.execute(
                    "INSERT INTO genesis_supersessions("
                    "revision_id, new_record_id, old_record_id) VALUES (?, ?, ?)",
                    (
                        str(manifest.revision_id),
                        str(record.record_id),
                        str(old_record_id),
                    ),
                )
        receipt = GenesisRevisionReceipt(
            schema_version=GenesisRevisionReceipt.SCHEMA_VERSION,
            receipt_id=deterministic_id(
                IdKind.GENESIS_RECEIPT,
                "cera.genesis.transaction",
                str(bundle.transaction_id),
            ),
            transaction_id=bundle.transaction_id,
            package_id=manifest.package_id,
            package_class=manifest.package_class,
            revision_id=manifest.revision_id,
            parent_revision_id=manifest.parent_revision_id,
            manifest_sha256=compiled.manifest_sha256,
            bundle_sha256=compiled.bundle_sha256,
            transaction_sha256=bundle.transaction_sha256,
            authorization_id=bundle.authorization.authorization_id,
            installed_record_ids=tuple(record.record_id for record in compiled.records),
            outcome="committed",
        )
        return receipt

    def _after_genesis_records_insert(
        self, connection: sqlite3.Connection, bundle: GenesisInstallBundle
    ) -> None:
        """Test seam for failure injection before publication."""

    def get_genesis_journal(self, transaction_id: TypedId) -> GenesisJournalEntry:
        require_kind(transaction_id, IdKind.TRANSACTION, "transaction_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM genesis_transaction_journal WHERE transaction_id = ?",
                (str(transaction_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"Genesis transaction does not exist: {transaction_id}")
        return self._genesis_journal_from_row(row)

    @staticmethod
    def _genesis_journal_from_row(row: sqlite3.Row) -> GenesisJournalEntry:
        return GenesisJournalEntry(
            transaction_id=TypedId.parse(row["transaction_id"], IdKind.TRANSACTION),
            idempotency_key=row["idempotency_key"],
            revision_id=TypedId.parse(row["revision_id"], IdKind.GENESIS_REVISION),
            transaction_sha256=row["transaction_sha256"],
            status=GenesisJournalStatus(row["status"]),
            receipt_id=(
                TypedId.parse(row["receipt_id"], IdKind.GENESIS_RECEIPT)
                if row["receipt_id"] is not None
                else None
            ),
            rollback_reason=row["rollback_reason"],
        )

    def get_genesis_receipt(self, receipt_id: TypedId) -> GenesisRevisionReceipt:
        require_kind(receipt_id, IdKind.GENESIS_RECEIPT, "receipt_id")
        with self._connect() as connection:
            return self._genesis_receipt_in_connection(connection, str(receipt_id))

    @staticmethod
    def _genesis_receipt_in_connection(
        connection: sqlite3.Connection, receipt_id: str | None
    ) -> GenesisRevisionReceipt:
        if receipt_id is None:
            raise TransactionError("committed Genesis journal has no receipt")
        row = connection.execute(
            "SELECT receipt_json FROM genesis_revision_receipts WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise TransactionError(f"Genesis receipt does not exist: {receipt_id}")
        return from_mapping(GenesisRevisionReceipt, json.loads(row["receipt_json"]))

    def active_genesis_records(
        self, revision_id: TypedId
    ) -> tuple[GenesisRecord, ...]:
        require_kind(revision_id, IdKind.GENESIS_REVISION, "revision_id")
        with self._connect() as connection:
            self._assert_revision_readable(connection, revision_id)
            return self._active_genesis_records_in_connection(connection, revision_id)

    def _assert_revision_readable(
        self, connection: sqlite3.Connection, revision_id: TypedId
    ) -> None:
        row = connection.execute(
            "SELECT package_class FROM genesis_revisions WHERE revision_id = ?",
            (str(revision_id),),
        ).fetchone()
        if row is None:
            raise TransactionError(f"Genesis revision does not exist: {revision_id}")
        if (
            row["package_class"] == GenesisPackageClass.SYNTHETIC_FIXTURE.value
            and not getattr(self, "allow_synthetic_genesis", False)
        ):
            raise GenesisImportBlockedError(
                ErrorCode.SYNTHETIC_GENESIS_REJECTED,
                "synthetic Genesis cannot be read by a production store",
            )

    def bind_world_to_genesis(
        self,
        world_id: TypedId,
        revision_id: TypedId,
        authorization_id: TypedId,
    ) -> None:
        """Bind a world once; newer Genesis revisions never rewrite it silently."""
        require_kind(world_id, IdKind.WORLD, "world_id")
        require_kind(revision_id, IdKind.GENESIS_REVISION, "revision_id")
        require_kind(authorization_id, IdKind.AUTHORIZATION, "authorization_id")
        with self._connect() as connection:
            self._begin(connection)
            revision = connection.execute(
                "SELECT authorization_id, package_class FROM genesis_revisions "
                "WHERE revision_id = ?",
                (str(revision_id),),
            ).fetchone()
            if revision is None:
                connection.rollback()
                raise TransactionError(f"Genesis revision does not exist: {revision_id}")
            if revision["authorization_id"] != str(authorization_id):
                connection.rollback()
                raise TransactionError("world binding authorization does not match revision")
            if (
                revision["package_class"] == GenesisPackageClass.SYNTHETIC_FIXTURE.value
                and not getattr(self, "allow_synthetic_genesis", False)
            ):
                connection.rollback()
                raise GenesisImportBlockedError(
                    ErrorCode.SYNTHETIC_GENESIS_REJECTED,
                    "production worlds cannot bind synthetic Genesis",
                )
            try:
                connection.execute(
                    "INSERT INTO world_genesis_bindings("
                    "world_id, revision_id, authorization_id, bound_at) VALUES (?, ?, ?, ?)",
                    (
                        str(world_id),
                        str(revision_id),
                        str(authorization_id),
                        self._now(),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise TransactionError(
                    "world Genesis binding is missing its world or already immutable"
                ) from exc

    def get_world_genesis_revision(self, world_id: TypedId) -> TypedId:
        require_kind(world_id, IdKind.WORLD, "world_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision_id FROM world_genesis_bindings WHERE world_id = ?",
                (str(world_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"world has no Genesis binding: {world_id}")
        return TypedId.parse(row["revision_id"], IdKind.GENESIS_REVISION)

    @staticmethod
    def _active_genesis_records_in_connection(
        connection: sqlite3.Connection, revision_id: TypedId | None
    ) -> tuple[GenesisRecord, ...]:
        if revision_id is None:
            return ()
        target = connection.execute(
            "SELECT revision_number FROM genesis_revisions WHERE revision_id = ?",
            (str(revision_id),),
        ).fetchone()
        if target is None:
            raise TransactionError(f"Genesis revision does not exist: {revision_id}")
        number = int(target["revision_number"])
        rows = connection.execute(
            "SELECT gr.record_json FROM genesis_records gr "
            "JOIN genesis_revisions rev ON rev.revision_id = gr.revision_id "
            "WHERE rev.revision_number <= ? AND gr.record_id NOT IN ("
            " SELECT gs.old_record_id FROM genesis_supersessions gs "
            " JOIN genesis_revisions sr ON sr.revision_id = gs.revision_id "
            " WHERE sr.revision_number <= ?"
            ") ORDER BY rev.revision_number, gr.record_id",
            (number, number),
        ).fetchall()
        return tuple(
            from_mapping(GenesisRecord, json.loads(row["record_json"])) for row in rows
        )

    def genesis_record_history(
        self, revision_id: TypedId
    ) -> tuple[GenesisRecord, ...]:
        require_kind(revision_id, IdKind.GENESIS_REVISION, "revision_id")
        with self._connect() as connection:
            self._assert_revision_readable(connection, revision_id)
            target = connection.execute(
                "SELECT revision_number FROM genesis_revisions WHERE revision_id = ?",
                (str(revision_id),),
            ).fetchone()
            if target is None:
                raise TransactionError(f"Genesis revision does not exist: {revision_id}")
            rows = connection.execute(
                "SELECT gr.record_json FROM genesis_records gr "
                "JOIN genesis_revisions rev ON rev.revision_id = gr.revision_id "
                "WHERE rev.revision_number <= ? ORDER BY rev.revision_number, gr.record_id",
                (int(target["revision_number"]),),
            ).fetchall()
        return tuple(from_mapping(GenesisRecord, json.loads(row[0])) for row in rows)

    def recover_genesis_prepared(
        self, *, reason: str = "restart recovery"
    ) -> GenesisRecoveryReport:
        if not reason.strip():
            raise ValueError("recovery reason must be non-empty")
        with self._connect() as connection:
            self._begin(connection)
            rows = connection.execute(
                "SELECT transaction_id FROM genesis_transaction_journal "
                "WHERE status = 'prepared' ORDER BY prepared_at, transaction_id"
            ).fetchall()
            transaction_ids = tuple(
                TypedId.parse(row["transaction_id"], IdKind.TRANSACTION) for row in rows
            )
            connection.execute(
                "UPDATE genesis_transaction_journal SET status = 'rolled_back', "
                "rollback_reason = ?, completed_at = ? WHERE status = 'prepared'",
                (reason, self._now()),
            )
            connection.commit()
        return GenesisRecoveryReport(rolled_back_transaction_ids=transaction_ids)

    def genesis_table_count(self, table_name: str) -> int:
        allowed = {
            "genesis_revisions",
            "genesis_sources",
            "genesis_records",
            "genesis_supersessions",
            "genesis_transaction_journal",
            "genesis_revision_receipts",
            "world_genesis_bindings",
        }
        if table_name not in allowed:
            raise ValueError("unsupported Genesis table")
        with self._connect() as connection:
            return int(connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0])
