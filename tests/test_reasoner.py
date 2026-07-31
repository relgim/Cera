from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import sqlite3
import unittest

from cera.config import Environment
from cera.contracts import (
    BeatState,
    CharacterMove,
    CurrentSegment,
    DecisionRoute,
    FutureSegment,
    SceneDecision,
    SequenceBeat,
)
from cera.evidence import (
    EvidenceFetchRequest,
    EvidenceLimits,
    EvidenceSearchRequest,
    EvidenceService,
)
from cera.errors import ConfigurationError, ContractValidationError, ErrorCode
from cera.ids import IdKind, TypedId
from cera.kernel import (
    AdultConsentStatus,
    ProposedStateRecord,
    ProtectedUserProvenance,
    StateMutationTarget,
    TurnKernel,
    TurnKernelFailure,
)
from cera.reasoner import (
    FakeReasonerFixture,
    FakeSceneReasonerPort,
    FakeToolCall,
    FakeToolOperation,
    InterventionReason,
    ParticipationRole,
    ParticipationSelection,
    ReasonerCoordinator,
    ReasonerEvidenceCitation,
    ReasonerEvidenceTools,
    ReasonerExecutionFailure,
    ReasonerOutcome,
    ReasonerOutcomeStatus,
    ReasonerSeedDossier,
    ReasonerSourceMode,
    ReasonerSourceUnit,
    ReasonerSourceView,
    ResolveEntitiesRequest,
    SceneReasonerRequest,
)
from cera.schema import from_mapping
from cera.serialization import to_primitive
import tests.test_sqlite_store as sqlite_test_support
import tests.test_turn_kernel as kernel_test_support


ident = kernel_test_support.ident


class ReasonerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = kernel_test_support.TurnKernelTests("runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.store = self.base.store
        self.service = self.base.service
        self.kernel = self.base.kernel
        self.coordinator = ReasonerCoordinator(self.service, self.kernel)
        self.alpha = self.base.alpha
        self.beta = self.base.beta
        self.ted = self.base.ted

    def test_hard_citation_preserves_every_authority_record_kind(self) -> None:
        for kind in (
            IdKind.RECORD,
            IdKind.EVENT,
            IdKind.MEMORY,
            IdKind.RELATIONSHIP,
            IdKind.THREAD,
            IdKind.MATERIAL,
            IdKind.DEVELOPMENT,
        ):
            citation = ReasonerEvidenceCitation(
                evidence_id=ident(IdKind.EVIDENCE, f"citation-{kind.value}"),
                record_id=ident(kind, f"citation-{kind.value}"),
                record_version=1,
            )
            self.assertIs(citation.record_id.kind, kind)

        with self.assertRaisesRegex(Exception, "authority-record ID"):
            ReasonerEvidenceCitation(
                evidence_id=ident(IdKind.EVIDENCE, "citation-invalid"),
                record_id=ident(IdKind.ARTIFACT, "citation-invalid"),
                record_version=1,
            )

    def prepared(
        self,
        suffix: str,
        *,
        responders: tuple[TypedId, ...] | None = None,
    ):
        return self.kernel.prepare_turn(
            self.base.command(suffix, responders=responders),
            access_scope=self.base.access,
        )

    def request(
        self,
        prepared,
        *,
        aware: tuple[TypedId, ...] | None = None,
        mode: ReasonerSourceMode = ReasonerSourceMode.ORDINARY_EXACT,
        protected: bool = False,
    ) -> SceneReasonerRequest:
        units = tuple(
            ReasonerSourceUnit(
                source_unit_id=unit.source_unit_id,
                classification=unit.classification,
                safe_text=f"Synthetic safe source unit {index}.",
            )
            for index, unit in enumerate(prepared.request.source_units)
        )
        return SceneReasonerRequest(
            schema_version=SceneReasonerRequest.SCHEMA_VERSION,
            prepared_turn=prepared,
            source_view=ReasonerSourceView(
                mode=mode,
                source_sha256=prepared.request.source_sha256,
                units=units,
                contains_exact_protected_adult_prose=protected,
            ),
            seed_dossier=ReasonerSeedDossier(
                snapshot_token=prepared.evidence_snapshot.snapshot_token,
                aware_character_ids=aware or (self.alpha, self.beta),
                exact_seed_evidence=(),
                scene_anchors=("Synthetic room anchor.",),
                explicit_unknowns=("Unprovided future choice remains unknown.",),
                prohibited_inferences=("Do not author the protected user.",),
            ),
            hard_boundaries=(
                "Return operational structure only.",
                "Do not author the protected user.",
            ),
        )

    def evidence_id(self, prepared, record_suffix: str) -> TypedId:
        return EvidenceService._evidence_id(
            prepared.evidence_snapshot,
            ident(IdKind.RECORD, record_suffix),
        )

    def decision(
        self,
        suffix: str,
        *,
        responders: tuple[TypedId, ...],
        evidence_by_character: dict[TypedId, TypedId],
        floor_owner: TypedId | None = None,
        route: DecisionRoute = DecisionRoute.ORDINARY,
        protected_actor: TypedId | None = None,
    ) -> SceneDecision:
        lead = floor_owner or responders[0]
        beats = tuple(
            SequenceBeat(
                beat_id=ident(IdKind.BEAT, f"reasoner-{suffix}-{index}"),
                actor_id=(protected_actor if index == 0 and protected_actor else character_id),
                state=BeatState.ATTEMPTED,
                neutral_event=f"Synthetic operational beat {index}.",
                evidence_ids=(evidence_by_character[character_id],),
            )
            for index, character_id in enumerate(responders)
        )
        return SceneDecision(
            schema_version=SceneDecision.SCHEMA_VERSION,
            decision_id=ident(IdKind.DECISION, f"reasoner-{suffix}"),
            route=route,
            scene_intent="Respond to the supplied synthetic situation.",
            responding_npc_ids=responders,
            floor_owner_id=lead,
            character_moves=tuple(
                CharacterMove(
                    character_id=character_id,
                    perception="Synthetic bounded perception.",
                    selected_intent=f"Synthetic intent {index}.",
                    action_direction=f"Synthetic tactic {index}.",
                    evidence_ids=(evidence_by_character[character_id],),
                    knowledge_constraints=("Use only owner-authorized evidence.",),
                )
                for index, character_id in enumerate(responders)
            ),
            current_segment=CurrentSegment(
                segment_id=ident(IdKind.SEGMENT, f"current-{suffix}"),
                ordered_beats=beats,
                stop_before="The protected user's next unsupplied choice.",
            ),
            future_segments=(
                FutureSegment(
                    segment_id=ident(IdKind.SEGMENT, f"future-{suffix}"),
                    status="conditional_plan_only",
                    activation_conditions=("If the protected user supplies a response.",),
                    invalidation_conditions=("If branch truth changes.",),
                    possible_consequences=("Character response may update.",),
                    open_user_choice="The protected user's next choice remains open.",
                ),
            ),
            writer_must_preserve=("Preserve the operational stop boundary.",),
            uncertainties=(),
            prohibited_inferences=("No protected-user private state.",),
            advisory_state_candidates=(),
        )

    def ready_outcome(
        self,
        decision: SceneDecision,
        *,
        record_by_evidence: dict[TypedId, tuple[TypedId, int]],
        secondary_reason: InterventionReason = InterventionReason.DIRECT_STAKE,
        deltas: tuple[ProposedStateRecord, ...] = (),
    ) -> ReasonerOutcome:
        participation = tuple(
            ParticipationSelection(
                character_id=character_id,
                role=(
                    ParticipationRole.LEAD
                    if character_id == decision.floor_owner_id
                    else ParticipationRole.SECONDARY
                ),
                intervention_reason=(
                    InterventionReason.FLOOR_OWNER
                    if character_id == decision.floor_owner_id
                    else secondary_reason
                ),
                evidence_ids=decision.character_moves[index].evidence_ids,
            )
            for index, character_id in enumerate(decision.responding_npc_ids)
        )
        used = {
            value
            for move in decision.character_moves
            for value in move.evidence_ids
        }
        return ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.DECISION_READY,
            decision=decision,
            participation=participation,
            hard_citations=tuple(
                ReasonerEvidenceCitation(
                    evidence_id=evidence_id,
                    record_id=record_by_evidence[evidence_id][0],
                    record_version=record_by_evidence[evidence_id][1],
                )
                for evidence_id in sorted(used, key=str)
            ),
            insufficiencies=(),
            blocker_code=None,
            advisory_state_deltas=deltas,
            protected_user_boundary_acknowledged=True,
        )

    def fixture(
        self,
        suffix: str,
        outcome: ReasonerOutcome,
        calls: tuple[FakeToolCall, ...] = (),
    ) -> FakeReasonerFixture:
        return FakeReasonerFixture(f"fixture-{suffix}", calls, outcome)

    def table_state(self) -> tuple[int, ...]:
        with closing(sqlite3.connect(self.base.fixture.database_path)) as connection:
            return tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "sources",
                    "generations",
                    "artifacts",
                    "authority_records",
                    "transaction_journal",
                    "commit_receipts",
                )
            )

    def test_ordinary_direct_decision_search_fetch_receipt_and_replay_are_deterministic(self) -> None:
        prepared = self.prepared("direct")
        evidence_id = self.evidence_id(prepared, "record-alpha-present")
        decision = self.decision(
            "direct",
            responders=(self.alpha,),
            evidence_by_character={self.alpha: evidence_id},
        )
        outcome = self.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 1)
            },
        )
        fixture = self.fixture(
            "direct",
            outcome,
            (
                FakeToolCall(FakeToolOperation.GET_TURN_SNAPSHOT, None),
                FakeToolCall(
                    FakeToolOperation.RESOLVE_ENTITIES,
                    ResolveEntitiesRequest((self.alpha,), limit=5),
                ),
                FakeToolCall(
                    FakeToolOperation.SEARCH_EVIDENCE,
                    EvidenceSearchRequest(terms=("present",), limit=5),
                ),
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((evidence_id,), ("claim", "provenance")),
                ),
            ),
        )
        port = FakeSceneReasonerPort(fixture)
        request = self.request(prepared)
        before = self.table_state()
        first = self.coordinator.execute(request, port, fixture=fixture)
        second = self.coordinator.execute(request, port, fixture=fixture)
        self.assertEqual(first, second)
        self.assertEqual(first.receipt.external_provider_calls, 0)
        self.assertEqual(first.receipt.tool_call_count, 4)
        self.assertEqual(first.receipt.reasoner_request_sha256, request.request_sha256)
        self.assertEqual(first.receipt.outcome_sha256, outcome.outcome_sha256)
        self.assertEqual(port.invocation_count, 2)
        self.assertEqual(before, self.table_state())

    def test_multi_character_floor_requires_justified_secondary(self) -> None:
        prepared = self.prepared("multi", responders=(self.alpha, self.beta))
        alpha_evidence = self.evidence_id(prepared, "record-alpha-present")
        beta_evidence = self.evidence_id(prepared, "record-beta-private")
        decision = self.decision(
            "multi",
            responders=(self.alpha, self.beta),
            evidence_by_character={self.alpha: alpha_evidence, self.beta: beta_evidence},
            floor_owner=self.alpha,
        )
        outcome = self.ready_outcome(
            decision,
            record_by_evidence={
                alpha_evidence: (ident(IdKind.RECORD, "record-alpha-present"), 1),
                beta_evidence: (ident(IdKind.RECORD, "record-beta-private"), 1),
            },
            secondary_reason=InterventionReason.DIRECT_STAKE,
        )
        fixture = self.fixture(
            "multi",
            outcome,
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest(
                        (alpha_evidence, beta_evidence), ("claim", "knowledge")
                    ),
                ),
            ),
        )
        result = self.coordinator.execute(
            self.request(prepared), FakeSceneReasonerPort(fixture), fixture=fixture
        )
        self.assertEqual(result.outcome.decision.floor_owner_id, self.alpha)
        self.assertEqual(len(result.outcome.participation), 2)

    def test_ineligible_unaware_or_protected_participant_is_rejected(self) -> None:
        prepared = self.prepared("unaware", responders=(self.alpha, self.beta))
        beta_evidence = self.evidence_id(prepared, "record-beta-private")
        decision = self.decision(
            "unaware",
            responders=(self.beta,),
            evidence_by_character={self.beta: beta_evidence},
        )
        outcome = self.ready_outcome(
            decision,
            record_by_evidence={
                beta_evidence: (ident(IdKind.RECORD, "record-beta-private"), 1)
            },
        )
        fixture = self.fixture(
            "unaware",
            outcome,
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((beta_evidence,), ("claim",)),
                ),
            ),
        )
        with self.assertRaises(ReasonerExecutionFailure) as unaware:
            self.coordinator.execute(
                self.request(prepared, aware=(self.alpha,)),
                FakeSceneReasonerPort(fixture),
                fixture=fixture,
            )
        self.assertIs(
            unaware.exception.envelope.error_code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )

        protected_prepared = replace(
            prepared,
            eligible_responding_npc_ids=(self.ted,),
            present_character_ids=(*prepared.present_character_ids,),
        )
        protected_evidence = beta_evidence
        protected_decision = self.decision(
            "protected",
            responders=(self.ted,),
            evidence_by_character={self.ted: protected_evidence},
            protected_actor=self.ted,
        )
        protected_outcome = self.ready_outcome(
            protected_decision,
            record_by_evidence={
                protected_evidence: (ident(IdKind.RECORD, "record-beta-private"), 1)
            },
        )
        protected_fixture = self.fixture(
            "protected",
            protected_outcome,
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((protected_evidence,), ("claim",)),
                ),
            ),
        )
        with self.assertRaises(ReasonerExecutionFailure):
            self.coordinator.execute(
                self.request(protected_prepared, aware=(self.ted,)),
                FakeSceneReasonerPort(protected_fixture),
                fixture=protected_fixture,
            )

    def test_false_memory_or_missing_evidence_stays_insufficient(self) -> None:
        prepared = self.prepared("insufficient")
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=("The asserted synthetic memory has no authorized evidence.",),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        fixture = self.fixture("insufficient", outcome)
        result = self.coordinator.execute(
            self.request(prepared), FakeSceneReasonerPort(fixture), fixture=fixture
        )
        self.assertIs(result.outcome.status, ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE)
        self.assertIsNone(result.outcome.decision)

    def test_search_reference_without_exact_fetch_cannot_support_hard_decision(self) -> None:
        prepared = self.prepared("search-only")
        evidence_id = self.evidence_id(prepared, "record-alpha-present")
        decision = self.decision(
            "search-only",
            responders=(self.alpha,),
            evidence_by_character={self.alpha: evidence_id},
        )
        outcome = self.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 1)
            },
        )
        fixture = self.fixture(
            "search-only",
            outcome,
            (
                FakeToolCall(
                    FakeToolOperation.SEARCH_EVIDENCE,
                    EvidenceSearchRequest(terms=("present",), limit=5),
                ),
            ),
        )
        with self.assertRaises(ReasonerExecutionFailure) as failure:
            self.coordinator.execute(
                self.request(prepared), FakeSceneReasonerPort(fixture), fixture=fixture
            )
        self.assertIn("never exactly authorized", failure.exception.envelope.message)

    def test_private_evidence_cannot_be_transferred_between_characters(self) -> None:
        prepared = self.prepared("privacy")
        beta_private = self.evidence_id(prepared, "record-beta-private")
        decision = self.decision(
            "privacy",
            responders=(self.alpha,),
            evidence_by_character={self.alpha: beta_private},
        )
        outcome = self.ready_outcome(
            decision,
            record_by_evidence={
                beta_private: (ident(IdKind.RECORD, "record-beta-private"), 1)
            },
        )
        fixture = self.fixture(
            "privacy",
            outcome,
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((beta_private,), ("claim", "knowledge")),
                ),
            ),
        )
        with self.assertRaises(ReasonerExecutionFailure) as failure:
            self.coordinator.execute(
                self.request(prepared), FakeSceneReasonerPort(fixture), fixture=fixture
            )
        self.assertIn("transferred", failure.exception.envelope.message)

    def test_unknown_or_stale_hard_citation_is_rejected(self) -> None:
        prepared = self.prepared("stale-citation")
        evidence_id = self.evidence_id(prepared, "record-alpha-present")
        decision = self.decision(
            "stale-citation",
            responders=(self.alpha,),
            evidence_by_character={self.alpha: evidence_id},
        )
        outcome = self.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 2)
            },
        )
        fixture = self.fixture(
            "stale-citation",
            outcome,
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((evidence_id,), ("claim",)),
                ),
            ),
        )
        with self.assertRaises(ReasonerExecutionFailure) as failure:
            self.coordinator.execute(
                self.request(prepared), FakeSceneReasonerPort(fixture), fixture=fixture
            )
        self.assertIn("unknown or stale", failure.exception.envelope.message)

    def test_stale_snapshot_between_tool_calls_fails_without_refresh(self) -> None:
        prepared = self.prepared("stale-tools")
        request = self.request(prepared)
        tools = ReasonerEvidenceTools(self.service, request)
        tools.search_evidence(EvidenceSearchRequest(terms=("alpha",), limit=1))
        builder = sqlite_test_support.SQLiteStoreTests("runTest")
        builder.store = self.store
        builder.branch_id = self.base.branch_id
        bundle = builder.bundle("reasoner-stale", parent=None)
        self.store.commit_turn(bundle)
        with self.assertRaisesRegex(Exception, "branch head or generation changed"):
            tools.search_evidence(EvidenceSearchRequest(terms=("alpha",), limit=1))

    def test_evidence_outage_and_budget_exhaustion_are_explicit_no_fallback(self) -> None:
        prepared = self.prepared("budget")
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=("No decision after bounded lookup.",),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        fixture = self.fixture(
            "budget",
            outcome,
            (
                FakeToolCall(
                    FakeToolOperation.SEARCH_EVIDENCE,
                    EvidenceSearchRequest(terms=("alpha",), limit=1),
                ),
                FakeToolCall(
                    FakeToolOperation.SEARCH_EVIDENCE,
                    EvidenceSearchRequest(terms=("beta",), limit=1),
                ),
            ),
        )
        limited_service = EvidenceService(
            self.store,
            limits=EvidenceLimits(maximum_followup_searches=1),
        )
        coordinator = ReasonerCoordinator(limited_service, TurnKernel(limited_service))
        port = FakeSceneReasonerPort(fixture)
        with self.assertRaises(ReasonerExecutionFailure) as failure:
            coordinator.execute(self.request(prepared), port, fixture=fixture)
        self.assertIs(
            failure.exception.envelope.error_code,
            ErrorCode.EVIDENCE_LIMIT_EXCEEDED,
        )
        self.assertEqual(port.invocation_count, 1)

        # Corrupt derived index is an explicit outage; no authority scan follows.
        self.store.rebuild_evidence_search_index()
        with closing(sqlite3.connect(self.base.fixture.database_path)) as connection:
            connection.execute(
                "INSERT INTO evidence_search_fts("
                "record_key, source_kind, record_id, record_sha256, searchable_text"
                ") VALUES ('authority|record:bad', 'authority', 'record:bad', ?, 'alpha')",
                ("0" * 64,),
            )
            connection.commit()
        one_search = self.fixture(
            "outage",
            outcome,
            (
                FakeToolCall(
                    FakeToolOperation.SEARCH_EVIDENCE,
                    EvidenceSearchRequest(terms=("alpha",), limit=1),
                ),
            ),
        )
        with self.assertRaises(ReasonerExecutionFailure) as outage:
            self.coordinator.execute(
                self.request(prepared),
                FakeSceneReasonerPort(one_search),
                fixture=one_search,
            )
        self.assertIs(outage.exception.envelope.error_code, ErrorCode.EVIDENCE_INDEX_INVALID)

    def test_advisory_state_delta_is_validated_but_never_commits(self) -> None:
        prepared = self.prepared("delta")
        evidence_id = self.evidence_id(prepared, "record-alpha-present")
        delta = ProposedStateRecord(
            record_id=ident(IdKind.DEVELOPMENT, "reasoner-advisory"),
            branch_id=prepared.request.branch_id,
            mutation_target=StateMutationTarget.BRANCH_OVERLAY,
            subject_ids=(self.alpha,),
            evidence_ids=(evidence_id,),
            protected_user_provenance=ProtectedUserProvenance.NOT_APPLICABLE,
        )
        decision = self.decision(
            "delta",
            responders=(self.alpha,),
            evidence_by_character={self.alpha: evidence_id},
        )
        outcome = self.ready_outcome(
            decision,
            record_by_evidence={
                evidence_id: (ident(IdKind.RECORD, "record-alpha-present"), 1)
            },
            deltas=(delta,),
        )
        fixture = self.fixture(
            "delta",
            outcome,
            (
                FakeToolCall(
                    FakeToolOperation.FETCH_EVIDENCE,
                    EvidenceFetchRequest((evidence_id,), ("claim",)),
                ),
            ),
        )
        before = self.table_state()
        result = self.coordinator.execute(
            self.request(prepared), FakeSceneReasonerPort(fixture), fixture=fixture
        )
        self.assertIsNotNone(result.state_delta_validation_receipt_id)
        self.assertEqual(before, self.table_state())

    def test_adult_preflight_failure_prevents_reasoner_and_safe_ledger_excludes_protected(self) -> None:
        insufficient = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=("Synthetic adult decision intentionally omitted.",),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        fixture = self.fixture("adult", insufficient)
        port = FakeSceneReasonerPort(fixture)
        with self.assertRaises(TurnKernelFailure):
            self.kernel.prepare_turn(
                self.base.command(
                    "adult-fail",
                    preflight=self.base.adult_authority(
                        consent=AdultConsentStatus.UNKNOWN
                    ),
                ),
                access_scope=self.base.access,
            )
        self.assertEqual(port.invocation_count, 0)

        adult_prepared = self.kernel.prepare_turn(
            self.base.command("adult-ok", preflight=self.base.adult_authority()),
            access_scope=self.base.access,
        )
        with self.assertRaises(ContractValidationError):
            self.request(
                adult_prepared,
                mode=ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER,
                protected=True,
            )
        safe_request = self.request(
            adult_prepared,
            mode=ReasonerSourceMode.ADULT_NON_GRAPHIC_LEDGER,
            protected=False,
        )
        result = self.coordinator.execute(safe_request, port, fixture=fixture)
        self.assertEqual(result.receipt.external_provider_calls, 0)

    def test_fake_is_rejected_in_production_and_unavailable_is_stable(self) -> None:
        prepared = self.prepared("unavailable")
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=("Unavailable fixture.",),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        fixture = self.fixture("unavailable", outcome)
        with self.assertRaises(ConfigurationError):
            FakeSceneReasonerPort(fixture, environment=Environment.PRODUCTION)
        port = FakeSceneReasonerPort(fixture, available=False)
        with self.assertRaises(ReasonerExecutionFailure) as failure:
            self.coordinator.execute(self.request(prepared), port, fixture=fixture)
        self.assertIs(failure.exception.envelope.error_code, ErrorCode.REASONER_UNAVAILABLE)
        self.assertFalse(failure.exception.envelope.story_state_committed)

    def test_blocked_outcome_is_operational_and_schema_rejects_story_prose(self) -> None:
        prepared = self.prepared("blocked-outcome")
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.BLOCKED,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=(),
            blocker_code=ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        fixture = self.fixture("blocked-outcome", outcome)
        before = self.table_state()
        result = self.coordinator.execute(
            self.request(prepared), FakeSceneReasonerPort(fixture), fixture=fixture
        )
        self.assertIs(result.outcome.status, ReasonerOutcomeStatus.BLOCKED)
        self.assertIsNone(result.outcome.decision)
        self.assertEqual(before, self.table_state())
        payload = to_primitive(outcome)
        payload["finished_story_prose"] = "Synthetic prose is not an operational field."
        with self.assertRaisesRegex(ContractValidationError, "unknown fields"):
            from_mapping(ReasonerOutcome, payload)

    def test_fake_fixture_identity_mismatch_is_rejected_before_invocation(self) -> None:
        prepared = self.prepared("fixture-mismatch")
        outcome = ReasonerOutcome(
            schema_version=ReasonerOutcome.SCHEMA_VERSION,
            status=ReasonerOutcomeStatus.INSUFFICIENT_EVIDENCE,
            decision=None,
            participation=(),
            hard_citations=(),
            insufficiencies=("Synthetic fixture mismatch test.",),
            blocker_code=None,
            advisory_state_deltas=(),
            protected_user_boundary_acknowledged=True,
        )
        actual = self.fixture("actual", outcome)
        claimed = self.fixture("claimed", outcome)
        port = FakeSceneReasonerPort(actual)
        with self.assertRaises(ReasonerExecutionFailure) as failure:
            self.coordinator.execute(self.request(prepared), port, fixture=claimed)
        self.assertIs(
            failure.exception.envelope.error_code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )
        self.assertEqual(port.invocation_count, 0)


if __name__ == "__main__":
    unittest.main()
