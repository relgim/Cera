from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from cera.pi_scene.qualification import (
    DEEPSEEK_HTTP_OPERATION_CEILING,
    DEEPSEEK_PER_INVOCATION_CEILING,
    QUALIFICATION_MANIFEST_SCHEMA,
    SOL_FAMILY_CEILING,
    TERRA_CEILING,
    ClientResponseV1,
    FullModelQualificationRunner,
    QualificationFixtureV1,
    QualificationPhase,
    QualificationRoute,
    build_qualification_manifest,
    load_qualification_fixtures,
    verify_qualification_artifacts,
)
from cera.pi_scene.qualification_isolation import (
    stage_qualification_sillytavern,
    verify_qualification_sillytavern,
)
from cera.serialization import canonical_bytes, canonical_sha256
from scripts import run_pi_scene_full_model_qualification as entrypoint

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "evaluation" / "fixtures" / "pi_scene_full_model_qualification_v1.json"


def _manifest() -> dict[str, Any]:
    body = {
        "schema_version": QUALIFICATION_MANIFEST_SCHEMA,
        "qualification_id": "qualification-fake-20260809",
        "source_commit": "a" * 40,
        "source_tree": "b" * 40,
        "route_model": "cera-alpha",
        "profile_id": "cera.pi_scene.lean.v1",
        "fixture_set_sha256": "c" * 64,
        "fixture_counts": {
            "backend": {"ordinary": 10, "adult": 10},
            "sillytavern": {"ordinary": 5, "adult": 5},
        },
        "provider_ceilings": {
            "sol": SOL_FAMILY_CEILING,
            "deepseek_http_operations": DEEPSEEK_HTTP_OPERATION_CEILING,
            "deepseek_per_invocation": DEEPSEEK_PER_INVOCATION_CEILING,
            "terra": TERRA_CEILING,
        },
        "execution_policy": {
            "one_sequential_session_per_phase": True,
        },
        "artifact_categories": {"test": []},
    }
    return {**body, "manifest_sha256": canonical_sha256(body)}


