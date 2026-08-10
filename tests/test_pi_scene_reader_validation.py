from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from cera.adult_pipeline.contracts import AdultFilterConflictClass
from cera.adult_pipeline.pi_roles import _FILTER_SYSTEM_PROMPT
from cera.errors import ContractValidationError
from cera.pi_scene.context import AcceptedBranchContextProvider
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.provider_stage_retry import (
    ProviderFamily,
    ProviderModelFamily,
    ProviderStage,
)
from cera.pi_scene.provider_stage_retry_packets import (
    ProviderStageConfigurationV1,
    freeze_reader_stage_packet,
)
from cera.pi_scene.provider_stage_retry_scope import provider_stage_owner
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.reader_validation import (
    SOL_READER_BASE_INSTRUCTIONS,
    SOL_READER_PROFILE,
    BoundReaderValidationV1,
    FreshSolReaderFactory,
    ReaderIssueV1,
    ReaderStatus,
    ReaderValidationRequestV1,
    ReaderVerdictV1,
    RetryFeedbackScope,
    build_reader_validation_input,
    build_reader_validation_prompt,
    reader_verdict_json_schema,
    sol_reader_route,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, canonical_sha256, text_sha256, to_primitive

from .test_cognition_contracts import SAKURA, _plan
from .test_pi_scene_branch_state_retrieval import (
    ADULT_PROTECTED_SENTINEL,
    _adult_event,
    _ContextSource,
    _ordinary_event,
    _seed,
)
from .test_pi_scene_store_quality import _candidate


def _qualified_candidate():
    authority_json = canonical_json(to_primitive(_plan()))
    return replace(
        _candidate(parent_turn_id="turn-prior", parent_head_sha256="a" * 64),
        primary_authority_kind="codex_cognition_plan",
        primary_authority_json=authority_json,
        primary_authority_sha256=text_sha256(authority_json),
    )


def _turn_input() -> LeanSceneTurnInputV1:
    return LeanSceneTurnInputV1(
        world_id="world-test",
        branch_id="branch-main",
        scene_id="scene-kitchen",
        exact_user_source="Continue 1.",
        current_state={
            "public_scene_state": "The closed doorway remains under Sakura's control.",
            "request_controls": {"scene_depth": "medium"},
        },
        characters={
            SAKURA: {
                "name": "Sakura Hanezawa",
                "role": "eldest daughter and doorway host",
                "character_logic": "Protect household privacy while remaining courteous.",
                "voice": "Calm, precise, and firm.",
            }
        },
        relationships={
            "ted-sakura": {
                "participants": ["character:ted", SAKURA],
                "summary": "Sakura has not independently verified Ted.",
            }
        },
        recent_prose=("Older accepted prose.", "Immediate accepted prose."),
        relevant_memories={},
        voice_examples={"sakura_hanezawa": {"guidance": "Use calm, economical questions."}},
        craft_index={},
    )


def _bound(
    verdict: ReaderVerdictV1,
) -> BoundReaderValidationV1:
    request, custody = build_reader_validation_input(
        candidate=_qualified_candidate(),
        turn_input=_turn_input(),
    )
    return BoundReaderValidationV1(request=request, custody=custody, verdict=verdict)


class _ReaderBackend:
    external_provider_boundary = False

    def __init__(self, verdict: ReaderVerdictV1, *, resumable: bool = False) -> None:
        self.verdict = verdict
        self.resumable = resumable
        self.started: list[tuple[str, str]] = []
        self.runs: list[tuple[str, object]] = []
        self.archived: list[str] = []

    def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str:
        self.started.append((base_instructions, profile))
        return f"thread-{len(self.started)}"

    def run_reader_once(self, *, thread_id: str, request: object) -> ReaderVerdictV1:
        self.runs.append((thread_id, request))
        return self.verdict

    def archive(self, thread_id: str) -> None:
        self.archived.append(thread_id)

    def is_resumable(self, thread_id: str) -> bool:
        del thread_id
        return self.resumable


class PiSceneReaderValidationTests(unittest.TestCase):
    def test_bridge_binds_frozen_candidate_plan_head_depth_and_context(self) -> None:
        candidate = _qualified_candidate()
        request, custody = build_reader_validation_input(
            candidate=candidate,
            turn_input=_turn_input(),
        )
        self.assertEqual(request.exact_candidate_prose, candidate.story_text)
        self.assertEqual(request.immediate_prior_accepted_prose, "Immediate accepted prose.")
        self.assertEqual(request.scene_depth, "medium")
        self.assertEqual(request.character_context[0].character_id, SAKURA)
        self.assertEqual(request.character_context[0].voice, "Calm, precise, and firm.")
        self.assertEqual(request.relationship_context[0].relationship_id, "ted-sakura")
        self.assertEqual(custody.accepted_head_sha256, candidate.accepted_head_before_sha256)
        self.assertEqual(custody.candidate_sha256, candidate.candidate_sha256)
        self.assertEqual(custody.cognition_plan_sha256, canonical_sha256(_plan()))
        self.assertEqual(custody.candidate_prose_sha256, text_sha256(candidate.story_text))
        self.assertEqual(
            custody.immediate_prior_accepted_prose_sha256,
            text_sha256("Immediate accepted prose."),
        )
        prompt = build_reader_validation_prompt(request)
        self.assertNotIn("semantic_validation", prompt)
        self.assertNotIn("luna", prompt.lower())

    def test_request_decode_rejects_every_raw_luna_verdict_field(self) -> None:
        request, _ = build_reader_validation_input(
            candidate=_qualified_candidate(),
            turn_input=_turn_input(),
        )
        for field_name in ("luna_verdict", "semantic_validation", "validator_result"):
            with self.subTest(field=field_name), self.assertRaises(ContractValidationError):
                payload = to_primitive(request)
                payload[field_name] = {"verdict": "pass"}
                from_mapping(ReaderValidationRequestV1, payload)

    def test_post_adult_reader_receives_only_ordinary_safe_head_context(self) -> None:
        sanitized: list[dict[str, Any]] = []
        ordinary_recent: list[dict[str, Any]] = []
        adult_recent: list[dict[str, Any]] = []
        parent_turn: str | None = None
        parent_sha: str | None = None
        for order in range(1, 3):
            reduced, raw = _ordinary_event(
                order,
                parent_turn_id=parent_turn,
                parent_receipt_sha256=parent_sha,
            )
            sanitized.append(reduced)
            ordinary_recent.append(raw)
            adult_recent.append(raw)
            parent_turn = str(reduced["receipt"]["accepted_turn_id"])
            parent_sha = str(reduced["accepted_receipt_sha256"])
        assert parent_turn is not None and parent_sha is not None
        adult, adult_raw = _adult_event(
            3,
            parent_turn_id=parent_turn,
            parent_receipt_sha256=parent_sha,
            pending=False,
        )
        sanitized.append(adult)
        ordinary_recent.append(adult)
        adult_recent.append(adult_raw)
        source = _ContextSource(
            reducer_payloads=sanitized,
            ordinary_payloads=ordinary_recent,
            adult_payloads=adult_recent,
        )
        actual_turn = AcceptedBranchContextProvider(source, _seed())(
            SceneRoute.ORDINARY,
            "What happens next?",
            (),
        )
        turn = replace(
            actual_turn,
            characters={
                **actual_turn.characters,
                SAKURA: {
                    "name": "Sakura Hanezawa",
                    "character_logic": "Protect household privacy.",
                    "voice": "Calm and exact.",
                },
            },
            voice_examples={
                **actual_turn.voice_examples,
                "sakura_hanezawa": {"guidance": "Ask concise questions."},
            },
        )
        candidate = replace(
            _qualified_candidate(),
            world_id=turn.world_id,
            branch_id=turn.branch_id,
            scene_id=turn.scene_id,
            generation=4,
            parent_accepted_turn_id=str(adult["receipt"]["accepted_turn_id"]),
            accepted_head_before_sha256=str(adult["accepted_receipt_sha256"]),
            exact_user_source=turn.exact_user_source,
            exact_user_source_sha256=text_sha256(turn.exact_user_source),
        )
        request, _ = build_reader_validation_input(candidate=candidate, turn_input=turn)
        wire = canonical_json(request)
        self.assertEqual(request.immediate_prior_accepted_prose, "Ordinary accepted prose 2.")
        self.assertIn("The adults remain together afterward.", wire)
        self.assertNotIn(ADULT_PROTECTED_SENTINEL, wire)
        self.assertNotIn(ADULT_PROTECTED_SENTINEL, build_reader_validation_prompt(request))

    def test_bound_reader_accepts_pass_and_projects_concise_failures(self) -> None:
        passed = _bound(ReaderVerdictV1(status=ReaderStatus.ACCEPTED))
        self.assertTrue(passed.passed)
        self.assertEqual(passed.concise_failures, ())

        rejected = _bound(
            ReaderVerdictV1(
                status=ReaderStatus.REJECTED,
                issues=(
                    ReaderIssueV1(
                        issue_code="severe_repetition",
                        concise_explanation="The same sentence repeats until the beat stalls.",
                        exact_quote="Accepted prose",
                        feedback_scope=RetryFeedbackScope.EXACT_QUOTE,
                    ),
                ),
            )
        )
        self.assertFalse(rejected.passed)
        self.assertEqual(
            rejected.concise_failures,
            ("The same sentence repeats until the beat stalls.",),
        )

    def test_bound_reader_rejects_false_quote_and_unknown_omitted_item(self) -> None:
        for issue, message in (
            (
                ReaderIssueV1(
                    issue_code="false_quote",
                    concise_explanation="Bad anchor.",
                    exact_quote="not in candidate",
                    feedback_scope=RetryFeedbackScope.EXACT_QUOTE,
                ),
                "not exact candidate prose",
            ),
            (
                ReaderIssueV1(
                    issue_code="false_omission",
                    concise_explanation="Bad plan key.",
                    omitted_planner_item_key="unknown_item",
                    feedback_scope=RetryFeedbackScope.OMITTED_PLANNER_ITEM,
                ),
                "unknown current plan item",
            ),
        ):
            with (
                self.subTest(issue=issue.issue_code),
                self.assertRaisesRegex(
                    ContractValidationError,
                    message,
                ),
            ):
                _bound(
                    ReaderVerdictV1(
                        status=ReaderStatus.REJECTED,
                        issues=(issue,),
                    )
                )

    def test_schema_closes_omission_keys_to_current_plan(self) -> None:
        schema = reader_verdict_json_schema(
            plan_item_keys=("sakura_checks_door", "natural_stop"),
        )
        branches = schema["properties"]["verdict"]["oneOf"]
        issue_branches = branches[1]["properties"]["issues"]["items"]["oneOf"]
        omitted = next(
            value
            for value in issue_branches
            if value["properties"]["feedback_scope"].get("const") == "omitted_planner_item"
        )
        self.assertEqual(
            omitted["properties"]["omitted_planner_item_key"]["enum"],
            ["sakura_checks_door", "natural_stop"],
        )

    def test_fresh_factory_is_one_operation_then_archive(self) -> None:
        request, custody = build_reader_validation_input(
            candidate=_qualified_candidate(),
            turn_input=_turn_input(),
        )
        backend = _ReaderBackend(ReaderVerdictV1(status=ReaderStatus.ACCEPTED))
        factory = FreshSolReaderFactory(backend)
        self.assertEqual(backend.started, [])
        self.assertEqual(backend.runs, [])
        result = factory.validate(request, custody)
        self.assertTrue(result.passed)
        self.assertEqual(backend.started, [(SOL_READER_BASE_INSTRUCTIONS, SOL_READER_PROFILE)])
        self.assertEqual(len(backend.runs), 1)
        self.assertEqual(backend.archived, ["thread-1"])

        resumable = _ReaderBackend(
            ReaderVerdictV1(status=ReaderStatus.ACCEPTED),
            resumable=True,
        )
        with self.assertRaisesRegex(ContractValidationError, "remains resumable"):
            FreshSolReaderFactory(resumable).validate(request, custody)

    def test_sol_medium_route_has_no_retry_or_fallback(self) -> None:
        route = sol_reader_route()
        self.assertEqual(route.model_name, "gpt-5.6-sol")
        self.assertEqual(route.reasoning_effort, "medium")
        self.assertEqual(route.automatic_retry_count, 0)
        self.assertFalse(route.fallback_enabled)

    def test_reader_is_closed_retry_stage_with_frozen_packet(self) -> None:
        self.assertEqual(
            provider_stage_owner(ProviderStage.READER),
            (ProviderFamily.CODEX, ProviderModelFamily.SOL),
        )
        request, custody = build_reader_validation_input(
            candidate=_qualified_candidate(),
            turn_input=_turn_input(),
        )
        configuration = ProviderStageConfigurationV1.create(
            stage=ProviderStage.READER,
            model_id="gpt-5.6-sol",
            reasoning_mode="medium",
            routing={"fresh_per_attempt": True},
            content_policy_route="ordinary",
            stage_configuration={"automatic_retry_count": 0, "fallback_enabled": False},
        )
        packet = freeze_reader_stage_packet(
            reader_request=request,
            reader_custody=custody,
            configuration=configuration,
        )
        payload = packet.to_payload()
        self.assertEqual(packet.stage, ProviderStage.READER)
        self.assertEqual(payload["packet_kind"], "reader")
        semantic_input = payload["semantic_input"]
        self.assertIsInstance(semantic_input, dict)
        self.assertNotIn("luna", canonical_json(semantic_input).lower())

    def test_generated_retry_fixture_contains_reader_stage(self) -> None:
        root = Path(__file__).resolve().parents[1]
        payload = json.loads(
            (root / "tests/fixtures/generated/provider_stage_retry_v1_positive.json").read_text(
                encoding="utf-8"
            )
        )
        reader_cases = [
            case
            for case in payload["cases"]
            if case["contract_schema_version"] == "cera.provider_stage_retry_status.v1"
            and case["value"]["stage"] == "reader"
        ]
        self.assertEqual(
            {case["value"]["state"] for case in reader_cases},
            {"succeeded", "attempts_exhausted"},
        )
        for reader_case in reader_cases:
            self.assertEqual(reader_case["value"]["provider"], "codex")
            self.assertEqual(reader_case["value"]["model_family"], "sol")

    def test_adult_filter_owns_same_narrow_severe_quality_floor(self) -> None:
        self.assertEqual(
            AdultFilterConflictClass.SEVERE_READER_QUALITY.value,
            "severe_reader_quality",
        )
        for phrase in (
            "incoherence",
            "clearly wrong character voice or logic",
            "severe repetition",
            "premature closure",
            "materially inadequate realization",
            "do not reject harmless",
        ):
            self.assertIn(phrase, _FILTER_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
