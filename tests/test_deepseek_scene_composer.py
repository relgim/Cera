from __future__ import annotations

from dataclasses import replace
import json
import urllib.error
import unittest

from cera.composer import (
    ComposerContextBlock,
    ComposerContextKind,
    ComposerContextSource,
    ComposerCoordinator,
    ComposerExecutionFailure,
    ComposerRealizationContext,
    DeepSeekSceneComposerPort,
    SceneComposerRequest,
    build_deepseek_composer_packet,
    build_deepseek_composer_messages,
    deepseek_composer_response_json_schema,
)
from cera.composer.obligations import (
    ComposerOutputDiagnosticCode,
    build_composer_output_obligations,
)
from cera.evidence import EvidenceFetchRequest
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId
from cera.providers import (
    DeepSeekChatTransport,
    ProviderFailureCallReceipt,
    ProviderFinishReason,
    deepseek_composer_candidate,
)
from cera.serialization import canonical_json, text_sha256, to_primitive
import tests.test_composer as composer_support
from tests.provider_fakes import OfflineDeepSeekChatTransport
from tests.structural_v2_fixtures import (
    composition_draft_v6_from_submission,
)


class FakeHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class RecordingOpener:
    def __init__(self, response_object: dict) -> None:
        self.response_object = response_object
        self.calls: list[tuple[object, int]] = []

    def __call__(self, request, *, timeout):
        self.calls.append((request, timeout))
        payload = {
            "id": "private-provider-request",
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "message": {"content": canonical_json(self.response_object)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 300,
                "completion_tokens": 120,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 300,
            },
        }
        return FakeHTTPResponse(payload)


class DeepSeekSceneComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = composer_support.ComposerTests("runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.coordinator = ComposerCoordinator()

    def request_with_context(self, suffix: str, **kwargs) -> SceneComposerRequest:
        request = self.base.request(suffix, **kwargs)
        citations = request.reasoner_outcome.hard_citations
        batch = self.base.support.service.fetch_evidence(
            request.prepared_turn.evidence_snapshot,
            EvidenceFetchRequest(
                tuple(value.evidence_id for value in citations),
                ("claim", "provenance"),
            ),
        )
        exact_by_id = {value.evidence_id: value for value in batch.exact_records}
        blocks = []
        for character_id in request.selected_npc_ids:
            citation = next(
                value
                for value in citations
                if value.evidence_id in exact_by_id
                and character_id in exact_by_id[value.evidence_id].subject_ids
            )
            exact = exact_by_id[citation.evidence_id]
            blocks.append(
                ComposerContextBlock(
                    context_id=exact.evidence_id,
                    source=ComposerContextSource.EXACT_EVIDENCE,
                    kind=ComposerContextKind.CHARACTER_VOICE,
                    applicable_character_ids=(character_id,),
                    exact_evidence=exact,
                    craft_reference_id=None,
                    craft_text=None,
                    content_class=exact.metadata.content_class,
                )
            )
        context = ComposerRealizationContext(
            selection_policy_version="synthetic-evidence-bound-v1",
            blocks=tuple(blocks),
            explicit_exclusions=("No uncited character facts.",),
        )
        return replace(request, realization_context=context)

    def test_prompt_teaches_exact_protected_user_kind_ownership(self) -> None:
        request = self.request_with_context("protected-user-kind-prompt")
        packet = build_deepseek_composer_packet(request)
        messages = build_deepseek_composer_messages(packet)
        self.assertIn("spoken words remain dialogue", messages[0].content)
        self.assertIn("physical motion remains action", messages[0].content)
        self.assertIn("exactly one authority_id", messages[0].content)
        self.assertIn("source_coverage owns supplied user material", messages[0].content)
        self.assertIn(
            "Do not return protected_user_id or source-unit realization_segments",
            messages[0].content,
        )

    def test_prompt_forbids_relational_protected_user_presuppositions(self) -> None:
        request = self.request_with_context(
            "protected-user-relational-presupposition"
        )
        packet = build_deepseek_composer_packet(request)
        system = build_deepseek_composer_messages(packet)[0].content
        self.assertIn(
            "relational or reciprocal wording",
            system,
        )
        self.assertIn("she looks at him", system)
        self.assertIn("she meets his gaze", system)
        self.assertIn("shared look", system)
        self.assertIn("returned smile", system)
        self.assertIn("Conventional social pleasantries are still factual claims", system)
        self.assertIn("prior trip or commute", system)

    def test_prompt_requires_complete_non_padded_beat_realization(self) -> None:
        request = self.request_with_context("complete-beat-realization-prompt")
        packet = build_deepseek_composer_packet(request)
        system = build_deepseek_composer_messages(packet)[0].content
        self.assertIn("causal scene sequence", system)
        self.assertIn("not as a compressed summary", system)
        self.assertIn("A short source cue does not require a short reply", system)
        self.assertIn("not a paragraph quota", system)
        self.assertIn("Multiple beats may share one story segment", system)
        self.assertIn("stacked interpretive metaphors", system)
        self.assertIn("does not authorize invented biography", system)
        self.assertIn("household roles", system)
        self.assertIn("prior frequency", system)
        self.assertIn("not mere texture", system)
        self.assertIn("Do not turn sequence depth into padding", system)
        self.assertIn("not a prose checklist", system)
        self.assertIn("do not restage", system)
        self.assertIn("off-page activity", system)
        self.assertIn("character_voice block", system)
        self.assertIn("Illustrative lines are voice references, not scripts", system)
        self.assertIn("do not flatten an evidence-rich character", system)
        self.assertIn("scene_development_contract binds realization scope", system)
        self.assertIn("Every materially distinct planned beat", system)

    def response_for(self, request: SceneComposerRequest, suffix: str) -> dict:
        submission = self.base.submission(request, suffix)
        return composition_draft_v6_from_submission(submission)

    def port_for(self, response: dict):
        opener = RecordingOpener(response)
        transport = OfflineDeepSeekChatTransport(
            deepseek_composer_candidate(),
            opener=opener,
            environment={"DEEPSEEK_API_KEY": "dummy"},
            external_provider_boundary=False,
        )
        return DeepSeekSceneComposerPort(transport), opener

    def test_context_is_required_before_any_provider_call(self) -> None:
        request = self.base.request("missing-context")
        port, opener = self.port_for({})
        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.coordinator.execute(request, port)
        self.assertIs(caught.exception.envelope.error_code, ErrorCode.COMPOSER_CONTRACT_INVALID)
        self.assertEqual(caught.exception.envelope.stage, "composer_context")
        self.assertEqual(len(opener.calls), 0)

    def test_packet_is_deterministic_and_evidence_bounded(self) -> None:
        request = self.request_with_context("packet")
        first = build_deepseek_composer_packet(request)
        second = build_deepseek_composer_packet(request)
        self.assertEqual(first, second)
        encoded = canonical_json(first)
        self.assertNotIn(request.request_sha256, encoded)
        self.assertNotIn(
            str(request.prepared_turn.evidence_snapshot.snapshot_token),
            encoded,
        )
        self.assertNotIn(str(request.reasoner_receipt.provider_receipt_id), encoded)
        provider_blocks = first["composition_dto"]["realization_context"]["blocks"]
        for block in request.realization_context.blocks:
            self.assertNotIn(str(block.context_id), encoded)
            if block.exact_evidence is not None:
                self.assertIn(
                    json.loads(block.exact_evidence.sections_json),
                    [value.get("sections") for value in provider_blocks],
                )
                self.assertNotIn(
                    block.exact_evidence.sections_json,
                    [value.get("sections") for value in provider_blocks],
                )
        self.assertTrue(first["authority_policy"]["durable_state_writes_forbidden"])
        visibility = first["composition_dto"]["protected_user_visibility_contract"]
        self.assertEqual(
            visibility["policy"],
            "closed_world_exact_source_or_allowance_only",
        )
        self.assertEqual(
            visibility["rendering_mode"],
            "npc_reaction_reference_only",
        )
        self.assertEqual(visibility["unsupported_detail_policy"], "omit")
        self.assertFalse(visibility["npc_perception_grants_new_ted_fact"])
        self.assertTrue(visibility["authorized_source_units"])
        self.assertIn("staging", visibility["applies_to"])
        obligations = first["output_obligations"]
        self.assertEqual(
            first["composition_dto"]["scene_development_contract"]["mode"],
            "auto",
        )
        self.assertTrue(
            first["composition_dto"]["scene_development_contract"][
                "binding_for_current_reply"
            ]
        )
        self.assertEqual(
            obligations["selected_npc_ids_requiring_realization"],
            [str(value) for value in request.selected_npc_ids],
        )
        self.assertEqual(
            obligations[
                "required_beat_authority_owner_associations"
            ],
            [
                {
                    "authority_id": str(value.beat_id),
                    "owner_id": str(value.actor_id),
                }
                for value in request.reasoner_outcome.decision.current_segment.ordered_beats
            ],
        )
        self.assertEqual(
            obligations["source_coverage"]["mode"],
            "must_be_empty",
        )
        self.assertEqual(obligations["source_coverage"]["values"], [])
        self.assertTrue(
            obligations["source_coverage"][
                "each_returned_entry_requires_one_or_more_existing_segment_keys"
            ]
        )
        self.assertEqual(
            obligations["obligation_sha256"],
            build_composer_output_obligations(
                request
            ).obligation_sha256,
        )
        self.assertEqual(
            first["output_schema"]["properties"]["source_coverage"][
                "maxItems"
            ],
            0,
        )
        self.assertEqual(
            first["output_schema"]["properties"]["specificity_coverage"][
                "maxItems"
            ],
            0,
        )
        self.assertFalse(deepseek_composer_response_json_schema()["additionalProperties"])
        realization_properties = first["output_schema"]["properties"][
            "realization_segments"
        ]["items"]["properties"]
        self.assertNotIn("owner_id", realization_properties)
        self.assertIn("authority_id", realization_properties)

    def test_request_bound_schema_requires_exact_creator_source_count(
        self,
    ) -> None:
        request = self.request_with_context(
            "request-bound-source-schema",
            source_texts=("Synthetic event.",),
            classifications=(composer_support.SourceUnitClassification.EVENT,),
            coverage=True,
        )
        packet = build_deepseek_composer_packet(request)
        source_schema = packet["output_schema"]["properties"][
            "source_coverage"
        ]
        self.assertEqual(source_schema["minItems"], 1)
        self.assertEqual(source_schema["maxItems"], 1)
        self.assertEqual(
            source_schema["items"]["properties"]["source_unit_id"]["enum"],
            [
                str(value.source_unit_id)
                for value in request.source_packet.units
            ],
        )

    def test_valid_ordinary_candidate_is_accepted_once_without_writes(self) -> None:
        request = self.request_with_context("ordinary")
        port, opener = self.port_for(self.response_for(request, "ordinary"))
        before = self.base.support.table_state()
        result = self.coordinator.execute(request, port)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(result.composer_receipt.external_provider_calls, 1)
        self.assertEqual(result.composer_receipt.adapter_role.value, "deepseek_scene_composer")
        self.assertIsNotNone(result.provider_call_receipt)
        self.assertEqual(result.validation_receipt.status, "accepted_in_memory")
        self.assertIsNone(result.accepted_artifact)
        self.assertFalse(result.validation_receipt.story_state_committed)
        self.assertEqual(before, self.base.support.table_state())
        sent = json.loads(opener.calls[0][0].data.decode("utf-8"))
        self.assertEqual(sent["thinking"], {"type": "disabled"})
        self.assertEqual(sent["response_format"], {"type": "json_object"})

    def test_thinking_mode_is_explicit_and_can_be_enabled_for_a_probe(self) -> None:
        request = self.request_with_context("thinking-enabled-probe")
        response = self.response_for(request, "thinking-enabled-probe")
        opener = RecordingOpener(response)
        transport = OfflineDeepSeekChatTransport(
            deepseek_composer_candidate(),
            opener=opener,
            environment={"DEEPSEEK_API_KEY": "dummy"},
            external_provider_boundary=False,
        )
        self.coordinator.execute(
            request,
            DeepSeekSceneComposerPort(transport, thinking_enabled=True),
        )
        sent = json.loads(opener.calls[0][0].data.decode("utf-8"))
        self.assertEqual(sent["thinking"], {"type": "enabled"})

    def test_adapter_evidence_distinguishes_thinking_modes(self) -> None:
        request = self.request_with_context("thinking-evidence")
        response = self.response_for(request, "thinking-evidence")
        disabled_opener = RecordingOpener(response)
        enabled_opener = RecordingOpener(response)
        disabled = DeepSeekSceneComposerPort(
            OfflineDeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=disabled_opener,
                environment={"DEEPSEEK_API_KEY": "dummy"},
                external_provider_boundary=False,
            )
        ).compose(request)
        enabled = DeepSeekSceneComposerPort(
            OfflineDeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=enabled_opener,
                environment={"DEEPSEEK_API_KEY": "dummy"},
                external_provider_boundary=False,
            ),
            thinking_enabled=True,
        ).compose(request)
        self.assertNotEqual(
            disabled.adapter_evidence_sha256,
            enabled.adapter_evidence_sha256,
        )
        self.assertTrue(disabled.adapter_evidence_id.endswith("thinking_disabled"))
        self.assertTrue(enabled.adapter_evidence_id.endswith("thinking_enabled"))

    def test_adapter_binds_canonical_obligation_hash_and_divergence_is_internal(
        self,
    ) -> None:
        request = self.request_with_context("obligation-binding")
        port, opener = self.port_for(
            self.response_for(request, "obligation-binding")
        )
        call = port.compose(request)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(
            call.validated_obligation_sha256,
            build_composer_output_obligations(
                request
            ).obligation_sha256,
        )

        bad_candidate = replace(
            call.submission.candidate,
            complete_core=False,
        )
        bad_call = replace(
            call,
            submission=replace(
                call.submission,
                candidate=bad_candidate,
            ),
        )

        class BoundPort:
            def compose(self, _request):
                return bad_call

        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.coordinator.execute(request, BoundPort())
        self.assertIs(
            caught.exception.envelope.error_code,
            ErrorCode.INTERNAL_CONTRACT_INVARIANT_FAILED,
        )
        self.assertEqual(
            caught.exception.safe_diagnostics,
            ("COMPLETE_CORE_ATOMIC_DIVERGENCE",),
        )

    def test_v6_maps_source_ranges_and_python_derives_owner(self) -> None:
        request = self.request_with_context(
            "v4-multi-source-coverage",
            source_texts=("Synthetic event.",),
            classifications=(composer_support.SourceUnitClassification.EVENT,),
            coverage=True,
        )
        decision = request.reasoner_outcome.decision
        assert decision is not None
        source_unit = request.source_packet.units[0]
        response = {
            "schema_version": "cera.deepseek_composition_draft.v6",
            "story_segments": [
                {"segment_key": "source_open", "text": "The supplied event begins."},
                {"segment_key": "response", "text": "The selected responder reacts visibly."},
                {"segment_key": "source_close", "text": "The supplied event reaches its stated stopping point."},
            ],
            "source_coverage": [
                {
                    "source_unit_id": str(source_unit.source_unit_id),
                    "segment_keys": ["source_open", "source_close"],
                }
            ],
            "realization_segments": [
                {
                    "kind": "action",
                    "segment_key": "response",
                    "authority_id": str(
                        decision.current_segment.ordered_beats[0].beat_id
                    ),
                }
            ],
            "specificity_coverage": [],
            "terminal_segment_key": "source_close",
        }
        port, opener = self.port_for(response)
        result = self.coordinator.execute(request, port)
        ranges = result.manifest.source_unit_coverage[0].ranges
        self.assertEqual(len(ranges), 2)
        self.assertLess(ranges[0].end, ranges[1].start)
        self.assertEqual(len(opener.calls), 1)

        duplicate = json.loads(json.dumps(response))
        duplicate["source_coverage"][0]["segment_keys"] = [
            "source_open",
            "source_open",
        ]
        bad_port, bad_opener = self.port_for(duplicate)
        with self.assertRaises(ComposerExecutionFailure):
            self.coordinator.execute(request, bad_port)
        self.assertEqual(len(bad_opener.calls), 1)

    def test_python_discards_redundant_protected_user_realization_bookkeeping(self) -> None:
        request = self.request_with_context(
            "python-owned-protected-kind",
            source_texts=('Ted says, "Hello."',),
            coverage=True,
        )
        source_unit = request.source_packet.units[0]
        request = replace(
            request,
            source_packet=replace(
                request.source_packet,
                ordinary_units=(
                    replace(
                        source_unit,
                        protected_user_allowed_kinds=(
                            composer_support.RealizationKind.DIALOGUE,
                        ),
                    ),
                ),
            ),
        )
        decision = request.reasoner_outcome.decision
        assert decision is not None
        response = {
            "schema_version": "cera.deepseek_composition_draft.v6",
            "story_segments": [
                {"segment_key": "source", "text": 'Ted says, "Hello."'},
                {
                    "segment_key": "answer",
                    "text": "The selected responder answers him.",
                },
            ],
            "source_coverage": [
                {
                    "source_unit_id": str(source_unit.source_unit_id),
                    "segment_keys": ["source"],
                }
            ],
            "realization_segments": [
                {
                    "kind": "reaction",
                    "segment_key": "source",
                    "authority_id": str(source_unit.source_unit_id),
                },
                {
                    "kind": "action",
                    "segment_key": "answer",
                    "authority_id": str(
                        decision.current_segment.ordered_beats[0].beat_id
                    ),
                },
            ],
            "specificity_coverage": [],
            "terminal_segment_key": "answer",
        }
        port, opener = self.port_for(response)
        result = self.coordinator.execute(request, port)
        self.assertFalse(
            any(
                value.owner_id
                == request.prepared_turn.request.protected_user_id
                for value in result.manifest.character_spans
            )
        )
        self.assertEqual(
            result.manifest.source_unit_coverage[0].source_unit_id,
            source_unit.source_unit_id,
        )
        self.assertEqual(len(opener.calls), 1)

        missing_authority = json.loads(json.dumps(response))
        del missing_authority["realization_segments"][0]["authority_id"]
        missing_port, missing_opener = self.port_for(missing_authority)
        with self.assertRaises(ComposerExecutionFailure):
            self.coordinator.execute(request, missing_port)
        self.assertEqual(len(missing_opener.calls), 1)

    def test_valid_multi_character_candidate_preserves_cast(self) -> None:
        request = self.request_with_context(
            "multi",
            responders=(self.base.alpha, self.base.beta),
        )
        port, opener = self.port_for(self.response_for(request, "multi"))
        result = self.coordinator.execute(request, port)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(
            {value.character_id for value in result.manifest.participant_realizations},
            {self.base.alpha, self.base.beta},
        )

    def test_python_normalizes_exact_duplicate_realization_bookkeeping(self) -> None:
        request = self.request_with_context("duplicate-realization-normalization")
        response = self.response_for(request, "duplicate-realization-normalization")
        response["realization_segments"].append(
            dict(response["realization_segments"][0])
        )
        port, opener = self.port_for(response)
        result = self.coordinator.execute(request, port)
        self.assertEqual(len(opener.calls), 1)
        self.assertEqual(
            len(result.manifest.character_spans),
            len({
                (
                    value.owner_id,
                    value.kind,
                    value.start,
                    value.end,
                    value.source_unit_id,
                    value.beat_id,
                )
                for value in result.manifest.character_spans
            }),
        )

    def test_segment_references_become_python_offsets_and_bad_reference_fails_once(self) -> None:
        request = self.request_with_context(
            "coverage",
            source_texts=("Synthetic event.",),
            classifications=(composer_support.SourceUnitClassification.EVENT,),
            coverage=True,
        )
        response = self.response_for(request, "coverage")
        port, opener = self.port_for(response)
        result = self.coordinator.execute(request, port)
        span = result.manifest.source_unit_coverage[0]
        self.assertEqual(
            result.candidate.story_text[span.start : span.end],
            response["story_segments"][0]["text"],
        )
        bad = json.loads(json.dumps(response))
        bad["source_coverage"][0]["segment_keys"] = ["absent_segment"]
        bad_port, bad_opener = self.port_for(bad)
        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.coordinator.execute(request, bad_port)
        self.assertIs(caught.exception.envelope.error_code, ErrorCode.COMPOSER_CONTRACT_INVALID)
        self.assertIsNotNone(caught.exception.provider_call_receipt)
        self.assertFalse(caught.exception.provider_call_receipt.retains_prompt)
        self.assertFalse(
            caught.exception.provider_call_receipt.retains_story_prose
        )
        self.assertEqual(len(bad_opener.calls), 1)

    def test_python_rejects_provider_drift_after_one_call(self) -> None:
        request = self.request_with_context("drift")
        base = self.response_for(request, "drift")
        cases = []
        wrong_owner = json.loads(json.dumps(base))
        wrong_owner["realization_segments"][0]["owner_id"] = str(
            self.base.beta
        )
        cases.append(wrong_owner)
        unknown_beat = json.loads(json.dumps(base))
        unknown_beat["realization_segments"][0][
            "authority_id"
        ] = "beat:unknown"
        cases.append(unknown_beat)
        wrong_beats = json.loads(json.dumps(base))
        wrong_beats["realization_segments"] = []
        cases.append(wrong_beats)
        for index, response in enumerate(cases):
            with self.subTest(index=index):
                port, opener = self.port_for(response)
                with self.assertRaises(ComposerExecutionFailure):
                    self.coordinator.execute(request, port)
                self.assertEqual(len(opener.calls), 1)

    def test_provider_set_drift_returns_field_level_safe_diagnostic(
        self,
    ) -> None:
        request = self.request_with_context("set-drift-diagnostic")
        decision = request.reasoner_outcome.decision
        assert decision is not None
        response = {
            "schema_version": "cera.deepseek_composition_draft.v6",
            "story_segments": [
                {
                    "segment_key": "mia_response",
                    "text": "The selected responder answers the question.",
                }
            ],
            "source_coverage": [],
            "realization_segments": [
                {
                    "kind": "dialogue",
                    "segment_key": "mia_response",
                    "authority_id": str(
                        decision.current_segment.ordered_beats[0].beat_id
                    ),
                }
            ],
            "specificity_coverage": [],
            "terminal_segment_key": "mia_response",
        }
        response["source_coverage"] = [
            {
                "source_unit_id": str(
                    request.source_packet.units[0].source_unit_id
                ),
                "segment_keys": [
                    response["story_segments"][0]["segment_key"]
                ],
            }
        ]
        port, opener = self.port_for(response)
        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.coordinator.execute(request, port)
        self.assertIn(
            ComposerOutputDiagnosticCode.SOURCE_COVERAGE_NOT_EMPTY.value,
            caught.exception.safe_diagnostics,
        )
        self.assertNotIn(
            response["story_segments"][0]["text"],
            caught.exception.envelope.message,
        )
        self.assertEqual(len(opener.calls), 1)

    def test_provider_failure_is_explicit_without_retry_or_fallback(self) -> None:
        request = self.request_with_context("unavailable")
        calls = 0

        def fail(_request, *, timeout):
            nonlocal calls
            calls += 1
            raise urllib.error.URLError("offline")

        transport = OfflineDeepSeekChatTransport(
            deepseek_composer_candidate(),
            opener=fail,
            environment={"DEEPSEEK_API_KEY": "dummy"},
            external_provider_boundary=False,
        )
        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.coordinator.execute(request, DeepSeekSceneComposerPort(transport))
        self.assertIs(caught.exception.envelope.error_code, ErrorCode.COMPOSER_UNAVAILABLE)
        self.assertEqual(calls, 1)
        self.assertEqual(caught.exception.external_provider_calls_observed, 1)

    def test_incomplete_provider_completion_retains_safe_failure_receipt(self) -> None:
        request = self.request_with_context("incomplete-completion")

        def incomplete(_request, *, timeout):
            del timeout
            return FakeHTTPResponse(
                {
                    "id": "private-incomplete-id",
                    "model": "deepseek-v4-flash",
                    "choices": [
                        {
                            "message": {"content": '{"schema_version":'},
                            "finish_reason": "length",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 500,
                        "completion_tokens": 32768,
                        "prompt_cache_hit_tokens": 100,
                        "prompt_cache_miss_tokens": 400,
                        "completion_tokens_details": {
                            "reasoning_tokens": 32000
                        },
                    },
                }
            )

        port = DeepSeekSceneComposerPort(
            OfflineDeepSeekChatTransport(
                deepseek_composer_candidate(),
                opener=incomplete,
                environment={"DEEPSEEK_API_KEY": "dummy"},
                external_provider_boundary=False,
            )
        )
        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.coordinator.execute(request, port)
        failure = caught.exception
        self.assertEqual(failure.external_provider_calls_observed, 1)
        self.assertEqual(
            failure.safe_diagnostics,
            ("DEEPSEEK_FINISH_REASON_LENGTH",),
        )
        self.assertIsInstance(
            failure.provider_call_receipt,
            ProviderFailureCallReceipt,
        )
        self.assertIs(
            failure.provider_call_receipt.finish_reason,
            ProviderFinishReason.LENGTH,
        )

    def test_adult_packet_is_composer_only_and_ordinary_rejects_protected_craft(self) -> None:
        adult = self.request_with_context("adult", adult=True)
        packet = build_deepseek_composer_packet(adult)
        encoded = canonical_json(packet)
        self.assertIn(adult.source_packet.units[0].exact_text, encoded)
        self.assertNotIn(
            adult.source_packet.units[0].exact_text,
            canonical_json(to_primitive(adult.reasoner_outcome)),
        )

        ordinary = self.request_with_context("ordinary-craft")
        craft_id = TypedId(IdKind.CRAFT_REFERENCE, "protected-craft")
        craft = ComposerContextBlock(
            context_id=craft_id,
            source=ComposerContextSource.CREATOR_CRAFT,
            kind=ComposerContextKind.CRAFT_REFERENCE,
            applicable_character_ids=ordinary.selected_npc_ids,
            exact_evidence=None,
            craft_reference_id=craft_id,
            craft_text="Synthetic craft technique only.",
            content_class="protected_adult",
        )
        context = replace(
            ordinary.realization_context,
            blocks=(*ordinary.realization_context.blocks, craft),
        )
        with self.assertRaises(ContractValidationError):
            replace(ordinary, realization_context=context)

    def test_unknown_provider_fields_are_rejected_after_one_call(self) -> None:
        request = self.request_with_context("unknown")
        response = self.response_for(request, "unknown")
        response["unexpected"] = True
        port, opener = self.port_for(response)
        with self.assertRaises(ComposerExecutionFailure) as caught:
            self.coordinator.execute(request, port)
        self.assertIs(caught.exception.envelope.error_code, ErrorCode.COMPOSER_CONTRACT_INVALID)
        self.assertEqual(len(opener.calls), 1)


if __name__ == "__main__":
    unittest.main()
