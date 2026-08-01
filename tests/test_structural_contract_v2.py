from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jsonschema import Draft202012Validator

from cera.composer import (
    ComposerContextAssembler,
    ComposerCoordinator,
    DeepSeekCompositionDraftV2,
    deepseek_composition_draft_v2_json_schema,
    deepseek_composer_response_json_schema,
)
from cera.evaluation import RealGenesisSandbox
from cera.evidence import (
    ContinuityRequest,
    EvidenceAmbiguityPolicy,
    EvidenceFetchRequest,
    EvidenceObligation,
    EvidenceObligationKind,
    EvidenceQueryPlan,
    EvidenceRecordType,
    EvidenceSearchRequest,
    EvidenceWorldMode,
)
from cera.continuous import (
    ContinuousIngressAuthorityStore,
    ContinuousSillyTavernShadowRequestBridge,
    PreparedContinuousIngressBridge,
    PreparedIngressClassifierRegistry,
    RepositoryPreparedIngressClassifierV1,
    build_default_prepared_classifier_registry,
)
from cera.errors import ContractValidationError, EvidenceServiceError, StateConflictError
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, deterministic_id
from cera.ingress import (
    IntentInterpretationDraft,
    InterpretedSourceSpan,
    RawTurnEnvelope,
    RawTurnIngressFacade,
    ScriptedIntentInterpreterPort,
)
from cera.kernel import (
    PreflightAuthority,
    RequestedContentClass,
    TurnKernel,
)
from cera.reasoner import (
    CodexReasonerDraftV2,
    SeedDossierAssembler,
    SeedDossierAssemblyRequest,
    codex_reasoner_draft_v2_json_schema,
    compile_reasoner_draft,
)
from cera.realization import (
    RealizationVerificationStatus,
    SceneRealizationVerificationDraft,
    ScriptedSceneRealizationVerifierPort,
)
from cera.runtime import (
    IngressPublicationEvidence,
    LiveShapedTurnPipeline,
    OrdinaryApplicationRequestV2,
    TurnStageAuditJournal,
)
from cera.registry import build_schema_registry
from cera.runtime.pipeline import LiveShapedTurnFailure
from cera.schema import from_mapping
from cera.serialization import canonical_json
from cera.sillytavern.models import (
    CERA_VIRTUAL_MODEL,
    ChatMessage,
    SillyTavernChatRequest,
)
from cera.source_inventory import validate_repository_source_inventory
from cera.storage import SQLiteAuthorityStore
from cera.contracts import SourceUnitClassification

import tests.test_deepseek_scene_composer as deepseek_support
import tests.test_live_shaped_pipeline as live_support
from tests.structural_v2_fixtures import (
    composition_draft_from_submission,
)


ROOT = Path(__file__).resolve().parents[1]


def _obligation(
    suffix: str,
    plan: EvidenceQueryPlan,
) -> EvidenceObligation:
    return EvidenceObligation(
        schema_version=EvidenceObligation.SCHEMA_VERSION,
        obligation_id=deterministic_id(
            IdKind.OBLIGATION,
            "cera.test.structural_v2.obligation",
            suffix,
        ),
        kind=EvidenceObligationKind.QUERY_PLAN,
        record_id=None,
        subject_id=None,
        record_type=None,
        required_sections=(
            "remembered_content",
            "learned_meaning_or_belief_pressure",
            "possible_retrieval_cues",
        ),
        query_plan=plan,
        reason="Resolve the indirect memory cue before reasoning.",
    )


def _hana_memory_plan(
    *,
    ambiguity_policy: EvidenceAmbiguityPolicy = (
        EvidenceAmbiguityPolicy.REQUIRE_UNAMBIGUOUS
    ),
) -> EvidenceQueryPlan:
    return EvidenceQueryPlan(
        schema_version=EvidenceQueryPlan.SCHEMA_VERSION,
        primary_terms=("unanswered", "messages"),
        alternate_term_sets=(
            ("missed", "calls"),
            ("years", "distance"),
        ),
        entity_ids=(CHARACTER_IDS["Hana"],),
        tags=(),
        record_types=(EvidenceRecordType.MEMORY,),
        limit=5,
        maximum_variants=4,
        ambiguity_policy=ambiguity_policy,
    )


