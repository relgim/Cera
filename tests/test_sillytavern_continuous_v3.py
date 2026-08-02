from __future__ import annotations

import json
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cera.creator_review import CreatorReviewAction
from cera.serialization import text_sha256
from cera.sillytavern.campaign import (
    CONTINUOUS_V3_CALL_SCHEDULE,
    CONTINUOUS_V3_RUN_IDENTITIES,
    ContinuousV3TwoRunCampaign,
)
from cera.sillytavern.continuous_test import (
    CONTINUOUS_V3_TEST_FIXTURE,
    AcceptedContinuousTestTurn,
    ContinuousSillyTavernTestAdapter,
    PreparedContinuousTestTurn,
)
from cera.sillytavern.models import CERA_CONTINUOUS_V3_TEST_MODEL
from cera.sillytavern.server import CeraSillyTavernServerConfig, build_server


class ContinuousSillyTavernV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.prepared: list[int] = []
        self.accepted: list[int] = []

        def prepare(turn: int) -> PreparedContinuousTestTurn:
            self.prepared.append(turn)
            return PreparedContinuousTestTurn(
                turn_number=turn,
                turn_id=f"turn-{turn:03d}",
                candidate_text=f"candidate {turn}",
                candidate_sha256=text_sha256(f"candidate {turn}"),
                sequence_beats=(f"beat {turn}",),
                validator_package_sha256=text_sha256(f"package {turn}"),
                provider_calls=4 if turn == 3 else 3,
                accept_allowed=True,
            )

        def accept(turn: int) -> AcceptedContinuousTestTurn:
            self.accepted.append(turn)
            return AcceptedContinuousTestTurn(
                turn_number=turn,
                artifact_id=f"artifact:{turn}",
                generation=turn,
                promotion_receipt_sha256=text_sha256(f"receipt {turn}"),
            )

        self.adapter = ContinuousSillyTavernTestAdapter(
            session_id="campaign-session",
            prepare_turn=prepare,
            accept_turn=accept,
            route_identity={"profile_id": "continuous-v3-test"},
        )
        self.server = build_server(
            self.adapter,
            CeraSillyTavernServerConfig(
                port=0,
                model=CERA_CONTINUOUS_V3_TEST_MODEL,
                service="cera-continuous-v3-test",
            ),
        )
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method: str, path: str, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def chat(self, turn: int, *, scene_change: bool = False):
        return self.request(
            "POST",
            "/v1/chat/completions",
            {
                "model": CERA_CONTINUOUS_V3_TEST_MODEL,
                "stream": False,
                "cera_session_id": "campaign-session",
                "cera_scene_change": scene_change,
                "messages": [{"role": "user", "content": CONTINUOUS_V3_TEST_FIXTURE[turn - 1]}],
            },
        )

    def test_real_http_review_and_accept_path_completes_three_turns(self) -> None:
        health_status, health = self.request("GET", "/health")
        models_status, models = self.request("GET", "/v1/models")
        self.assertEqual((health_status, models_status), (200, 200))
        self.assertEqual(health["model"], CERA_CONTINUOUS_V3_TEST_MODEL)
        self.assertEqual(models["data"][0]["id"], CERA_CONTINUOUS_V3_TEST_MODEL)
        for turn in (1, 2, 3):
            status, reply = self.chat(turn, scene_change=turn == 3)
            self.assertEqual(status, 200)
            review_id = reply["cera"]["provisional_review_id"]
            status, review = self.request("GET", f"/v1/cera/reviews/{review_id}")
            self.assertEqual(status, 200)
            self.assertTrue(review["accept_enabled"])
            status, decision = self.request(
                "POST",
                f"/v1/cera/reviews/{review_id}/decision",
                {"action": CreatorReviewAction.ACCEPT.value},
            )
            self.assertEqual(status, 200)
            self.assertEqual(decision["status"], "accepted")
        self.assertEqual(self.prepared, [1, 2, 3])
        self.assertEqual(self.accepted, [1, 2, 3])

    def test_route_and_review_fail_closed(self) -> None:
        status, _ = self.request(
            "POST",
            "/v1/chat/completions",
            {
                "model": "cera-alpha",
                "stream": False,
                "cera_session_id": "campaign-session",
                "messages": [{"role": "user", "content": CONTINUOUS_V3_TEST_FIXTURE[0]}],
            },
        )
        self.assertEqual(status, 400)
        status, reply = self.chat(1)
        self.assertEqual(status, 200)
        status, _ = self.chat(1)
        self.assertEqual(status, 400)
        review_id = reply["cera"]["provisional_review_id"]
        status, _ = self.request(
            "POST",
            f"/v1/cera/reviews/{review_id}/decision",
            {"action": CreatorReviewAction.FALSE_POSITIVE.value},
        )
        self.assertEqual(status, 400)


class ContinuousV3CampaignStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.identity = text_sha256("frozen execution bytes")

    def dispatch_all(self, campaign: ContinuousV3TwoRunCampaign) -> None:
        for label in CONTINUOUS_V3_CALL_SCHEDULE:
            campaign.record_dispatch(label)

    def test_two_passes_require_restart_and_exact_schedule(self) -> None:
        campaign = ContinuousV3TwoRunCampaign(self.identity)
        campaign.begin_run(CONTINUOUS_V3_RUN_IDENTITIES[0], execution_identity_sha256=self.identity)
        self.dispatch_all(campaign)
        campaign.terminalize(passed=True, reason="qualified")
        with self.assertRaises(Exception):
            campaign.begin_run(CONTINUOUS_V3_RUN_IDENTITIES[1], execution_identity_sha256=self.identity)
        campaign.record_controlled_restart(execution_identity_sha256=self.identity)
        campaign.begin_run(CONTINUOUS_V3_RUN_IDENTITIES[1], execution_identity_sha256=self.identity)
        self.dispatch_all(campaign)
        campaign.terminalize(passed=True, reason="qualified")
        self.assertTrue(campaign.complete)
        self.assertEqual(campaign.total_provider_calls, 20)

    def test_failure_and_execution_change_reset_streak(self) -> None:
        campaign = ContinuousV3TwoRunCampaign(self.identity)
        campaign.begin_run(CONTINUOUS_V3_RUN_IDENTITIES[0], execution_identity_sha256=self.identity)
        campaign.record_dispatch(CONTINUOUS_V3_CALL_SCHEDULE[0])
        campaign.terminalize(passed=False, reason="provider failed")
        self.assertEqual(campaign.consecutive_passes, 0)
        with self.assertRaises(Exception):
            campaign.begin_run(
                CONTINUOUS_V3_RUN_IDENTITIES[1],
                execution_identity_sha256=text_sha256("changed"),
            )

    def test_out_of_order_dispatch_and_fifth_run_fail_closed(self) -> None:
        campaign = ContinuousV3TwoRunCampaign(self.identity)
        campaign.begin_run(CONTINUOUS_V3_RUN_IDENTITIES[0], execution_identity_sha256=self.identity)
        with self.assertRaises(Exception):
            campaign.record_dispatch(CONTINUOUS_V3_CALL_SCHEDULE[1])


if __name__ == "__main__":
    unittest.main()
