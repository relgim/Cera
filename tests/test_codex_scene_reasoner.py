from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from cera.contracts import (
    CurrentSegment,
    SceneDecision,
    SceneDepthMode,
    SequenceBeat,
)
from cera.adult_craft.json_schema import PROVIDER_BINDING_PLACEHOLDER
from cera.errors import ContractValidationError, ErrorCode
from cera.ids import IdKind
from cera.providers import (
    CodexMcpRuntimeBinding,
    CodexSDKTransport,
    CodexWorkerResult,
    codex_reasoner_candidate,
)
from cera.reasoner import (
    CODEX_REASONER_ADAPTER_VERSION,
    CODEX_REASONER_PACKET_VERSION,
    ENABLED_MCP_EVIDENCE_TOOLS,
    MCP_SERVER_NAME,
    CodexSceneReasonerPort,
    CodexReasonerDraftV4,
    McpEvidenceDispatcher,
    McpEvidenceToolName,
    ReasonerAdapterRole,
    ReasonerEvidenceTools,
    ReasonerExecutionFailure,
    ReasonerOutcome,
    ReasonerOutcomeStatus,
    ReasonerSourceMode,
    RequestBoundMcpEvidenceBridge,
    build_codex_reasoner_packet,
    build_codex_reasoner_prompt,
    reasoner_outcome_json_schema,
)
from cera.reasoner.codex import (
    _bind_python_owned_adult_fields,
    _normalize_provider_structural_fields,
    _resolve_provider_evidence_aliases,
)
from cera.reasoner.drafts import safe_reasoner_contract_diagnostics
from cera.registry import build_schema_registry
from cera.schema import from_mapping
from cera.serialization import canonical_json, to_primitive
import tests.test_adult_route as adult_test_support
import tests.test_reasoner as reasoner_test_support
from tests.structural_v2_fixtures import reasoner_draft_from_outcome


ident = reasoner_test_support.ident


class StaticReasonerRunner:
    def __init__(
        self,
        outcome: ReasonerOutcome,
        *,
        tool_names: tuple[str, ...] = (),
    ) -> None:
        self.authoritative_fixture = outcome
        self.output_text = canonical_json(reasoner_draft_from_outcome(outcome))
        self.tool_names = tool_names
        self.calls = 0
        self.last_prompt: str | None = None
        self.last_schema: dict | None = None
        self.last_binding: CodexMcpRuntimeBinding | None = None

    def run(self, *, route, prompt, output_schema, workspace, mcp_binding):
        self.calls += 1
        self.last_prompt = prompt
        self.last_schema = output_schema
        self.last_binding = mcp_binding
        return CodexWorkerResult(
            output_text=self.output_text,
            provider_request_id=f"private-codex-{self.calls}",
            returned_model=route.model_name,
            duration_ms=25,
            input_tokens=500,
            cached_input_tokens=0,
            output_tokens=250,
            reasoning_output_tokens=20,
            transport_version=route.transport_version,
            mcp_server_names=tuple(MCP_SERVER_NAME for _ in self.tool_names),
            mcp_tool_names=self.tool_names,
            mcp_tool_call_count=len(self.tool_names),
            mcp_failed_tool_call_count=0,
            pre_registered_turn_count=1,
        )


class FailingReasonerRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, **_kwargs):
        self.calls += 1
        raise RuntimeError("private provider failure")