class StructuralV2SchemaDomainTests(unittest.TestCase):
    def test_reasoner_schema_and_domain_reject_channel_owner_mutations(self) -> None:
        support = live_support.LiveShapedPipelineTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        args = support.adult_case("v2-schema-domain")
        runner = args[4]
        valid = json.loads(runner.output_text)
        schema = codex_reasoner_draft_v2_json_schema()
        Draft202012Validator(schema).validate(valid)
        from_mapping(CodexReasonerDraftV2, valid)

        mutations: list[dict[str, object]] = []
        owned_scene_channel = json.loads(json.dumps(valid))
        owned_scene_channel["adult_craft_need"]["beat_requirements"][0][
            "channel_requirements"
        ][0]["character_id"] = str(CHARACTER_IDS["Hana"])
        mutations.append(owned_scene_channel)

        ownerless_dialogue = json.loads(json.dumps(valid))
        ownerless_dialogue["adult_craft_need"]["beat_requirements"][0][
            "channel_requirements"
        ][0]["channel"] = "dialogue"
        mutations.append(ownerless_dialogue)

        malformed_enum = json.loads(json.dumps(valid))
        malformed_enum["adult_craft_need"]["beat_requirements"][0][
            "channel_requirements"
        ][0]["channel"] = "trust"
        mutations.append(malformed_enum)

        for index, payload in enumerate(mutations):
            with self.subTest(index=index):
                self.assertTrue(
                    tuple(Draft202012Validator(schema).iter_errors(payload))
                )
                with self.assertRaises(ContractValidationError):
                    from_mapping(CodexReasonerDraftV2, payload)

    def test_python_derives_canonical_reasoner_bookkeeping(self) -> None:
        support = live_support.LiveShapedPipelineTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        args = support.adult_case("v2-python-bookkeeping")
        request, runner = args[0], args[4]
        draft_payload = json.loads(runner.output_text)
        draft = from_mapping(CodexReasonerDraftV2, draft_payload)
        citation_ids = tuple(
            value
            for move in draft.character_moves
            for value in move.evidence_ids
        )
        fetched = support.real.sandbox.service.fetch_evidence(
            request.prepared_turn.evidence_snapshot,
            EvidenceFetchRequest(
                tuple(dict.fromkeys(citation_ids)),
                ("claim", "knowledge"),
            ),
        )
        first = compile_reasoner_draft(
            request,
            draft,
            authorized_exact_evidence=fetched.exact_records,
        )
        second = compile_reasoner_draft(
            request,
            draft,
            authorized_exact_evidence=fetched.exact_records,
        )
        self.assertEqual(first, second)
        self.assertIsNotNone(first.decision)
        assert first.decision is not None
        provider_json = canonical_json(draft_payload)
        self.assertNotIn(str(first.decision.decision_id), provider_json)
        self.assertNotIn(
            str(first.decision.current_segment.ordered_beats[0].beat_id),
            provider_json,
        )
        self.assertEqual(
            first.hard_citations[0].record_version,
            fetched.exact_records[0].metadata.record_version,
        )

    def test_composer_dto_rejects_removed_bookkeeping_and_bad_enums(self) -> None:
        support = deepseek_support.DeepSeekSceneComposerTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        request = support.request_with_context("v2-minimal-composer")
        valid = composition_draft_from_submission(
            support.base.submission(request, "v2-minimal-composer")
        )
        schema = deepseek_composition_draft_v2_json_schema()
        Draft202012Validator(schema).validate(valid)
        from_mapping(DeepSeekCompositionDraftV2, valid)

        mutations = []
        provider_bookkeeping = json.loads(json.dumps(valid))
        provider_bookkeeping["semantic_inference"] = {
            "source": "trust",
            "target": "belief",
        }
        mutations.append(provider_bookkeeping)
        bad_kind = json.loads(json.dumps(valid))
        bad_kind["realization_anchors"][0]["kind"] = "trust"
        mutations.append(bad_kind)
        missing_owner = json.loads(json.dumps(valid))
        del missing_owner["realization_anchors"][0]["owner_id"]
        mutations.append(missing_owner)

        for index, payload in enumerate(mutations):
            with self.subTest(index=index):
                self.assertTrue(
                    tuple(Draft202012Validator(schema).iter_errors(payload))
                )
                with self.assertRaises(ContractValidationError):
                    from_mapping(DeepSeekCompositionDraftV2, payload)


