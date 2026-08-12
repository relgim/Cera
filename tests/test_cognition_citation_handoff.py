from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from cera.cognition import (
    CharacterAutonomyMode,
    CognitionCitationClass,
    CognitionDynamicEvidenceV1,
    CognitionEvidenceVisibility,
    CognitionValidationContextV1,
    LogicRoute,
    classify_cognition_plan_citations,
    cognition_provider_turn_handoff,
    validate_cognition_plan,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.sessions import ContinuousSessionRole
from cera.continuous.world_mcp import (
    WORLD_MCP_SERVER_NAME,
    ContinuousWorldMcpBridge,
    ContinuousWorldToolDispatcher,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.cognition_evidence import selected_validation_evidence
from cera.pi_scene.retrieval import (
    COGNITION_CONTEXT_ONLY_EXACT_RECORD_TOO_LARGE,
    COGNITION_CONTEXT_ONLY_VISIBILITY_NOT_CITABLE,
    COGNITION_ELIGIBLE_AFTER_EXACT_FETCH,
    DOSSIER_INDEX_SCHEMA,
    BranchRetrievalService,
)
from cera.pi_scene.retrieval_tools import (
    COGNITION_NAMED_RETRIEVAL_PROVIDER_CONTRACT_ID,
    BoundNamedRetrievalTools,
    NamedRetrievalRequestBindingV1,
    RetrievalProviderRole,
)
from cera.semantic_validation import ValidationEvidenceV1
from cera.sequence_first.contracts import (
    EvidenceRecordV1,
    Visibility,
)
from cera.serialization import (
    canonical_bytes,
    canonical_json,
    canonical_sha256,
    domain_sha256,
    text_sha256,
)

from .test_cognition_contracts import SAKURA, _decision, _plan, _turn

HANA = "character:hana_hanezawa"


class _PreChangeAdditionalHandler:
    tool_names = ("legacy_lookup",)
    provider_request_failure_policy_id = "legacy.additional_request_failures.v1"
    binding_sha256 = "f" * 64

    def __init__(self) -> None:
        self.invocations: list[tuple[str, dict[str, object]]] = []

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        self.invocations.append((tool_name, arguments))
        return {"legacy": True}


class _IncompleteCognitionAdditionalHandler(_PreChangeAdditionalHandler):
    provider_contract_id = COGNITION_NAMED_RETRIEVAL_PROVIDER_CONTRACT_ID


def _dynamic(
    citation_class: CognitionCitationClass,
    *,
    evidence_ref: str = "binding_record_0123456789abcdefabcd",
    request_id: str = "request:current",
    owner_id: str | None = None,
    fact: str | None = None,
    tool_name: str = "get_exact_record",
    call_index: int = 2,
) -> CognitionDynamicEvidenceV1:
    return CognitionDynamicEvidenceV1.create(
        evidence_ref=evidence_ref,
        citation_class=citation_class,
        world_id="world:test",
        branch_id="branch:main",
        request_id=request_id,
        binding_sha256="a" * 64,
        relative_path="ACTIVE/GenesisRecords/test/0000.json",
        source_sha256="b" * 64,
        source_read_operation_sha256="c" * 64,
        tool_name=tool_name,
        call_index=call_index,
        provider_request_sha256="e" * 64,
        visibility=(
            CognitionEvidenceVisibility.PUBLIC
            if owner_id is None
            else CognitionEvidenceVisibility.CHARACTER_PRIVATE
        ),
        knowledge_owner_id=owner_id,
        record_id="record:test" if tool_name == "get_exact_record" else None,
        concise_authoritative_fact=fact,
    )


def _recreate_dynamic(
    value: CognitionDynamicEvidenceV1,
    **changes: object,
) -> CognitionDynamicEvidenceV1:
    fields: dict[str, object] = {
        "evidence_ref": value.evidence_ref,
        "citation_class": value.citation_class,
        "world_id": value.world_id,
        "branch_id": value.branch_id,
        "request_id": value.request_id,
        "binding_sha256": value.binding_sha256,
        "relative_path": value.relative_path,
        "source_sha256": value.source_sha256,
        "source_read_operation_sha256": value.source_read_operation_sha256,
        "tool_name": value.tool_name,
        "call_index": value.call_index,
        "provider_request_sha256": value.provider_request_sha256,
        "visibility": value.visibility,
        "knowledge_owner_id": value.knowledge_owner_id,
        "record_id": value.record_id,
        "concise_authoritative_fact": value.concise_authoritative_fact,
    }
    fields.update(changes)
    return CognitionDynamicEvidenceV1.create(**fields)  # type: ignore[arg-type]


def _plan_citing(reference: str, *, location: str = "all"):
    decision = _decision(CharacterAutonomyMode.BOTH)
    causal = (reference,) if location in {"all", "causal"} else ("source:current",)
    decisive = (reference,) if location in {"all", "decisive"} else ("source:current",)
    perceived = decision.observer_frame.directly_perceived
    if location in {"all", "perceived"}:
        perceived = (replace(perceived[0], source_ref=reference),)
    pressures = decision.material_pressures
    if location in {"all", "pressure"}:
        pressures = (replace(pressures[0], evidence_refs=(reference,)),)
    return replace(
        _plan(),
        decision_records=(
            replace(
                decision,
                causal_trigger_refs=causal,
                decisive_factor_refs=decisive,
                observer_frame=replace(
                    decision.observer_frame,
                    directly_perceived=perceived,
                ),
                material_pressures=pressures,
            ),
        ),
    )


def _plan_item_citing(reference: str):
    plan = _plan()
    items = (
        replace(plan.sequence.items[0], evidence_keys=(reference,)),
        *plan.sequence.items[1:],
    )
    return replace(plan, sequence=replace(plan.sequence, items=items))


def _context(
    turn,
    *dynamic: CognitionDynamicEvidenceV1,
) -> CognitionValidationContextV1:
    return CognitionValidationContextV1(
        autonomy_mode=CharacterAutonomyMode.BOTH,
        logic_route=LogicRoute.ORDINARY,
        available_evidence_refs=(
            turn.current_source_key,
            *(value.evidence_key for value in turn.evidence_records),
        ),
        dynamic_evidence=tuple(dynamic),
    )


class CognitionCitationClassifierTests(unittest.TestCase):
    def test_current_source_is_allowed_but_not_selected(self) -> None:
        handoff = cognition_provider_turn_handoff(
            plan=_plan(),
            turn=_turn(),
            provider_thread_sha256="d" * 64,
        )
        self.assertEqual(handoff.selected_evidence, ())

    def test_draft_local_sequence_item_is_allowed_but_not_selected(self) -> None:
        plan = _plan_citing("sakura_checks_door")
        validate_cognition_plan(plan, turn=_turn(), context=_context(_turn()))
        handoff = cognition_provider_turn_handoff(
            plan=plan,
            turn=_turn(),
            provider_thread_sha256="d" * 64,
        )
        self.assertEqual(handoff.selected_evidence, ())

    def test_static_public_evidence_is_selected_once(self) -> None:
        record = EvidenceRecordV1(
            evidence_key="evidence:static",
            subject_id=SAKURA,
            visibility=Visibility.PUBLIC,
            exact_content="One bounded static fact.",
        )
        turn = replace(_turn(), evidence_records=(record,))
        plan = _plan_citing(record.evidence_key)
        handoff = cognition_provider_turn_handoff(
            plan=plan,
            turn=turn,
            provider_thread_sha256="d" * 64,
        )
        self.assertEqual(len(handoff.selected_evidence), 1)
        selected = handoff.selected_evidence[0]
        self.assertIs(
            selected.citation_class,
            CognitionCitationClass.STATIC_VALIDATION_EVIDENCE,
        )
        self.assertEqual(selected.concise_authoritative_fact, record.exact_content)

    def test_static_4000_unicode_characters_is_citable(self) -> None:
        record = EvidenceRecordV1(
            evidence_key="evidence:static-unicode-4000",
            subject_id=SAKURA,
            visibility=Visibility.PUBLIC,
            exact_content="é" * 4_000,
        )
        turn = replace(_turn(), evidence_records=(record,))
        handoff = cognition_provider_turn_handoff(
            plan=_plan_citing(record.evidence_key),
            turn=turn,
            provider_thread_sha256="d" * 64,
        )
        self.assertEqual(len(handoff.selected_evidence[0].concise_authoritative_fact), 4_000)

    def test_static_4001_unicode_characters_is_rejected(self) -> None:
        record = EvidenceRecordV1(
            evidence_key="evidence:static-unicode-4001",
            subject_id=SAKURA,
            visibility=Visibility.PUBLIC,
            exact_content="é" * 4_001,
        )
        turn = replace(_turn(), evidence_records=(record,))
        with self.assertRaisesRegex(ContractValidationError, "context-only"):
            validate_cognition_plan(
                _plan_citing(record.evidence_key),
                turn=turn,
                context=_context(turn),
            )

    def test_unselected_static_oversize_is_context_only_and_not_retained(self) -> None:
        for size in (4_001, 8_000):
            oversized = "é" * size
            record = EvidenceRecordV1(
                evidence_key=f"evidence:static-unicode-{size}",
                subject_id=SAKURA,
                visibility=Visibility.PUBLIC,
                exact_content=oversized,
            )
            turn = replace(_turn(), evidence_records=(record,))
            for plan in (_plan(), _plan_citing("sakura_checks_door")):
                with self.subTest(size=size, plan=plan.semantic_sha256):
                    validate_cognition_plan(plan, turn=turn, context=_context(turn))
                    catalog, uses = classify_cognition_plan_citations(
                        plan=plan,
                        turn=turn,
                    )
                    entry = catalog.entry(record.evidence_key)
                    self.assertIs(
                        entry.citation_class,
                        CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING,
                    )
                    self.assertTrue(entry.static_context_only)
                    self.assertIsNone(entry.concise_authoritative_fact)
                    self.assertIsNone(entry.authoritative_fact_sha256)
                    self.assertNotIn(oversized, repr(catalog))
                    handoff = cognition_provider_turn_handoff(
                        plan=plan,
                        turn=turn,
                        provider_thread_sha256="d" * 64,
                    )
                    self.assertEqual(handoff.selected_evidence, ())
                    self.assertNotIn(oversized, repr(handoff))
                    self.assertNotIn(record.evidence_key, {use.evidence_ref for use in uses})

    def test_sequence_item_static_evidence_uses_same_unicode_boundary(self) -> None:
        for size, accepted in ((4_000, True), (4_001, False), (8_000, False)):
            record = EvidenceRecordV1(
                evidence_key=f"evidence:item-unicode-{size}",
                subject_id=SAKURA,
                visibility=Visibility.PUBLIC,
                exact_content=chr(0xE9) * size,
            )
            turn = replace(_turn(), evidence_records=(record,))
            plan = _plan_item_citing(record.evidence_key)
            with self.subTest(size=size):
                if accepted:
                    validate_cognition_plan(plan, turn=turn, context=_context(turn))
                else:
                    with self.assertRaisesRegex(
                        ContractValidationError, "sequence item cites context-only"
                    ):
                        validate_cognition_plan(plan, turn=turn, context=_context(turn))

    def test_typed_selected_evidence_rejects_4001_unicode_characters(self) -> None:
        record = EvidenceRecordV1(
            evidence_key="evidence:selected-size",
            subject_id=SAKURA,
            visibility=Visibility.PUBLIC,
            exact_content="bounded",
        )
        turn = replace(_turn(), evidence_records=(record,))
        selected = cognition_provider_turn_handoff(
            plan=_plan_citing(record.evidence_key),
            turn=turn,
            provider_thread_sha256="d" * 64,
        ).selected_evidence[0]
        oversized = "é" * 4_001
        with self.assertRaisesRegex(ContractValidationError, "selected evidence changed"):
            replace(
                selected,
                concise_authoritative_fact=oversized,
                authoritative_fact_sha256=text_sha256(oversized),
            )

    def test_static_private_evidence_requires_exact_decision_owner(self) -> None:
        record = EvidenceRecordV1(
            evidence_key="evidence:private",
            subject_id=SAKURA,
            visibility=Visibility.CHARACTER_PRIVATE,
            exact_content="One owner-private static fact.",
            knowledge_owner_id=SAKURA,
        )
        turn = replace(_turn(), evidence_records=(record,))
        plan = _plan_citing(record.evidence_key)
        validate_cognition_plan(plan, turn=turn, context=_context(turn))
        wrong = replace(
            plan,
            decision_records=(replace(plan.decision_records[0], owner_id=HANA),),
        )
        wrong_turn = replace(turn, known_character_ids=(*turn.known_character_ids, HANA))
        with self.assertRaisesRegex(ContractValidationError, "another character"):
            validate_cognition_plan(wrong, turn=wrong_turn, context=_context(wrong_turn))

    def test_private_owner_is_enforced_at_every_decision_citation_location(self) -> None:
        dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            owner_id=HANA,
            fact='{"record_id":"record:test"}',
        )
        for location in ("causal", "decisive", "perceived", "pressure"):
            with self.subTest(location=location):
                with self.assertRaisesRegex(ContractValidationError, "another character"):
                    validate_cognition_plan(
                        _plan_citing(dynamic.evidence_ref, location=location),
                        turn=_turn(),
                        context=_context(_turn(), dynamic),
                    )

    def test_dynamic_exact_record_is_selected_with_full_custody(self) -> None:
        fact = '{"record_id":"record:test","value":"bounded"}'
        dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            owner_id=SAKURA,
            fact=fact,
        )
        plan = _plan_citing(dynamic.evidence_ref)
        handoff = cognition_provider_turn_handoff(
            plan=plan,
            turn=_turn(),
            provider_thread_sha256="d" * 64,
            dynamic_evidence=(dynamic,),
        )
        selected = handoff.selected_evidence[0]
        self.assertEqual(selected.concise_authoritative_fact, fact)
        self.assertNotIn(dynamic.relative_path, repr(selected))
        self.assertNotIn(dynamic.request_id, repr(selected))

    def test_handoff_drops_unselected_fact_and_dynamic_custody(self) -> None:
        selected_dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            evidence_ref="binding_record_selected0000000001",
            fact='{"record_id":"record:selected","value":"selected_marker"}',
        )
        unselected_dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            evidence_ref="binding_record_unselected00000001",
            fact='{"record_id":"record:unselected","value":"unselected_marker"}',
        )
        handoff = cognition_provider_turn_handoff(
            plan=_plan_citing(selected_dynamic.evidence_ref),
            turn=_turn(),
            provider_thread_sha256="d" * 64,
            dynamic_evidence=(selected_dynamic, unselected_dynamic),
        )
        rendered = repr(handoff)
        self.assertIn("selected_marker", rendered)
        self.assertNotIn("unselected_marker", rendered)
        self.assertNotIn(selected_dynamic.relative_path, rendered)
        self.assertNotIn(selected_dynamic.source_sha256, rendered)

    def test_dynamic_context_only_binding_is_forbidden(self) -> None:
        dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING,
            fact=None,
            tool_name="search_evidence",
            call_index=1,
        )
        with self.assertRaisesRegex(ContractValidationError, "context-only"):
            validate_cognition_plan(
                _plan_citing(dynamic.evidence_ref),
                turn=_turn(),
                context=_context(_turn(), dynamic),
            )

    def test_unknown_or_expired_dynamic_ref_is_rejected(self) -> None:
        current = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            evidence_ref="binding_record_current000000000001",
            fact='{"record_id":"record:test"}',
        )
        with self.assertRaisesRegex(ContractValidationError, "unavailable evidence"):
            validate_cognition_plan(
                _plan_citing("binding_record_expired00000000001"),
                turn=_turn(),
                context=_context(_turn(), current),
            )

    def test_cross_namespace_collision_is_rejected(self) -> None:
        collision = _dynamic(
            CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING,
            evidence_ref="source:current",
            fact=None,
            tool_name="search_evidence",
            call_index=1,
        )
        with self.assertRaisesRegex(ContractValidationError, "namespaces collide"):
            classify_cognition_plan_citations(
                plan=_plan(),
                turn=_turn(),
                dynamic_evidence=(collision,),
            )

    def test_tampered_authenticated_dynamic_fields_are_rejected(self) -> None:
        dynamic = _dynamic(
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
            fact='{"record_id":"record:test"}',
        )
        for field, value in (
            ("relative_path", "../escape.json"),
            ("source_sha256", "0" * 64),
            ("source_read_operation_sha256", "1" * 64),
            ("binding_sha256", "2" * 64),
            ("world_id", "world:other"),
            ("branch_id", "branch:other"),
            ("request_id", "request:other"),
            (
                "citation_class",
                CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING,
            ),
            ("visibility", CognitionEvidenceVisibility.CHARACTER_PRIVATE),
            ("tool_name", "search_evidence"),
            ("call_index", 3),
            ("provider_request_sha256", "3" * 64),
            ("record_id", "record:other"),
            ("concise_authoritative_fact", '{"record_id":"record:changed"}'),
            ("knowledge_owner_id", SAKURA),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ContractValidationError):
                    replace(dynamic, **{field: value})

    def test_postprocessing_uses_handoff_without_reclassification(self) -> None:
        record = EvidenceRecordV1(
            evidence_key="evidence:static",
            subject_id=SAKURA,
            visibility=Visibility.PUBLIC,
            exact_content="One bounded static fact.",
        )
        turn = replace(_turn(), evidence_records=(record,))
        plan = _plan_citing(record.evidence_key)
        handoff = cognition_provider_turn_handoff(
            plan=plan,
            turn=turn,
            provider_thread_sha256="d" * 64,
        )
        with patch(
            "cera.cognition.citations.classify_cognition_plan_citations",
            side_effect=AssertionError("postprocessing rediscovered citations"),
        ):
            selected = selected_validation_evidence(
                plan=plan,
                turn=turn,
                handoff=handoff,
            )
        self.assertEqual(
            selected,
            (
                ValidationEvidenceV1(
                    evidence_ref=record.evidence_key,
                    concise_authoritative_fact=record.exact_content,
                ),
            ),
        )


