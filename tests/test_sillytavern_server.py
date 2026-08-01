from __future__ import annotations

import http.client
import json
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cera.creator_review import (
    CreatorReviewAction,
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    CreatorReviewState,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.ids import IdKind, TypedId
from cera.sillytavern import (
    CeraSillyTavernServerConfig,
    SillyTavernTurnReply,
    build_server,
)
from scripts.run_cera_sillytavern_server import (
    ROOT as SERVER_ROOT,
    _require_repository_virtual_environment,
)


class StubAdapter:
    def __init__(self):
        self.last_request = None
        self.review_id = TypedId(IdKind.REVIEW_PACKET, "stub-review")
        self.review = SimpleNamespace(
            review_id=self.review_id,
            state=CreatorReviewState.REVIEW_READY,
            candidate_text="Provisional reply.",
            candidate_sha256="a" * 64,
            candidate_text_sha256="b" * 64,
            sequence_beats=("Sakura verifies the caller.",),
            sequence_plan_sha256="c" * 64,
            assessment=CreatorReviewAssessment(
                schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
                severity=CreatorReviewSeverity.GOOD,
                publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
                issue_owner=ReviewIssueOwner.NONE,
                reason_codes=(),
                creator_reason="The sequence was realized within scope.",
                verifier_status="verified",
            ),
            prepared_package=SimpleNamespace(
                package_id=TypedId(IdKind.PREPARED_PUBLICATION, "stub-package")
            ),
            creator_action=None,
            live_result_json=json.dumps({
                "composer": {
                    "manifest": {
                        "character_spans": [
                            {
                                "kind": "dialogue",
                                "owner_id": "character:sakura_hanezawa",
                                "start": 0,
                                "end": len("Provisional reply."),
                            }
                        ]
                    }
                }
            }),
        )
        self.last_review_action = None

    @property
    def reasoner_session_status(self):
        return {
            "mode": "test_factory",
            "active": False,
        }

    def complete(self, request):
        self.last_request = request
        return SillyTavernTurnReply(
            prose="Accepted presentation-neutral reply.",
            request_id="request:stub",
            artifact_id="artifact:stub",
            generation=1,
            provider_calls=3,
            exact_replay=False,
        )

    def get_review(self, review_id):
        if review_id != self.review_id:
            raise ValueError("unknown review")
        return self.review

    def review_action(self, review_id, action, *, feedback=None):
        if review_id != self.review_id:
            raise ValueError("unknown review")
        self.last_review_action = (action, feedback)
        if action in {
            CreatorReviewAction.ACCEPT,
            CreatorReviewAction.FALSE_POSITIVE,
        }:
            return SimpleNamespace(
                publication=SimpleNamespace(
                    receipt=SimpleNamespace(
                        new_artifact_id=TypedId(IdKind.ARTIFACT, "stub-artifact"),
                        generation_after=1,
                    )
                )
            )
        return self.review

    def get_accept_timing(self, review_id):
        if review_id != self.review_id:
            return None
        return SimpleNamespace(accept_to_commit_microseconds=1250)


class SillyTavernServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = StubAdapter()
        self.server = build_server(
            self.adapter,
            CeraSillyTavernServerConfig(port=0),
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def request(self, method: str, path: str, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = None if payload is None else json.dumps(payload)
        headers = {} if body is None else {"Content-Type": "application/json"}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        data = json.loads(response.read())
        connection.close()
        return response.status, data

    def test_models_and_chat_completion_are_openai_compatible(self) -> None:
        status, models = self.request("GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(models["data"][0]["id"], "cera-alpha")
        status, result = self.request(
            "POST",
            "/v1/chat/completions",
            {
                "model": "cera-alpha",
                "stream": False,
                "cera_session_id": "st_chat_1234567890abcdef",
                "cera_scene_depth": "AUTO",
                "cera_character_autonomy": "MIND",
                "cera_prompt_handling": "MODIFICATION",
                "cera_reasoning_effort": "XHIGH",
                "cera_scene_change": True,
                "messages": [{"role": "user", "content": "test"}],
            },
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            result["choices"][0]["message"]["content"],
            "Accepted presentation-neutral reply.",
        )
        self.assertEqual(result["cera"]["provider_calls"], 3)
        self.assertEqual(
            self.adapter.last_request.cera_session_id,
            "st_chat_1234567890abcdef",
        )
        self.assertEqual(self.adapter.last_request.cera_scene_depth, "auto")
        self.assertEqual(self.adapter.last_request.cera_character_autonomy, "mind")
        self.assertEqual(self.adapter.last_request.cera_prompt_handling, "modification")
        self.assertEqual(self.adapter.last_request.cera_reasoning_effort, "xhigh")
        self.assertTrue(self.adapter.last_request.cera_scene_change)

    def test_health_exposes_the_validated_active_runtime_profile(self) -> None:
        status, result = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["active_runtime"]["valid"])
        self.assertEqual(
            result["active_runtime"]["profile_id"],
            "cera.active_runtime.d180.v1",
        )
        self.assertEqual(
            result["active_runtime"]["reasoner"]["prompt_version"],
            "cera.codex_scene_reasoner_prompt.v25",
        )

    def test_streaming_and_unknown_models_fail_closed(self) -> None:
        status, result = self.request(
            "POST",
            "/v1/chat/completions",
            {
                "model": "other",
                "stream": True,
                "messages": [{"role": "user", "content": "test"}],
            },
        )
        self.assertEqual(status, 400)
        self.assertFalse(result["error"]["retryable"])
        self.assertFalse(result["error"]["fallback_used"])

    def test_reasoner_failure_returns_privacy_safe_actionable_diagnostic(self) -> None:
        failure = RuntimeError("scene reasoner failed before typed acceptance")
        failure.envelope = SimpleNamespace(
            stage="reasoner_dispatch",
            error_code=SimpleNamespace(value="CERA_REASONER_UNAVAILABLE"),
            message="scene reasoner failed before typed acceptance",
        )
        failure.failure_evidence_bundle = SimpleNamespace(
            safe_diagnostic_codes=("REASONER_WORKER_STAGE_THREAD_RESUME",)
        )

        def fail(_request):
            raise failure

        self.adapter.complete = fail
        status, result = self.request(
            "POST",
            "/v1/chat/completions",
            {
                "model": "cera-alpha",
                "stream": False,
                "cera_session_id": "st_chat_diagnostic",
                "messages": [{"role": "user", "content": "test"}],
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            result["error"]["diagnostics"],
            ["REASONER_WORKER_STAGE_THREAD_RESUME"],
        )
        self.assertIn(
            "REASONER_WORKER_STAGE_THREAD_RESUME",
            result["error"]["message"],
        )
        self.assertFalse(result["error"]["retryable"])
        self.assertFalse(result["error"]["fallback_used"])

    def test_server_launch_requires_repository_virtual_environment(self) -> None:
        with patch(
            "scripts.run_cera_sillytavern_server.sys.prefix",
            str(SERVER_ROOT / ".venv"),
        ):
            _require_repository_virtual_environment()
        with patch(
            "scripts.run_cera_sillytavern_server.sys.prefix",
            str(SERVER_ROOT / "wrong-python"),
        ):
            with self.assertRaisesRegex(RuntimeError, "must run from"):
                _require_repository_virtual_environment()

    def test_creator_review_lookup_and_accept_transport_are_typed(self) -> None:
        path = f"/v1/cera/reviews/{self.adapter.review_id}"
        status, review = self.request("GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(review["state"], "review_ready")
        self.assertEqual(review["sequence_plan"], ["Sakura verifies the caller."])
        self.assertTrue(review["accept_enabled"])

        status, accepted = self.request(
            "POST",
            path + "/decision",
            {"action": "accept", "feedback": None},
        )
        self.assertEqual(status, 200)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["provider_calls"], 0)
        self.assertEqual(accepted["automatic_retries"], 0)
        self.assertEqual(accepted["accept_to_commit_microseconds"], 1250)
        self.assertEqual(
            self.adapter.last_review_action,
            (CreatorReviewAction.ACCEPT, None),
        )

    def test_false_positive_acceptance_is_a_distinct_typed_action(self) -> None:
        self.adapter.review.assessment = CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.CRITICAL,
            publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
            issue_owner=ReviewIssueOwner.VERIFIER,
            reason_codes=("invented_history",),
            creator_reason="The verifier identified a possible unsupported detail.",
            verifier_status="verified_with_concern",
        )
        path = f"/v1/cera/reviews/{self.adapter.review_id}"
        status, accepted = self.request(
            "POST",
            path + "/decision",
            {"action": "false_positive", "feedback": None},
        )
        self.assertEqual(status, 200)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(accepted["creator_action"], "false_positive")
        self.assertEqual(
            self.adapter.last_review_action,
            (CreatorReviewAction.FALSE_POSITIVE, None),
        )

    def test_review_payload_exposes_only_validated_visible_speaker_quotes(self) -> None:
        candidate = 'Sakura paused. "Who are you?"'
        self.adapter.review.candidate_text = candidate
        self.adapter.review.live_result_json = json.dumps({
            "composer": {
                "manifest": {
                    "character_spans": [
                        {
                            "kind": "dialogue",
                            "owner_id": "character:sakura_hanezawa",
                            "start": 0,
                            "end": len(candidate),
                        },
                        {
                            "kind": "private_state",
                            "owner_id": "character:enne_hanezawa",
                            "start": 0,
                            "end": len(candidate),
                        },
                    ]
                }
            }
        })
        status, review = self.request(
            "GET", f"/v1/cera/reviews/{self.adapter.review_id}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            review["speaker_marks"],
            [{"speaker": "sakura", "kind": "dialogue", "quote": '"Who are you?"'}],
        )


if __name__ == "__main__":
    unittest.main()
