from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from uuid import uuid4
from urllib.error import HTTPError
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
from cera.errors import StateConflictError
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, TypedId
from cera.runtime import HanezawaContinuousManualWorld
from cera.serialization import bytes_sha256, canonical_sha256, text_sha256
from cera.sillytavern.continuous_manual import (
    CONTINUOUS_V3_MANUAL_PORT,
    CONTINUOUS_V3_MANUAL_PROFILE_ID,
    CONTINUOUS_V3_MANUAL_SERVICE,
    ContinuousManualStateStore,
    ContinuousSillyTavernManualAdapter,
)
from cera.sillytavern.models import (
    CERA_CONTINUOUS_V3_MANUAL_MODEL,
    CERA_VIRTUAL_MODEL,
    SillyTavernChatRequest,
)
from cera.sillytavern.server import CeraSillyTavernServerConfig, build_server
from scripts.run_cera_sillytavern_continuous_manual import (
    DEFAULT_MANUAL_SESSION_ID,
    PROJECT_ROOT,
    ManualJobHarness,
    _http_json,
    _process_path,
    _record_process_stop_failure,
    _signed_record,
    _stop_request_path,
    _validate_manual_sillytavern_profile,
    build_scripted_manual_adapter,
    manual_execution_manifest,
    manual_status,
    pending_review_status,
    recover_stale_process_record,
    reset_manual_root,
    start_manual_root,
    stop_manual_root,
    verify_isolation,
)
from scripts.run_cera_sillytavern_continuous_manual_readiness import (
    FUTURE_LIVE_V2_RUN_IDS,
    readiness_manifest,
    readiness_run_ids,
    run_readiness,
)
from scripts.run_continuous_planner_validator_job4 import (
    ROOT,
    compatibility,
    seed_world,
)


class _ManualFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.execution_identity = "a" * 64
        self.session_id = "manual-http-test"
        self.authority_world = HanezawaContinuousManualWorld.initialize(
            ROOT,
            root / "manual_authority.sqlite3",
            replace=True,
        )
        self.world_id = self.authority_world.world_id.value
        self.branch_id = self.authority_world.branch_id.value
        self.world = ContinuousWorldStore(root / "continuous_world")
        seed_world(
            self.world,
            ROOT,
            world_id=self.world_id,
            branch_id=self.branch_id,
        )
        self.lifecycle = root / "provider_workspaces"
        self.lifecycle.mkdir()
        self.session_port = InMemoryContinuousStoredSessionPort()
        self.planner = ContinuousSessionCoordinator(
            compatibility(
                self.world,
                ContinuousSessionRole.PLANNER,
                world_id=self.world_id,
                branch_id=self.branch_id,
            ),
            self.session_port,
        )
        self.validator = ContinuousSessionCoordinator(
            compatibility(
                self.world,
                ContinuousSessionRole.VALIDATOR,
                world_id=self.world_id,
                branch_id=self.branch_id,
            ),
            self.session_port,
        )
        self.planner.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
        planner_handle = self.planner.ensure_session().provider_thread_id
        validator_handle = self.validator.ensure_session().provider_thread_id
        self.scripted = ScriptedJob4FixtureRuntime(
            world_id=self.world_id,
            branch_id=self.branch_id,
        )
        self.harness = ManualJobHarness(
            source_root=ROOT,
            cycle=root / "cycle",
            world=self.world,
            planner_session=self.planner,
            validator_session=self.validator,
            planner_handle=planner_handle,
            validator_handle=validator_handle,
            lifecycle_root=self.lifecycle,
            call_ledger=ContinuousProviderCallLedger(
                root / "PROVIDER_CALL_LEDGER.jsonl",
                maximum_calls=10,
            ),
            planner_transport_factory=self.scripted.planner_transport,
            validator_transport_factory=self.scripted.validator_transport,
            composer_transport_factory=self.scripted.composer_transport,
            scripted_provider_free=True,
            authority_world=self.authority_world,
            execution_identity_sha256=self.execution_identity,
        )
        self.scripted.bind(self.harness)
        self.state = ContinuousManualStateStore(
            root / "manual_state",
            identity={
                "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                "port": CONTINUOUS_V3_MANUAL_PORT,
                "service": CONTINUOUS_V3_MANUAL_SERVICE,
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "session_id": self.session_id,
                "execution_identity_sha256": self.execution_identity,
            },
        )

    def adapter(self, process: str) -> ContinuousSillyTavernManualAdapter:
        return ContinuousSillyTavernManualAdapter(
            session_id=self.session_id,
            profile_id=CONTINUOUS_V3_MANUAL_PROFILE_ID,
            execution_identity_sha256=self.execution_identity,
            process_instance_id=process,
            state_store=self.state,
            prepare_turn=self.harness.prepare_manual_http_turn,
            accept_turn=self.harness.accept_manual_http_turn,
            reject_turn=self.harness.reject_manual_http_turn,
            route_identity={"port": CONTINUOUS_V3_MANUAL_PORT},
        )