class OfflineEvidenceBridge:
    """No-server test bridge that exercises the real dispatcher and receipt logic."""

    def __init__(self, tools, request, script) -> None:
        self.inner = RequestBoundMcpEvidenceBridge(
            tools,
            reasoner_request_sha256=request.request_sha256,
            snapshot=request.prepared_turn.evidence_snapshot,
            minimum_tool_calls=0,
            maximum_tool_calls=12,
        )
        self.script = script

    @property
    def runtime_binding(self) -> CodexMcpRuntimeBinding:
        return CodexMcpRuntimeBinding(
            server_name=MCP_SERVER_NAME,
            url="http://127.0.0.1:43124/mcp",
            bearer_token_environment_variable="CERA_REQUEST_EVIDENCE_TOKEN",
            bearer_token="offline-test-secret",
            enabled_tools=ENABLED_MCP_EVIDENCE_TOOLS,
            binding_sha256=self.inner.dispatcher.bridge_binding_sha256,
            minimum_tool_calls=0,
            maximum_tool_calls=12,
        )

    def finalize(self, provider_result):
        return self.inner.finalize(provider_result)

    def __enter__(self):
        for tool_name, payload in self.script:
            self.inner.dispatcher.invoke(tool_name, payload)
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None


class RecordingBridgeFactory:
    def __init__(self, script=()) -> None:
        self.script = script
        self.instances: list[OfflineEvidenceBridge] = []

    def __call__(self, tools, request):
        bridge = OfflineEvidenceBridge(tools, request, self.script)
        self.instances.append(bridge)
        return bridge


class CodexSceneReasonerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.support = reasoner_test_support.ReasonerTests("runTest")
        self.support.setUp()
        self.addCleanup(self.support.doCleanups)
        self.temporary = tempfile.TemporaryDirectory(prefix="cera_reasoner_adapter_")
        self.addCleanup(self.temporary.cleanup)

    def port(self, outcome, *, script=(), tool_names=()):
        runner = StaticReasonerRunner(outcome, tool_names=tool_names)
        transport = CodexSDKTransport(
            codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
            workspace=Path(self.temporary.name),
            runner=runner,
        )
        factory = RecordingBridgeFactory(script)
        return CodexSceneReasonerPort(
            transport,
            bridge_factory=factory,
        ), runner, factory

    def test_packet_schema_and_prompt_are_deterministic_closed_and_source_safe(self) -> None:
        prepared = self.support.prepared("packet")
        request = self.support.request(prepared)
        first = build_codex_reasoner_packet(request)
        second = build_codex_reasoner_packet(request)
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], CODEX_REASONER_PACKET_VERSION)
        packet_json = canonical_json(first)
        self.assertNotIn("raw_source_text", packet_json)
        self.assertNotIn(request.request_sha256, packet_json)
        self.assertNotIn(
            str(request.prepared_turn.evidence_snapshot.snapshot_token),
            packet_json,
        )

        schema = reasoner_outcome_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("uniqueItems", canonical_json(schema))
        self.assertEqual(schema["properties"]["status"]["type"], "string")
        self.assertEqual(
            schema["properties"]["protected_user_boundary_acknowledged"]["type"],
            "boolean",
        )
        self.assertNotIn("$defs", schema)
        self.assertNotIn(
            "aftermath",
            canonical_json(schema["properties"]["route"]),
        )
        self.assertIn("adult_craft_need", schema["properties"])
        adult_schema = schema["properties"]["adult_craft_need"]["anyOf"][0]
        self.assertNotIn("craft_need_id", adult_schema["properties"])
        self.assertIn("beat_requirements", adult_schema["properties"])
        prompt = build_codex_reasoner_prompt(first)
        self.assertIn("Search results are references", prompt)
        self.assertIn("expands one record per call", prompt)
        self.assertIn("record_types is optional", prompt)
        self.assertIn("Python owns ambiguity handling", prompt)
        self.assertIn("do not supply an ambiguity_policy field", prompt)
        self.assertEqual(
            first["tool_policy"]["maximum_followup_search_operations"],
            4,
        )
        self.assertEqual(
            set(first["tool_policy"]["shared_search_budget_operations"]),
            {
                "cera_resolve_entities",
                "cera_search_evidence",
                "cera_search_query_plan",
            },
        )
        self.assertIn("share exactly four search operations", prompt)
        self.assertIn("remaining_followup_searches is zero", prompt)
        self.assertIn("one or two discriminative concepts per set", prompt)
        self.assertIn("Once exact fetched evidence is sufficient", prompt)
        self.assertIn("Never author", prompt)
        self.assertIn("relational or reciprocal wording", prompt)
        self.assertIn("action_direction", prompt)
        self.assertIn("NPC-controlled half", prompt)
        self.assertIn("classify every materially relevant exact source span", prompt)
        self.assertIn("Own the causal sequence", prompt)
        self.assertIn("binding current-reply scope obligation", prompt)
        self.assertIn("default to developed ordinary scope", prompt)
        self.assertIn("move beyond the floor owner's first complete answer", prompt)
        self.assertIn("materially different supported follow-through", prompt)
        self.assertIn("never transfers knowledge between characters", prompt)
        self.assertIn("owner_private evidence may support only its exact owner", prompt)
        self.assertIn("concrete existing world information", prompt)
        self.assertIn("never ask the Composer to fill factual substance", prompt)
        self.assertIn("Do not use a fixed beat count", prompt)
        self.assertIn("A short user cue does not imply a one-beat reply", prompt)
        self.assertIn("meaningful user-relevant boundary after that sequence", prompt)
        self.assertIn("Do not stop simply because Ted could respond earlier", prompt)
        self.assertIn("WriterScaffold", prompt)
        self.assertIn("creative space DeepSeek must retain", prompt)
        self.assertIn("For a substantive cue", prompt)
        self.assertIn("include them as ordered event_blocks", prompt)
        self.assertIn("continue_beyond_prompt_endpoint", prompt)
        self.assertIn("NON-READY RESET IS MANDATORY", prompt)
        self.assertIn("Never preserve source classification", prompt)
        self.assertIn(CODEX_REASONER_PACKET_VERSION, prompt)
        self.assertEqual(
            first["scene_development_contract"]["scope_obligation"],
            "adaptive_complete_causal_reply",
        )

        epic = build_codex_reasoner_packet(
            replace(request, scene_depth_mode=SceneDepthMode.EPIC)
        )
        self.assertEqual(
            epic["scene_development_contract"]["scope_obligation"],
            "all_materially_distinct_supported_progression",
        )
        self.assertTrue(
            epic["scene_development_contract"]["binding_for_current_reply"]
        )
        self.assertIn(
            "first natural handoff",
            epic["scene_development_contract"]["instruction"],
        )

    def test_active_v4_provider_payload_runs_inherited_domain_validation(self) -> None:
        payload = {
            "schema_version": "cera.codex_reasoner_draft.v4",
            "status": "insufficient_evidence",
            "route": None,
            "scene_intent": None,
            "responding_npc_ids": [],
            "floor_owner_id": None,
            "participation": [],
            "character_moves": [],
            "current_beats": [],
            "stop_before": None,
            "future_segments": [],
            "writer_must_preserve": [],
            "uncertainties": [],
            "prohibited_inferences": [],
            "insufficiencies": ["probe has no story evidence"],
            "blocker_code": None,
            "adult_craft_need": None,
            "protected_user_boundary_acknowledged": True,
            "protected_user_source_claims": [],
        }
        draft = from_mapping(CodexReasonerDraftV4, payload)
        self.assertEqual(draft.status, ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(draft.protected_user_source_claims, ())

    def test_reasoner_contract_diagnostics_are_value_free_and_field_specific(self) -> None:
        evidence_failure = ContractValidationError(
            "behavioral reasoner draft cites evidence that was never exactly authorized"
        )
        self.assertEqual(
            safe_reasoner_contract_diagnostics(evidence_failure),
            ("evidence_ids:not_exactly_authorized",),
        )

        private_value = "private-provider-story-value"
        unknown_failure = ContractValidationError(
            f"unexpected provider output containing {private_value}"
        )
        diagnostics = safe_reasoner_contract_diagnostics(unknown_failure)
        self.assertEqual(
            diagnostics,
            ("reasoner_output:domain_contract_violation",),
        )
        self.assertNotIn(private_value, canonical_json(diagnostics))

    def test_pre_dispatch_mcp_failure_retains_value_free_field_diagnostic(self) -> None:
        prepared = self.support.prepared("pre-dispatch-diagnostic")
        request = self.support.request(prepared)
        dispatcher = McpEvidenceDispatcher(
            ReasonerEvidenceTools(self.support.service, request),
            reasoner_request_sha256=request.request_sha256,
            snapshot=prepared.evidence_snapshot,
        )

        class FrameworkValidationError(Exception):
            @staticmethod
            def errors():
                return [
                    {
                        "loc": ("evidence_ids",),
                        "type": "missing",
                        "input": "private-value-must-not-survive",
                    }
                ]

        dispatcher.record_pre_dispatch_failure(
            "cera_fetch_evidence",
            {"unexpected": "private-value-must-not-survive"},
            FrameworkValidationError(),
        )
        receipt = dispatcher.calls[0]
        self.assertFalse(receipt.success)
        self.assertEqual(receipt.error_field_path, "input.evidence_ids")
        self.assertEqual(receipt.error_reason, "missing")
        self.assertNotIn("private-value", canonical_json(to_primitive(receipt)))

    def test_python_binds_adult_craft_need_to_decoded_sequence(self) -> None:
        prepared = self.support.prepared("codex-adult-binding")
        request = self.support.request(prepared, aware=(self.support.alpha,))
        evidence_id = self.support.evidence_id(prepared, "record-alpha-present")
        decision = self.support.decision(
            "codex-adult-binding",
            responders=(self.support.alpha,),
            evidence_by_character={self.support.alpha: evidence_id},
            route=reasoner_test_support.DecisionRoute.CONSENT_VALID_ADULT,
        )
        outcome = self.support.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 1)
            },
        )
        payload = to_primitive(outcome)
        payload["adult_craft_need"] = {
            "schema_version": "cera.adult_craft_need.v1",
            "craft_need_id": PROVIDER_BINDING_PLACEHOLDER,
            "request_id": PROVIDER_BINDING_PLACEHOLDER,
            "decision_id": PROVIDER_BINDING_PLACEHOLDER,
            "sequence_plan_sha256": PROVIDER_BINDING_PLACEHOLDER,
            "mode": "adult_on",
            "families": ["general"],
            "subfamilies": ["consent-valid intimacy"],
            "axes": ["direct_vocabulary"],
            "channel_needs": [
                {
                    "channel": "narration",
                    "character_id": None,
                    "minimum_register": "direct",
                    "required_concepts": ["anatomy"],
                }
            ],
            "beat_needs": [
                {
                    "beat_id": str(decision.current_segment.ordered_beats[0].beat_id),
                    "actor_id": str(self.support.alpha),
                    "action_family": "validated_current_beat",
                    "object_concepts": ["anatomy"],
                    "axes": ["direct_vocabulary"],
                    "channels": ["narration"],
                    "priority": 3,
                }
            ],
            "climax": {
                "authority_state": "allowed",
                "current_segment_commitment": "not_selected",
            },
            "aftermath": {
                "authority_state": "allowed",
                "current_segment_commitment": "not_selected",
            },
            "character_card_sections": [
                {
                    "character_id": str(self.support.alpha),
                    "section_queries": ["voice", "boundaries"],
                }
            ],
            "all_participants_confirmed_adults": True,
            "consent_valid_for_current_segment": True,
            "blocked_nonconsensual_generation_excluded": True,
        }
        bound = _bind_python_owned_adult_fields(request, payload)
        decoded = reasoner_test_support.from_mapping(ReasonerOutcome, bound)
        self.assertEqual(decoded.adult_craft_need.request_id, prepared.request.request_id)
        self.assertEqual(decoded.adult_craft_need.decision_id, decision.decision_id)
        self.assertNotEqual(
            decoded.adult_craft_need.sequence_plan_sha256,
            PROVIDER_BINDING_PLACEHOLDER,
        )

        untrusted = to_primitive(outcome)
        untrusted["adult_craft_need"] = dict(payload["adult_craft_need"])
        untrusted["adult_craft_need"]["sequence_plan_sha256"] = "0" * 64
        with self.assertRaisesRegex(ContractValidationError, "Python-owned field"):
            _bind_python_owned_adult_fields(request, untrusted)

    def test_python_canonicalizes_only_the_selected_leads_redundant_reason(self) -> None:
        prepared = self.support.prepared("codex-lead-normalization")
        evidence_id = self.support.evidence_id(prepared, "record-alpha-present")
        decision = self.support.decision(
            "codex-lead-normalization",
            responders=(self.support.alpha,),
            evidence_by_character={self.support.alpha: evidence_id},
        )
        outcome = self.support.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 1)
            },
        )
        payload = to_primitive(outcome)
        payload["participation"][0]["intervention_reason"] = "direct_stake"
        normalized = _normalize_provider_structural_fields(payload)
        self.assertEqual(
            normalized["participation"][0]["intervention_reason"],
            "floor_owner",
        )
        self.assertEqual(from_mapping(ReasonerOutcome, normalized), outcome)

    def test_ordinary_search_fetch_reaches_existing_python_validation(self) -> None:
        prepared = self.support.prepared("codex-retrieval")
        request = self.support.request(prepared, aware=(self.support.alpha,))
        evidence_id = self.support.evidence_id(prepared, "record-alpha-present")
        decision = self.support.decision(
            "codex-retrieval",
            responders=(self.support.alpha,),
            evidence_by_character={self.support.alpha: evidence_id},
        )
        outcome = self.support.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 1)
            },
        )
        script = (
            (
                McpEvidenceToolName.SEARCH_EVIDENCE.value,
                {"terms": ["present"], "limit": 1},
            ),
            (
                McpEvidenceToolName.FETCH_EVIDENCE.value,
                {
                    "evidence_ids": [str(evidence_id)],
                    "sections": ["claim", "provenance"],
                    "include_superseded_audit": False,
                },
            ),
        )
        tool_names = tuple(value[0] for value in script)
        port, runner, factory = self.port(
            outcome, script=script, tool_names=tool_names
        )
        before = self.support.table_state()
        result = self.support.coordinator.execute(request, port)
        self.assertEqual(before, self.support.table_state())
        self.assertEqual(result.outcome.status, outcome.status)
        self.assertEqual(
            result.outcome.decision.responding_npc_ids,
            outcome.decision.responding_npc_ids,
        )
        self.assertIs(result.receipt.adapter_role, ReasonerAdapterRole.CODEX)
        self.assertEqual(result.receipt.adapter_version, CODEX_REASONER_ADAPTER_VERSION)
        self.assertEqual(result.receipt.external_provider_calls, 1)
        self.assertEqual(result.receipt.tool_call_count, 2)
        self.assertEqual(len(result.receipt.evidence_lookup_receipt_ids), 2)
        self.assertIs(result.receipt.bridge_receipt_id.kind, IdKind.MCP_BRIDGE_RECEIPT)
        self.assertIsNotNone(result.provider_call_receipt)
        self.assertIsNotNone(result.mcp_bridge_receipt)
        self.assertEqual(
            result.provider_call_receipt.provider_receipt_id,
            result.receipt.provider_receipt_id,
        )
        self.assertEqual(
            result.mcp_bridge_receipt.bridge_receipt_id,
            result.receipt.bridge_receipt_id,
        )
        self.assertEqual(runner.calls, 1)
        self.assertEqual(len(factory.instances), 1)
        rendered_schema = canonical_json(runner.last_schema)
        self.assertIn("evidence:fetch_001", rendered_schema)
        self.assertNotIn(str(evidence_id), rendered_schema)
        self.assertNotIn("offline-test-secret", repr(result.receipt))
        self.assertEqual(
            build_schema_registry().decode(to_primitive(result.receipt)),
            result.receipt,
        )

    def test_request_local_evidence_aliases_resolve_without_guessing(self) -> None:
        canonical = ident(IdKind.EVIDENCE, "alias-canonical")
        payload = {
            "participation": [
                {"evidence_ids": ["evidence:seed_001"]},
            ],
            "character_moves": [
                {"evidence_ids": ["evidence:fetch_001"]},
            ],
            "event_blocks": [
                {"evidence_ids": [str(canonical)]},
            ],
            "development_atoms": [
                {"evidence_ids": ["evidence:fetch_999"]},
            ],
        }
        resolved = _resolve_provider_evidence_aliases(
            payload,
            alias_map={
                "evidence:seed_001": canonical,
                "evidence:fetch_001": canonical,
            },
            authorized_evidence_ids={canonical},
        )
        self.assertEqual(
            resolved["participation"][0]["evidence_ids"], [str(canonical)]
        )
        self.assertEqual(
            resolved["character_moves"][0]["evidence_ids"], [str(canonical)]
        )
        self.assertEqual(
            resolved["event_blocks"][0]["evidence_ids"], [str(canonical)]
        )
        self.assertEqual(
            resolved["development_atoms"][0]["evidence_ids"],
            ["evidence:fetch_999"],
        )
        self.assertEqual(
            payload["participation"][0]["evidence_ids"],
            ["evidence:seed_001"],
        )

    def test_multi_character_decision_survives_typed_adapter_and_validation(self) -> None:
        prepared = self.support.prepared(
            "codex-multi",
            responders=(self.support.alpha, self.support.beta),
        )
        request = self.support.request(
            prepared,
            aware=(self.support.alpha, self.support.beta),
        )
        alpha_evidence = self.support.evidence_id(prepared, "record-alpha-present")
        beta_evidence = self.support.evidence_id(prepared, "record-beta-private")
        decision = self.support.decision(
            "codex-multi",
            responders=(self.support.alpha, self.support.beta),
            evidence_by_character={
                self.support.alpha: alpha_evidence,
                self.support.beta: beta_evidence,
            },
            floor_owner=self.support.alpha,
        )
        outcome = self.support.ready_outcome(
            decision,
            record_by_evidence={
                alpha_evidence: (ident(IdKind.RECORD, "record-alpha-present"), 1),
                beta_evidence: (ident(IdKind.RECORD, "record-beta-private"), 1),
            },
        )
        script = (
            (
                McpEvidenceToolName.FETCH_EVIDENCE.value,
                {
                    "evidence_ids": [str(alpha_evidence), str(beta_evidence)],
                    "sections": ["claim", "knowledge"],
                    "include_superseded_audit": False,
                },
            ),
        )
        port, runner, _ = self.port(
            outcome,
            script=script,
            tool_names=(McpEvidenceToolName.FETCH_EVIDENCE.value,),
        )
        result = self.support.coordinator.execute(request, port)
        self.assertEqual(result.outcome.decision.responding_npc_ids, (
            self.support.alpha,
            self.support.beta,
        ))
        self.assertEqual(result.outcome.decision.floor_owner_id, self.support.alpha)
        self.assertEqual(result.receipt.tool_call_count, 1)
        self.assertEqual(runner.calls, 1)

    def test_python_rejects_protected_user_beat_from_typed_codex_output(self) -> None:
        prepared = self.support.prepared("codex-protected")
        request = self.support.request(prepared, aware=(self.support.alpha,))
        evidence_id = self.support.evidence_id(prepared, "record-alpha-present")
        decision = self.support.decision(
            "codex-protected",
            responders=(self.support.alpha,),
            evidence_by_character={self.support.alpha: evidence_id},
        )
        original = decision.current_segment.ordered_beats[0]
        invalid_beat = SequenceBeat(
            beat_id=original.beat_id,
            actor_id=self.support.ted,
            state=original.state,
            neutral_event=original.neutral_event,
            evidence_ids=original.evidence_ids,
        )
        invalid_decision = replace(
            decision,
            current_segment=CurrentSegment(
                segment_id=decision.current_segment.segment_id,
                ordered_beats=(invalid_beat,),
                stop_before=decision.current_segment.stop_before,
            ),
        )
        outcome = self.support.ready_outcome(
            invalid_decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 1)
            },
        )
        script = (
            (
                McpEvidenceToolName.FETCH_EVIDENCE.value,
                {
                    "evidence_ids": [str(evidence_id)],
                    "sections": ["claim", "provenance"],
                    "include_superseded_audit": False,
                },
            ),
        )
        port, runner, _ = self.port(
            outcome,
            script=script,
            tool_names=(McpEvidenceToolName.FETCH_EVIDENCE.value,),
        )
        with self.assertRaises(ReasonerExecutionFailure) as caught:
            self.support.coordinator.execute(request, port)
        self.assertEqual(
            caught.exception.envelope.error_code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )
        self.assertIsNotNone(caught.exception.provider_call_receipt)
        self.assertFalse(caught.exception.provider_call_receipt.retains_prompt)
        self.assertFalse(
            caught.exception.provider_call_receipt.retains_story_prose
        )
        self.assertEqual(runner.calls, 1)

    def test_python_rejects_private_evidence_transfer_from_codex_output(self) -> None:
        prepared = self.support.prepared("codex-privacy")
        request = self.support.request(prepared)
        beta_private = self.support.evidence_id(prepared, "record-beta-private")
        decision = self.support.decision(
            "codex-privacy",
            responders=(self.support.alpha,),
            evidence_by_character={self.support.alpha: beta_private},
        )
        outcome = self.support.ready_outcome(
            decision,
            record_by_evidence={
                beta_private: (ident(IdKind.RECORD, "record-beta-private"), 1)
            },
        )
        script = (
            (
                McpEvidenceToolName.FETCH_EVIDENCE.value,
                {
                    "evidence_ids": [str(beta_private)],
                    "sections": ["claim", "knowledge"],
                    "include_superseded_audit": False,
                },
            ),
        )
        port, runner, _ = self.port(
            outcome,
            script=script,
            tool_names=(McpEvidenceToolName.FETCH_EVIDENCE.value,),
        )
        with self.assertRaises(ReasonerExecutionFailure) as caught:
            self.support.coordinator.execute(request, port)
        self.assertEqual(
            caught.exception.envelope.error_code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )
        self.assertIn("transferred", caught.exception.envelope.message)
        self.assertEqual(
            caught.exception.envelope.details,
            ("character_moves.evidence_ids:private_owner_mismatch",),
        )
        self.assertEqual(runner.calls, 1)

    def test_insufficient_and_blocked_outputs_decode_without_inventing_decision(self) -> None:
        prepared = self.support.prepared("codex-status")
        request = self.support.request(prepared)
        outcomes = (
            ReasonerOutcome(
                schema_version=ReasonerOutcome.SCHEMA_VERSION,
                status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
                decision=None,
                participation=(),
                hard_citations=(),
                insufficiencies=("Required exact evidence is unavailable.",),
                blocker_code=None,
                advisory_state_deltas=(),
                protected_user_boundary_acknowledged=True,
            ),
            ReasonerOutcome(
                schema_version=ReasonerOutcome.SCHEMA_VERSION,
                status=ReasonerOutcomeStatus.BLOCKED,
                decision=None,
                participation=(),
                hard_citations=(),
                insufficiencies=(),
                blocker_code=ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
                advisory_state_deltas=(),
                protected_user_boundary_acknowledged=True,
            ),
        )
        for outcome in outcomes:
            with self.subTest(status=outcome.status):
                port, runner, _ = self.port(outcome)
                result = self.support.coordinator.execute(request, port)
                self.assertEqual(result.outcome.status, outcome.status)
                self.assertEqual(result.receipt.tool_call_count, 0)
                self.assertEqual(result.receipt.external_provider_calls, 1)
                self.assertEqual(runner.calls, 1)

    def test_exact_seed_mode_can_disable_mcp_without_losing_provider_receipt(self) -> None:
        prepared = self.support.prepared("codex-no-tools")
        request = self.support.request(prepared)
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=("The exact seed dossier is insufficient.",),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        runner = StaticReasonerRunner(outcome)
        transport = CodexSDKTransport(
            codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
            workspace=Path(self.temporary.name),
            runner=runner,
        )
        port = CodexSceneReasonerPort(transport, evidence_tools_enabled=False)
        result = self.support.coordinator.execute(request, port)
        self.assertIsNone(runner.last_binding)
        self.assertIn('"evidence_tools_available":false', runner.last_prompt)
        self.assertIsNone(result.receipt.bridge_receipt_id)
        self.assertIsNone(result.mcp_bridge_receipt)
        self.assertIsNotNone(result.provider_call_receipt)
        self.assertEqual(result.receipt.tool_call_count, 0)
        self.assertEqual(result.receipt.external_provider_calls, 1)
        allowed = sorted(
            str(value.evidence_id)
            for value in request.seed_dossier.exact_seed_evidence
        )
        schema = runner.last_schema
        self.assertIsNotNone(schema)
        for collection in ("participation", "character_moves", "event_blocks"):
            evidence_items = (
                schema["properties"][collection]["items"]["properties"]
                ["evidence_ids"]
            )
            if allowed:
                self.assertEqual(sorted(evidence_items["items"]["enum"]), allowed)
            else:
                self.assertEqual(evidence_items["maxItems"], 0)

    def test_unknown_output_field_fails_typed_decode_without_retry(self) -> None:
        prepared = self.support.prepared("codex-malformed")
        request = self.support.request(prepared)
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=("Evidence unavailable.",),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        port, runner, _ = self.port(outcome)
        payload = reasoner_draft_from_outcome(outcome)
        payload["untrusted_extension"] = True
        runner.output_text = canonical_json(payload)
        with self.assertRaises(ReasonerExecutionFailure) as caught:
            self.support.coordinator.execute(request, port)
        self.assertEqual(
            caught.exception.envelope.error_code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )
        self.assertEqual(runner.calls, 1)

    def test_provider_failure_is_explicit_and_unretried(self) -> None:
        prepared = self.support.prepared("codex-unavailable")
        request = self.support.request(prepared)
        runner = FailingReasonerRunner()
        transport = CodexSDKTransport(
            codex_reasoner_candidate(model="gpt-5.6-sol", effort="medium"),
            workspace=Path(self.temporary.name),
            runner=runner,
        )
        port = CodexSceneReasonerPort(
            transport,
            bridge_factory=RecordingBridgeFactory(),
        )
        with self.assertRaises(ReasonerExecutionFailure) as caught:
            self.support.coordinator.execute(request, port)
        self.assertEqual(
            caught.exception.envelope.error_code,
            ErrorCode.REASONER_UNAVAILABLE,
        )
        self.assertFalse(caught.exception.envelope.story_state_committed)
        self.assertEqual(runner.calls, 1)

    def test_adult_packet_contains_only_non_graphic_ledger(self) -> None:
        adult = adult_test_support.AdultRouteTests("runTest")
        adult.setUp()
        self.addCleanup(adult.doCleanups)
        restricted_marker = "RESTRICTED-ZXQ-941"
        prepared, _route_input, _envelope, preparation = adult.prepare(
            "codex-ledger",
            source_texts=(restricted_marker,),
        )
        base_request = adult.support.request(
            prepared,
            aware=(adult.alpha,),
            mode=ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER,
        )
        request = replace(
            base_request,
            source_view=preparation.reasoner_source_view,
        )
        packet_text = canonical_json(build_codex_reasoner_packet(request))
        self.assertNotIn(restricted_marker, packet_text)
        self.assertIn(ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER.value, packet_text)
        self.assertIn("contains_exact_protected_adult_prose\":false", packet_text)
        self.assertIn("active_content_families=general_intimacy", packet_text)
        self.assertIn("scenario=consensual_activity", packet_text)


if __name__ == "__main__":
    unittest.main()