class _FakeQualificationClient:
    def __init__(
        self,
        runtime_root: Path,
        *,
        reject_first: bool = False,
        reject_fixture_id: str | None = None,
        automatic_repair_fixture_id: str | None = None,
    ) -> None:
        self.runtime_root = runtime_root
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.reject_first = reject_first
        self.reject_fixture_id = reject_fixture_id
        self.automatic_repair_fixture_id = automatic_repair_fixture_id
        self.rejected = False
        self.calls = 0
        self.regenerates = 0
        self.session_id: str | None = None

    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: dict[str, Any] | Any,
    ) -> ClientResponseV1:
        value = dict(payload)
        if self.session_id is None:
            self.session_id = session_id
        self.assert_equal(self.session_id, session_id)
        self.assert_equal(len(value["messages"]), self.calls * 2 + 1)
        self.assert_equal(value["model"], "cera-alpha")
        self.calls += 1
        planner = (
            1
            if fixture.expected_route is QualificationRoute.ORDINARY
            or fixture.initial_route is QualificationRoute.ORDINARY
            else 0
        )
        reject_now = (
            self.reject_first and not self.rejected
        ) or self.reject_fixture_id == fixture.fixture_id
        if self.automatic_repair_fixture_id == fixture.fixture_id:
            if fixture.expected_route is not QualificationRoute.ORDINARY:
                raise AssertionError("automatic repair fake must use the ordinary route")
            self._append_sol("planner")
            for _ in range(2):
                self._append_deepseek("writer")
                self._append_sol("validator")
            self._append_deepseek("recorder")
            body = self._accepted_body(
                fixture,
                planner=1,
                writer=2,
                validator=2,
            )
        elif reject_now and not self.rejected:
            self.rejected = True
            self._append_attempt(fixture, planner=planner, accepted=False)
            body = self._rejected_body(fixture, planner=planner)
        else:
            self._append_attempt(fixture, planner=planner, accepted=True)
            body = self._accepted_body(fixture, planner=planner)
        return ClientResponseV1(
            transport="fake",
            path="/v1/chat/completions",
            status_code=200,
            duration_ms=10,
            body=body,
        )

    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1:
        self.assert_equal(review_id, "review-0123456789abcdef0123456789ab")
        self.regenerates += 1
        self._append_attempt(fixture, planner=0, accepted=True)
        return ClientResponseV1(
            transport="fake-review",
            path=f"/v1/cera/reviews/{review_id}/decision",
            status_code=200,
            duration_ms=8,
            body={"successor": self._accepted_body(fixture, planner=0)},
        )

    def _append_attempt(
        self,
        fixture: QualificationFixtureV1,
        *,
        planner: int,
        accepted: bool,
    ) -> None:
        if planner:
            self._append_sol("planner")
        if fixture.expected_route.value == "ordinary":
            self._append_sol("validator")
            self._append_deepseek("writer")
            if accepted:
                self._append_deepseek("recorder")
        else:
            self._append_deepseek("adult-scene")
            self._append_deepseek("adult-filter")

    def _append_sol(self, owner: str) -> None:
        path = self.runtime_root / "SOL_PROVIDER_CALLS.jsonl"
        existing = _jsonl(path)
        call_id = f"call-{len(existing) + 1}-{owner}"
        timestamp = datetime.now(UTC).isoformat(timespec="microseconds")
        for state in ("transport_invoked", "provider_completed"):
            event = {
                "event_index": len(existing) + 1,
                "call_id": call_id,
                "owner": owner,
                "operation": owner,
                "state": state,
                "route": "fake",
                "model": "gpt-5.6-luna" if owner == "validator" else "gpt-5.6-sol",
                "stored_thread_sha256": "d" * 64,
                "provider_receipt_sha256": "e" * 64,
                "recorded_at_utc": timestamp,
            }
            _append_jsonl(path, event)
            existing.append(event)

    def _append_deepseek(self, purpose: str) -> None:
        path = self.runtime_root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl"
        existing = _jsonl(path)
        global_index = (
            sum(value.get("event") == "provider_operation_started" for value in existing) + 1
        )
        invocation_id = f"piop-{global_index:04d}-{purpose}"
        timestamp = datetime.now(UTC).isoformat(timespec="microseconds")
        _append_jsonl(
            path,
            {
                "event": "provider_operation_started",
                "invocation_id": invocation_id,
                "operation_index": 1,
                "global_operation_index": global_index,
                "recorded_at_utc": timestamp,
            },
        )
        _append_jsonl(
            path,
            {
                "event": "provider_operation_completed",
                "invocation_id": invocation_id,
                "operation_index": 1,
                "input_tokens": 3000,
                "cached_input_tokens": 2400,
                "output_tokens": 300,
                "reasoning_tokens": 0,
                "finish_status": "stop",
                "recorded_at_utc": timestamp,
            },
        )

    def _accepted_body(
        self,
        fixture: QualificationFixtureV1,
        *,
        planner: int,
        writer: int = 1,
        validator: int = 1,
    ) -> dict[str, Any]:
        content = (
            f"Accepted {fixture.fixture_id} story continuation with enough text for validation."
        )
        common: dict[str, Any] = {
            "profile_id": "cera.pi_scene.lean.v1",
            "route_mode": fixture.expected_route.value,
            "status": "accepted",
            "provisional": False,
            "story_state_committed": True,
            "canon_status": "accepted",
            "accepted_turn_id": f"turn-{self.calls:04d}",
            "accepted_receipt_sha256": "f" * 64,
            "operational_warnings": [],
        }
        if fixture.expected_route.value == "ordinary":
            if validator == 1:
                provider_attempts = [
                    {
                        "attempt_number": 1,
                        "candidate_id": f"candidate:{fixture.fixture_id}:one",
                        "disposition": "semantic_pass",
                        "provider_operations": {
                            "planner": planner,
                            "writer": writer,
                            "validator": validator,
                        },
                    }
                ]
            else:
                provider_attempts = [
                    {
                        "attempt_number": 1,
                        "candidate_id": f"candidate:{fixture.fixture_id}:one",
                        "disposition": "semantic_rejected",
                        "provider_operations": {
                            "planner": planner,
                            "writer": 1,
                            "validator": 1,
                        },
                    },
                    {
                        "attempt_number": 2,
                        "candidate_id": f"candidate:{fixture.fixture_id}:two",
                        "disposition": "semantic_pass",
                        "provider_operations": {
                            "planner": 0,
                            "writer": writer - 1,
                            "validator": validator - 1,
                        },
                    },
                ]
            common.update(
                {
                    "semantic_validation": {"verdict": "pass"},
                    "recording_status": "complete",
                    "route_transition": None,
                    "creator_trace": {
                        "logic_owner": "codex_cognition",
                        "route_transition": None,
                    },
                    "provider_attempts": provider_attempts,
                    "provider_operations": {
                        "planner": planner,
                        "writer": writer,
                        "validator": validator,
                        "recorder": 1,
                    },
                }
            )
        else:
            expected_return = fixture.expected_next_route is QualificationRoute.ORDINARY
            transition = {
                "to_route": fixture.expected_next_route.value,
                "reason": "fake route transition",
            }
            common.update(
                {
                    "recording_status": "complete_preaccept_filter",
                    "recorder_required": False,
                    "adult_filter": {"verdict": "pass"},
                    "candidate_id": "candidate:adult:" + "a" * 32,
                    "operation_sha256": "3" * 64,
                    "protected_full_record_sha256": "1" * 64,
                    "codex_projection_sha256": "2" * 64,
                    "route_transition": transition,
                    "current_logic_route": fixture.expected_next_route.value,
                    "return_to_codex": expected_return,
                    "creator_trace": {
                        "logic_owner": "deepseek_adult_scene",
                        "route_transition": {
                            **transition,
                            "return_to_codex": expected_return,
                        },
                        "recording": {
                            "status": "complete_preaccept_filter",
                            "recorder_required": False,
                            "projection_status": "complete",
                            "protected_record_status": "complete",
                        },
                    },
                    "repair_attempts": [],
                    "provider_operations": {
                        "planner": planner,
                        "adult_scene": 1,
                        "adult_filter": 1,
                        "recorder": 0,
                    },
                }
            )
        return {
            "choices": [
                {
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "cera": common,
        }

    def _rejected_body(
        self,
        fixture: QualificationFixtureV1,
        *,
        planner: int,
    ) -> dict[str, Any]:
        operations = (
            {
                "planner": planner,
                "writer": 1,
                "validator": 1,
                "recorder": 0,
            }
            if fixture.expected_route.value == "ordinary"
            else {
                "planner": planner,
                "adult_scene": 1,
                "adult_filter": 1,
                "recorder": 0,
            }
        )
        conflict = {
            "conflict_class": (
                "severe_incompleteness"
                if fixture.expected_route is QualificationRoute.ORDINARY
                else "logic_not_realized"
            ),
            "summary": "candidate drift",
        }
        if fixture.expected_route is QualificationRoute.ORDINARY:
            route_validation = {
                "semantic_validation": {"verdict": "reject", "conflict": conflict},
                "provisional_review_id": "review-0123456789abcdef0123456789ab",
                "route_transition": None,
                "creator_trace": {
                    "logic_owner": "codex_cognition",
                    "route_transition": None,
                },
                "provider_attempts": [
                    {
                        "attempt_number": 1,
                        "candidate_id": f"candidate:{fixture.fixture_id}:rejected",
                        "disposition": "semantic_rejected",
                        "provider_operations": {
                            "planner": planner,
                            "writer": 1,
                            "validator": 1,
                        },
                    }
                ],
            }
        else:
            transition = {
                "to_route": fixture.expected_next_route.value,
                "reason": "fake route transition",
            }
            route_validation = {
                "adult_filter": {"verdict": "reject", "conflict": conflict},
                "review_id": "review-0123456789abcdef0123456789ab",
                "provisional_review_id": "review-0123456789abcdef0123456789ab",
                "route_transition": transition,
                "current_logic_route": fixture.initial_route.value,
                "creator_trace": {
                    "logic_owner": "deepseek_adult_scene",
                    "route_transition": {
                        **transition,
                        "return_to_codex": False,
                    },
                },
            }
        return {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": (
                            "Rejected first-pass continuation retained only for "
                            "inspection and testing."
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "cera": {
                "profile_id": "cera.pi_scene.lean.v1",
                "route_mode": fixture.expected_route.value,
                "status": "validation_rejected",
                "provisional": True,
                "story_state_committed": False,
                "regenerate_enabled": True,
                "provider_operations": operations,
                **(
                    {"repair_attempts": []}
                    if fixture.expected_route is QualificationRoute.ADULT
                    else {}
                ),
                **route_validation,
            },
        }

    def assert_equal(self, left: object, right: object) -> None:
        if left != right:
            raise AssertionError(f"{left!r} != {right!r}")


class FullModelQualificationTests(unittest.TestCase):
    def test_fixture_set_is_two_ordered_sequential_campaigns(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        self.assertEqual(len(fixtures), 30)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            )
            client = _FakeQualificationClient(runtime)
            backend = runner.run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(backend["passed_fixtures"], 20)
            self.assertEqual(backend["first_pass_accepted"], 20)
            self.assertEqual(backend["retained_conversation_messages"], 40)
            self.assertEqual(client.calls, 20)
            routes = [value["expected_route"] for value in backend["results"]]
            self.assertEqual(
                routes, ["ordinary"] * 5 + ["adult"] * 5 + ["ordinary"] * 5 + ["adult"] * 5
            )
            boundaries = {
                value["fixture_id"]: (
                    value["initial_route"],
                    value["observed_route"],
                    value["observed_next_route"],
                )
                for value in backend["results"]
                if value["fixture_id"]
                in {
                    "backend-ordinary-05",
                    "backend-adult-05",
                    "backend-ordinary-10",
                    "backend-adult-10",
                }
            }
            self.assertEqual(
                boundaries,
                {
                    "backend-ordinary-05": ("ordinary", "ordinary", "ordinary"),
                    "backend-adult-05": ("adult", "adult", "ordinary"),
                    "backend-ordinary-10": ("ordinary", "ordinary", "ordinary"),
                    "backend-adult-10": ("adult", "adult", "ordinary"),
                },
            )
            evidence = (root / "evidence" / "QUALIFICATION_EVENTS.jsonl").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("Accepted backend", evidence)
            self.assertNotIn(fixtures[0].user_source, evidence)

            st_runtime = root / "st-runtime"
            st_runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=st_runtime,
                evidence_root=root / "st-evidence",
            )
            st_result = st_runner.run_phase(
                QualificationPhase.SILLYTAVERN,
                fixtures,
                _FakeQualificationClient(st_runtime),
            )
            st_boundaries = {
                value["fixture_id"]: (
                    value["initial_route"],
                    value["observed_route"],
                    value["observed_next_route"],
                )
                for value in st_result["results"]
                if value["fixture_id"]
                in {
                    "sillytavern-ordinary-03",
                    "sillytavern-adult-03",
                    "sillytavern-ordinary-05",
                    "sillytavern-adult-05",
                }
            }
            self.assertEqual(
                st_boundaries,
                {
                    "sillytavern-ordinary-03": ("ordinary", "ordinary", "ordinary"),
                    "sillytavern-adult-03": ("adult", "adult", "ordinary"),
                    "sillytavern-ordinary-05": ("ordinary", "ordinary", "ordinary"),
                    "sillytavern-adult-05": ("adult", "adult", "ordinary"),
                },
            )

    def test_one_noncritical_rejection_allows_one_explicit_regenerate(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            )
            client = _FakeQualificationClient(runtime, reject_first=True)
            result = runner.run_phase(QualificationPhase.BACKEND, fixtures, client)
            self.assertEqual(result["passed_fixtures"], 20)
            self.assertEqual(result["first_pass_accepted"], 19)
            self.assertEqual(result["explicit_regenerate_actions"], 1)
            self.assertEqual(client.regenerates, 1)

    def test_adult_continuation_uses_no_planner_and_its_review_id_regenerates(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            )
            client = _FakeQualificationClient(
                runtime,
                reject_fixture_id="backend-adult-02",
            )
            result = runner.run_phase(QualificationPhase.BACKEND, fixtures, client)
            adult_two = next(
                value
                for value in result["results"]
                if value["fixture_id"] == "backend-adult-02"
            )
            self.assertEqual(adult_two["provider_operations"]["planner"], 0)
            self.assertFalse(adult_two["first_pass_accepted"])
            self.assertEqual(adult_two["explicit_regenerate_actions"], 1)
            self.assertEqual(client.regenerates, 1)

    def test_automatic_repair_uses_explicit_validator_ledger_parity(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "runtime"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime,
                evidence_root=root / "evidence",
            )
            result = runner.run_phase(
                QualificationPhase.BACKEND,
                fixtures,
                _FakeQualificationClient(
                    runtime,
                    automatic_repair_fixture_id="backend-ordinary-02",
                ),
            )
            repaired = next(
                value
                for value in result["results"]
                if value["fixture_id"] == "backend-ordinary-02"
            )
            self.assertEqual(repaired["provider_operations"]["planner"], 1)
            self.assertEqual(repaired["provider_operations"]["writer"], 2)
            self.assertEqual(
                repaired["provider_operations"]["validator"],
                2,
            )
            self.assertEqual(repaired["provider_operations"]["recorder"], 1)
            self.assertEqual(repaired["sol_http_operations"], 3)
            self.assertEqual(repaired["deepseek_http_operations"], 3)
            self.assertFalse(repaired["first_pass_accepted"])
            self.assertEqual(repaired["automatic_repair_actions"], 1)
            self.assertEqual(result["first_pass_accepted"], 19)
            self.assertEqual(result["automatic_repair_actions"], 1)
            self.assertEqual(result["explicit_regenerate_actions"], 0)

    def test_sillytavern_campaign_restart_retains_session_and_ledger_prefix(self) -> None:
        fixtures = load_qualification_fixtures(FIXTURES)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime_a = root / "runtime-a"
            runtime_b = root / "runtime-b"
            runner = FullModelQualificationRunner(
                manifest=_manifest(),
                runtime_root=runtime_a,
                evidence_root=root / "evidence",
            )
            client = _FakeQualificationClient(runtime_a)
            campaign = runner.start_phase(QualificationPhase.SILLYTAVERN, fixtures)
            campaign.run_segment(
                client=client,
                runtime_root=runtime_a,
                turn_count=5,
            )
            runtime_b.mkdir()
            for name in (
                "SOL_PROVIDER_CALLS.jsonl",
                "DEEPSEEK_PROVIDER_OPERATIONS.jsonl",
            ):
                shutil.copy2(runtime_a / name, runtime_b / name)
            client.runtime_root = runtime_b
            campaign.run_segment(
                client=client,
                runtime_root=runtime_b,
                turn_count=5,
                restarted=True,
            )
            result = campaign.finish()
            self.assertEqual(result["passed_fixtures"], 10)
            self.assertEqual(result["restart_count"], 1)
            self.assertEqual(result["retained_conversation_messages"], 20)
            self.assertEqual(client.calls, 10)

    def test_manifest_verification_fails_closed_after_artifact_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            artifact = root / "artifact.txt"
            artifact.write_text("one", encoding="utf-8")
            fixture = root / "fixtures.json"
            fixture.write_bytes(FIXTURES.read_bytes())
            manifest = build_qualification_manifest(
                repository_root=root,
                qualification_id="qualification-test-20260809",
                source_commit="a" * 40,
                source_tree="b" * 40,
                fixture_path=fixture,
                repository_artifacts={"test": (Path("artifact.txt"), Path("fixtures.json"))},
                external_artifacts={"external": (artifact,)},
            )
            verify_qualification_artifacts(manifest, repository_root=root)
            artifact.write_text("two", encoding="utf-8")
            with self.assertRaisesRegex(Exception, "artifact changed"):
                verify_qualification_artifacts(manifest, repository_root=root)

    def test_freeze_binds_exact_disposable_tree_before_live(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _fake_sillytavern_source(root)
            output = root / "qualification"
            with (
                patch.object(entrypoint, "_assert_clean_exact_repository"),
                patch.object(
                    entrypoint,
                    "_git",
                    side_effect=("a" * 40, "b" * 40),
                ),
                patch.object(
                    entrypoint,
                    "_repository_artifacts",
                    return_value={
                        "fixture": (FIXTURES.relative_to(ROOT),),
                    },
                ),
                patch.object(
                    entrypoint,
                    "_external_artifacts",
                    side_effect=lambda isolated: {
                        "sillytavern_executable_tree_binding": (
                            isolated
                            / "CERA_QUALIFICATION_ISOLATED_COPY_MANIFEST.json",
                        ),
                    },
                ),
            ):
                manifest = entrypoint.freeze(
                    output_root=output,
                    fixture_path=FIXTURES,
                    qualification_id="qualification-freeze-20260809",
                    sillytavern_source=source,
                )
            verify_qualification_artifacts(manifest, repository_root=ROOT)
            isolated = output / "isolated_sillytavern"
            frozen = verify_qualification_sillytavern(isolated)
            self.assertEqual(
                frozen["metadata_bridge_contract"],
                "cera.full_model.capture_function.v1",
            )
            check = entrypoint.provider_free_check(
                fixture_path=FIXTURES,
                manifest_path=output / "QUALIFICATION_MANIFEST.json",
            )
            self.assertEqual(
                check["isolated_sillytavern_manifest_sha256"],
                frozen["manifest_sha256"],
            )
            (isolated / "public/scripts/openai.js").write_text(
                "tampered",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(Exception, "metadata bridge changed"):
                verify_qualification_sillytavern(isolated)

    def test_qualification_isolation_installs_only_repository_cera_integrations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _fake_sillytavern_source(root)
            proxy = root / "proxy"
            extension = root / "extension"
            proxy.mkdir()
            extension.mkdir()
            (proxy / "index.js").write_text("proxy", encoding="utf-8")
            (proxy / "package.json").write_text("{}", encoding="utf-8")
            (extension / "index.js").write_text("extension", encoding="utf-8")
            target = root / "target"
            manifest = stage_qualification_sillytavern(
                source,
                target,
                repository_proxy_root=proxy,
                repository_extension_root=extension,
            )
            verify_qualification_sillytavern(target)
            self.assertTrue(manifest["only_repository_cera_integrations"])
            self.assertEqual(
                manifest["review_loopback_override_env"],
                "CERA_REVIEW_LOOPBACK_ROOT",
            )
            self.assertTrue(manifest["installed_default_port_5101_untouched"])
            self.assertFalse((target / "data" / "private.json").exists())
            self.assertFalse(
                (target / "public/scripts/extensions/third-party/unapproved/index.js").exists()
            )
            self.assertTrue((target / "plugins/cera-review-proxy/index.js").is_file())
            openai = (target / "public/scripts/openai.js").read_text(encoding="utf-8")
            self.assertIn("window.ceraCaptureCompletionMetadata(data.cera)", openai)
            self.assertNotIn("data?.cera?.provisional", openai)

    def test_provider_free_entrypoint_reports_exact_campaign_counts(self) -> None:
        result = entrypoint.provider_free_check(fixture_path=FIXTURES)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["backend"], 20)
        self.assertEqual(result["sillytavern"], 10)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("ab") as stream:
        stream.write(canonical_bytes(payload) + b"\n")


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _fake_sillytavern_source(root: Path) -> Path:
    source = root / "source"
    for directory in (
        "default",
        "node_modules",
        "public/scripts/extensions/third-party/unapproved",
        "src",
        "data",
        "plugins/unapproved",
    ):
        (source / directory).mkdir(parents=True, exist_ok=True)
    for relative, content in {
        "server.js": "server",
        "package.json": "{}",
        "package-lock.json": "{}",
        "default/config.yaml": "enableServerPlugins: false\n",
        "node_modules/module.js": "module",
        "public/index.html": "index",
        "public/scripts/openai.js": (
            "async function request() {\n"
            "        if (data?.cera?.provisional && "
            "data.cera.provisional_review_id) {\n"
            "            const queue = "
            "Array.isArray(window.ceraCompletionMetadataQueue)\n"
            "                ? window.ceraCompletionMetadataQueue\n"
            "                : (window.ceraCompletionMetadataQueue = []);\n"
            "            queue.push(structuredClone(data.cera));\n"
            "            if (queue.length > 8) "
            "queue.splice(0, queue.length - 8);\n"
            "            window.dispatchEvent(new "
            "CustomEvent('cera:completion-metadata', {\n"
            "                detail: data.cera,\n"
            "            }));\n"
            "        }\n"
            "}\n"
        ),
        "public/scripts/extensions/third-party/unapproved/index.js": "bad",
        "src/app.js": "app",
        "data/private.json": "private",
        "plugins/unapproved/index.js": "bad",
    }.items():
        (source / relative).write_text(content, encoding="utf-8")
    return source


if __name__ == "__main__":
    unittest.main()