class ContinuousManualHttpTests(unittest.TestCase):
    @staticmethod
    def _request(base: str, method: str, path: str, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def _serve(self, adapter):
        server = build_server(
            adapter,
            CeraSillyTavernServerConfig(
                port=0,
                model=CERA_CONTINUOUS_V3_MANUAL_MODEL,
                service=CONTINUOUS_V3_MANUAL_SERVICE,
            ),
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    @staticmethod
    def _turn_payload(fixture: _ManualFixture, message: str, *, scene_change=False):
        return {
            "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
            "stream": False,
            "cera_session_id": fixture.session_id,
            "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
            "cera_scene_change": scene_change,
            "messages": [{"role": "user", "content": message}],
        }

    def test_arbitrary_three_turn_two_scene_route_uses_exact_review_and_accept(self) -> None:
        with TemporaryDirectory(prefix="cera-continuous-manual-") as directory:
            fixture = _ManualFixture(Path(directory))
            server, thread, base = self._serve(fixture.adapter("process-one"))
            prompts = (
                "Ted knocks and asks whether this is the Hanezawa residence.",
                "Continue the doorway conversation with only the current character.",
                "The next morning, Ted is in the kitchen with Mia and asks about Sakura.",
            )
            try:
                status, health = self._request(base, "GET", "/health")
                self.assertEqual(status, 200)
                self.assertEqual(health["model"], CERA_CONTINUOUS_V3_MANUAL_MODEL)
                status, models = self._request(base, "GET", "/v1/models")
                self.assertEqual(status, 200)
                self.assertEqual(models["data"][0]["id"], CERA_CONTINUOUS_V3_MANUAL_MODEL)
                for turn, prompt in enumerate(prompts, 1):
                    status, reply = self._request(
                        base,
                        "POST",
                        "/v1/chat/completions",
                        self._turn_payload(
                            fixture,
                            prompt,
                            scene_change=turn == 3,
                        ),
                    )
                    self.assertEqual(status, 200, reply)
                    review_id = reply["cera"]["provisional_review_id"]
                    status, review = self._request(
                        base, "GET", f"/v1/cera/reviews/{review_id}"
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(review["accept_enabled"])
                    self.assertEqual(review["raw_source_sha256"], fixture.state.load()["attempts"][-1]["raw_source_sha256"])
                    status, decision = self._request(
                        base,
                        "POST",
                        f"/v1/cera/reviews/{review_id}/decision",
                        {"action": CreatorReviewAction.ACCEPT.value},
                    )
                    self.assertEqual(status, 200, decision)
                    self.assertEqual(decision["status"], "accepted")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
            self.assertEqual(len(fixture.state.accepted_turn_ids), 3)
            self.assertEqual(fixture.harness.scripted_transport_invocations, 10)
            self.assertEqual(fixture.harness.provider_calls, 0)
            state = fixture.state.load()
            self.assertEqual(state["scene_number"], 2)
            self.assertEqual(state["current_scene_accepted_turn_ids"], ["turn-003"])

    def test_restart_blocks_stale_accept_but_allows_exact_decline(self) -> None:
        with TemporaryDirectory(prefix="cera-continuous-manual-restart-") as directory:
            fixture = _ManualFixture(Path(directory))
            server, thread, base = self._serve(fixture.adapter("process-one"))
            try:
                status, reply = self._request(
                    base,
                    "POST",
                    "/v1/chat/completions",
                    self._turn_payload(fixture, "An arbitrary arrival cue for Sakura."),
                )
                self.assertEqual(status, 200, reply)
                review_id = reply["cera"]["provisional_review_id"]
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

            restarted = fixture.adapter("process-two")
            server, thread, base = self._serve(restarted)
            try:
                status, review = self._request(
                    base, "GET", f"/v1/cera/reviews/{review_id}"
                )
                self.assertEqual(status, 200)
                self.assertFalse(review["accept_enabled"])
                self.assertTrue(review["decline_enabled"])
                self.assertTrue(review["recovered_after_restart"])
                status, failed_accept = self._request(
                    base,
                    "POST",
                    f"/v1/cera/reviews/{review_id}/decision",
                    {"action": CreatorReviewAction.ACCEPT.value},
                )
                self.assertEqual(status, 400, failed_accept)
                status, declined = self._request(
                    base,
                    "POST",
                    f"/v1/cera/reviews/{review_id}/decision",
                    {"action": CreatorReviewAction.DECLINE.value},
                )
                self.assertEqual(status, 200, declined)
                self.assertEqual(declined["status"], "rejected")
                self.assertTrue(declined["recovered_after_restart"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
            self.assertEqual(fixture.state.accepted_turn_ids, ())
            self.assertIsNone(fixture.state.current_review_id)

    def test_current_scene_cast_is_durable_and_new_scene_cast_is_explicit(self) -> None:
        with TemporaryDirectory(prefix="cera-continuous-manual-cast-") as directory:
            fixture = _ManualFixture(Path(directory))
            server, thread, base = self._serve(fixture.adapter("process-cast"))
            try:
                prompts = (
                    "Ted rings the Hanezawa doorbell.",
                    "Mia joins Sakura at the doorway.",
                )
                expected_casts = (
                    {str(CHARACTER_IDS["Sakura"])},
                    {
                        str(CHARACTER_IDS["Sakura"]),
                        str(CHARACTER_IDS["Mia"]),
                    },
                )
                for prompt, expected_cast in zip(
                    prompts, expected_casts, strict=True
                ):
                    status, reply = self._request(
                        base,
                        "POST",
                        "/v1/chat/completions",
                        self._turn_payload(fixture, prompt),
                    )
                    self.assertEqual(status, 200, reply)
                    review_id = reply["cera"]["provisional_review_id"]
                    status, review = self._request(
                        base, "GET", f"/v1/cera/reviews/{review_id}"
                    )
                    self.assertEqual(status, 200, review)
                    self.assertEqual(
                        set(review["active_character_ids"]), expected_cast
                    )
                    status, decision = self._request(
                        base,
                        "POST",
                        f"/v1/cera/reviews/{review_id}/decision",
                        {"action": CreatorReviewAction.ACCEPT.value},
                    )
                    self.assertEqual(status, 200, decision)
                self.assertEqual(
                    set(fixture.state.load()["current_scene_character_ids"]),
                    expected_casts[-1],
                )
                resolved = fixture.harness._candidate_character_ids(
                    "Continue with only the current characters.",
                    scene_change=False,
                    current_scene_character_ids=tuple(
                        fixture.state.load()["current_scene_character_ids"]
                    ),
                )
                self.assertEqual(
                    {str(value) for value in resolved}, expected_casts[-1]
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

        with TemporaryDirectory(prefix="cera-m-sc-") as directory:
            fixture = _ManualFixture(Path(directory))
            server, thread, base = self._serve(
                fixture.adapter("process-scene-cast")
            )
            try:
                status, reply = self._request(
                    base,
                    "POST",
                    "/v1/chat/completions",
                    self._turn_payload(fixture, "Sakura answers the door."),
                )
                self.assertEqual(status, 200, reply)
                review_id = reply["cera"]["provisional_review_id"]
                status, decision = self._request(
                    base,
                    "POST",
                    f"/v1/cera/reviews/{review_id}/decision",
                    {"action": CreatorReviewAction.ACCEPT.value},
                )
                self.assertEqual(status, 200, decision)
                status, rejected = self._request(
                    base,
                    "POST",
                    "/v1/chat/completions",
                    self._turn_payload(
                        fixture,
                        "The next morning, continue in the new room.",
                        scene_change=True,
                    ),
                )
                self.assertEqual(status, 400, rejected)
                self.assertEqual(
                    fixture.harness.scripted_transport_invocations, 3
                )
                self.assertIsNone(fixture.state.current_review_id)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_tampered_review_bytes_block_lookup_and_accept_without_mutation(self) -> None:
        with TemporaryDirectory(prefix="cera-continuous-manual-tamper-") as directory:
            fixture = _ManualFixture(Path(directory))
            server, thread, base = self._serve(fixture.adapter("process-tamper"))
            try:
                status, reply = self._request(
                    base,
                    "POST",
                    "/v1/chat/completions",
                    self._turn_payload(fixture, "Sakura answers the door."),
                )
                self.assertEqual(status, 200, reply)
                review_id = reply["cera"]["provisional_review_id"]
                state_before = fixture.state.load()
                review_paths = tuple(fixture.state.reviews_root.glob("*.json"))
                self.assertEqual(len(review_paths), 1)
                wrapper = json.loads(review_paths[0].read_text(encoding="utf-8"))
                wrapper["record"]["prepared"]["candidate_text"] += " altered"
                review_paths[0].write_text(json.dumps(wrapper), encoding="utf-8")

                status, lookup = self._request(
                    base, "GET", f"/v1/cera/reviews/{review_id}"
                )
                self.assertEqual(status, 400, lookup)
                self.assertEqual(lookup["error"]["code"], "cera_review_failed")
                status, decision = self._request(
                    base,
                    "POST",
                    f"/v1/cera/reviews/{review_id}/decision",
                    {"action": CreatorReviewAction.ACCEPT.value},
                )
                self.assertEqual(status, 400, decision)
                self.assertEqual(fixture.state.load(), state_before)
                self.assertEqual(fixture.state.accepted_turn_ids, ())
                self.assertEqual(str(fixture.state.current_review_id), review_id)

                wrapper = json.loads(review_paths[0].read_text(encoding="utf-8"))
                wrapper["record"]["prepared"]["process_instance_id"] = (
                    "rehashed-substitution"
                )
                wrapper["record_sha256"] = canonical_sha256(wrapper["record"])
                review_paths[0].write_text(json.dumps(wrapper), encoding="utf-8")
                status, rehashed = self._request(
                    base, "GET", f"/v1/cera/reviews/{review_id}"
                )
                self.assertEqual(status, 400, rehashed)
                self.assertEqual(rehashed["error"]["code"], "cera_review_failed")
                self.assertEqual(fixture.state.load(), state_before)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_rehashed_semantic_state_substitution_fails_closed(self) -> None:
        with TemporaryDirectory(prefix="cera-manual-state-") as directory:
            fixture = _ManualFixture(Path(directory))
            state = json.loads(fixture.state.path.read_text(encoding="utf-8"))
            state.pop("state_sha256")
            state["unrecognized_semantic_field"] = "substituted"
            state["state_sha256"] = canonical_sha256(state)
            fixture.state.path.write_text(
                json.dumps(state, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                StateConflictError, "continuous manual state hash changed"
            ):
                fixture.state.load()
            self.assertEqual(fixture.harness.scripted_transport_invocations, 0)
            self.assertEqual(fixture.harness.provider_calls, 0)

    def test_route_rejects_model_profile_controls_and_non_loopback_bind(self) -> None:
        with TemporaryDirectory(prefix="cera-continuous-manual-route-") as directory:
            fixture = _ManualFixture(Path(directory))
            adapter = fixture.adapter("process-route")
            with self.assertRaisesRegex(ValueError, "loopback-only"):
                CeraSillyTavernServerConfig(
                    host="0.0.0.0",
                    port=CONTINUOUS_V3_MANUAL_PORT,
                    model=CERA_CONTINUOUS_V3_MANUAL_MODEL,
                )
            with self.assertRaisesRegex(ValueError, "model identities"):
                build_server(
                    adapter,
                    CeraSillyTavernServerConfig(model=CERA_VIRTUAL_MODEL),
                )
            server, thread, base = self._serve(adapter)
            try:
                payload = self._turn_payload(fixture, "Sakura answers the door.")
                payload["model"] = CERA_VIRTUAL_MODEL
                status, body = self._request(
                    base, "POST", "/v1/chat/completions", payload
                )
                self.assertEqual(status, 400, body)
                payload = self._turn_payload(fixture, "Sakura answers the door.")
                payload["cera_profile_id"] = "cera.active_runtime.d180.v1"
                status, body = self._request(
                    base, "POST", "/v1/chat/completions", payload
                )
                self.assertEqual(status, 400, body)
                payload = self._turn_payload(fixture, "Sakura answers the door.")
                payload["cera_reasoning_effort"] = "medium"
                status, body = self._request(
                    base, "POST", "/v1/chat/completions", payload
                )
                self.assertEqual(status, 400, body)
                payload = self._turn_payload(fixture, "Sakura answers the door.")
                payload["cera_unreviewed_control"] = True
                status, body = self._request(
                    base, "POST", "/v1/chat/completions", payload
                )
                self.assertEqual(status, 400, body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)
            self.assertEqual(fixture.harness.scripted_transport_invocations, 0)
            self.assertEqual(fixture.harness.provider_calls, 0)


class ContinuousManualLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = (
            PROJECT_ROOT
            / "runtime"
            / "manual"
            / f"unittest-{uuid4().hex}"
        )
        self.assertFalse(self.root.exists())
        reset_manual_root(self.root, session_id=DEFAULT_MANUAL_SESSION_ID)

    def tearDown(self) -> None:
        if not self.root.exists():
            return
        try:
            status = manual_status(self.root, verify_execution=False)
            if status["status"] == "running":
                stop_manual_root(self.root)
        finally:
            if self.root.exists():
                self.assertEqual(
                    self.root.parent.resolve(),
                    (PROJECT_ROOT / "runtime" / "manual").resolve(),
                )
                if _process_path(self.root).exists():
                    record = json.loads(
                        _process_path(self.root).read_text(encoding="utf-8")
                    )
                    pid = int(record.get("pid", 0))
                    if pid > 0:
                        os.kill(pid, 15)
                shutil.rmtree(self.root)

    @staticmethod
    def _submit(message: str, *, scene_change: bool = False):
        return _http_json(
            "POST",
            "/v1/chat/completions",
            {
                "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                "stream": False,
                "cera_session_id": DEFAULT_MANUAL_SESSION_ID,
                "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                "cera_scene_change": scene_change,
                "messages": [{"role": "user", "content": message}],
            },
            timeout=20,
        )

    @staticmethod
    def _accept(review_id: str):
        return _http_json(
            "POST",
            f"/v1/cera/reviews/{review_id}/decision",
            {"action": CreatorReviewAction.ACCEPT.value},
            timeout=20,
        )

    def test_real_process_start_health_restart_and_isolation(self) -> None:
        started = start_manual_root(self.root)
        self.assertEqual(started["status"], "running")
        status, health = _http_json("GET", "/health")
        self.assertEqual(status, 200, health)
        self.assertEqual(health["model"], CERA_CONTINUOUS_V3_MANUAL_MODEL)
        self.assertEqual(
            health["reasoner_session"]["profile_id"],
            CONTINUOUS_V3_MANUAL_PROFILE_ID,
        )
        status, models = _http_json("GET", "/v1/models")
        self.assertEqual(status, 200, models)
        self.assertEqual(models["data"][0]["id"], CERA_CONTINUOUS_V3_MANUAL_MODEL)

        status, reply = self._submit(
            "Ted knocks and asks Sakura whether this is the Hanezawa residence."
        )
        self.assertEqual(status, 200, reply)
        review_id = reply["cera"]["provisional_review_id"]
        pending = pending_review_status(self.root)
        self.assertTrue(pending["unresolved_review"])
        self.assertEqual(pending["http_status"], 200)
        self.assertEqual(pending["review_id"], review_id)
        status, review = _http_json("GET", f"/v1/cera/reviews/{review_id}")
        self.assertEqual(status, 200, review)
        self.assertTrue(review["accept_enabled"])
        status, decision = self._accept(review_id)
        self.assertEqual(status, 200, decision)
        self.assertEqual(decision["status"], "accepted")
        self.assertEqual(
            pending_review_status(self.root),
            {"status": "none", "unresolved_review": False},
        )
        first_stop = stop_manual_root(self.root)
        self.assertFalse(first_stop["port_open"])

        restarted = start_manual_root(self.root)
        self.assertEqual(restarted["status"], "running")
        self.assertNotEqual(
            restarted["process_instance_sha256"],
            started["process_instance_sha256"],
        )
        status, reply = self._submit(
            "Continue the doorway conversation with only the current character."
        )
        self.assertEqual(status, 200, reply)
        review_id = reply["cera"]["provisional_review_id"]
        status, decision = self._accept(review_id)
        self.assertEqual(status, 200, decision)
        self.assertEqual(decision["generation"], 2)
        stop_manual_root(self.root)
        isolation = verify_isolation(self.root)
        self.assertTrue(isolation["passed"], isolation)

    def test_unresolved_review_restart_requires_exact_recovered_decline(self) -> None:
        start_manual_root(self.root)
        status, reply = self._submit("Sakura answers an ordinary arrival cue.")
        self.assertEqual(status, 200, reply)
        review_id = reply["cera"]["provisional_review_id"]
        stop_manual_root(self.root)
        start_manual_root(self.root)
        status, review = _http_json("GET", f"/v1/cera/reviews/{review_id}")
        self.assertEqual(status, 200, review)
        self.assertFalse(review["accept_enabled"])
        self.assertTrue(review["decline_enabled"])
        self.assertTrue(review["recovered_after_restart"])
        status, failed = self._accept(review_id)
        self.assertEqual(status, 400, failed)
        status, declined = _http_json(
            "POST",
            f"/v1/cera/reviews/{review_id}/decision",
            {"action": CreatorReviewAction.DECLINE.value},
            timeout=20,
        )
        self.assertEqual(status, 200, declined)
        self.assertTrue(declined["recovered_after_restart"])
        stop_manual_root(self.root)
        self.assertTrue(verify_isolation(self.root)["passed"])

    def test_cli_review_lookup_rejects_a_different_stopped_root(self) -> None:
        other_root = self.root.with_name(f"{self.root.name}-b")
        self.assertFalse(other_root.exists())
        reset_manual_root(other_root, session_id=DEFAULT_MANUAL_SESSION_ID)
        try:
            start_manual_root(self.root)
            with self.assertRaisesRegex(
                RuntimeError, "requested root is not running"
            ):
                pending_review_status(other_root)
            stop_manual_root(self.root)
        finally:
            if other_root.exists():
                self.assertEqual(
                    other_root.parent.resolve(), self.root.parent.resolve()
                )
                shutil.rmtree(other_root)

    def test_restart_reconciles_exact_accept_after_manual_terminalization_cut(self) -> None:
        adapter, first_harness = build_scripted_manual_adapter(
            self.root,
            process_instance_id="manual-cut-process-one",
        )
        request = SillyTavernChatRequest.from_mapping(
            {
                "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                "stream": False,
                "cera_session_id": DEFAULT_MANUAL_SESSION_ID,
                "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                "cera_scene_change": False,
                "messages": [{"role": "user", "content": "Sakura answers."}],
            }
        )
        reply = adapter.complete(request)
        review_id = TypedId.parse(
            str(reply.provisional_review_id), IdKind.REVIEW_PACKET
        )

        def fail_manual_terminalization(_record):
            raise RuntimeError("injected manual terminalization cut")

        adapter.state_store.terminalize_review = fail_manual_terminalization
        with self.assertRaisesRegex(RuntimeError, "terminalization cut"):
            adapter.review_action(review_id, CreatorReviewAction.ACCEPT)
        cut_state = adapter.state_store.load()
        self.assertIsNotNone(cut_state["decision_intent"])
        self.assertEqual(cut_state["accepted_turn_ids"], [])
        self.assertEqual(
            first_harness.world.accepted_turn_pairs(
                first_harness.world_id,
                first_harness.branch_id,
                ("turn-001",),
            )[0].accepted_turn_id,
            "turn-001",
        )

        recovered, second_harness = build_scripted_manual_adapter(
            self.root,
            process_instance_id="manual-cut-process-two",
        )
        recovered_state = recovered.state_store.load()
        self.assertIsNone(recovered_state["decision_intent"])
        self.assertIsNone(recovered_state["current_review_id"])
        self.assertEqual(recovered_state["accepted_turn_ids"], ["turn-001"])
        recovered_review = recovered.get_review(review_id)
        review_payload = recovered.review_payload(recovered_review)
        self.assertEqual(review_payload["state"], "accepted")
        self.assertFalse(review_payload["accept_enabled"])
        self.assertFalse(review_payload["decline_enabled"])

        second = recovered.complete(
            SillyTavernChatRequest.from_mapping(
                {
                    "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                    "stream": False,
                    "cera_session_id": DEFAULT_MANUAL_SESSION_ID,
                    "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                    "cera_scene_change": False,
                    "messages": [
                        {"role": "user", "content": "Continue the conversation."}
                    ],
                }
            )
        )
        self.assertEqual(second.request_id, "turn-002")
        self.assertEqual(second.generation, 2)
        second_review_id = TypedId.parse(
            str(second.provisional_review_id), IdKind.REVIEW_PACKET
        )
        recovered.review_action(second_review_id, CreatorReviewAction.DECLINE)
        self.assertEqual(second_harness.provider_calls, 0)

    def test_unproven_decision_intent_disables_actions_and_restart_fails_closed(self) -> None:
        adapter, harness = build_scripted_manual_adapter(
            self.root,
            process_instance_id="manual-ambiguous-process-one",
        )
        reply = adapter.complete(
            SillyTavernChatRequest.from_mapping(
                {
                    "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                    "stream": False,
                    "cera_session_id": DEFAULT_MANUAL_SESSION_ID,
                    "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                    "cera_scene_change": False,
                    "messages": [{"role": "user", "content": "Sakura answers."}],
                }
            )
        )
        review_id = TypedId.parse(
            str(reply.provisional_review_id), IdKind.REVIEW_PACKET
        )
        record = adapter.get_review(review_id)
        adapter.state_store.begin_decision(
            record,
            CreatorReviewAction.ACCEPT,
            process_instance_id=adapter.process_instance_id,
        )
        payload = adapter.review_payload(record)
        self.assertTrue(payload["decision_pending"])
        self.assertFalse(payload["accept_enabled"])
        self.assertFalse(payload["decline_enabled"])
        with self.assertRaisesRegex(StateConflictError, "cannot be proven"):
            build_scripted_manual_adapter(
                self.root,
                process_instance_id="manual-ambiguous-process-two",
            )
        self.assertEqual(harness.provider_calls, 0)

    def test_restart_finishes_terminal_review_record_after_state_write_cut(self) -> None:
        adapter, _harness = build_scripted_manual_adapter(
            self.root,
            process_instance_id="manual-state-cut-process-one",
        )
        reply = adapter.complete(
            SillyTavernChatRequest.from_mapping(
                {
                    "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                    "stream": False,
                    "cera_session_id": DEFAULT_MANUAL_SESSION_ID,
                    "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                    "cera_scene_change": False,
                    "messages": [{"role": "user", "content": "Sakura answers."}],
                }
            )
        )
        review_id = TypedId.parse(
            str(reply.provisional_review_id), IdKind.REVIEW_PACKET
        )
        original_write = adapter.state_store._write_without_hash
        write_count = 0

        def fail_second_state_write(state):
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                raise RuntimeError("injected post-review state write cut")
            original_write(state)

        adapter.state_store._write_without_hash = fail_second_state_write
        with self.assertRaisesRegex(RuntimeError, "post-review state write cut"):
            adapter.review_action(review_id, CreatorReviewAction.ACCEPT)
        self.assertEqual(
            adapter.get_review(review_id).state.value,
            "accepted",
        )
        self.assertIsNotNone(adapter.state_store.decision_intent)

        recovered, _second_harness = build_scripted_manual_adapter(
            self.root,
            process_instance_id="manual-state-cut-process-two",
        )
        state = recovered.state_store.load()
        self.assertIsNone(state["decision_intent"])
        self.assertIsNone(state["current_review_id"])
        self.assertEqual(state["accepted_turn_ids"], ["turn-001"])
        self.assertEqual(recovered.get_review(review_id).state.value, "accepted")

    def test_restart_reconciles_exact_decline_after_manual_terminalization_cut(self) -> None:
        adapter, _harness = build_scripted_manual_adapter(
            self.root,
            process_instance_id="manual-decline-cut-process-one",
        )
        reply = adapter.complete(
            SillyTavernChatRequest.from_mapping(
                {
                    "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                    "stream": False,
                    "cera_session_id": DEFAULT_MANUAL_SESSION_ID,
                    "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                    "cera_scene_change": False,
                    "messages": [{"role": "user", "content": "Sakura answers."}],
                }
            )
        )
        review_id = TypedId.parse(
            str(reply.provisional_review_id), IdKind.REVIEW_PACKET
        )

        def fail_manual_terminalization(_record):
            raise RuntimeError("injected manual decline terminalization cut")

        adapter.state_store.terminalize_review = fail_manual_terminalization
        with self.assertRaisesRegex(RuntimeError, "decline terminalization cut"):
            adapter.review_action(review_id, CreatorReviewAction.DECLINE)
        self.assertIsNotNone(adapter.state_store.decision_intent)

        recovered, _second_harness = build_scripted_manual_adapter(
            self.root,
            process_instance_id="manual-decline-cut-process-two",
        )
        state = recovered.state_store.load()
        self.assertIsNone(state["decision_intent"])
        self.assertIsNone(state["current_review_id"])
        self.assertEqual(state["accepted_turn_ids"], [])
        record = recovered.get_review(review_id)
        self.assertEqual(record.state.value, "rejected")
        self.assertTrue(record.rejected.recovered_after_restart)

    def test_dead_process_and_stop_request_recovery_is_identity_bound(self) -> None:
        manifest = json.loads(
            (self.root / "EXECUTION_MANIFEST.json").read_text(encoding="utf-8")
        )
        process_instance_id = "dead-process-fixture"
        process_instance_sha256 = text_sha256(process_instance_id)
        process = _signed_record(
            {
                "schema_version": "cera.continuous_manual_process.v1",
                "pid": 999999,
                "executable": str(Path(sys.executable).resolve()),
                "root": str(self.root.resolve()),
                "host": "127.0.0.1",
                "port": CONTINUOUS_V3_MANUAL_PORT,
                "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                "process_instance_id": process_instance_id,
                "process_instance_sha256": process_instance_sha256,
                "execution_identity_sha256": manifest["execution_identity_sha256"],
                "provider_mode": "scripted_provider_free",
                "external_provider_calls_authorized": 0,
                "started_unix_ns": 1,
            },
            hash_field="process_record_sha256",
        )
        _process_path(self.root).write_text(
            json.dumps(process, sort_keys=True) + "\n", encoding="utf-8"
        )
        stop_request = _signed_record(
            {
                "schema_version": "cera.continuous_manual_stop_request.v1",
                "requested_by_pid": 1,
                "requested_unix_ns": 2,
                "process_instance_sha256": process_instance_sha256,
                "execution_identity_sha256": manifest["execution_identity_sha256"],
            },
            hash_field="stop_request_sha256",
        )
        _stop_request_path(self.root).write_text(
            json.dumps(stop_request, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.assertEqual(manual_status(self.root)["status"], "stale_or_conflicting")
        recovered = recover_stale_process_record(self.root)
        self.assertEqual(recovered["status"], "recovered")
        final_status = manual_status(self.root)
        stored_manifest = json.loads(
            (self.root / "EXECUTION_MANIFEST.json").read_text(encoding="utf-8")
        )
        current_manifest = manual_execution_manifest()
        manifest_drift = {
            key: {"stored": stored_manifest.get(key), "current": current_manifest.get(key)}
            for key in sorted(set(stored_manifest) | set(current_manifest))
            if stored_manifest.get(key) != current_manifest.get(key)
        }
        self.assertEqual(
            final_status["status"],
            "stopped",
            {"status": final_status, "manifest_drift": manifest_drift},
        )
        self.assertFalse(_process_path(self.root).exists())
        self.assertFalse(_stop_request_path(self.root).exists())

    def test_stop_terminalization_failure_is_frozen_without_raw_error_text(self) -> None:
        manifest = json.loads(
            (self.root / "EXECUTION_MANIFEST.json").read_text(encoding="utf-8")
        )
        process_instance_sha256 = text_sha256("stop-failure-process")
        process_record = _signed_record(
            {
                "process_instance_sha256": process_instance_sha256,
                "execution_identity_sha256": manifest[
                    "execution_identity_sha256"
                ],
            },
            hash_field="process_record_sha256",
        )
        stop_request = _signed_record(
            {
                "schema_version": "cera.continuous_manual_stop_request.v1",
                "process_instance_sha256": process_instance_sha256,
                "execution_identity_sha256": manifest[
                    "execution_identity_sha256"
                ],
            },
            hash_field="stop_request_sha256",
        )
        _stop_request_path(self.root).write_text(
            json.dumps(stop_request, sort_keys=True) + "\n", encoding="utf-8"
        )
        failure = _record_process_stop_failure(
            self.root,
            process_record,
            _stop_request_path(self.root),
            RuntimeError("private terminalization diagnostic"),
        )
        self.assertFalse(_stop_request_path(self.root).exists())
        rejected = (
            self.root
            / "process"
            / f"STOP_FAILED_INPUT_{process_instance_sha256}.json"
        )
        self.assertEqual(
            bytes_sha256(rejected.read_bytes()),
            failure["stop_request_bytes_sha256"],
        )
        self.assertEqual(
            failure["error_message_sha256"],
            text_sha256("private terminalization diagnostic"),
        )
        self.assertNotIn("private terminalization diagnostic", json.dumps(failure))
        self.assertEqual(failure["external_provider_calls"], 0)

    def test_execution_manifest_and_process_tampering_fail_closed(self) -> None:
        current_manifest = manual_execution_manifest()
        altered_profile = json.loads(
            json.dumps(current_manifest["route_profile"]["profile"])
        )
        altered_profile["sillytavern_custom_endpoint"][
            "installed_review_relay_changed"
        ] = True
        with self.assertRaisesRegex(RuntimeError, "profile changed"):
            _validate_manual_sillytavern_profile(altered_profile)

        manifest_path = self.root / "EXECUTION_MANIFEST.json"
        original = manifest_path.read_bytes()
        manifest = json.loads(original)
        manifest["model"] = CERA_VIRTUAL_MODEL
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "execution identity drifted"):
            start_manual_root(self.root)
        manifest_path.write_bytes(original)

        process = _signed_record(
            {
                "schema_version": "cera.continuous_manual_process.v1",
                "pid": 999999,
                "process_instance_sha256": "e" * 64,
                "execution_identity_sha256": json.loads(original)[
                    "execution_identity_sha256"
                ],
            },
            hash_field="process_record_sha256",
        )
        process["pid"] = 999998
        _process_path(self.root).write_text(json.dumps(process), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "process record integrity"):
            manual_status(self.root)
        _process_path(self.root).unlink()

        process_instance_id = "semantically-substituted-process"
        semantic_substitution = _signed_record(
            {
                "schema_version": "cera.continuous_manual_process.v1",
                "pid": 999999,
                "root": str(self.root.resolve()),
                "host": "127.0.0.1",
                "port": CONTINUOUS_V3_MANUAL_PORT,
                "model": CERA_VIRTUAL_MODEL,
                "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                "process_instance_id": process_instance_id,
                "process_instance_sha256": text_sha256(process_instance_id),
                "execution_identity_sha256": json.loads(original)[
                    "execution_identity_sha256"
                ],
                "provider_mode": "scripted_provider_free",
                "external_provider_calls_authorized": 0,
            },
            hash_field="process_record_sha256",
        )
        _process_path(self.root).write_text(
            json.dumps(semantic_substitution), encoding="utf-8"
        )
        with self.assertRaisesRegex(RuntimeError, "process identity changed"):
            manual_status(self.root)
        _process_path(self.root).unlink()

        process_instance_id = "dead-process-invalid-stop"
        process_instance_sha256 = text_sha256(process_instance_id)
        valid_dead_process = _signed_record(
            {
                "schema_version": "cera.continuous_manual_process.v1",
                "pid": 999999,
                "executable": str(Path(sys.executable).resolve()),
                "root": str(self.root.resolve()),
                "host": "127.0.0.1",
                "port": CONTINUOUS_V3_MANUAL_PORT,
                "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                "process_instance_id": process_instance_id,
                "process_instance_sha256": process_instance_sha256,
                "execution_identity_sha256": json.loads(original)[
                    "execution_identity_sha256"
                ],
                "provider_mode": "scripted_provider_free",
                "external_provider_calls_authorized": 0,
                "started_unix_ns": 1,
            },
            hash_field="process_record_sha256",
        )
        invalid_stop = _signed_record(
            {
                "schema_version": "cera.continuous_manual_stop_request.v1",
                "requested_by_pid": "1",
                "requested_unix_ns": 2,
                "process_instance_sha256": process_instance_sha256,
                "execution_identity_sha256": json.loads(original)[
                    "execution_identity_sha256"
                ],
            },
            hash_field="stop_request_sha256",
        )
        _process_path(self.root).write_text(
            json.dumps(valid_dead_process, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        process_bytes = _process_path(self.root).read_bytes()
        _stop_request_path(self.root).write_text(
            json.dumps(invalid_stop, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(RuntimeError, "stop request identity changed"):
            recover_stale_process_record(self.root)
        self.assertEqual(_process_path(self.root).read_bytes(), process_bytes)
        self.assertTrue(_stop_request_path(self.root).is_file())
        self.assertFalse(
            (
                self.root
                / "process"
                / f"RECOVERED_{process_instance_sha256}.json"
            ).exists()
        )
        _process_path(self.root).unlink()
        _stop_request_path(self.root).unlink()

    def test_overlong_windows_root_fails_before_state_creation(self) -> None:
        if os.name != "nt":
            self.skipTest("Windows transaction-path guard")
        overlong = (
            PROJECT_ROOT / "runtime" / "manual" / ("x" * 100)
        )
        with self.assertRaisesRegex(RuntimeError, "too long"):
            reset_manual_root(overlong)
        self.assertFalse(overlong.exists())


class ContinuousManualReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manual_parent = (
            PROJECT_ROOT
            / "runtime"
            / "manual"
            / f"ur-{uuid4().hex[:8]}"
        )

    def tearDown(self) -> None:
        if self.manual_parent.exists():
            self.assertEqual(
                self.manual_parent.parent.resolve(),
                (PROJECT_ROOT / "runtime" / "manual").resolve(),
            )
            for child in self.manual_parent.iterdir():
                if child.is_dir():
                    status = manual_status(child, verify_execution=False)
                    self.assertNotEqual(status["status"], "running")
                    self.assertFalse(status["port_open"])
            shutil.rmtree(self.manual_parent)

    def test_manifest_preserves_run001_debit_and_fresh_v2_identities(self) -> None:
        value = readiness_manifest()
        self.assertEqual(value["immutable_live_predecessor"]["codex_family_calls"], 1)
        self.assertFalse(value["immutable_live_predecessor"]["reuse_permitted"])
        self.assertEqual(value["global_budgets"]["codex_family_remaining"], 799)
        self.assertEqual(value["global_budgets"]["deepseek_remaining"], 800)
        self.assertEqual(
            tuple(value["future_live_v2"]["run_ids"]),
            FUTURE_LIVE_V2_RUN_IDS,
        )
        self.assertEqual(
            value["future_live_v2"]["next_unused_run_id"],
            FUTURE_LIVE_V2_RUN_IDS[0],
        )
        self.assertFalse(value["future_live_v2"]["provider_call_authority_active"])

    def test_production_shaped_two_run_route_passes_locally_and_resets(self) -> None:
        with TemporaryDirectory(prefix="cera-manual-readiness-evidence-") as temporary:
            evidence = Path(temporary) / "evidence"
            attempt_id = f"provider-free-unittest-{uuid4().hex}"
            result = run_readiness(
                evidence_root=evidence,
                manual_parent=self.manual_parent,
                attempt_id=attempt_id,
            )
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["consecutive_passes"], 2)
            self.assertTrue(result["controlled_restart_between_passes"])
            self.assertEqual(result["scripted_transport_invocations"], 20)
            self.assertEqual(result["external_provider_calls"], 0)
            self.assertEqual(
                result["next_unused_live_v2_run_id"],
                FUTURE_LIVE_V2_RUN_IDS[0],
            )
            self.assertFalse(result["live_dispatch_authorized"])
            self.assertEqual(
                tuple(run["run_id"] for run in result["runs"]),
                readiness_run_ids(attempt_id),
            )
            self.assertTrue((evidence / "READINESS_MANIFEST.json").is_file())
            self.assertTrue((evidence / "READINESS_RESULT.json").is_file())
            for index, run in enumerate(result["runs"], start=1):
                self.assertEqual(run["status"], "passed")
                self.assertEqual(run["scripted_transport_invocations"], 10)
                self.assertEqual(run["external_provider_calls"], 0)
                self.assertEqual(run["clean_generation"], 0)
                self.assertEqual(len(run["reviews"]), 3)
                root = self.manual_parent / f"r{index}"
                self.assertEqual(manual_status(root)["status"], "stopped")
                self.assertTrue(verify_isolation(root)["passed"])


if __name__ == "__main__":
    unittest.main()
