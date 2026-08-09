"""Provider-free regressions for Pi Scene HTTP/session/review quality fixes."""

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cera.errors import StateConflictError
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.pi_scene.context import initial_hana_seed
from cera.pi_scene.contracts import RecordingStatus, SceneRoute
from cera.pi_scene.http import (
    PI_SCENE_ORDINARY_MODEL,
    PI_SCENE_PROFILE,
    PiSceneHttpAdapter,
    PiSceneServerConfigV1,
    build_pi_scene_server,
)
from cera.pi_scene.runtime import (
    LeanPiSceneCoordinator,
    LeanReviewState,
    LeanSceneRequestControlsV1,
)
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.planner_state import PlannerThreadStateStore, PlannerThreadStateV1
from cera.pi_scene.writer_view import WriterViewMaterializer
import scripts.run_pi_scene_lean_server as launcher
from scripts.run_pi_scene_lean_server import (
    _PiScenePlannerRegistry,
    _initialize_live_runtime_roots,
    _seed_live_runtime_state,
    build_session_context_provider,
    default_planner_session_factory,
    pi_scene_session_scope,
)
from tests.test_pi_scene_lean_v1 import FakePi, FakePlanner, turn


class FailablePlanner(FakePlanner):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next = False

    def plan(self, request):
        if self.fail_next:
            self.fail_next = False
            raise StateConflictError("injected Planner failure")
        return super().plan(request)


class FailablePi(FakePi):
    def __init__(self) -> None:
        super().__init__()
        self.fail_next_writer = False

    def invoke(self, request):
        if request.purpose == "writer" and self.fail_next_writer:
            self.fail_next_writer = False
            raise StateConflictError("injected Writer failure")
        return super().invoke(request)


class FailingDebug:
    def write(self, **_kwargs) -> None:
        raise OSError("private debug path must not escape")


class PlannerThreadBackend:
    def __init__(self) -> None:
        self.started = 0
        self.turns = 0

    def start_stored_thread(self, **_kwargs) -> str:
        self.started += 1
        return "thread-provider-stable"

    def run_planner_turn(self, **_kwargs):
        self.turns += 1
        return {"ok": True}

    def is_resumable(self, _thread_id: str) -> bool:
        return True


def controls(
    session_id: str,
    *,
    regeneration_key: str | None = None,
    effort: str = "medium",
) -> LeanSceneRequestControlsV1:
    return LeanSceneRequestControlsV1(
        schema_version=LeanSceneRequestControlsV1.SCHEMA_VERSION,
        session_id=session_id,
        scene_depth="long",
        regeneration_key=regeneration_key,
        character_autonomy="mind",
        prompt_handling="modification",
        reasoning_effort=effort,
        scene_change=True,
    )


def request_payload(
    session_id: str,
    *,
    regeneration_key: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": PI_SCENE_ORDINARY_MODEL,
        "messages": [{"role": "user", "content": "Continue the scene."}],
        "stream": False,
        "cera_profile_id": PI_SCENE_PROFILE,
        "cera_session_id": session_id,
        "cera_scene_depth": "LONG",
        "cera_character_autonomy": "MIND",
        "cera_prompt_handling": "MODIFICATION",
        "cera_reasoning_effort": "HIGH",
        "cera_scene_change": True,
    }
    if regeneration_key is not None:
        payload["cera_regeneration_key"] = regeneration_key
    return payload


