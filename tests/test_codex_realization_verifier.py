from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from jsonschema import Draft202012Validator

from cera.composer import RealizationKind
from cera.contracts import BeatState
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, deterministic_id
from cera.providers import (
    CodexSDKTransport,
    CodexWorkerResult,
    ProviderTransportError,
    ProviderSchemaDialect,
    active_provider_schema_inventory,
    codex_realization_verifier_candidate,
    codex_reasoner_candidate,
    project_provider_output_schema,
)
from cera.realization import (
    CodexSceneRealizationVerifierPort,
    ProtectedUserRealizationAuthority,
    ProtectedUserRealizationClaim,
    RealizationBoundaryCheck,
    RealizationVerificationStatus,
    SceneRealizationBeatExpectation,
    SceneRealizationVerificationCoordinator,
    SceneRealizationVerificationFailure,
    SceneRealizationVerificationRequest,
    build_codex_realization_verifier_packet,
    codex_realization_verifier_draft_json_schema,
    validate_codex_realization_verifier_payload_semantics,
)
from cera.serialization import text_sha256


class StaticVerifierRunner:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0
        self.last_prompt: str | None = None
        self.last_schema: dict[str, object] | None = None

    def run(self, *, route, prompt, output_schema, workspace, mcp_binding):
        self.calls += 1
        self.last_prompt = prompt
        self.last_schema = output_schema
        output = json.dumps(self.payload, ensure_ascii=False, sort_keys=True)
        return CodexWorkerResult(
            output_text=output,
            provider_request_id="private-verifier-request",
            returned_model=route.model_name,
            duration_ms=3210,
            input_tokens=440,
            cached_input_tokens=120,
            output_tokens=90,
            reasoning_output_tokens=40,
            transport_version=route.transport_version,
            pre_registered_turn_count=1,
        )


class TimedOutVerifierTransport:
    def __init__(self) -> None:
        self.route = codex_realization_verifier_candidate()
        self.calls = 0

    def invoke(self, *_args, **_kwargs):
        self.calls += 1
        raise ProviderTransportError(
            ErrorCode.REASONER_UNAVAILABLE,
            "Codex qualification call timed out",
            safe_diagnostics=("transport:timeout", "worker_stage:thread_run"),
            external_provider_calls_observed=1,
        )


def verifier_request() -> SceneRealizationVerificationRequest:
    story = 'Guide nodded. Ted said, "Hello." Ted opened the locked drawer.'
    return SceneRealizationVerificationRequest(
        schema_version=SceneRealizationVerificationRequest.SCHEMA_VERSION,
        request_id=deterministic_id(IdKind.REQUEST, "cera.test.verifier", "request"),
        branch_id=deterministic_id(IdKind.BRANCH, "cera.test.verifier", "branch"),
        generation_id=deterministic_id(
            IdKind.GENERATION, "cera.test.verifier", "generation"
        ),
        candidate_sha256=text_sha256(story),
        story_text=story,
        expected_beats=(
            SceneRealizationBeatExpectation(
                beat_id=deterministic_id(
                    IdKind.BEAT, "cera.test.verifier", "guide-nods"
                ),
                actor_id=deterministic_id(
                    IdKind.CHARACTER, "cera.test.verifier", "guide"
                ),
                neutral_event="The guide nods visibly.",
                required_state=BeatState.COMPLETED,
            ),
        ),
        selected_participant_ids=(
            deterministic_id(IdKind.CHARACTER, "cera.test.verifier", "guide"),
        ),
        protected_user_id=deterministic_id(
            IdKind.CHARACTER, "cera.test.verifier", "ted"
        ),
        protected_user_authorities=(
            ProtectedUserRealizationAuthority(
                source_unit_id=deterministic_id(
                    IdKind.SOURCE_UNIT, "cera.test.verifier", "ted-says-hello"
                ),
                exact_text='Ted says, "Hello."',
                allowed_kinds=(RealizationKind.DIALOGUE,),
                claims=(
                    ProtectedUserRealizationClaim(
                        kind=RealizationKind.DIALOGUE,
                        exact_text='Ted says, "Hello."',
                    ),
                ),
            ),
        ),
        required_boundary_checks=(
            RealizationBoundaryCheck.PROTECTED_USER_NO_UNSUPPLIED_REALIZATION,
        ),
        realization_anchors=(),
        hard_boundaries=(
            "Do not invent protected-user action, dialogue, thought, or consent.",
        ),
    )


