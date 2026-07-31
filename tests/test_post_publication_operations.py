from __future__ import annotations

import unittest
import sqlite3

from cera.composer import PurePresentationRenderer, RendererProfile
from cera.errors import ErrorCode
from cera.ids import IdKind, deterministic_id
from cera.runtime import (
    PostPublicationCoordinator,
    PostPublicationOperation,
    PostPublicationOperationsService,
)
from cera.registry import build_schema_registry
from cera.serialization import to_primitive
from cera.serialization import canonical_json, text_sha256
from cera.storage import PostPublicationWorkKind, PostPublicationWorkStatus
import tests.test_post_publication as post_support


class PostPublicationOperationsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.harness = post_support.PostPublicationTests("runTest")
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    @property
    def store(self):
        return self.harness.live.real.sandbox.store

    @staticmethod
    def renderer():
        return PurePresentationRenderer((RendererProfile("plain", "v1"),))

    def crash_after_publication(self, suffix: str):
        result = self.harness.result(suffix)
        consolidator = self.harness.no_change_port(suffix)
        with self.assertRaises(RuntimeError):
            post_support.CrashAfterPublicationCoordinator(self.store).execute(
                result,
                renderer=self.renderer(),
                renderer_profile="plain",
                consolidator=consolidator,
            )
        return result, consolidator

    def test_actionable_listing_and_dispatch_are_secret_safe(self) -> None:
        result, consolidator = self.crash_after_publication("phase19-dispatch")
        artifact = result.accepted_artifact
        service = PostPublicationOperationsService(self.store)

        actionable = service.list_actionable()
        self.assertEqual(len(actionable), 1)
        report = actionable[0]
        self.assertEqual(report.pending_count, 3)
        self.assertEqual(report.failed_count, 0)
        serialized = canonical_json(report)
        self.assertNotIn(artifact.accepted_prose, serialized)
        self.assertNotIn("renderer_profile", serialized)
        self.assertNotIn("protected_user_id", serialized)
        self.assertNotIn("exact_evidence", serialized)
        decoded = build_schema_registry().decode(to_primitive(report))
        self.assertEqual(decoded, report)

        dispatched = service.dispatch_pending(
            artifact.artifact_id,
            renderer=self.renderer(),
            consolidator=consolidator,
        )
        self.assertEqual(
            dispatched.receipt.operation,
            PostPublicationOperation.DISPATCH_PENDING,
        )
        self.assertEqual(len(dispatched.receipt.requested_work_ids), 3)
        self.assertEqual(len(dispatched.receipt.affected_work_ids), 3)
        self.assertEqual(dispatched.reports[0].terminal_count, 3)
        self.assertEqual(dispatched.reports[0].failed_count, 0)
        self.assertEqual(consolidator.invocation_count, 1)
        self.assertEqual(service.list_actionable(), ())

    def test_failure_envelopes_are_sanitized_and_retry_is_exact(self) -> None:
        result = self.harness.result("phase19-retry")
        unavailable = self.harness.no_change_port("phase19-unavailable")
        unavailable.available = False
        PostPublicationCoordinator(self.store).execute(
            result,
            renderer=post_support.FailingRenderer(),
            renderer_profile="plain",
            consolidator=unavailable,
        )
        artifact_id = result.accepted_artifact.artifact_id
        service = PostPublicationOperationsService(self.store)
        report = service.inspect_artifact(artifact_id)
        self.assertEqual(report.failed_count, 2)
        self.assertEqual(report.pending_count, 1)
        self.assertEqual(len(report.errors), 2)
        public = canonical_json(report)
        self.assertNotIn("simulated renderer outage", public)
        self.assertNotIn("scripted consolidator is unavailable", public)
        self.assertTrue(
            all(
                value.error_code is ErrorCode.POST_PUBLICATION_WORK_FAILED
                for value in report.errors
            )
        )

        wrong = deterministic_id(
            IdKind.POST_PUBLICATION_WORK,
            "phase19.wrong",
            "not-this-artifact",
        )
        with self.assertRaisesRegex(ValueError, "only failed work"):
            service.retry_failed(
                artifact_id,
                work_ids=(wrong,),
                authorization_reason="reviewed",
                renderer=self.renderer(),
                consolidator=self.harness.no_change_port("unused"),
            )

        failed_ids = tuple(value.work_id for value in report.errors)
        healthy = self.harness.no_change_port("phase19-retry-healthy")
        reason = "operator reviewed the two simulated failures"
        retried = service.retry_failed(
            artifact_id,
            work_ids=failed_ids,
            authorization_reason=reason,
            renderer=self.renderer(),
            consolidator=healthy,
        )
        self.assertEqual(retried.reports[0].failed_count, 0)
        self.assertEqual(retried.reports[0].terminal_count, 3)
        self.assertEqual(
            retried.receipt.authorization_reason_sha256,
            text_sha256(reason),
        )
        self.assertNotIn(reason, canonical_json(retried))
        self.assertEqual(healthy.invocation_count, 1)
        with sqlite3.connect(self.store.database_path) as connection:
            stored_reasons = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT authorization_reason_sha256 FROM post_publication_attempts"
                )
            )
        self.assertIn(text_sha256(reason), stored_reasons)
        self.assertNotIn(reason, stored_reasons)

    def test_explicit_recovery_reports_interrupted_work_without_reason_leak(self) -> None:
        result, _ = self.crash_after_publication("phase19-recovery")
        artifact_id = result.accepted_artifact.artifact_id
        render_work = next(
            value
            for value in self.store.post_publication_work_for_artifact(artifact_id)
            if value.kind is PostPublicationWorkKind.RENDER
        )
        self.store.claim_post_publication_work(render_work.work_id)
        service = PostPublicationOperationsService(self.store)
        reason = "operator confirmed the previous worker process ended"
        recovered = service.recover_interrupted(authorization_reason=reason)
        self.assertEqual(
            recovered.receipt.operation,
            PostPublicationOperation.RECOVER_INTERRUPTED,
        )
        self.assertEqual(recovered.receipt.requested_work_ids, (render_work.work_id,))
        self.assertEqual(recovered.receipt.affected_work_ids, (render_work.work_id,))
        report = recovered.reports[0]
        self.assertEqual(report.failed_count, 1)
        self.assertEqual(report.errors[0].error_code, ErrorCode.POST_PUBLICATION_WORK_INTERRUPTED)
        self.assertEqual(
            next(
                value.status
                for value in report.work_items
                if value.work_id == render_work.work_id
            ),
            PostPublicationWorkStatus.FAILED,
        )
        self.assertNotIn(reason, canonical_json(recovered))
        failed = self.store.get_post_publication_work(render_work.work_id)
        self.assertNotIn(reason, failed.error_json)
        self.assertIn(text_sha256(reason), failed.error_json)


if __name__ == "__main__":
    unittest.main()