class StructuralV2RetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RealGenesisSandbox.create(ROOT)
        self.addCleanup(self.sandbox.close)

    def test_paraphrase_plan_resolves_hana_memory_and_typed_continuity(self) -> None:
        snapshot = self.sandbox.open_snapshot(
            "v2-paraphrase",
            scope=self.sandbox.character_scope("Hana"),
        )
        search = self.sandbox.service.search_query_plan(
            snapshot,
            _hana_memory_plan(),
        )
        self.assertEqual(len(search.references), 1)
        reference = search.references[0]
        self.assertIs(reference.metadata.record_type, EvidenceRecordType.MEMORY)
        self.assertIn("genesis_alias:hm08", reference.tags)
        self.assertEqual(search.receipt.followup_search_count, 1)

        continuity = self.sandbox.service.get_continuity(
            snapshot,
            ContinuityRequest(
                (reference.evidence_id,),
                ("claim", "knowledge"),
                maximum_depth=2,
            ),
        )
        self.assertEqual(
            tuple(value.metadata.record_type for value in continuity.exact_records),
            (EvidenceRecordType.MEMORY, EvidenceRecordType.EVENT_FACT),
        )

    def test_seed_assembler_reauthorizes_exact_records_and_detects_tamper(self) -> None:
        snapshot = self.sandbox.open_snapshot(
            "v2-seed-reauth",
            scope=self.sandbox.character_scope("Hana"),
        )
        search = self.sandbox.service.search_query_plan(
            snapshot,
            _hana_memory_plan(),
        )
        fetched = self.sandbox.service.fetch_evidence(
            snapshot,
            EvidenceFetchRequest(
                (search.references[0].evidence_id,),
                (
                    "remembered_content",
                    "learned_meaning_or_belief_pressure",
                    "possible_retrieval_cues",
                ),
            ),
        )
        assembler = SeedDossierAssembler(self.sandbox.service)
        request = SeedDossierAssemblyRequest(
            snapshot=snapshot,
            aware_character_ids=(CHARACTER_IDS["Hana"],),
            preexpanded_exact_evidence=fetched.exact_records,
            evidence_obligations=(),
            scene_anchors=("Shared room after dinner.",),
            explicit_unknowns=("Hana does not know the objective affair evidence.",),
            prohibited_inferences=("Do not transfer Enne-private knowledge.",),
        )
        result = assembler.assemble(request)
        self.assertTrue(result.ready)
        self.assertEqual(len(result.receipt.reauthorized_seed_hashes), 1)
        self.assertEqual(result.receipt.authoritative_store_writes, 0)

        tampered = replace(
            fetched.exact_records[0],
            sections_json='{"remembered_content":"invented"}',
        )
        with self.assertRaises(EvidenceServiceError):
            assembler.assemble(
                replace(
                    request,
                    preexpanded_exact_evidence=(tampered,),
                )
            )

        mia_snapshot = self.sandbox.open_snapshot(
            "v2-seed-wrong-owner",
            scope=self.sandbox.character_scope("Mia"),
        )
        with self.assertRaises(EvidenceServiceError):
            assembler.assemble(
                replace(
                    request,
                    snapshot=mia_snapshot,
                    aware_character_ids=(CHARACTER_IDS["Mia"],),
                )
            )

        wrong_revision = replace(
            fetched.exact_records[0],
            metadata=replace(
                fetched.exact_records[0].metadata,
                genesis_revision_id=deterministic_id(
                    IdKind.GENESIS_REVISION,
                    "cera.test.structural_v2.revision",
                    "wrong",
                ),
            ),
        )
        with self.assertRaises(EvidenceServiceError):
            assembler.assemble(
                replace(
                    request,
                    preexpanded_exact_evidence=(wrong_revision,),
                )
            )

    def test_ambiguous_and_private_queries_fail_closed(self) -> None:
        system_snapshot = self.sandbox.open_snapshot("v2-ambiguous")
        broad = EvidenceQueryPlan(
            schema_version=EvidenceQueryPlan.SCHEMA_VERSION,
            primary_terms=("ted",),
            alternate_term_sets=(),
            entity_ids=(),
            tags=(),
            record_types=(EvidenceRecordType.MEMORY,),
            limit=8,
            maximum_variants=1,
            ambiguity_policy=EvidenceAmbiguityPolicy.REQUIRE_UNAMBIGUOUS,
        )
        broad_result = SeedDossierAssembler(self.sandbox.service).assemble(
            SeedDossierAssemblyRequest(
                snapshot=system_snapshot,
                aware_character_ids=(CHARACTER_IDS["Hana"],),
                preexpanded_exact_evidence=(),
                evidence_obligations=(_obligation("ambiguous", broad),),
                scene_anchors=("Shared room.",),
                explicit_unknowns=("The cue is ambiguous.",),
                prohibited_inferences=("Do not choose a memory arbitrarily.",),
            )
        )
        self.assertFalse(broad_result.ready)
        self.assertEqual(
            broad_result.receipt.obligation_resolutions[0].status.value,
            "ambiguous",
        )

        mia_snapshot = self.sandbox.open_snapshot(
            "v2-private-denial",
            scope=self.sandbox.character_scope("Mia"),
        )
        private_result = SeedDossierAssembler(self.sandbox.service).assemble(
            SeedDossierAssemblyRequest(
                snapshot=mia_snapshot,
                aware_character_ids=(CHARACTER_IDS["Mia"],),
                preexpanded_exact_evidence=(),
                evidence_obligations=(
                    _obligation("private-denial", _hana_memory_plan()),
                ),
                scene_anchors=("Mia is present.",),
                explicit_unknowns=("Hana's private memory is unavailable.",),
                prohibited_inferences=("Do not leak owner-private evidence.",),
            )
        )
        self.assertFalse(private_result.ready)
        self.assertEqual(private_result.receipt.exact_evidence_ids, ())


