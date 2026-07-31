"""Durable operational journal for post-publication consumers."""

from __future__ import annotations

import sqlite3

from cera.errors import StateConflictError, TransactionError
from cera.ids import IdKind, TypedId, require_kind
from cera.serialization import canonical_json, text_sha256

from .models import (
    PostPublicationWork,
    PostPublicationWorkKind,
    PostPublicationWorkStatus,
)


class PostPublicationStoreMixin:
    """SQLite operations used by the explicit post-publication dispatcher.

    A failed or interrupted item is never retried by these methods unless the
    caller supplies both ``retry_failed=True`` and a non-empty authorization
    reason. This is an operational journal, not story authority.
    """

    def get_post_publication_work(self, work_id: TypedId) -> PostPublicationWork:
        require_kind(work_id, IdKind.POST_PUBLICATION_WORK, "work_id")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM post_publication_work WHERE work_id = ?",
                (str(work_id),),
            ).fetchone()
        if row is None:
            raise TransactionError(f"post-publication work does not exist: {work_id}")
        return self._post_publication_work_from_row(row)

    def post_publication_work_for_artifact(
        self, artifact_id: TypedId
    ) -> tuple[PostPublicationWork, ...]:
        require_kind(artifact_id, IdKind.ARTIFACT, "artifact_id")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM post_publication_work WHERE artifact_id = ? "
                "ORDER BY CASE work_kind WHEN 'render' THEN 1 "
                "WHEN 'consolidate' THEN 2 ELSE 3 END, work_id",
                (str(artifact_id),),
            ).fetchall()
        return tuple(self._post_publication_work_from_row(row) for row in rows)

    def list_post_publication_work(
        self,
        *,
        statuses: tuple[PostPublicationWorkStatus, ...] = (),
        branch_id: TypedId | None = None,
    ) -> tuple[PostPublicationWork, ...]:
        if branch_id is not None:
            require_kind(branch_id, IdKind.BRANCH, "branch_id")
        if len(statuses) != len(set(statuses)):
            raise ValueError("post-publication status filter cannot contain duplicates")
        clauses: list[str] = []
        parameters: list[str] = []
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            parameters.extend(value.value for value in statuses)
        if branch_id is not None:
            clauses.append("branch_id = ?")
            parameters.append(str(branch_id))
        where = "" if not clauses else " WHERE " + " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM post_publication_work"
                + where
                + " ORDER BY created_at, artifact_id, work_kind, work_id",
                tuple(parameters),
            ).fetchall()
        return tuple(self._post_publication_work_from_row(row) for row in rows)

    def claim_post_publication_work(
        self,
        work_id: TypedId,
        *,
        retry_failed: bool = False,
        authorization_reason: str | None = None,
    ) -> PostPublicationWork:
        require_kind(work_id, IdKind.POST_PUBLICATION_WORK, "work_id")
        with self._connect() as connection:
            self._begin(connection)
            row = connection.execute(
                "SELECT * FROM post_publication_work WHERE work_id = ?",
                (str(work_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise TransactionError(f"post-publication work does not exist: {work_id}")
            status = PostPublicationWorkStatus(row["status"])
            if status in {
                PostPublicationWorkStatus.COMPLETED,
                PostPublicationWorkStatus.NO_CHANGE,
            }:
                connection.commit()
                return self._post_publication_work_from_row(row)
            if status is PostPublicationWorkStatus.RUNNING:
                connection.rollback()
                raise StateConflictError(
                    "post-publication work is already running; reconcile it before retry"
                )
            if status is PostPublicationWorkStatus.FAILED:
                if not retry_failed:
                    connection.rollback()
                    raise StateConflictError(
                        "failed post-publication work requires explicit retry authorization"
                    )
                if authorization_reason is None or not authorization_reason.strip():
                    connection.rollback()
                    raise ValueError("retry authorization_reason must be non-empty")
                reason_sha256 = text_sha256(authorization_reason.strip())
            else:
                reason_sha256 = text_sha256("initial_dispatch")
            dependency_id = row["depends_on_work_id"]
            if dependency_id is not None:
                dependency = connection.execute(
                    "SELECT status FROM post_publication_work WHERE work_id = ?",
                    (dependency_id,),
                ).fetchone()
                if dependency is None:
                    connection.rollback()
                    raise TransactionError("post-publication dependency is missing")
                if dependency["status"] not in ("completed", "no_change"):
                    connection.rollback()
                    raise StateConflictError(
                        "post-publication dependency has not reached a successful terminal state"
                    )
            attempt_number = int(row["attempt_count"]) + 1
            now = self._now()
            connection.execute(
                "UPDATE post_publication_work SET status = 'running', attempt_count = ?, "
                "result_json = NULL, result_sha256 = NULL, error_json = NULL, "
                "error_sha256 = NULL, completed_at = NULL, updated_at = ? "
                "WHERE work_id = ?",
                (attempt_number, now, str(work_id)),
            )
            connection.execute(
                "INSERT INTO post_publication_attempts("
                "work_id, attempt_number, authorization_reason_sha256, status, result_sha256, "
                "error_sha256, started_at, completed_at"
                ") VALUES (?, ?, ?, 'running', NULL, NULL, ?, NULL)",
                (str(work_id), attempt_number, reason_sha256, now),
            )
            connection.commit()
        return self.get_post_publication_work(work_id)

    def complete_post_publication_work(
        self,
        work_id: TypedId,
        *,
        result_payload: object,
        no_change: bool = False,
    ) -> PostPublicationWork:
        require_kind(work_id, IdKind.POST_PUBLICATION_WORK, "work_id")
        result_json = canonical_json(result_payload)
        result_sha256 = text_sha256(result_json)
        terminal = (
            PostPublicationWorkStatus.NO_CHANGE
            if no_change
            else PostPublicationWorkStatus.COMPLETED
        )
        with self._connect() as connection:
            self._begin(connection)
            row = connection.execute(
                "SELECT status, attempt_count FROM post_publication_work WHERE work_id = ?",
                (str(work_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise TransactionError(f"post-publication work does not exist: {work_id}")
            if row["status"] != PostPublicationWorkStatus.RUNNING.value:
                connection.rollback()
                raise StateConflictError("only running post-publication work can complete")
            now = self._now()
            connection.execute(
                "UPDATE post_publication_work SET status = ?, result_json = ?, "
                "result_sha256 = ?, error_json = NULL, error_sha256 = NULL, "
                "updated_at = ?, completed_at = ? WHERE work_id = ?",
                (terminal.value, result_json, result_sha256, now, now, str(work_id)),
            )
            connection.execute(
                "UPDATE post_publication_attempts SET status = ?, result_sha256 = ?, "
                "completed_at = ? WHERE work_id = ? AND attempt_number = ?",
                (terminal.value, result_sha256, now, str(work_id), int(row["attempt_count"])),
            )
            connection.commit()
        return self.get_post_publication_work(work_id)

    def fail_post_publication_work(
        self,
        work_id: TypedId,
        *,
        stage: str,
        error_type: str,
    ) -> PostPublicationWork:
        require_kind(work_id, IdKind.POST_PUBLICATION_WORK, "work_id")
        if not stage.strip() or not error_type.strip():
            raise ValueError("post-publication failure stage and type must be non-empty")
        error_json = canonical_json(
            {
                "schema_version": "cera.post_publication_work_error.v1",
                "stage": stage.strip(),
                "error_type": error_type.strip(),
                "message": "Post-publication work failed; operator review is required.",
                "retry_mode": "manual_after_review",
            }
        )
        error_sha256 = text_sha256(error_json)
        with self._connect() as connection:
            self._begin(connection)
            row = connection.execute(
                "SELECT status, attempt_count FROM post_publication_work WHERE work_id = ?",
                (str(work_id),),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise TransactionError(f"post-publication work does not exist: {work_id}")
            if row["status"] != PostPublicationWorkStatus.RUNNING.value:
                connection.rollback()
                raise StateConflictError("only running post-publication work can fail")
            now = self._now()
            connection.execute(
                "UPDATE post_publication_work SET status = 'failed', result_json = NULL, "
                "result_sha256 = NULL, error_json = ?, error_sha256 = ?, "
                "updated_at = ?, completed_at = ? WHERE work_id = ?",
                (error_json, error_sha256, now, now, str(work_id)),
            )
            connection.execute(
                "UPDATE post_publication_attempts SET status = 'failed', error_sha256 = ?, "
                "completed_at = ? WHERE work_id = ? AND attempt_number = ?",
                (error_sha256, now, str(work_id), int(row["attempt_count"])),
            )
            connection.commit()
        return self.get_post_publication_work(work_id)

    def recover_interrupted_post_publication_work(
        self, *, reason: str = "process_restart"
    ) -> tuple[TypedId, ...]:
        if not reason.strip():
            raise ValueError("recovery reason must be non-empty")
        error_json = canonical_json(
            {
                "schema_version": "cera.post_publication_work_error.v1",
                "stage": "restart_recovery",
                "error_type": "InterruptedWork",
                "message": "Work was running when the process stopped; reconciliation is required.",
                "retry_mode": "manual_after_review",
                "reason_sha256": text_sha256(reason.strip()),
            }
        )
        error_sha256 = text_sha256(error_json)
        with self._connect() as connection:
            self._begin(connection)
            rows = connection.execute(
                "SELECT work_id, attempt_count FROM post_publication_work "
                "WHERE status = 'running' ORDER BY created_at, work_id"
            ).fetchall()
            now = self._now()
            for row in rows:
                connection.execute(
                    "UPDATE post_publication_work SET status = 'failed', error_json = ?, "
                    "error_sha256 = ?, updated_at = ?, completed_at = ? WHERE work_id = ?",
                    (error_json, error_sha256, now, now, row["work_id"]),
                )
                connection.execute(
                    "UPDATE post_publication_attempts SET status = 'failed', error_sha256 = ?, "
                    "completed_at = ? WHERE work_id = ? AND attempt_number = ?",
                    (error_sha256, now, row["work_id"], int(row["attempt_count"])),
                )
            connection.commit()
        return tuple(
            TypedId.parse(row["work_id"], IdKind.POST_PUBLICATION_WORK) for row in rows
        )

    @staticmethod
    def _post_publication_work_from_row(row: sqlite3.Row) -> PostPublicationWork:
        return PostPublicationWork(
            work_id=TypedId.parse(row["work_id"], IdKind.POST_PUBLICATION_WORK),
            kind=PostPublicationWorkKind(row["work_kind"]),
            branch_id=TypedId.parse(row["branch_id"], IdKind.BRANCH),
            artifact_id=TypedId.parse(row["artifact_id"], IdKind.ARTIFACT),
            request_json=row["request_json"],
            request_sha256=row["request_sha256"],
            depends_on_work_id=(
                None
                if row["depends_on_work_id"] is None
                else TypedId.parse(
                    row["depends_on_work_id"], IdKind.POST_PUBLICATION_WORK
                )
            ),
            status=PostPublicationWorkStatus(row["status"]),
            attempt_count=int(row["attempt_count"]),
            result_json=row["result_json"],
            result_sha256=row["result_sha256"],
            error_json=row["error_json"],
            error_sha256=row["error_sha256"],
        )