class CognitionNamedRetrievalV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "ACTIVE" / "GenesisRecords" / "test").mkdir(parents=True)
        (self.root / "DERIVED" / "CurrentCharacterDossiers").mkdir(parents=True)
        (self.root / "BRANCH_IDENTITY.json").write_bytes(
            canonical_bytes({"world_id": "world:test", "branch_id": "branch:main"})
        )
        index_payload = {
            "schema_version": DOSSIER_INDEX_SCHEMA,
            "_cera_revision": 1,
            "world_id": "world:test",
            "branch_id": "branch:main",
            "accepted_head_sha256": None,
            "genesis_revision": "revision:test",
            "characters": {},
        }
        (self.root / "DERIVED" / "CurrentCharacterDossiers" / "INDEX.json").write_bytes(
            canonical_bytes({**index_payload, "index_sha256": canonical_sha256(index_payload)})
        )

    def _write_record(
        self,
        record: dict[str, object],
        *,
        visibility: str = "public",
        owner: str | None = None,
        name: str = "0000.json",
    ) -> None:
        projection = {
            "schema_version": "cera.pi_scene_genesis_record_projection.v1",
            "_cera_revision": 1,
            "visibility": visibility,
            "knowledge_owner_id": owner,
            "record": record,
        }
        (self.root / "ACTIVE" / "GenesisRecords" / "test" / name).write_bytes(
            canonical_bytes(projection)
        )

    def _handler(
        self,
        request_id: str,
        *,
        role: RetrievalProviderRole = RetrievalProviderRole.COGNITION_PLANNER,
        private_ids: tuple[str, ...] = (SAKURA,),
    ) -> BoundNamedRetrievalTools:
        workspace = type(
            "Workspace",
            (),
            {
                "branch_root": self.root,
                "world_id": "world:test",
                "branch_id": "branch:main",
            },
        )()
        service = BranchRetrievalService(workspace)
        return BoundNamedRetrievalTools(
            service,
            binding=NamedRetrievalRequestBindingV1(
                schema_version=NamedRetrievalRequestBindingV1.SCHEMA_VERSION,
                request_id=request_id,
                world_id="world:test",
                branch_id="branch:main",
                accepted_head_sha256=None,
                role=role,
                private_character_ids=private_ids,
            ),
            evidence_registry=RequestEvidenceBindingRegistry(
                world_id="world:test",
                branch_id="branch:main",
                turn_id=request_id,
            ),
        )

    def _dispatcher(
        self,
        handler: BoundNamedRetrievalTools,
    ) -> ContinuousWorldToolDispatcher:
        return ContinuousWorldToolDispatcher(
            self.root,
            ContinuousSessionRole.PLANNER,
            world_id="world:test",
            branch_id="branch:main",
            current_turn_id=handler.binding.request_id,
            evidence_registry=handler.evidence_registry,
            require_private_search_scope=True,
            allowed_private_character_ids=handler.binding.private_character_ids,
            additional_tool_handler=handler,
        )

    def test_prechange_generic_handler_keeps_old_hash_invoke_and_finalize_seam(
        self,
    ) -> None:
        def dispatcher_for(
            handler: _PreChangeAdditionalHandler,
        ) -> ContinuousWorldToolDispatcher:
            return ContinuousWorldToolDispatcher(
                self.root,
                ContinuousSessionRole.PLANNER,
                world_id="world:test",
                branch_id="branch:main",
                current_turn_id="turn:legacy",
                maximum_calls=3,
                additional_tool_handler=handler,
            )

        handler = _PreChangeAdditionalHandler()
        dispatcher = dispatcher_for(handler)
        expected_binding = domain_sha256(
            "cera.continuous_world_mcp.v1",
            {
                "branch_root_sha256": text_sha256(str(self.root).casefold()),
                "world_id": "world:test",
                "branch_id": "branch:main",
                "role": ContinuousSessionRole.PLANNER.value,
                "current_turn_id": "turn:legacy",
                "tools": handler.tool_names,
                "maximum_calls": 3,
                "private_character_ids": None,
                "additional_binding_sha256": handler.binding_sha256,
                "provider_request_failure_policy_id": (handler.provider_request_failure_policy_id),
            },
        )
        self.assertEqual(dispatcher.binding_sha256, expected_binding)
        self.assertEqual(
            dispatcher.invoke("legacy_lookup", {"key": "value"}),
            {"legacy": True},
        )
        self.assertEqual(
            handler.invocations,
            [("legacy_lookup", {"key": "value"})],
        )
        bridge = ContinuousWorldMcpBridge(dispatcher)
        finalized = bridge.finalize(
            SimpleNamespace(
                tool_names=("legacy_lookup",),
                tool_server_names=(WORLD_MCP_SERVER_NAME,),
            )
        )
        self.assertEqual(finalized["binding_sha256"], expected_binding)
        self.assertIsNone(bridge.take_additional_finalization())

        failed_dispatcher = dispatcher_for(_PreChangeAdditionalHandler())
        failed_dispatcher.invoke("legacy_lookup", {"key": "value"})
        failed_bridge = ContinuousWorldMcpBridge(failed_dispatcher)
        with self.assertRaisesRegex(StateConflictError, "tool sequence changed"):
            failed_bridge.finalize(SimpleNamespace(tool_names=(), tool_server_names=()))
        self.assertIsNone(failed_bridge.take_additional_finalization())

    def test_cognition_contract_missing_typed_hooks_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            ContractValidationError,
            "lacks provider-turn hooks",
        ):
            ContinuousWorldToolDispatcher(
                self.root,
                ContinuousSessionRole.PLANNER,
                world_id="world:test",
                branch_id="branch:main",
                current_turn_id="request:cognition",
                additional_tool_handler=_IncompleteCognitionAdditionalHandler(),
                require_additional_provider_contract=True,
            )

    @staticmethod
    def _sized_record(characters: int) -> dict[str, object]:
        base = {
            "record_id": "record:unicode",
            "record_type": "technical_fact",
            "value": "",
        }
        overhead = len(canonical_json(base))
        record = {**base, "value": "é" * (characters - overhead)}
        assert len(canonical_json(record)) == characters
        return record

    def test_search_is_context_only_then_exact_fetch_promotes_same_binding(self) -> None:
        record = {
            "record_id": "record:promote",
            "record_type": "technical_fact",
            "value": "promotion_marker",
        }
        self._write_record(record)
        handler = self._handler("request:promote")
        search = handler.invoke(
            "search_evidence",
            {"terms": ["promotion_marker"], "character_id": None, "limit": 5},
        )
        self.assertEqual(search["schema_version"], "cera.pi_scene.named_retrieval_result.v2")
        self.assertEqual(search["evidence_refs"], [])
        self.assertEqual(len(search["context_refs"]), 1)
        self.assertEqual(
            search["data"]["records"][0]["citation_eligibility"],
            COGNITION_ELIGIBLE_AFTER_EXACT_FETCH,
        )
        exact = handler.invoke("get_exact_record", {"record_id": "record:promote"})
        self.assertEqual(exact["context_refs"], [])
        self.assertEqual(exact["evidence_refs"], search["context_refs"])
        candidates = handler.finalize_provider_turn()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].evidence_ref, search["context_refs"][0])
        self.assertIs(
            candidates[0].citation_class,
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
        )
        self.assertEqual(candidates[0].concise_authoritative_fact, canonical_json(record))
        self.assertEqual(handler._cognition_dynamic_by_ref, {})
        self.assertNotIn("promotion_marker", repr(handler.evidence_registry.bindings))
        with self.assertRaisesRegex(StateConflictError, "one-shot"):
            handler.finalize_provider_turn()

    def test_restarted_request_uses_new_binding_and_expires_old_reference(self) -> None:
        record = {
            "record_id": "record:restart",
            "record_type": "technical_fact",
            "value": "restart_marker",
        }
        self._write_record(record)
        first = self._handler("request:restart-one")
        first.invoke(
            "search_evidence",
            {"terms": ["restart_marker"], "character_id": None, "limit": 5},
        )
        first_result = first.invoke("get_exact_record", {"record_id": "record:restart"})
        first_ref = first_result["evidence_refs"][0]
        first.finalize_provider_turn()

        restarted = self._handler("request:restart-two")
        restarted.invoke(
            "search_evidence",
            {"terms": ["restart_marker"], "character_id": None, "limit": 5},
        )
        second_result = restarted.invoke("get_exact_record", {"record_id": "record:restart"})
        second_ref = second_result["evidence_refs"][0]
        current = restarted.finalize_provider_turn()

        self.assertNotEqual(first.binding.request_id, restarted.binding.request_id)
        self.assertNotEqual(first_ref, second_ref)
        with self.assertRaisesRegex(ContractValidationError, "unavailable evidence"):
            validate_cognition_plan(
                _plan_citing(first_ref),
                turn=_turn(),
                context=_context(_turn(), *current),
            )
        self.assertEqual(first._cognition_dynamic_by_ref, {})
        self.assertEqual(restarted._cognition_dynamic_by_ref, {})

    def test_dispatcher_call_index_includes_prior_failed_call(self) -> None:
        self._write_record(
            {
                "record_id": "record:dispatcher-index",
                "record_type": "technical_fact",
                "value": "dispatcher_index_marker",
            }
        )
        handler = self._handler("request:dispatcher-index")
        dispatcher = self._dispatcher(handler)
        with self.assertRaises(ContractValidationError):
            dispatcher.invoke("search_evidence", {"terms": "invalid"})
        dispatcher.invoke(
            "search_evidence",
            {
                "terms": ["dispatcher_index_marker"],
                "character_id": None,
                "limit": 5,
            },
        )
        dispatcher.invoke(
            "get_exact_record",
            {"record_id": "record:dispatcher-index"},
        )
        selected = handler.finalize_provider_turn(tuple(dispatcher.calls))
        self.assertEqual(selected[0].call_index, 3)
        self.assertEqual(selected[0].tool_name, "get_exact_record")
        self.assertEqual(
            selected[0].provider_request_sha256,
            dispatcher.calls[2].request_sha256,
        )

    def test_provider_finalization_failure_clears_all_retrieval_facts(self) -> None:
        self._write_record(
            {
                "record_id": "record:finalize-failure",
                "record_type": "technical_fact",
                "value": "finalize_failure_marker",
            }
        )
        handler = self._handler("request:finalize-failure")
        dispatcher = self._dispatcher(handler)
        dispatcher.invoke(
            "search_evidence",
            {
                "terms": ["finalize_failure_marker"],
                "character_id": None,
                "limit": 5,
            },
        )
        dispatcher.invoke("get_exact_record", {"record_id": "record:finalize-failure"})
        self.assertTrue(handler._cognition_dynamic_by_ref)

        bridge = ContinuousWorldMcpBridge(dispatcher)
        with self.assertRaisesRegex(StateConflictError, "tool sequence"):
            bridge.finalize(
                SimpleNamespace(
                    tool_names=(),
                    tool_server_names=(),
                )
            )

        self.assertEqual(handler._cognition_dynamic_by_ref, {})
        self.assertEqual(handler._cognition_expected_sha256_by_ref, {})
        self.assertEqual(handler._advertised_exact_record_locators, {})
        self.assertEqual(handler._successful_provider_calls, {})
        self.assertIsNone(bridge.take_additional_finalization())

    def test_finalization_rejects_reauthenticated_custody_forgery_matrix(self) -> None:
        self._write_record(
            {
                "record_id": "record:tamper",
                "record_type": "technical_fact",
                "value": "tamper_marker",
            }
        )
        cases: tuple[tuple[str, dict[str, object]], ...] = (
            ("binding", {"binding_sha256": "0" * 64}),
            (
                "owner_visibility",
                {
                    "visibility": CognitionEvidenceVisibility.CHARACTER_PRIVATE,
                    "knowledge_owner_id": SAKURA,
                },
            ),
            (
                "class",
                {
                    "citation_class": CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING,
                    "concise_authoritative_fact": None,
                },
            ),
            ("record", {"record_id": "record:forged"}),
            (
                "fact",
                {"concise_authoritative_fact": '{"record_id":"record:forged"}'},
            ),
            ("request", {"request_id": "request:forged"}),
            ("world", {"world_id": "world:forged"}),
            ("branch", {"branch_id": "branch:forged"}),
            ("source_read", {"source_read_operation_sha256": "1" * 64}),
            ("path", {"relative_path": "ACTIVE/GenesisRecords/forged.json"}),
            ("source", {"source_sha256": "2" * 64}),
            (
                "tool",
                {
                    "citation_class": CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING,
                    "tool_name": "search_evidence",
                    "concise_authoritative_fact": None,
                },
            ),
            ("call", {"call_index": 1}),
            ("provider_request", {"provider_request_sha256": "3" * 64}),
        )
        for label, changes in cases:
            with self.subTest(label=label):
                handler = self._handler(f"request:tamper-{label}")
                dispatcher = self._dispatcher(handler)
                dispatcher.invoke(
                    "search_evidence",
                    {
                        "terms": ["tamper_marker"],
                        "character_id": None,
                        "limit": 5,
                    },
                )
                dispatcher.invoke(
                    "get_exact_record",
                    {"record_id": "record:tamper"},
                )
                original = next(iter(handler._cognition_dynamic_by_ref.values()))
                forged = _recreate_dynamic(original, **changes)
                handler._cognition_dynamic_by_ref[original.evidence_ref] = forged
                with self.assertRaisesRegex(StateConflictError, "changed custody"):
                    handler.finalize_provider_turn(tuple(dispatcher.calls))
                self.assertEqual(handler._cognition_dynamic_by_ref, {})

    def test_exact_4000_unicode_characters_is_citable(self) -> None:
        record = self._sized_record(4_000)
        self._write_record(record)
        handler = self._handler("request:size-4000")
        handler.invoke(
            "search_evidence",
            {"terms": ["é"], "character_id": None, "limit": 5},
        )
        exact = handler.invoke("get_exact_record", {"record_id": "record:unicode"})
        self.assertEqual(len(exact["evidence_refs"]), 1)
        self.assertEqual(
            exact["data"]["citation_eligibility"],
            COGNITION_ELIGIBLE_AFTER_EXACT_FETCH,
        )
        self.assertEqual(
            len(handler.finalize_provider_turn()[0].concise_authoritative_fact or ""),
            4_000,
        )

    def test_exact_4001_unicode_characters_succeeds_as_context_only(self) -> None:
        record = self._sized_record(4_001)
        self._write_record(record)
        handler = self._handler("request:size-4001")
        search = handler.invoke(
            "search_evidence",
            {"terms": ["é"], "character_id": None, "limit": 5},
        )
        self.assertEqual(
            search["data"]["records"][0]["citation_eligibility"],
            COGNITION_CONTEXT_ONLY_EXACT_RECORD_TOO_LARGE,
        )
        exact = handler.invoke("get_exact_record", {"record_id": "record:unicode"})
        self.assertEqual(exact["evidence_refs"], [])
        self.assertEqual(len(exact["context_refs"]), 1)
        self.assertEqual(
            exact["data"]["citation_eligibility"],
            COGNITION_CONTEXT_ONLY_EXACT_RECORD_TOO_LARGE,
        )
        candidate = handler.finalize_provider_turn()[0]
        self.assertIs(
            candidate.citation_class,
            CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING,
        )
        self.assertIsNone(candidate.concise_authoritative_fact)

    def test_exact_without_prior_locator_fails_locally(self) -> None:
        self._write_record(
            {
                "record_id": "record:no-locator",
                "record_type": "technical_fact",
                "value": "no_locator_marker",
            }
        )
        handler = self._handler("request:no-locator")
        with patch.object(handler.service, "get_exact_record") as fetched:
            with self.assertRaisesRegex(StateConflictError, "prior locator custody"):
                handler.invoke(
                    "get_exact_record",
                    {"record_id": "record:no-locator"},
                )
        fetched.assert_not_called()

    def test_duplicate_search_record_ids_fail_before_locator_registration(self) -> None:
        for name in ("0000.json", "0001.json"):
            self._write_record(
                {
                    "record_id": "record:duplicate",
                    "record_type": "technical_fact",
                    "value": "duplicate_marker",
                },
                name=name,
            )
        handler = self._handler("request:duplicate")
        with self.assertRaisesRegex(StateConflictError, "duplicate record identity"):
            handler.invoke(
                "search_evidence",
                {"terms": ["duplicate_marker"], "character_id": None, "limit": 5},
            )
        self.assertEqual(handler._advertised_exact_record_locators, {})
        self.assertEqual(handler._cognition_dynamic_by_ref, {})

    def test_changed_source_after_search_cannot_mint_exact_evidence(self) -> None:
        record = {
            "record_id": "record:changed",
            "record_type": "technical_fact",
            "value": "changed_marker",
        }
        self._write_record(record)
        handler = self._handler("request:changed")
        search = handler.invoke(
            "search_evidence",
            {"terms": ["changed_marker"], "character_id": None, "limit": 5},
        )
        self._write_record({**record, "value": "changed_marker_after_search"})
        with self.assertRaisesRegex(StateConflictError, "changed after its locator"):
            handler.invoke("get_exact_record", {"record_id": "record:changed"})
        selected = handler.finalize_provider_turn()
        self.assertEqual(
            tuple(value.evidence_ref for value in selected), tuple(search["context_refs"])
        )
        self.assertTrue(
            all(
                value.citation_class is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
                for value in selected
            )
        )

    def test_moved_source_after_search_cannot_mint_exact_evidence(self) -> None:
        self._write_record(
            {
                "record_id": "record:moved",
                "record_type": "technical_fact",
                "value": "moved_marker",
            }
        )
        handler = self._handler("request:moved")
        handler.invoke(
            "search_evidence",
            {"terms": ["moved_marker"], "character_id": None, "limit": 5},
        )
        source = self.root / "ACTIVE" / "GenesisRecords" / "test" / "0000.json"
        source.replace(source.with_name("0001.json"))
        with self.assertRaisesRegex(StateConflictError, "changed after its locator"):
            handler.invoke("get_exact_record", {"record_id": "record:moved"})
        self.assertTrue(
            all(
                value.citation_class is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
                for value in handler.finalize_provider_turn()
            )
        )

    def test_private_search_returns_per_record_descriptor_not_dossier(self) -> None:
        self._write_record(
            {
                "record_id": "record:private",
                "record_type": "memory_seed",
                "owner_id": SAKURA,
                "knowledge_owner_ids": [SAKURA],
                "value": "private_marker",
            },
            visibility="character_private",
            owner=SAKURA,
        )
        handler = self._handler("request:private")
        result = handler.invoke(
            "search_evidence",
            {"terms": ["private_marker"], "character_id": SAKURA, "limit": 5},
        )
        row = result["data"]["records"][0]
        self.assertEqual(row["record_id"], "record:private")
        self.assertNotEqual(row["kind"] if "kind" in row else None, "character_dossier")
        self.assertIn("context_ref", row)
        self.assertNotIn("evidence_ref", row)

    def test_other_owner_is_rejected_before_search_service(self) -> None:
        handler = self._handler("request:other-owner")
        with patch.object(handler.service, "search_cognition_evidence") as searched:
            with self.assertRaisesRegex(PermissionError, "private retrieval scope"):
                handler.invoke(
                    "search_evidence",
                    {"terms": ["marker"], "character_id": HANA, "limit": 5},
                )
        searched.assert_not_called()

    def test_protected_user_private_authority_is_rejected_at_binding(self) -> None:
        with self.assertRaisesRegex(PermissionError, "protected-user"):
            self._handler(
                "request:protected-authority",
                private_ids=("character:ted",),
            )

    def test_protected_user_private_record_emits_no_signal_or_exact_ref(self) -> None:
        self._write_record(
            {
                "record_id": "record:ted-private",
                "record_type": "memory_seed",
                "owner_id": "character:ted",
                "knowledge_owner_ids": ["character:ted"],
                "value": "ted_private_marker",
            },
            visibility="character_private",
            owner="character:ted",
        )
        handler = self._handler("request:ted-excluded")
        result = handler.invoke(
            "search_evidence",
            {"terms": ["ted_private_marker"], "character_id": None, "limit": 5},
        )
        self.assertEqual(result["data"]["records"], [])
        self.assertEqual(result["context_refs"], [])
        self.assertEqual(result["evidence_refs"], [])
        with patch.object(handler.service, "search_cognition_evidence") as searched:
            with self.assertRaisesRegex(PermissionError, "private retrieval scope"):
                handler.invoke(
                    "search_evidence",
                    {
                        "terms": ["ted_private_marker"],
                        "character_id": "character:ted",
                        "limit": 5,
                    },
                )
        searched.assert_not_called()
        with patch.object(handler.service, "get_exact_record") as fetched:
            with self.assertRaisesRegex(StateConflictError, "prior locator custody"):
                handler.invoke(
                    "get_exact_record",
                    {"record_id": "record:ted-private"},
                )
        fetched.assert_not_called()

    def test_creator_system_multi_and_cross_owner_records_emit_no_signal(self) -> None:
        records = (
            ("0000.json", "creator_private", None, [SAKURA]),
            ("0001.json", "system_private", SAKURA, [SAKURA]),
            ("0002.json", "character_private", None, [SAKURA, HANA]),
            ("0003.json", "character_private", HANA, [HANA]),
        )
        for name, visibility, owner, owners in records:
            self._write_record(
                {
                    "record_id": f"record:{name[:4]}",
                    "record_type": "technical_fact",
                    "knowledge_owner_ids": owners,
                    "value": "excluded_marker",
                },
                visibility=visibility,
                owner=owner,
                name=name,
            )
        handler = self._handler("request:excluded")
        result = handler.invoke(
            "search_evidence",
            {"terms": ["excluded_marker"], "character_id": SAKURA, "limit": 10},
        )
        self.assertEqual(result["data"]["records"], [])
        self.assertEqual(result["context_refs"], [])
        self.assertNotIn(COGNITION_CONTEXT_ONLY_VISIBILITY_NOT_CITABLE, repr(result))

    def test_legacy_planner_result_and_contract_remain_unchanged(self) -> None:
        self._write_record(
            {
                "record_id": "record:legacy",
                "record_type": "technical_fact",
                "value": "legacy_marker",
            }
        )
        handler = self._handler(
            "request:legacy",
            role=RetrievalProviderRole.PLANNER,
        )
        result = handler.invoke(
            "search_evidence",
            {"terms": ["legacy_marker"], "character_id": None, "limit": 5},
        )
        self.assertEqual(result["schema_version"], "cera.pi_scene.named_retrieval_result.v1")
        self.assertIn("evidence_refs", result)
        self.assertNotIn("context_refs", result)
        self.assertIsNone(handler.provider_contract_id)
        self.assertEqual(handler.finalize_provider_turn(), ())

    def test_cognition_provider_contract_identity_is_explicit(self) -> None:
        handler = self._handler("request:contract")
        self.assertEqual(
            handler.provider_contract_id,
            COGNITION_NAMED_RETRIEVAL_PROVIDER_CONTRACT_ID,
        )


if __name__ == "__main__":
    unittest.main()
