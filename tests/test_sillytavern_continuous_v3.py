from __future__ import annotations

import json
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cera.creator_review import CreatorReviewAction
from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.continuous.prompting import PLANNER_STABLE_INSTRUCTIONS
from cera.serialization import canonical_bytes, canonical_sha256, text_sha256
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
from scripts import run_sillytavern_continuous_v3_campaign as campaign_script
from scripts.run_sillytavern_continuous_v3_campaign import (
    assert_exact_execution_manifest,
    execution_manifest,
    recover_prior_campaign,
)


class ContinuousSillyTavernV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.prepared: list[int] = []
        self.accepted: list[int] = []
        self.prepared_records: dict[int, PreparedContinuousTestTurn] = {}

        def prepare(turn: int) -> PreparedContinuousTestTurn:
            self.prepared.append(turn)
            assessment = CreatorReviewAssessment(
                schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
                severity=CreatorReviewSeverity.GOOD,
                publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
                issue_owner=ReviewIssueOwner.NONE,
                reason_codes=(),
                creator_reason="The exact scripted candidate passed validation.",
                verifier_status="accepted",
            )
            prepared = PreparedContinuousTestTurn(
                turn_number=turn,
                turn_id=f"turn-{turn:03d}",
                candidate_text=f"candidate {turn}",
                candidate_sha256=text_sha256(f"candidate {turn}"),
                candidate_text_sha256=text_sha256(f"candidate {turn}"),
                sequence_beats=(f"beat {turn}",),
                sequence_plan_sha256=text_sha256(f"sequence {turn}"),
                validator_package_id=f"validator-package-{turn}",
                validator_package_sha256=text_sha256(f"package {turn}"),
                validator_semantic_status="accepted",
                assessment_schema_version=assessment.schema_version,
                assessment_severity=assessment.severity,
                assessment_publication_eligibility=(
                    assessment.publication_eligibility
                ),
                assessment_issue_owner=assessment.issue_owner,
                assessment_reason_codes=assessment.reason_codes,
                assessment_creator_reason=assessment.creator_reason,
                assessment_verifier_status=assessment.verifier_status,
                assessment_receipt_sha256=assessment.assessment_sha256,
                provider_calls=4 if turn == 3 else 3,
                accept_allowed=True,
            )
            self.prepared_records[turn] = prepared
            return prepared

        def accept(prepared: PreparedContinuousTestTurn) -> AcceptedContinuousTestTurn:
            turn = prepared.turn_number
            if prepared != self.prepared_records[turn]:
                raise StateConflictError("test review binding changed")
            self.accepted.append(turn)
            return AcceptedContinuousTestTurn(
                turn_number=turn,
                artifact_id=f"artifact:{turn}",
                generation=turn,
                promotion_receipt_sha256=text_sha256(f"receipt {turn}"),
                review_binding_sha256=prepared.review_binding_sha256,
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
            prepared = self.prepared_records[turn]
            self.assertEqual(
                review["candidate_text_sha256"], prepared.candidate_text_sha256
            )
            self.assertEqual(
                review["sequence_plan_sha256"], prepared.sequence_plan_sha256
            )
            self.assertEqual(
                review["validator_package_id"], prepared.validator_package_id
            )
            self.assertEqual(
                review["validator_package_sha256"],
                prepared.validator_package_sha256,
            )
            self.assertEqual(
                review["assessment"]["assessment_receipt_sha256"],
                prepared.assessment_receipt_sha256,
            )
            self.assertEqual(
                review["review_binding_sha256"], prepared.review_binding_sha256
            )
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

    def test_synthetic_hash_and_severity_cannot_enable_accept(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError, "candidate prose hash changed"
        ):
            replace(
                self.prepared_records.get(1)
                or self.adapter._prepare_turn(1),
                candidate_text_sha256="0" * 64,
            )

        concern = CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.CONCERN,
            publication_eligibility=PublicationEligibility.ACCEPT_BLOCKED,
            issue_owner=ReviewIssueOwner.VERIFIER,
            reason_codes=("scripted_concern",),
            creator_reason="The scripted candidate requires review.",
            verifier_status="concern",
        )
        base = self.prepared_records[1]
        blocked = replace(
            base,
            validator_semantic_status="concern",
            assessment_schema_version=concern.schema_version,
            assessment_severity=concern.severity,
            assessment_publication_eligibility=concern.publication_eligibility,
            assessment_issue_owner=concern.issue_owner,
            assessment_reason_codes=concern.reason_codes,
            assessment_creator_reason=concern.creator_reason,
            assessment_verifier_status=concern.verifier_status,
            assessment_receipt_sha256=concern.assessment_sha256,
            accept_allowed=False,
        )
        adapter = ContinuousSillyTavernTestAdapter(
            session_id="blocked-session",
            prepare_turn=lambda _: blocked,
            accept_turn=lambda _: (_ for _ in ()).throw(
                AssertionError("blocked review crossed Accept")
            ),
            route_identity={"profile_id": "continuous-v3-test"},
        )
        server = build_server(
            adapter,
            CeraSillyTavernServerConfig(
                port=0,
                model=CERA_CONTINUOUS_V3_TEST_MODEL,
                service="cera-continuous-v3-blocked-test",
            ),
        )
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original_base = self.base
        try:
            self.base = f"http://127.0.0.1:{server.server_port}"
            status, reply = self.request(
                "POST",
                "/v1/chat/completions",
                {
                    "model": CERA_CONTINUOUS_V3_TEST_MODEL,
                    "stream": False,
                    "cera_session_id": "blocked-session",
                    "messages": [
                        {"role": "user", "content": CONTINUOUS_V3_TEST_FIXTURE[0]}
                    ],
                },
            )
            self.assertEqual(status, 200)
            review_id = reply["cera"]["provisional_review_id"]
            status, review = self.request("GET", f"/v1/cera/reviews/{review_id}")
            self.assertEqual(status, 200)
            self.assertEqual(review["assessment"]["severity"], "concern")
            self.assertEqual(
                review["assessment"]["publication_eligibility"],
                "accept_blocked",
            )
            self.assertFalse(review["accept_enabled"])
            status, _ = self.request(
                "POST",
                f"/v1/cera/reviews/{review_id}/decision",
                {"action": CreatorReviewAction.ACCEPT.value},
            )
            self.assertEqual(status, 400)
        finally:
            self.base = original_base
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_stale_review_record_fails_before_acceptance(self) -> None:
        status, _ = self.chat(1)
        self.assertEqual(status, 200)
        review_id, record = next(iter(self.adapter._reviews.items()))
        substituted = replace(
            record.prepared,
            candidate_text="substituted candidate",
            candidate_text_sha256=text_sha256("substituted candidate"),
            candidate_sha256=text_sha256("substituted candidate identity"),
        )
        self.adapter._reviews[review_id] = replace(record, prepared=substituted)
        with self.assertRaisesRegex(StateConflictError, "review binding changed"):
            self.adapter.review_action(review_id, CreatorReviewAction.ACCEPT)
        self.assertEqual(self.accepted, [])


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

    def test_planner_instruction_declares_role_arrays_mutually_exclusive(self) -> None:
        self.assertIn("closed and mutually exclusive within each beat", PLANNER_STABLE_INSTRUCTIONS)
        self.assertIn("split those assertions into separate causally ordered beats", PLANNER_STABLE_INSTRUCTIONS)

    def test_exact_child_execution_manifest_rejects_any_drift(self) -> None:
        unsigned = {
            "schema_version": "test.execution_manifest.v1",
            "authority": {"manifest_root_sha256": text_sha256("root")},
        }
        supplied = {
            **unsigned,
            "execution_identity_sha256": canonical_sha256(unsigned),
        }
        assert_exact_execution_manifest(
            supplied,
            dict(supplied),
            expected_identity_sha256=supplied["execution_identity_sha256"],
        )
        changed_unsigned = {
            **unsigned,
            "authority": {"manifest_root_sha256": text_sha256("changed")},
        }
        changed = {
            **changed_unsigned,
            "execution_identity_sha256": canonical_sha256(changed_unsigned),
        }
        with self.assertRaisesRegex(ValueError, "recomputed execution"):
            assert_exact_execution_manifest(
                supplied,
                changed,
                expected_identity_sha256=supplied["execution_identity_sha256"],
            )
        tampered = dict(supplied)
        tampered["authority"] = {"manifest_root_sha256": text_sha256("tampered")}
        with self.assertRaisesRegex(ValueError, "not self-bound"):
            assert_exact_execution_manifest(
                tampered,
                supplied,
                expected_identity_sha256=supplied["execution_identity_sha256"],
            )

    def test_single_run_recomputes_identity_before_run_root_or_provider_setup(self) -> None:
        supplied_unsigned = {
            "schema_version": "test.execution_manifest.v1",
            "authority": {"manifest_root_sha256": text_sha256("published")},
        }
        supplied = {
            **supplied_unsigned,
            "execution_identity_sha256": canonical_sha256(supplied_unsigned),
        }
        changed_unsigned = {
            **supplied_unsigned,
            "authority": {"manifest_root_sha256": text_sha256("drifted")},
        }
        changed = {
            **changed_unsigned,
            "execution_identity_sha256": canonical_sha256(changed_unsigned),
        }
        with TemporaryDirectory(prefix="cera-child-authority-") as temporary:
            root = Path(temporary)
            source_db = root / "source.sqlite3"
            source_db.write_bytes(b"source")
            manifest_path = root / "EXECUTION_MANIFEST.json"
            manifest_path.write_bytes(canonical_bytes(supplied) + b"\n")
            run_root = root / "run"
            arguments = Namespace(
                cycle_directory=root / "cycle",
                source_database=source_db,
                runtime_root=run_root,
                run_id=CONTINUOUS_V3_RUN_IDENTITIES[0],
                execution_manifest=manifest_path,
                expected_checkpoint_sha="a" * 40,
                expected_authorization_sha256=text_sha256("authorization"),
                expected_cycle_id="cycle-test",
                expected_cycle_sequence=22,
                expected_job4_task_id="provider-free-audit",
                execution_identity_sha256=supplied["execution_identity_sha256"],
            )
            with (
                patch.object(campaign_script, "execution_manifest", return_value=changed),
                self.assertRaisesRegex(ValueError, "recomputed execution"),
            ):
                campaign_script.run_single(arguments)
            self.assertFalse(run_root.exists())

    def test_campaign_recomputes_identity_before_each_child_dispatch(self) -> None:
        supplied_unsigned = {
            "schema_version": "test.execution_manifest.v1",
            "authority": {"manifest_root_sha256": text_sha256("published")},
        }
        supplied = {
            **supplied_unsigned,
            "execution_identity_sha256": canonical_sha256(supplied_unsigned),
        }
        changed_unsigned = {
            **supplied_unsigned,
            "authority": {"manifest_root_sha256": text_sha256("later-drift")},
        }
        changed = {
            **changed_unsigned,
            "execution_identity_sha256": canonical_sha256(changed_unsigned),
        }
        with TemporaryDirectory(prefix="cera-parent-authority-") as temporary:
            root = Path(temporary)
            source_db = root / "source.sqlite3"
            source_db.write_bytes(b"source")
            campaign_root = root / "campaign"
            arguments = Namespace(
                cycle_directory=root / "cycle",
                source_database=source_db,
                runtime_root=campaign_root,
                expected_checkpoint_sha="a" * 40,
                expected_authorization_sha256=text_sha256("authorization"),
                expected_cycle_id="cycle-test",
                expected_cycle_sequence=22,
                expected_job4_task_id="provider-free-audit",
                prior_campaign_root=None,
            )
            with (
                patch.object(
                    campaign_script,
                    "execution_manifest",
                    side_effect=(supplied, changed),
                ),
                patch.object(campaign_script.subprocess, "run") as dispatch,
                self.assertRaisesRegex(ValueError, "recomputed execution"),
            ):
                campaign_script.run_campaign(arguments)
            dispatch.assert_not_called()
            self.assertFalse((campaign_root / "runs").exists())

    def test_manifest_binds_git_cycle_route_prompts_schemas_and_policy(self) -> None:
        checkpoint = "a" * 40
        authority = {
            "manifest": {
                "cycle_id": "cycle-test",
                "cycle_sequence": 22,
                "manifest_root_sha256": text_sha256("manifest-root"),
                "repository_identity_sha256": text_sha256("repository"),
                "task_set_sha256": text_sha256("task-set"),
                "job4": {
                    "task_id": "provider-free-audit",
                    "scope_sha256": text_sha256("scope"),
                },
            },
            "cycle_manifest_sha256": text_sha256("cycle-manifest"),
            "changed_source_manifest_sha256": text_sha256("changed-source"),
            "published_receipt_sha256": text_sha256("published"),
            "job4_started_receipt_sha256": text_sha256("started"),
            "trigger_receipt_sha256": text_sha256("trigger"),
            "cycle_state_sha256": text_sha256("state"),
            "cycle_state": "job4_in_progress",
            "authorization_record_sha256": text_sha256("authorization"),
        }
        with TemporaryDirectory(prefix="cera-execution-manifest-") as temporary:
            source_db = Path(temporary) / "source.sqlite3"
            source_db.write_bytes(b"disposable source")
            with (
                patch.object(campaign_script, "validate_authority", return_value=authority),
                patch.object(
                    campaign_script,
                    "_tracked_execution_files",
                    return_value=(
                        {"src/cera/example.py": text_sha256("source")},
                        checkpoint,
                        "b" * 40,
                    ),
                ),
            ):
                value = execution_manifest(
                    source_db,
                    cycle=Path(temporary),
                    expected_checkpoint_sha=checkpoint,
                    expected_authorization_sha256=text_sha256("authorization"),
                    expected_cycle_id="cycle-test",
                    expected_cycle_sequence=22,
                    expected_task_id="provider-free-audit",
                )
        self.assertEqual(
            value["schema_version"],
            "cera.sillytavern_continuous_v3_execution_manifest.v2",
        )
        self.assertEqual(value["git"]["actual_head_sha"], checkpoint)
        self.assertEqual(value["cycle_authority"]["cycle_sequence"], 22)
        self.assertEqual(value["route"]["profile_id"], campaign_script.PROFILE_ID)
        self.assertIn("planner_prompt_version", value["prompt_bindings"])
        self.assertIn("validator_package", value["schema_bindings"])
        self.assertIn("persistence_policy_sha256", value["policy_bindings"])
        assert_exact_execution_manifest(
            value,
            dict(value),
            expected_identity_sha256=value["execution_identity_sha256"],
        )

    def test_prior_failed_run_recovers_dispatched_call_without_mutation(self) -> None:
        with TemporaryDirectory(prefix="cera-st-v3-recovery-") as temporary:
            root = Path(temporary)
            run_id = CONTINUOUS_V3_RUN_IDENTITIES[0]
            run_root = root / "runs" / run_id
            run_root.mkdir(parents=True)
            old_identity = text_sha256("old execution")
            new_identity = text_sha256("repaired execution")

            def write(path: Path, payload) -> None:
                path.write_bytes(canonical_bytes(payload) + b"\n")

            write(root / "EXECUTION_MANIFEST.json", {"execution_identity_sha256": old_identity})
            write(
                root / "CAMPAIGN_RESULT.json",
                {
                    "campaign_id": "2026-08-02-continuous-sillytavern-two-run-v1",
                    "execution_identity_sha256": old_identity,
                    "campaign": {
                        "runs": [
                            {
                                "run_id": run_id,
                                "state": "failed",
                                "calls": [],
                                "terminal_reason": "RuntimeError",
                            }
                        ]
                    },
                },
            )
            write(
                run_root / "RUN_RESULT.json",
                {"run_id": run_id, "status": "failed", "provider_calls": 1},
            )
            write(
                run_root / "EXECUTION_MANIFEST.json",
                {"execution_identity_sha256": old_identity},
            )
            ledger_path = run_root / "PROVIDER_CALL_LEDGER.jsonl"
            ledger_path.write_bytes(
                canonical_bytes(
                    {
                        "event_index": 1,
                        "call_id": "call-1",
                        "state": "transport_invoked",
                        "owner": "planner",
                    }
                )
                + b"\n"
            )
            historical_bytes = {
                path: path.read_bytes()
                for path in (
                    root / "CAMPAIGN_RESULT.json",
                    root / "EXECUTION_MANIFEST.json",
                    run_root / "RUN_RESULT.json",
                    run_root / "EXECUTION_MANIFEST.json",
                    ledger_path,
                )
            }
            campaign, recovery = recover_prior_campaign(
                root,
                execution_identity_sha256=new_identity,
            )
            self.assertEqual(campaign.total_provider_calls, 1)
            self.assertEqual(campaign.runs[0].calls, (CONTINUOUS_V3_CALL_SCHEDULE[0],))
            self.assertEqual(campaign.consecutive_passes, 0)
            self.assertEqual(recovery["recovered_total_provider_calls"], 1)
            self.assertEqual(
                recovery["runs"][0]["call_source"],
                "recovered_from_transport_invoked_prefix",
            )
            campaign.begin_run(
                CONTINUOUS_V3_RUN_IDENTITIES[1],
                execution_identity_sha256=new_identity,
            )
            self.assertEqual(campaign.current.run_id, CONTINUOUS_V3_RUN_IDENTITIES[1])
            self.assertTrue(all(path.read_bytes() == data for path, data in historical_bytes.items()))

            campaign.record_dispatch(CONTINUOUS_V3_CALL_SCHEDULE[0])
            campaign.terminalize(passed=False, reason="second failure")
            next_root = root / "next-campaign"
            next_run_id = CONTINUOUS_V3_RUN_IDENTITIES[1]
            next_run_root = next_root / "runs" / next_run_id
            next_run_root.mkdir(parents=True)
            write(next_root / "EXECUTION_MANIFEST.json", {"execution_identity_sha256": new_identity})
            write(
                next_root / "CAMPAIGN_RESULT.json",
                {
                    "campaign_id": "2026-08-02-continuous-sillytavern-two-run-v1",
                    "execution_identity_sha256": new_identity,
                    "campaign": campaign.to_dict(),
                },
            )
            write(next_root / "PRIOR_CAMPAIGN_RECOVERY.json", recovery)
            write(
                next_run_root / "RUN_RESULT.json",
                {"run_id": next_run_id, "status": "failed", "provider_calls": 1},
            )
            write(
                next_run_root / "EXECUTION_MANIFEST.json",
                {"execution_identity_sha256": new_identity},
            )
            (next_run_root / "PROVIDER_CALL_LEDGER.jsonl").write_bytes(
                canonical_bytes(
                    {
                        "event_index": 1,
                        "call_id": "call-2",
                        "state": "transport_invoked",
                        "owner": "planner",
                    }
                )
                + b"\n"
            )
            repaired_again = text_sha256("repaired execution twice")
            second_campaign, second_recovery = recover_prior_campaign(
                next_root,
                execution_identity_sha256=repaired_again,
            )
            self.assertEqual(len(second_campaign.runs), 2)
            self.assertEqual(second_campaign.total_provider_calls, 2)
            self.assertEqual(
                Path(second_recovery["runs"][0]["evidence_root"]),
                run_root.resolve(),
            )

    def test_prior_recovery_rejects_provider_count_disagreement(self) -> None:
        with TemporaryDirectory(prefix="cera-st-v3-recovery-invalid-") as temporary:
            root = Path(temporary)
            run_id = CONTINUOUS_V3_RUN_IDENTITIES[0]
            run_root = root / "runs" / run_id
            run_root.mkdir(parents=True)
            identity = text_sha256("old execution")

            def write(path: Path, payload) -> None:
                path.write_bytes(canonical_bytes(payload) + b"\n")

            write(root / "EXECUTION_MANIFEST.json", {"execution_identity_sha256": identity})
            write(
                root / "CAMPAIGN_RESULT.json",
                {
                    "campaign_id": "2026-08-02-continuous-sillytavern-two-run-v1",
                    "execution_identity_sha256": identity,
                    "campaign": {
                        "runs": [
                            {"run_id": run_id, "state": "failed", "terminal_reason": "failed"}
                        ]
                    },
                },
            )
            write(
                run_root / "RUN_RESULT.json",
                {"run_id": run_id, "status": "failed", "provider_calls": 1},
            )
            write(run_root / "EXECUTION_MANIFEST.json", {"execution_identity_sha256": identity})
            (run_root / "PROVIDER_CALL_LEDGER.jsonl").write_text("", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "provider count disagrees"):
                recover_prior_campaign(
                    root,
                    execution_identity_sha256=text_sha256("repaired execution"),
                )


if __name__ == "__main__":
    unittest.main()