class StructuralV2IngressTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RealGenesisSandbox.create(ROOT)
        self.addCleanup(self.sandbox.close)

    def _envelope(self, message: str) -> RawTurnEnvelope:
        ted = self.sandbox.protected_user_id()
        hana = CHARACTER_IDS["Hana"]
        return RawTurnEnvelope(
            schema_version=RawTurnEnvelope.SCHEMA_VERSION,
            world_id=self.sandbox.world_id,
            request_id=deterministic_id(
                IdKind.REQUEST,
                "cera.test.structural_v2.raw_turn",
                message,
            ),
            session_id=self.sandbox.session_id(),
            branch_id=self.sandbox.branch_id,
            expected_generation=0,
            expected_parent_artifact_id=None,
            genesis_revision_id=self.sandbox.revision_id,
            protected_user_id=ted,
            present_character_ids=(ted, hana),
            eligible_responder_ids=(hana,),
            raw_message=message,
            idempotency_key="structural-v2-raw-turn",
            world_mode=EvidenceWorldMode.REAL,
            access_scope=self.sandbox.system_scope(),
            preflight_authority=PreflightAuthority(
                RequestedContentClass.ORDINARY
            ),
            hard_boundaries=(
                "Do not author the protected user.",
                "Do not infer unavailable private evidence.",
            ),
        )

    def test_raw_ingress_preserves_exact_ordinary_source_and_assembles_seed(self) -> None:
        message = "Ted asks Hana about years of unanswered messages."
        envelope = self._envelope(message)
        interpretation = IntentInterpretationDraft(
            schema_version=IntentInterpretationDraft.SCHEMA_VERSION,
            source_spans=(
                InterpretedSourceSpan(
                    0,
                    len(message),
                    SourceUnitClassification.MESSAGE,
                    message,
                ),
            ),
            requested_content_class="ordinary",
            requested_responder_ids=(CHARACTER_IDS["Hana"],),
            requested_route_hints=(),
            evidence_obligations=(
                _obligation("raw-ingress", _hana_memory_plan()),
            ),
            scene_anchors=("Hana and Ted are in the shared room.",),
            explicit_unknowns=("Ted's next action is unknown.",),
            prohibited_inferences=("Do not transfer private knowledge.",),
        )
        facade = RawTurnIngressFacade(
            turn_kernel=TurnKernel(self.sandbox.service),
            intent_interpreter=ScriptedIntentInterpreterPort(interpretation),
            seed_assembler=SeedDossierAssembler(self.sandbox.service),
        )
        prepared = facade.prepare(envelope)
        self.assertTrue(prepared.ready_for_reasoner)
        self.assertEqual(
            prepared.reasoner_request.source_view.units[0].safe_text,
            message,
        )
        self.assertEqual(
            prepared.reasoner_request.seed_dossier.seed_receipt_id,
            prepared.seed_assembly.receipt.seed_receipt_id,
        )
        publication_evidence = IngressPublicationEvidence.create(
            request_id=envelope.request_id,
            source_sha256=prepared.reasoner_request.prepared_turn.request.source_sha256,
            reasoner_request_sha256=prepared.reasoner_request.request_sha256,
            interpretation_receipt=prepared.interpretation_receipt,
            seed_receipt=prepared.seed_assembly.receipt,
            seed_lookup_receipts=prepared.seed_assembly.lookup_receipts,
        )
        self.assertEqual(
            build_schema_registry().decode(
                json.loads(canonical_json(publication_evidence))
            ),
            publication_evidence,
        )
        self.assertIn(
            OrdinaryApplicationRequestV2.SCHEMA_VERSION,
            build_schema_registry().versions,
        )

        rewritten = replace(
            interpretation,
            source_spans=(
                replace(
                    interpretation.source_spans[0],
                    reasoner_safe_text="A different event.",
                ),
            ),
        )
        with self.assertRaises(ContractValidationError):
            RawTurnIngressFacade(
                turn_kernel=TurnKernel(self.sandbox.service),
                intent_interpreter=ScriptedIntentInterpreterPort(rewritten),
                seed_assembler=SeedDossierAssembler(self.sandbox.service),
            ).prepare(
                replace(
                    envelope,
                    request_id=deterministic_id(
                        IdKind.REQUEST,
                        "cera.test.structural_v2.raw_turn",
                        "rewritten",
                    ),
                    idempotency_key="structural-v2-rewritten",
                )
            )

    def test_default_ingress_is_exact_provider_free_and_leaves_semantics_to_codex(self) -> None:
        facade = RawTurnIngressFacade(
            turn_kernel=TurnKernel(self.sandbox.service),
            seed_assembler=SeedDossierAssembler(self.sandbox.service),
        )
        first_message = "Ted knocks on Hana's door and asks whether she wants coffee."
        first = facade.prepare(self._envelope(first_message))
        self.assertTrue(first.ready_for_reasoner)
        self.assertEqual(
            first.reasoner_request.source_view.units[0].safe_text,
            first_message,
        )
        self.assertEqual(first.interpretation.evidence_obligations, ())
        self.assertEqual(first.interpretation_receipt.external_provider_calls, 0)
        self.assertEqual(first.interpretation_receipt.authoritative_store_writes, 0)
        self.assertEqual(
            first.interpretation.requested_responder_ids,
            self._envelope(first_message).eligible_responder_ids,
        )
        self.assertIn(
            "Responder selection",
            first.interpretation.explicit_unknowns[0],
        )

    def test_prepared_continuous_ingress_is_durable_and_revalidated_after_restart(self) -> None:
        message = "Ted asks Hana about the unanswered messages."
        envelope = self._envelope(message)
        prepared = RawTurnIngressFacade(
            turn_kernel=TurnKernel(self.sandbox.service),
            seed_assembler=SeedDossierAssembler(self.sandbox.service),
        ).prepare(envelope)
        with TemporaryDirectory() as directory:
            root = Path(directory) / "continuous-ingress"
            registry = build_default_prepared_classifier_registry()
            first_store = ContinuousIngressAuthorityStore(
                root,
                prepared_classifier_registry=registry,
            )
            receipt = PreparedContinuousIngressBridge(
                authority=first_store,
                classifier_registry=registry,
            ).issue(
                envelope=envelope,
                prepared=prepared,
                turn_id="turn-001",
            )

            restarted_store = ContinuousIngressAuthorityStore(
                root,
                prepared_classifier_registry=build_default_prepared_classifier_registry(),
            )
            self.assertEqual(
                restarted_store.resolve(
                    receipt_id=receipt.receipt_id,
                    receipt_sha256=receipt.receipt_sha256,
                ),
                receipt,
            )

            record_path = root / f"{receipt.receipt_sha256}.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["authority_evidence"]["classification_receipt"][
                "classification_adapter_id"
            ] = "cera.prepared_ingress_classifier.substituted.v1"
            record_path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaises(StateConflictError):
                restarted_store.resolve(
                    receipt_id=receipt.receipt_id,
                    receipt_sha256=receipt.receipt_sha256,
                )

    def test_prepared_continuous_ingress_rejects_envelope_substitution(self) -> None:
        message = "Ted asks Hana whether she wants coffee."
        envelope = self._envelope(message)
        prepared = RawTurnIngressFacade(
            turn_kernel=TurnKernel(self.sandbox.service),
            seed_assembler=SeedDossierAssembler(self.sandbox.service),
        ).prepare(envelope)
        substituted = replace(
            envelope,
            raw_message=message + " Now.",
            request_id=deterministic_id(
                IdKind.REQUEST,
                "cera.test.structural_v2.raw_turn",
                "substituted-continuous-ingress",
            ),
        )
        with TemporaryDirectory() as directory:
            registry = build_default_prepared_classifier_registry()
            bridge = PreparedContinuousIngressBridge(
                authority=ContinuousIngressAuthorityStore(
                    Path(directory), prepared_classifier_registry=registry
                ),
                classifier_registry=registry,
            )
            with self.assertRaises(StateConflictError):
                bridge.issue(
                    envelope=substituted,
                    prepared=prepared,
                    turn_id="turn-001",
                )

    def test_sillytavern_shadow_request_uses_resolved_prepared_receipt(self) -> None:
        message = "Ted asks Hana whether she wants coffee."
        envelope = self._envelope(message)
        registry = build_default_prepared_classifier_registry()
        with TemporaryDirectory() as directory:
            store = ContinuousIngressAuthorityStore(
                Path(directory) / "authority",
                prepared_classifier_registry=registry,
            )
            shadow = ContinuousSillyTavernShadowRequestBridge(
                raw_ingress=RawTurnIngressFacade(
                    turn_kernel=TurnKernel(self.sandbox.service),
                    seed_assembler=SeedDossierAssembler(self.sandbox.service),
                ),
                prepared_ingress=PreparedContinuousIngressBridge(
                    authority=store,
                    classifier_registry=registry,
                ),
            )
            chat = SillyTavernChatRequest(
                model=CERA_VIRTUAL_MODEL,
                messages=(ChatMessage(role="user", content=message),),
                stream=False,
                cera_scene_change=True,
            )
            result = shadow.prepare(
                chat_request=chat,
                envelope=envelope,
                scene_id="scene:arrival",
                turn_id="turn-001",
                current_authority_packet={"authority": "shadow_only"},
            )
            resolved = store.resolve(
                receipt_id=result.ingress_receipt_id,
                receipt_sha256=result.ingress_receipt_sha256,
            )
            self.assertEqual(result.request.user_message, message)
            self.assertEqual(result.request.ingress_receipt_sha256, resolved.receipt_sha256)
            self.assertTrue(result.request.cera_scene_change)
            self.assertEqual(resolved.source_units[0].kind.value, "narration")
            self.assertIsNone(resolved.source_units[0].actor_id)

            changed_chat = replace(
                chat,
                messages=(ChatMessage(role="user", content=message + " Changed."),),
            )
            with self.assertRaises(StateConflictError):
                shadow.prepare(
                    chat_request=changed_chat,
                    envelope=envelope,
                    scene_id="scene:arrival",
                    turn_id="turn-002",
                    current_authority_packet={},
                )

    def test_prepared_classifier_registry_rejects_unknown_and_substituted_types(self) -> None:
        registry = build_default_prepared_classifier_registry()
        with TemporaryDirectory() as directory:
            store = ContinuousIngressAuthorityStore(
                Path(directory), prepared_classifier_registry=registry
            )
            with self.assertRaises(ContractValidationError):
                PreparedContinuousIngressBridge(
                    authority=store,
                    classifier_registry=registry,
                    classification_adapter_id="cera.prepared_ingress_classifier.unknown.v1",
                )

        class RenamedClassifier(RepositoryPreparedIngressClassifierV1):
            pass

        with self.assertRaisesRegex(
            ContractValidationError, "not repository controlled"
        ):
            PreparedIngressClassifierRegistry((RenamedClassifier(),))

    def test_prepared_ingress_authority_rejects_identity_and_span_substitutions(self) -> None:
        message = "Ted asks Hana whether she wants coffee."
        envelope = self._envelope(message)
        prepared = RawTurnIngressFacade(
            turn_kernel=TurnKernel(self.sandbox.service),
            seed_assembler=SeedDossierAssembler(self.sandbox.service),
        ).prepare(envelope)
        mutations = {
            "world": lambda record: record["authority_evidence"]["envelope"].__setitem__(
                "world_id", "world:substituted"
            ),
            "branch": lambda record: record["authority_evidence"]["envelope"].__setitem__(
                "branch_id", "branch:substituted"
            ),
            "session": lambda record: record["authority_evidence"]["envelope"].__setitem__(
                "session_id", "session:substituted"
            ),
            "request": lambda record: record["authority_evidence"]["envelope"].__setitem__(
                "request_id", "request:substituted"
            ),
            "idempotency": lambda record: record["authority_evidence"]["envelope"].__setitem__(
                "idempotency_key", "substituted-idempotency"
            ),
            "raw_source": lambda record: record["authority_evidence"]["envelope"].__setitem__(
                "raw_message", message + " Changed."
            ),
            "protected_user": lambda record: record["authority_evidence"]["envelope"].__setitem__(
                "protected_user_id", "character:substituted"
            ),
            "adapter": lambda record: record["authority_evidence"]["classification_receipt"].__setitem__(
                "classification_adapter_id", "cera.prepared_ingress_classifier.substituted.v1"
            ),
            "classifier_source": lambda record: record["authority_evidence"][
                "classifier_descriptor"
            ].__setitem__("implementation_source_sha256", "0" * 64),
            "classifier_receipt_descriptor": lambda record: record[
                "authority_evidence"
            ]["classification_receipt"].__setitem__(
                "classifier_descriptor_sha256", "0" * 64
            ),
            "source_span": lambda record: record["authority_evidence"]["classification_receipt"][
                "source_units"
            ][0].__setitem__("exact_text", message[:-1]),
            "actor": lambda record: record["authority_evidence"]["classification_receipt"][
                "source_units"
            ][0].__setitem__("actor_id", "character:substituted"),
            "speaker": lambda record: record["authority_evidence"]["classification_receipt"][
                "source_units"
            ][0].__setitem__("speaker_id", "character:substituted"),
            "turn": lambda record: record["receipt"].__setitem__(
                "turn_id", "turn-substituted"
            ),
        }
        with TemporaryDirectory() as directory:
            root = Path(directory)
            registry = build_default_prepared_classifier_registry()
            store = ContinuousIngressAuthorityStore(
                root, prepared_classifier_registry=registry
            )
            receipt = PreparedContinuousIngressBridge(
                authority=store,
                classifier_registry=registry,
            ).issue(envelope=envelope, prepared=prepared, turn_id="turn-001")
            record_path = root / f"{receipt.receipt_sha256}.json"
            original = json.loads(record_path.read_text(encoding="utf-8"))
            for label, mutate in mutations.items():
                with self.subTest(label=label):
                    changed = json.loads(json.dumps(original))
                    mutate(changed)
                    record_path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaises(
                        (StateConflictError, ContractValidationError)
                    ):
                        store.resolve(
                            receipt_id=receipt.receipt_id,
                            receipt_sha256=receipt.receipt_sha256,
                        )
            record_path.write_text(json.dumps(original), encoding="utf-8")
            self.assertEqual(
                store.resolve(
                    receipt_id=receipt.receipt_id,
                    receipt_sha256=receipt.receipt_sha256,
                ),
                receipt,
            )

    def test_continuous_fixture_prefix_is_not_authority(self) -> None:
        with TemporaryDirectory() as directory:
            store = ContinuousIngressAuthorityStore(Path(directory))
            with self.assertRaises(ContractValidationError):
                store.issue_frozen_fixture(
                    fixture_id="cera.fixture.unregistered",
                    idempotency_key="unregistered-fixture",
                )