class PiSceneSessionAndReviewTests(unittest.TestCase):
    def make_runtime(self, root: Path, *, planner=None, pi=None):
        planner = FailablePlanner() if planner is None else planner
        pi = FailablePi() if pi is None else pi
        store = LeanSceneStore(root / "world")
        coordinator = LeanPiSceneCoordinator(
            store=store,
            planner=planner,
            writer_views=WriterViewMaterializer(root / "views"),
            pi=pi,
            session_root=root / "sessions",
        )
        return coordinator, planner, pi, store

    def test_session_scope_and_context_are_stable_and_isolated(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = LeanSceneStore(root / "world")
            provider = build_session_context_provider(store, initial_hana_seed())
            first = provider(
                SceneRoute.ORDINARY,
                "Continue.",
                (),
                controls("chat-alpha"),
            )
            repeated = provider(
                SceneRoute.ORDINARY,
                "Continue again.",
                (),
                controls("chat-alpha"),
            )
            second = provider(
                SceneRoute.ORDINARY,
                "Continue.",
                (),
                controls("chat-beta"),
            )

            self.assertEqual(
                (first.world_id, first.branch_id, first.scene_id),
                pi_scene_session_scope("chat-alpha"),
            )
            self.assertEqual(first.world_id, repeated.world_id)
            self.assertEqual(first.branch_id, repeated.branch_id)
            self.assertNotEqual(first.world_id, second.world_id)
            self.assertNotEqual(first.branch_id, second.branch_id)

    def test_dynamic_http_propagates_controls_and_regeneration_is_idempotent(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, planner, pi, store = self.make_runtime(root)
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                request_context_provider=build_session_context_provider(
                    store,
                    initial_hana_seed(),
                ),
            )

            alpha = adapter.complete(request_payload("chat-alpha"))
            beta = adapter.complete(request_payload("chat-beta"))
            self.assertNotEqual(
                planner.calls[0].world_id,
                planner.calls[1].world_id,
            )
            received = planner.calls[0].request_controls
            assert received is not None
            self.assertEqual(received.scene_depth, "long")
            self.assertEqual(received.character_autonomy, "mind")
            self.assertEqual(received.prompt_handling, "modification")
            self.assertEqual(received.reasoning_effort, "high")
            self.assertTrue(received.scene_change)
            self.assertEqual(
                planner.calls[0].current_state["request_controls"]["scene_depth"],
                "long",
            )

            regenerated = adapter.complete(
                request_payload("chat-alpha", regeneration_key="regen_alpha_1")
            )
            self.assertNotEqual(
                regenerated["cera"]["candidate_id"],
                alpha["cera"]["candidate_id"],
            )
            calls_after_regeneration = (len(planner.calls), len(pi.calls))
            replayed = adapter.complete(
                request_payload("chat-alpha", regeneration_key="regen_alpha_1")
            )
            self.assertEqual(replayed["cera"]["candidate_id"], regenerated["cera"]["candidate_id"])
            self.assertEqual((len(planner.calls), len(pi.calls)), calls_after_regeneration)
            changed = request_payload(
                "chat-alpha",
                regeneration_key="regen_alpha_1",
            )
            changed["cera_scene_depth"] = "EPIC"
            with self.assertRaisesRegex(StateConflictError, "reused"):
                adapter.complete(changed)
            self.assertNotEqual(
                beta["cera"]["provisional_review_id"],
                regenerated["cera"]["provisional_review_id"],
            )

    def test_later_chat_message_auto_accepts_only_its_prior_branch_review(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, _, _, store = self.make_runtime(root)
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                request_context_provider=build_session_context_provider(
                    store,
                    initial_hana_seed(),
                ),
            )
            alpha_first = adapter.complete(request_payload("chat-alpha"))
            beta_first = adapter.complete(request_payload("chat-beta"))
            alpha_next_payload = request_payload("chat-alpha")
            alpha_next_payload["messages"] = [
                {"role": "user", "content": "A distinct later message."}
            ]
            alpha_second = adapter.complete(alpha_next_payload)

            alpha_review_id = alpha_first["cera"]["provisional_review_id"]
            beta_review_id = beta_first["cera"]["provisional_review_id"]
            self.assertEqual(
                adapter.get_review(alpha_review_id)["state"],
                LeanReviewState.ACCEPTED,
            )
            self.assertEqual(
                adapter.get_review(beta_review_id)["state"],
                LeanReviewState.REVIEW_READY,
            )
            self.assertEqual(alpha_second["cera"]["provisional"], True)
            alpha_world, alpha_branch, _ = pi_scene_session_scope("chat-alpha")
            beta_world, beta_branch, _ = pi_scene_session_scope("chat-beta")
            self.assertEqual(
                store.load_head(world_id=alpha_world, branch_id=alpha_branch).generation,
                1,
            )
            self.assertEqual(
                store.load_head(world_id=beta_world, branch_id=beta_branch).generation,
                0,
            )

    def test_provisional_review_and_candidate_counter_recover_after_restart(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, _, _, store = self.make_runtime(root)
            original = first.start_ordinary(turn(source="Restart-safe provisional."))

            restarted = LeanPiSceneCoordinator(
                store=store,
                planner=FakePlanner(),
                writer_views=WriterViewMaterializer(root / "restart-views"),
                pi=FakePi(),  # type: ignore[arg-type]
                session_root=root / "sessions",
            )
            recovered = restarted.get_review(original.review_id)
            self.assertEqual(
                recovered.candidate.candidate_sha256,
                original.candidate.candidate_sha256,
            )
            with self.assertRaisesRegex(StateConflictError, "unresolved"):
                restarted.start_ordinary(turn(source="Must remain blocked."))

            restarted.decline(recovered.review_id)
            replacement = restarted.start_ordinary(
                turn(source="Restart-safe provisional.")
            )
            self.assertNotEqual(replacement.candidate.candidate_id, original.candidate.candidate_id)

    def test_accept_crash_window_reconstructs_replay_from_branch_receipt(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, _, _, store = self.make_runtime(root)
            review = first.start_ordinary(turn(source="Crash after phase one."))
            store.accept(review.candidate)

            restarted, _, restarted_pi, restarted_store = self.make_runtime(root)
            adapter = PiSceneHttpAdapter(
                coordinator=restarted,
                request_context_provider=build_session_context_provider(
                    restarted_store,
                    initial_hana_seed(),
                ),
            )
            recovered = adapter.decide(review.review_id, {"action": "accept"})
            self.assertTrue(recovered["story_state_committed"])
            self.assertEqual(
                recovered["review"]["recording_status"],
                RecordingStatus.PROJECTION_PENDING.value,
            )
            self.assertEqual(restarted_pi.calls, [])

            replayed, _, replayed_pi, replayed_store = self.make_runtime(root)
            replayed_adapter = PiSceneHttpAdapter(
                coordinator=replayed,
                request_context_provider=build_session_context_provider(
                    replayed_store,
                    initial_hana_seed(),
                ),
            )
            self.assertEqual(
                replayed_adapter.decide(review.review_id, {"action": "accept"}),
                recovered,
            )
            self.assertEqual(replayed_pi.calls, [])

    def test_accepted_incomplete_review_recovers_and_repairs_through_http(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, _, pi, store = self.make_runtime(root)
            adapter = PiSceneHttpAdapter(
                coordinator=first,
                request_context_provider=build_session_context_provider(
                    store,
                    initial_hana_seed(),
                ),
            )
            review = first.start_ordinary(turn(source="Repair after restart."))
            pi.fail_next_recorder = True
            accepted = adapter.decide(review.review_id, {"action": "accept"})
            self.assertTrue(accepted["story_state_committed"])
            self.assertEqual(
                accepted["review"]["recording_status"],
                RecordingStatus.PENDING_REPAIR.value,
            )

            restarted, _, _, restarted_store = self.make_runtime(root)
            restarted_adapter = PiSceneHttpAdapter(
                coordinator=restarted,
                request_context_provider=build_session_context_provider(
                    restarted_store,
                    initial_hana_seed(),
                ),
            )
            recovered = restarted_adapter.get_review(review.review_id)
            self.assertTrue(recovered["repair_recording_enabled"])
            repaired = restarted_adapter.decide(
                review.review_id,
                {"action": "repair_recording"},
            )
            self.assertTrue(repaired["story_state_committed"])
            self.assertEqual(
                repaired["review"]["recording_status"],
                RecordingStatus.COMPLETE.value,
            )
            same_process_replay = restarted_adapter.decide(
                review.review_id,
                {"action": "accept"},
            )
            self.assertEqual(
                same_process_replay["review"]["recording_status"],
                RecordingStatus.COMPLETE.value,
            )

            final, _, final_pi, final_store = self.make_runtime(root)
            final_adapter = PiSceneHttpAdapter(
                coordinator=final,
                request_context_provider=build_session_context_provider(
                    final_store,
                    initial_hana_seed(),
                ),
            )
            replayed = final_adapter.decide(review.review_id, {"action": "accept"})
            self.assertTrue(replayed["story_state_committed"])
            self.assertEqual(replayed["review"]["recording_status"], "complete")
            self.assertEqual(replayed, same_process_replay)
            self.assertEqual(final_pi.calls, [])

    def test_http_empty_regenerate_means_no_creator_guidance(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, _, pi, store = self.make_runtime(root)
            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                request_context_provider=build_session_context_provider(
                    store,
                    initial_hana_seed(),
                ),
            )
            original = coordinator.start_ordinary(turn(source="Regenerate cleanly."))
            regenerated = adapter.decide(
                original.review_id,
                {"action": "regenerate", "feedback": ""},
            )
            self.assertIsNone(regenerated["successor"]["cera"]["creator_guidance"])
            self.assertNotIn("creator guidance", pi.calls[-1].prompt.casefold())
            self.assertEqual(
                adapter.decide(original.review_id, {"action": "regenerate"}),
                regenerated,
            )

    def test_http_decisions_replay_without_duplicate_work_in_process_or_restart(self) -> None:
        cases = (
            ("accept", {"action": "accept"}, "decline"),
            ("decline", {"action": "decline"}, "accept"),
            (
                "regenerate",
                {"action": "regenerate", "feedback": "Fresh wording."},
                "decline",
            ),
            ("replan", {"action": "replan", "feedback": ""}, "accept"),
        )
        for action, payload, conflicting_action in cases:
            with self.subTest(action=action), TemporaryDirectory() as temporary:
                root = Path(temporary)
                first, planner, pi, store = self.make_runtime(root)
                adapter = PiSceneHttpAdapter(
                    coordinator=first,
                    request_context_provider=build_session_context_provider(
                        store,
                        initial_hana_seed(),
                    ),
                )
                review = first.start_ordinary(turn(source=f"Replay {action}."))
                initial = adapter.decide(review.review_id, payload)
                calls_after_decision = (len(planner.calls), len(pi.calls))
                replayed = adapter.decide(review.review_id, payload)
                self.assertEqual(replayed, initial)
                self.assertEqual((len(planner.calls), len(pi.calls)), calls_after_decision)
                with self.assertRaisesRegex(StateConflictError, "already finalized"):
                    adapter.decide(
                        review.review_id,
                        {"action": conflicting_action},
                    )

                restarted, restarted_planner, restarted_pi, restarted_store = (
                    self.make_runtime(root)
                )
                restarted_adapter = PiSceneHttpAdapter(
                    coordinator=restarted,
                    request_context_provider=build_session_context_provider(
                        restarted_store,
                        initial_hana_seed(),
                    ),
                )
                after_restart = restarted_adapter.decide(review.review_id, payload)
                self.assertEqual(after_restart, initial)
                self.assertEqual(restarted_planner.calls, [])
                self.assertEqual(restarted_pi.calls, [])

                if action == "regenerate":
                    with self.assertRaisesRegex(StateConflictError, "request changed"):
                        restarted_adapter.decide(
                            review.review_id,
                            {"action": "regenerate", "feedback": "Different."},
                        )

    def test_regenerate_and_replan_failures_leave_old_review_actionable(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            coordinator, planner, pi, _ = self.make_runtime(root)
            original = coordinator.start_ordinary(turn())

            pi.fail_next_writer = True
            with self.assertRaisesRegex(StateConflictError, "Writer failure"):
                coordinator.regenerate(original.review_id)
            self.assertEqual(
                coordinator.get_review(original.review_id).state,
                LeanReviewState.REVIEW_READY,
            )

            planner.fail_next = True
            with self.assertRaisesRegex(StateConflictError, "Planner failure"):
                coordinator.replan(original.review_id, feedback="")
            self.assertEqual(
                coordinator.get_review(original.review_id).state,
                LeanReviewState.REVIEW_READY,
            )
            accepted = coordinator.accept(original.review_id)
            self.assertTrue(accepted.review.accepted_receipt is not None)

    def test_empty_replan_guidance_is_typed_and_never_changes_exact_source(self) -> None:
        with TemporaryDirectory() as temporary:
            coordinator, planner, pi, _ = self.make_runtime(Path(temporary))
            original = coordinator.start_ordinary(turn(source="Exact story source."))
            replanned = coordinator.replan(original.review_id, feedback="")
            successor = replanned.successor
            assert successor is not None

            self.assertEqual(planner.calls[-1].exact_user_source, "Exact story source.")
            self.assertIsNotNone(planner.calls[-1].creator_guidance)
            self.assertEqual(planner.calls[-1].creator_guidance.text, "")
            self.assertEqual(successor.turn_input.exact_user_source, "Exact story source.")
            self.assertIsNotNone(successor.creator_guidance)
            self.assertEqual(successor.creator_guidance.text, "")
            self.assertEqual(
                planner.calls[-1].current_state["creator_control_guidance"]["text"],
                "",
            )
            self.assertNotIn("guidance", pi.calls[-1].prompt.casefold())

    def test_planner_registry_uses_exact_turn_and_rotates_on_effort(self) -> None:
        built: list[tuple[str, str, str, str]] = []

        class Planner:
            def plan(self, _request):
                raise AssertionError("not used")

        def builder(session_id, effort, request_turn):
            built.append((session_id, effort, request_turn.world_id, request_turn.branch_id))
            return Planner()

        registry = _PiScenePlannerRegistry(builder)  # type: ignore[arg-type]
        medium_turn = replace(
            turn(),
            request_controls=controls("chat-registry", effort="medium"),
        )
        first = registry.resolve(medium_turn)
        self.assertIs(registry.resolve(medium_turn), first)
        high_turn = replace(
            medium_turn,
            request_controls=controls("chat-registry", effort="high"),
        )
        high = registry.resolve(high_turn)
        self.assertIsNot(high, first)
        self.assertEqual(
            built,
            [
                ("chat-registry", "medium", "world-test", "branch-main"),
                ("chat-registry", "high", "world-test", "branch-main"),
            ],
        )
        with self.assertRaisesRegex(StateConflictError, "different world or branch"):
            registry.resolve(replace(high_turn, branch_id="branch-other"))


class PiSceneLauncherDurabilityTests(unittest.TestCase):
    def test_default_planner_factory_restores_thread_and_rejects_tampering(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = PlannerThreadBackend()
            session = default_planner_session_factory(root)(
                "chat-thread",
                "medium",
                backend,  # type: ignore[arg-type]
            )
            with (
                patch(
                    "cera.sequence_first.sessions.planner_turn_prompt",
                    return_value="prompt",
                ),
                patch(
                    "cera.sequence_first.sessions.ProviderReferenceScopeV1.from_turn",
                    return_value="scope",
                ),
            ):
                session.plan(object())  # type: ignore[arg-type]
            self.assertEqual(session.thread_id, "thread-provider-stable")

            restored = default_planner_session_factory(root)(
                "chat-thread",
                "medium",
                backend,  # type: ignore[arg-type]
            )
            self.assertEqual(restored.thread_id, "thread-provider-stable")

            state_path = next(root.rglob("PLANNER_THREAD_STATE.json"))
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            payload["thread_id"] = "thread-tampered"
            state_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(StateConflictError, "hash verification"):
                default_planner_session_factory(root)(
                    "chat-thread",
                    "medium",
                    backend,  # type: ignore[arg-type]
                )

    def test_resume_copies_ledgers_and_thread_state_but_not_writer_views(self) -> None:
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "source"
            source.mkdir()
            for name in ("accepted_world", "pi_sessions", "planner_threads"):
                (source / name).mkdir()
            (source / "writer_views").mkdir()
            (source / "writer_views" / "transient.txt").write_text(
                "not authority",
                encoding="utf-8",
            )
            compatibility = "a" * 64
            PlannerThreadStateStore(source / "planner_threads").persist(
                PlannerThreadStateV1(
                    "chat-resume",
                    "medium",
                    compatibility,
                    "thread-resume",
                )
            )

            sol = ContinuousProviderCallLedger(
                (source / "SOL_PROVIDER_CALLS.jsonl").resolve(),
                maximum_calls=1,
            )

            def dispatch(mark_invoked):
                mark_invoked()
                return object()

            sol.execute(
                owner="other",
                operation="seed",
                route="test-route",
                model="test-model",
                effort=None,
                dispatch_with_invocation_marker=dispatch,
                finalize=lambda value: value,
            )
            deepseek = PiProviderOperationLedger(
                (source / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl").resolve(),
                maximum_operations=1,
                maximum_operations_per_invocation=1,
            )
            invocation_id = deepseek.begin(
                candidate_id="candidate-seed",
                purpose="writer",
                route="ordinary",
                request_sha256="b" * 64,
            )
            deepseek.observe_line(invocation_id, json.dumps({"type": "turn_start"}))

            target = base / "target"
            _initialize_live_runtime_roots(target)
            _seed_live_runtime_state(target, source)
            resumed_sol = ContinuousProviderCallLedger(
                (target / "SOL_PROVIDER_CALLS.jsonl").resolve(),
                maximum_calls=1,
            )
            resumed_deepseek = PiProviderOperationLedger(
                (target / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl").resolve(),
                maximum_operations=1,
                maximum_operations_per_invocation=1,
            )
            self.assertEqual(resumed_sol.dispatched_call_count, 1)
            self.assertEqual(resumed_deepseek.operation_count, 1)
            with self.assertRaisesRegex(StateConflictError, "ceiling"):
                resumed_sol.execute(
                    owner="other",
                    operation="blocked",
                    route="test-route",
                    model="test-model",
                    effort=None,
                    dispatch_with_invocation_marker=lambda _mark: self.fail(
                        "resumed Sol dispatch escaped its ceiling"
                    ),
                    finalize=lambda value: value,
                )
            with self.assertRaisesRegex(StateConflictError, "ceiling"):
                resumed_deepseek.begin(
                    candidate_id="candidate-blocked",
                    purpose="writer",
                    route="ordinary",
                    request_sha256="c" * 64,
                )
            state = PlannerThreadStateStore(target / "planner_threads").load(
                session_id="chat-resume",
                reasoning_effort="medium",
                compatibility_sha256=compatibility,
            )
            assert state is not None
            self.assertEqual(state.thread_id, "thread-resume")
            self.assertFalse((target / "writer_views").exists())

    def test_cli_and_serve_forward_typed_provider_ceilings(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "runtime"
            resume = Path(temporary) / "resume"
            argv = [
                "run_pi_scene_lean_server.py",
                "serve",
                "--runtime-root",
                str(root),
                "--sol-ceiling",
                "7",
                "--deepseek-ceiling",
                "19",
                "--deepseek-per-invocation-ceiling",
                "3",
                "--resume-from-runtime-root",
                str(resume),
            ]
            with patch("sys.argv", argv), patch.object(launcher, "serve") as serve_call:
                self.assertEqual(launcher.main(), 0)
            serve_call.assert_called_once_with(
                root,
                port=5127,
                session_id="cera-pi-scene-test",
                sol_ceiling=7,
                deepseek_ceiling=19,
                deepseek_per_invocation_ceiling=3,
                resume_from_runtime_root=resume,
            )

            runtime = SimpleNamespace(
                coordinator=object(),
                store=object(),
                readable_debug=None,
                close=Mock(),
            )
            server = SimpleNamespace(
                serve_forever=Mock(),
                server_close=Mock(),
            )
            with (
                patch.dict(
                    os.environ,
                    {"CERA_PI_SCENE_TOKEN": "x" * 32},
                    clear=False,
                ),
                patch.object(
                    launcher,
                    "build_live_runtime",
                    return_value=runtime,
                ) as build,
                patch.object(
                    launcher,
                    "build_pi_scene_server",
                    return_value=server,
                ),
            ):
                launcher.serve(
                    root,
                    port=7001,
                    session_id="ignored-compatibility",
                    sol_ceiling=7,
                    deepseek_ceiling=19,
                    deepseek_per_invocation_ceiling=3,
                    resume_from_runtime_root=resume,
                )
            build.assert_called_once_with(
                root,
                sol_ceiling=7,
                deepseek_ceiling=19,
                deepseek_per_invocation_ceiling=3,
                seed_runtime_root=resume,
            )
            server.serve_forever.assert_called_once_with()
            server.server_close.assert_called_once_with()
            runtime.close.assert_called_once_with()


class PiSceneTypedHttpTests(unittest.TestCase):
    @staticmethod
    def request(url: str, *, token: str, payload: dict[str, object]):
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_typed_status_errors_and_post_accept_debug_failure(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            planner = FakePlanner()
            store = LeanSceneStore(root / "world")
            coordinator = LeanPiSceneCoordinator(
                store=store,
                planner=planner,
                writer_views=WriterViewMaterializer(root / "views"),
                pi=FakePi(),  # type: ignore[arg-type]
                session_root=root / "sessions",
            )

            base_provider = build_session_context_provider(store, initial_hana_seed())

            def provider(route, source, messages, request_controls):
                if request_controls.session_id == "chat-crash":
                    raise RuntimeError("SECRET_LOCAL_PATH")
                return base_provider(route, source, messages, request_controls)

            adapter = PiSceneHttpAdapter(
                coordinator=coordinator,
                request_context_provider=provider,
                readable_debug=FailingDebug(),  # type: ignore[arg-type]
            )
            token = "typed-http-token-0123456789abcdef"
            server = build_pi_scene_server(
                adapter,
                PiSceneServerConfigV1(
                    host="127.0.0.1",
                    port=0,
                    authorization_token=token,
                    approved_origins=("http://127.0.0.1:8000",),
                ),
            )
            worker = Thread(target=server.serve_forever, daemon=True)
            worker.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                invalid = request_payload("chat-invalid")
                invalid["cera_scene_depth"] = "impossible"
                status, error = self.request(
                    base + "/v1/chat/completions",
                    token=token,
                    payload=invalid,
                )
                self.assertEqual(status, 422)
                self.assertEqual(error["error"]["schema_version"], "cera.error.v1")
                self.assertEqual(error["error"]["error_code"], "CERA_INTAKE_INVALID")
                self.assertFalse(error["story_state_committed"])

                status, internal = self.request(
                    base + "/v1/chat/completions",
                    token=token,
                    payload=request_payload("chat-crash"),
                )
                self.assertEqual(status, 500)
                self.assertEqual(internal["error"]["error_code"], "CERA_INTERNAL_ERROR")
                self.assertNotIn("SECRET_LOCAL_PATH", json.dumps(internal))

                status, completion = self.request(
                    base + "/v1/chat/completions",
                    token=token,
                    payload=request_payload("chat-commit"),
                )
                self.assertEqual(status, 200)
                review_id = completion["cera"]["provisional_review_id"]
                self.assertIn(
                    "readable_debug_write_failed",
                    completion["cera"]["operational_warnings"],
                )
                status, replayed_completion = self.request(
                    base + "/v1/chat/completions",
                    token=token,
                    payload=request_payload("chat-commit"),
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    replayed_completion["cera"]["provisional_review_id"],
                    review_id,
                )
                self.assertFalse(
                    replayed_completion["cera"]["story_state_committed"]
                )
                status, decision = self.request(
                    base + f"/v1/cera/reviews/{review_id}/decision",
                    token=token,
                    payload={"action": "accept"},
                )
                self.assertEqual(status, 200)
                self.assertTrue(decision["story_state_committed"])
                self.assertEqual(decision["status"], "story_committed")
                self.assertIn(
                    "readable_debug_write_failed",
                    decision["operational_warnings"],
                )
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