def accepted_payload(request: SceneRealizationVerificationRequest) -> dict[str, object]:
    return {
        "schema_version": "cera.codex_scene_realization_verifier_draft.v1",
        "status": "accepted",
        "verified_beat_ids": [str(value) for value in request.expected_beat_ids],
        "verified_participant_ids": [
            str(value) for value in request.selected_participant_ids
        ],
        "verified_boundary_checks": [
            value.value for value in request.required_boundary_checks
        ],
        "violation_codes": [],
        "violation_findings": [],
    }


class CodexRealizationVerifierTests(unittest.TestCase):
    def _port(self, payload: dict[str, object]):
        runner = StaticVerifierRunner(payload)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        transport = CodexSDKTransport(
            codex_realization_verifier_candidate(),
            workspace=Path(temporary.name),
            runner=runner,
        )
        return CodexSceneRealizationVerifierPort(transport), runner

    def test_transport_timeout_retains_stage_and_dispatched_call_observation(self) -> None:
        transport = TimedOutVerifierTransport()
        port = CodexSceneRealizationVerifierPort(transport)
        with self.assertRaises(SceneRealizationVerificationFailure) as caught:
            port.verify(verifier_request())
        self.assertEqual(transport.calls, 1)
        self.assertEqual(caught.exception.external_provider_calls, 1)
        self.assertEqual(
            caught.exception.safe_diagnostics,
            (
                "provider:CERA_REASONER_UNAVAILABLE",
                "transport:timeout",
                "worker_stage:thread_run",
            ),
        )

    def test_route_and_schema_are_provider_compatible_and_inventoried(self) -> None:
        route = codex_realization_verifier_candidate()
        self.assertEqual(route.role.value, "scene_realization_verifier")
        self.assertEqual(route.model_name, "gpt-5.6-sol")
        self.assertEqual(route.reasoning_effort, "medium")
        schema = codex_realization_verifier_draft_json_schema()
        Draft202012Validator.check_schema(schema)
        projection = project_provider_output_schema(
            schema,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        self.assertNotIn("oneOf", json.dumps(projection.provider_schema))
        self.assertNotIn("uniqueItems", json.dumps(projection.provider_schema))
        self.assertIn(
            "codex_realization_verifier_draft_v3",
            tuple(value.name for value in active_provider_schema_inventory()),
        )

    def test_adapter_requires_the_exact_sol_medium_verifier_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            transport = CodexSDKTransport(
                codex_reasoner_candidate(),
                workspace=Path(directory),
                runner=StaticVerifierRunner({}),
            )
            with self.assertRaises(ContractValidationError):
                CodexSceneRealizationVerifierPort(transport)

    def test_packet_is_semantic_and_prompt_forbids_prose_generation(self) -> None:
        request = replace(
            verifier_request(),
            authoritative_evidence_context=(
                '{"authoritative_text":"Labelled food remains reserved."}',
            ),
        )
        packet = build_codex_realization_verifier_packet(request)
        serialized = json.dumps(packet)
        self.assertEqual(packet["candidate_story_text"], request.story_text)
        self.assertEqual(
            packet["protected_user"]["authorities"][0]["exact_text"],
            'Ted says, "Hello."',
        )
        self.assertEqual(
            packet["protected_user"]["authorities"][0]["exact_claims"],
            [{"kind": "dialogue", "exact_text": 'Ted says, "Hello."'}],
        )
        self.assertNotIn(request.candidate_sha256, serialized)
        self.assertEqual(
            packet["authoritative_evidence_context"],
            ['{"authoritative_text":"Labelled food remains reserved."}'],
        )
        port, runner = self._port(accepted_payload(request))
        port.verify(request)
        assert runner.last_prompt is not None
        self.assertIn("do not continue, rewrite, repair", runner.last_prompt.lower())
        self.assertIn("sole allowances", runner.last_prompt.lower())
        self.assertIn("inverse,\nconverse, default", runner.last_prompt.lower())
        self.assertIn("unsupported_world_fact", runner.last_prompt)
        self.assertEqual(runner.calls, 1)

    def test_context_only_source_supports_npc_reaction_without_user_authority(self) -> None:
        request = verifier_request()
        contextual = ProtectedUserRealizationAuthority(
            source_unit_id=deterministic_id(
                IdKind.SOURCE_UNIT,
                "cera.test.verifier",
                "context-only-question",
            ),
            exact_text='Ted asks, "Would you like coffee?" and waits.',
            allowed_kinds=(),
            claims=(),
        )
        contextual_request = replace(
            request,
            protected_user_authorities=(contextual,),
        )
        packet = build_codex_realization_verifier_packet(contextual_request)
        authority = packet["protected_user"]["authorities"][0]
        self.assertEqual(authority["exact_claims"], [])
        self.assertEqual(authority["allowed_kinds"], [])
        port, runner = self._port(accepted_payload(contextual_request))
        port.verify(contextual_request)
        assert runner.last_prompt is not None
        self.assertIn("selected npc may perceive", runner.last_prompt.lower())
        self.assertIn("his words", runner.last_prompt.lower())
        self.assertIn("story-progressing", runner.last_prompt.lower())
        self.assertIn("npc's focalized perception", runner.last_prompt.lower())

    def test_established_scene_context_allows_reference_but_not_new_user_action(self) -> None:
        request = replace(
            verifier_request(),
            established_scene_context=(
                "The doorbell has already rung, and Sakura is handling the door.",
            ),
        )
        packet = build_codex_realization_verifier_packet(request)
        self.assertEqual(
            packet["established_scene_context"],
            ["The doorbell has already rung, and Sakura is handling the door."],
        )
        port, runner = self._port(accepted_payload(request))
        port.verify(request)
        assert runner.last_prompt is not None
        self.assertIn("completed doorbell ring", runner.last_prompt.lower())
        self.assertIn("do not authorize repeating", runner.last_prompt.lower())

    def test_live_shaped_acceptance_retains_one_safe_provider_receipt(self) -> None:
        request = verifier_request()
        port, runner = self._port(accepted_payload(request))
        result = SceneRealizationVerificationCoordinator().execute(request, port)
        self.assertTrue(result.accepted)
        self.assertEqual(result.receipt.external_provider_calls, 1)
        self.assertTrue(result.receipt.qualification_eligible)
        self.assertIsNotNone(result.provider_call_receipt)
        self.assertEqual(runner.calls, 1)
        receipt_text = json.dumps(result.receipt, default=str)
        self.assertNotIn(request.story_text, receipt_text)
        self.assertNotIn('Ted says, "Hello."', receipt_text)

    def test_protected_user_rejection_uses_python_derived_anchor_evidence(self) -> None:
        request = verifier_request()
        payload = {
            **accepted_payload(request),
            "status": "rejected",
            "verified_beat_ids": [],
            "verified_participant_ids": [],
            "verified_boundary_checks": [],
            "violation_codes": ["protected_user_unsupplied_realization"],
            "violation_findings": [
                {
                    "code": "protected_user_unsupplied_realization",
                    "quote": "Ted opened the locked drawer.",
                    "occurrence": 0,
                }
            ],
        }
        port, runner = self._port(payload)
        with self.assertRaises(SceneRealizationVerificationFailure) as caught:
            SceneRealizationVerificationCoordinator().execute(request, port)
        failure = caught.exception
        self.assertIsNotNone(failure.receipt)
        self.assertIsNotNone(failure.provider_call_receipt)
        self.assertEqual(failure.receipt.external_provider_calls, 1)
        self.assertEqual(len(failure.receipt.violation_finding_sha256s), 1)
        self.assertEqual(runner.calls, 1)
        self.assertNotIn("locked drawer", json.dumps(failure.receipt, default=str))

    def test_ambiguous_anchor_and_malformed_status_fail_once_with_safe_receipt(self) -> None:
        request = verifier_request()
        repeated = {
            **accepted_payload(request),
            "status": "rejected",
            "verified_beat_ids": [],
            "verified_participant_ids": [],
            "verified_boundary_checks": [],
            "violation_codes": ["protected_user_unsupplied_realization"],
            "violation_findings": [
                {
                    "code": "protected_user_unsupplied_realization",
                    "quote": "Ted",
                    "occurrence": 0,
                }
            ],
        }
        port, runner = self._port(repeated)
        with self.assertRaises(SceneRealizationVerificationFailure) as caught:
            port.verify(request)
        self.assertIsNotNone(caught.exception.provider_call_receipt)
        self.assertEqual(caught.exception.external_provider_calls, 1)
        self.assertEqual(runner.calls, 1)
        self.assertNotIn(request.story_text, str(caught.exception))

        invalid = accepted_payload(request)
        invalid["violation_codes"] = [
            "protected_user_unsupplied_realization",
            "protected_user_unsupplied_realization",
        ]
        with self.assertRaisesRegex(Exception, "semantic validation") as semantic:
            validate_codex_realization_verifier_payload_semantics(invalid)
        self.assertIn("violation_codes:duplicate_items", semantic.exception.diagnostics)
        self.assertNotIn("protected_user_unsupplied_realization", str(semantic.exception))


if __name__ == "__main__":
    unittest.main()