class StructuralV2FailureAndInventoryTests(unittest.TestCase):
    def test_rejected_realization_is_journaled_without_story_truth(self) -> None:
        support = live_support.LiveShapedPipelineTests("runTest")
        support.setUp()
        self.addCleanup(support.doCleanups)
        args = support.ordinary_case("v2-realization-reject")
        journal = TurnStageAuditJournal(support.real.sandbox.store)
        verifier = ScriptedSceneRealizationVerifierPort(
            SceneRealizationVerificationDraft(
                status=RealizationVerificationStatus.REJECTED,
                verified_beat_ids=(),
                verified_participant_ids=(),
                violation_codes=("participant_not_realized",),
            )
        )
        pipeline = LiveShapedTurnPipeline(
            support.real.reasoner,
            ComposerContextAssembler(support.real.sandbox.service),
            ComposerCoordinator(),
            realization_verifier_port=verifier,
            stage_audit_journal=journal,
        )
        with self.assertRaises(LiveShapedTurnFailure):
            pipeline.execute(*args[:4])
        self.assertEqual(
            support.real.sandbox.store.table_count("artifacts"),
            0,
        )
        audit = journal.inspect_restart(
            args[0].prepared_turn.request.request_id
        )
        self.assertFalse(audit.automatic_resume_permitted)
        self.assertEqual(audit.last_status.value, "failed")
        self.assertEqual(
            audit.entries[-1].stage,
            "scene_realization_verification",
        )
        failure_id = audit.entries[-1].failure_bundle_id
        assert failure_id is not None
        bundle = support.real.sandbox.store.get_turn_failure_evidence(failure_id)
        kinds = {value.evidence_kind for value in bundle.retained_evidence}
        self.assertTrue(
            {
                "scene_reasoner_receipt",
                "composer_context_assembly_receipt",
                "scene_composer_receipt",
                "composer_validation_receipt",
                "reasoner_provider_call_receipt",
                "composer_provider_call_receipt",
            }.issubset(kinds)
        )
        self.assertFalse(bundle.retains_raw_source)
        self.assertFalse(bundle.retains_story_prose)
        self.assertFalse(bundle.retains_private_evidence)
        self.assertFalse(bundle.story_state_committed)

        restarted = SQLiteAuthorityStore(support.real.sandbox.database_path)
        restarted_audit = TurnStageAuditJournal(restarted).inspect_restart(
            args[0].prepared_turn.request.request_id
        )
        self.assertEqual(restarted_audit.entries, audit.entries)
        self.assertFalse(restarted_audit.automatic_resume_permitted)

        with self.assertRaises(ContractValidationError):
            replace(bundle, retains_story_prose=True)

    def test_repository_source_inventory_includes_runtime_and_v2_packages(self) -> None:
        receipt = validate_repository_source_inventory(ROOT)
        self.assertEqual(receipt.status, "valid")
        self.assertIn("runtime", receipt.package_names)
        self.assertIn("ingress", receipt.package_names)
        self.assertIn("realization", receipt.package_names)
        self.assertIn("continuous", receipt.package_names)
        self.assertIn("src/cera/runtime/pipeline.py", receipt.source_paths)
        self.assertIn("src/cera/continuous/runtime.py", receipt.source_paths)
