"""Append-only operational stage and failure evidence store."""

from __future__ import annotations

import json
import sqlite3

from cera.errors import TransactionError
from cera.ids import IdKind, TypedId, require_kind
from cera.schema import from_mapping
from cera.serialization import canonical_json, to_primitive


class StageAuditStoreMixin:
    def append_turn_failure_evidence(self, bundle) -> None:
        from cera.runtime.failure import (
            TurnFailureEvidenceBundle,
            TurnFailureEvidenceBundleV2,
            TurnFailureEvidenceBundleV3,
        )

        if not isinstance(
            bundle,
            (
                TurnFailureEvidenceBundle,
                TurnFailureEvidenceBundleV2,
                TurnFailureEvidenceBundleV3,
            ),
        ):
            raise TypeError("failure evidence store requires a typed bundle")
        payload = canonical_json(to_primitive(bundle))
        with self._connect() as connection:
            self._begin(connection)
            try:
                connection.execute(
                    "INSERT INTO turn_failure_evidence("
                    "failure_bundle_id, request_id, branch_id, generation_id, "
                    "stage, error_code, bundle_json, bundle_sha256, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(bundle.failure_bundle_id),
                        str(bundle.request_id),
                        str(bundle.branch_id),
                        str(bundle.generation_id),
                        bundle.stage,
                        bundle.error_code.value,
                        payload,
                        bundle.bundle_sha256,
                        self._now(),
                    ),
                )
                connection.commit()
            except sqlite3.IntegrityError:
                connection.rollback()
                existing = self.get_turn_failure_evidence(bundle.failure_bundle_id)
                if existing != bundle:
                    raise TransactionError(
                        "failure bundle identity belongs to different evidence"
                    )

    def get_turn_failure_evidence(self, failure_bundle_id: TypedId):
        from cera.runtime.failure import (
            TurnFailureEvidenceBundle,
            TurnFailureEvidenceBundleV2,
            TurnFailureEvidenceBundleV3,
        )

        require_kind(
            failure_bundle_id,
            IdKind.FAILURE_BUNDLE,
            "failure_bundle_id",
        )
        with self._connect() as connection:
            row = connection.execute(
                "SELECT bundle_json FROM turn_failure_evidence "
                "WHERE failure_bundle_id = ?",
                (str(failure_bundle_id),),
            ).fetchone()
        if row is None:
            raise TransactionError("turn failure evidence does not exist")
        payload = json.loads(row["bundle_json"])
        model = _failure_bundle_model(
            payload,
            TurnFailureEvidenceBundle,
            TurnFailureEvidenceBundleV2,
            TurnFailureEvidenceBundleV3,
        )
        return from_mapping(model, payload)

    def turn_failure_evidence_bundles(self, request_id: TypedId):
        """Return every privacy-safe failure bundle for one request in stage order."""

        from cera.runtime.failure import (
            TurnFailureEvidenceBundle,
            TurnFailureEvidenceBundleV2,
            TurnFailureEvidenceBundleV3,
        )

        require_kind(request_id, IdKind.REQUEST, "request_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT f.bundle_json FROM turn_stage_journal AS j "
                "JOIN turn_failure_evidence AS f "
                "ON f.failure_bundle_id = j.failure_bundle_id "
                "WHERE j.request_id = ? AND j.status = 'failed' "
                "ORDER BY j.sequence",
                (str(request_id),),
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["bundle_json"])
            model = _failure_bundle_model(
                payload,
                TurnFailureEvidenceBundle,
                TurnFailureEvidenceBundleV2,
                TurnFailureEvidenceBundleV3,
            )
            result.append(from_mapping(model, payload))
        return tuple(result)

    def append_turn_stage_entry(self, entry) -> None:
        from cera.runtime.failure import TurnStageAuditEntry

        if not isinstance(entry, TurnStageAuditEntry):
            raise TypeError("stage journal requires a typed entry")
        payload = canonical_json(to_primitive(entry))
        with self._connect() as connection:
            self._begin(connection)
            try:
                current = connection.execute(
                    "SELECT COALESCE(MAX(sequence), 0) FROM turn_stage_journal "
                    "WHERE request_id = ?",
                    (str(entry.request_id),),
                ).fetchone()[0]
                if entry.sequence != int(current) + 1:
                    raise TransactionError(
                        "stage journal sequence is not the next append position"
                    )
                if entry.failure_bundle_id is not None:
                    failure = connection.execute(
                        "SELECT failure_bundle_id FROM turn_failure_evidence "
                        "WHERE failure_bundle_id = ?",
                        (str(entry.failure_bundle_id),),
                    ).fetchone()
                    if failure is None:
                        raise TransactionError(
                            "failed stage references absent failure evidence"
                        )
                connection.execute(
                    "INSERT INTO turn_stage_journal("
                    "journal_id, request_id, branch_id, generation_id, sequence, "
                    "stage, status, input_sha256, output_sha256, "
                    "failure_bundle_id, external_provider_calls_observed, "
                    "entry_json, entry_sha256, created_at"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        str(entry.journal_id),
                        str(entry.request_id),
                        str(entry.branch_id),
                        str(entry.generation_id),
                        entry.sequence,
                        entry.stage,
                        entry.status.value,
                        entry.input_sha256,
                        entry.output_sha256,
                        (
                            str(entry.failure_bundle_id)
                            if entry.failure_bundle_id is not None
                            else None
                        ),
                        entry.external_provider_calls_observed,
                        payload,
                        entry.entry_sha256,
                        self._now(),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def turn_stage_entries(self, request_id: TypedId):
        from cera.runtime.failure import TurnStageAuditEntry
        require_kind(request_id, IdKind.REQUEST, "request_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT entry_json FROM turn_stage_journal "
                "WHERE request_id = ? ORDER BY sequence",
                (str(request_id),),
            ).fetchall()
        return tuple(
            from_mapping(TurnStageAuditEntry, json.loads(row["entry_json"]))
            for row in rows
        )


def _failure_bundle_model(payload, v1, v2, v3):
    schema_version = payload.get("schema_version")
    if schema_version == v3.SCHEMA_VERSION:
        return v3
    if schema_version == v2.SCHEMA_VERSION:
        return v2
    return v1
