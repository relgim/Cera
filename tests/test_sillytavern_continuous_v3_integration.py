from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.request import Request, urlopen

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS
from cera.continuous.scripted_job4 import ScriptedJob4FixtureRuntime
from cera.continuous.sessions import (
    ContinuousSessionCoordinator,
    ContinuousSessionRole,
    InMemoryContinuousStoredSessionPort,
)
from cera.continuous.world import ContinuousWorldStore
from cera.creator_review import CreatorReviewAction
from cera.sillytavern.campaign import CONTINUOUS_V3_CALL_SCHEDULE
from cera.sillytavern.continuous_test import (
    CONTINUOUS_V3_TEST_FIXTURE,
    ContinuousSillyTavernTestAdapter,
)
from cera.sillytavern.models import CERA_CONTINUOUS_V3_TEST_MODEL
from cera.sillytavern.server import CeraSillyTavernServerConfig, build_server
from scripts.run_continuous_planner_validator_job4 import (
    BRANCH_ID,
    ROOT,
    WORLD_ID,
    JobHarness,
    compatibility,
    seed_world,
)


class ContinuousV3HttpIntegrationTests(unittest.TestCase):
    def test_scripted_v3_crosses_exact_http_review_and_ten_stage_route(self) -> None:
        with TemporaryDirectory(prefix="cera-st-v3-http-") as temporary:
            root = Path(temporary)
            world = ContinuousWorldStore(root / "worlds")
            seed_world(world, ROOT)
            lifecycle = root / "provider_workspaces"
            lifecycle.mkdir()
            session_port = InMemoryContinuousStoredSessionPort()
            planner = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.PLANNER), session_port
            )
            validator = ContinuousSessionCoordinator(
                compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
            )
            planner.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
            planner_handle = planner.ensure_session().provider_thread_id
            validator_handle = validator.ensure_session().provider_thread_id
            fixture = ScriptedJob4FixtureRuntime(world_id=WORLD_ID, branch_id=BRANCH_ID)
            harness = JobHarness(
                source_root=ROOT,
                cycle=root / "cycle",
                world=world,
                planner_session=planner,
                validator_session=validator,
                planner_handle=planner_handle,
                validator_handle=validator_handle,
                lifecycle_root=lifecycle,
                call_ledger=ContinuousProviderCallLedger(
                    root / "PROVIDER_CALL_LEDGER.jsonl", maximum_calls=10
                ),
                planner_transport_factory=fixture.planner_transport,
                validator_transport_factory=fixture.validator_transport,
                composer_transport_factory=fixture.composer_transport,
                scripted_provider_free=True,
            )
            fixture.bind(harness)
            adapter = ContinuousSillyTavernTestAdapter(
                session_id="scripted-http-run",
                prepare_turn=harness.prepare_http_turn,
                accept_turn=harness.accept_http_turn,
                route_identity={"profile_id": "continuous-v3-scripted"},
            )
            server = build_server(
                adapter,
                CeraSillyTavernServerConfig(
                    port=0,
                    model=CERA_CONTINUOUS_V3_TEST_MODEL,
                    service="cera-continuous-v3-scripted",
                ),
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"

            def request(method: str, path: str, payload=None):
                data = None if payload is None else json.dumps(payload).encode("utf-8")
                with urlopen(
                    Request(
                        base + path,
                        data=data,
                        method=method,
                        headers={"Content-Type": "application/json"},
                    ),
                    timeout=10,
                ) as response:
                    return response.status, json.load(response)

            try:
                for turn, message in enumerate(CONTINUOUS_V3_TEST_FIXTURE, 1):
                    status, reply = request(
                        "POST",
                        "/v1/chat/completions",
                        {
                            "model": CERA_CONTINUOUS_V3_TEST_MODEL,
                            "stream": False,
                            "cera_session_id": "scripted-http-run",
                            "cera_scene_change": turn == 3,
                            "messages": [{"role": "user", "content": message}],
                        },
                    )
                    self.assertEqual(status, 200)
                    review_id = reply["cera"]["provisional_review_id"]
                    status, review = request("GET", f"/v1/cera/reviews/{review_id}")
                    self.assertEqual(status, 200)
                    self.assertTrue(review["accept_enabled"])
                    status, decision = request(
                        "POST",
                        f"/v1/cera/reviews/{review_id}/decision",
                        {"action": CreatorReviewAction.ACCEPT.value},
                    )
                    self.assertEqual(status, 200)
                    self.assertEqual(decision["status"], "accepted")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual(
                tuple(record["label"] for record in harness.call_records),
                CONTINUOUS_V3_CALL_SCHEDULE,
            )
            self.assertEqual(harness.provider_calls, 0)
            self.assertEqual(harness.scripted_transport_invocations, 10)
            self.assertEqual(len(harness.accepted_pairs), 3)
            self.assertEqual(
                tuple(value["planner_packet_kind"] for value in harness.http_turn_results),
                (
                    "first_turn_initialization",
                    "lean_continuous_continuation",
                    "scene_change",
                ),
            )
            self.assertFalse(planner.unsynchronized_accepted_turn_ids)


if __name__ == "__main__":
    unittest.main()
