from __future__ import annotations

from contextlib import closing
from dataclasses import replace
import sqlite3
import unittest

from cera.contracts import SourceUnitClassification
from cera.evidence import (
    EvidenceAccessScope,
    EvidenceRequesterRole,
    EvidenceSearchRequest,
    EvidenceService,
    EvidenceWorldMode,
)
from cera.errors import ErrorCode
from cera.ids import IdKind, TypedId
from cera.kernel import (
    AdultCapacityStatus,
    AdultConsentStatus,
    AdultFreedomToStop,
    AdultIdentityStatus,
    AdultPressureStatus,
    IntakeSourceUnit,
    ParticipantAdultAuthority,
    PreflightAuthority,
    ProposedStateRecord,
    ProtectedUserProvenance,
    RequestedContentClass,
    StateMutationTarget,
    TurnIntakeCommand,
    TurnKernel,
    TurnKernelFailure,
    TurnRoute,
)
import tests.test_genesis_repository as genesis_test_support


ident = genesis_test_support.ident


class TurnKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = genesis_test_support.GenesisRepositoryTests("runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        stored, _, _ = self.fixture.install_root()
        self.store = self.fixture.store
        self.store.rebuild_evidence_search_index()
        self.revision_id = stored.receipt.revision_id
        self.world_id = self.fixture.world_id
        self.branch_id = self.fixture.branch_id
        self.alpha = self.fixture.alpha
        self.beta = self.fixture.beta
        self.ted = ident(IdKind.CHARACTER, "synthetic-protected-user")
        self.service = EvidenceService(self.store)
        self.kernel = TurnKernel(self.service)
        self.access = EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            permitted_private_owner_ids=(self.alpha, self.beta),
            allow_system_private=True,
        )
        seed_snapshot = self.service.open_snapshot(
            request_id=ident(IdKind.REQUEST, "kernel-seed-lookup"),
            world_id=self.world_id,
            branch_id=self.branch_id,
            access_scope=self.access,
            world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        )
        self.alpha_adult_evidence = next(
            item.evidence_id
            for item in self.service.search_evidence(
                seed_snapshot,
                EvidenceSearchRequest(terms=("adult",), entity_ids=(self.alpha,), limit=5),
            ).references
            if item.metadata.record_id.value == "record-alpha-adult"
        )

    def command(
        self,
        suffix: str,
        *,
        preflight: PreflightAuthority | None = None,
        expected_generation: int = 0,
        expected_parent: TypedId | None = None,
        responders: tuple[TypedId, ...] | None = None,
        present: tuple[TypedId, ...] | None = None,
        units: tuple[IntakeSourceUnit, ...] | None = None,
    ) -> TurnIntakeCommand:
        return TurnIntakeCommand(
            world_id=self.world_id,
            request_id=ident(IdKind.REQUEST, f"kernel-{suffix}"),
            session_id=ident(IdKind.SESSION, "synthetic-session"),
            branch_id=self.branch_id,
            expected_generation=expected_generation,
            expected_parent_artifact_id=expected_parent,
            genesis_revision_id=self.revision_id,
            protected_user_id=self.ted,
            present_character_ids=present or (self.ted, self.alpha, self.beta),
            requested_responding_npc_ids=responders or (self.alpha,),
            source_units=units
            or (
                IntakeSourceUnit(
                    SourceUnitClassification.MESSAGE,
                    f"Synthetic ordinary request {suffix}.",
                ),
            ),
            requested_route_hints=(),
            idempotency_key=f"kernel-{suffix}",
            preflight_authority=preflight
            or PreflightAuthority(RequestedContentClass.ORDINARY),
            world_mode=EvidenceWorldMode.SYNTHETIC_FIXTURE,
        )

    def adult_authority(
        self,
        *,
        consent: AdultConsentStatus = AdultConsentStatus.GRANTED,
    ) -> PreflightAuthority:
        return PreflightAuthority(
            requested_content_class=RequestedContentClass.ADULT,
            adult_participants=(
                ParticipantAdultAuthority(
                    participant_id=self.alpha,
                    identity_status=AdultIdentityStatus.CONFIRMED_ADULT,
                    consent_status=consent,
                    capacity_status=AdultCapacityStatus.CLEAR,
                    pressure_status=AdultPressureStatus.NONE,
                    freedom_to_stop=AdultFreedomToStop.PRESENT,
                    evidence_ids=(self.alpha_adult_evidence,),
                ),
            ),
        )

    def table_state(self) -> tuple[int, ...]:
        with closing(sqlite3.connect(self.fixture.database_path)) as connection:
            return tuple(
                connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "sources",
                    "generations",
                    "artifacts",
                    "authority_records",
                    "transaction_journal",
                    "commit_receipts",
                    "genesis_revisions",
                    "genesis_records",
                )
            )

    def test_ordinary_turn_is_prepared_without_provider_or_commit(self) -> None:
        before = self.table_state()
        prepared = self.kernel.prepare_turn(self.command("ordinary"), access_scope=self.access)
        self.assertIs(prepared.route, TurnRoute.ORDINARY)
        self.assertEqual(prepared.provider_calls_made, 0)
        self.assertFalse(prepared.story_state_committed)
        self.assertEqual(prepared.request.snapshot_token, prepared.evidence_snapshot.snapshot_token)
        self.assertEqual(prepared.source_record.request_id, prepared.request.request_id)
        self.assertEqual(before, self.table_state())

    def test_consent_valid_adult_authority_selects_adult_route(self) -> None:
        prepared = self.kernel.prepare_turn(
            self.command("adult", preflight=self.adult_authority()),
            access_scope=self.access,
        )
        self.assertIs(prepared.route, TurnRoute.CONSENT_VALID_ADULT)
        self.assertEqual(len(prepared.adult_authority), 1)
        self.assertEqual(prepared.provider_calls_made, 0)

    def test_unresolved_adult_authority_fails_closed(self) -> None:
        before = self.table_state()
        with self.assertRaises(TurnKernelFailure) as failure:
            self.kernel.prepare_turn(
                self.command(
                    "adult-unresolved",
                    preflight=self.adult_authority(consent=AdultConsentStatus.UNKNOWN),
                ),
                access_scope=self.access,
            )
        self.assertIs(
            failure.exception.envelope.error_code,
            ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
        )
        self.assertFalse(failure.exception.envelope.story_state_committed)
        self.assertEqual(before, self.table_state())

    def test_blocked_crossing_stops_before_any_provider_or_story_commit(self) -> None:
        before = self.table_state()
        preflight = PreflightAuthority(
            requested_content_class=RequestedContentClass.ADULT,
            adult_participants=(),
            blocked_nonconsensual_crossing_established=True,
            blocker_boundary_unit_index=1,
            facts_established_before_blocker=("Synthetic neutral setup occurred.",),
        )
        units = (
            IntakeSourceUnit(SourceUnitClassification.CONTEXT, "Synthetic neutral setup."),
            IntakeSourceUnit(SourceUnitClassification.EVENT, "Synthetic blocked crossing marker."),
        )
        with self.assertRaises(TurnKernelFailure) as failure:
            self.kernel.prepare_turn(
                self.command("blocked", preflight=preflight, units=units),
                access_scope=self.access,
            )
        self.assertIs(
            failure.exception.envelope.error_code,
            ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
        )
        self.assertEqual(failure.exception.envelope.stage, "preflight_blocker")
        self.assertEqual(before, self.table_state())

    def test_stale_generation_or_parent_is_rejected(self) -> None:
        with self.assertRaises(TurnKernelFailure) as failure:
            self.kernel.prepare_turn(
                self.command("stale", expected_generation=1),
                access_scope=self.access,
            )
        self.assertIs(failure.exception.envelope.error_code, ErrorCode.STATE_CONFLICT)

    def test_cast_and_protected_user_boundaries_are_deterministic(self) -> None:
        with self.assertRaises(TurnKernelFailure) as protected:
            self.kernel.prepare_turn(
                self.command("protected", responders=(self.ted,)),
                access_scope=self.access,
            )
        self.assertIs(protected.exception.envelope.error_code, ErrorCode.INTAKE_INVALID)
        absent = ident(IdKind.CHARACTER, "synthetic-absent")
        with self.assertRaises(TurnKernelFailure) as cast:
            self.kernel.prepare_turn(
                self.command("absent", responders=(absent,)),
                access_scope=self.access,
            )
        self.assertEqual(cast.exception.envelope.stage, "cast_validation")

    def test_state_delta_accepts_branch_overlay_but_never_commits(self) -> None:
        prepared = self.kernel.prepare_turn(self.command("delta"), access_scope=self.access)
        evidence_id = ident(IdKind.EVIDENCE, "authorized-delta-evidence")
        proposal = ProposedStateRecord(
            record_id=ident(IdKind.DEVELOPMENT, "synthetic-development"),
            branch_id=self.branch_id,
            mutation_target=StateMutationTarget.BRANCH_OVERLAY,
            subject_ids=(self.alpha,),
            evidence_ids=(evidence_id,),
            protected_user_provenance=ProtectedUserProvenance.NOT_APPLICABLE,
        )
        before = self.table_state()
        receipt = self.kernel.validate_state_delta(
            prepared,
            (proposal,),
            authorized_evidence_ids=(evidence_id,),
        )
        self.assertEqual(receipt.status, "validated_advisory_only")
        self.assertFalse(receipt.story_state_committed)
        self.assertEqual(before, self.table_state())

    def test_state_delta_rejects_genesis_rewrite_and_protected_user_inference(self) -> None:
        prepared = self.kernel.prepare_turn(self.command("delta-reject"), access_scope=self.access)
        evidence_id = ident(IdKind.EVIDENCE, "authorized-delta-evidence")
        base = ProposedStateRecord(
            record_id=ident(IdKind.DEVELOPMENT, "synthetic-rejected-development"),
            branch_id=self.branch_id,
            mutation_target=StateMutationTarget.GENESIS,
            subject_ids=(self.alpha,),
            evidence_ids=(evidence_id,),
            protected_user_provenance=ProtectedUserProvenance.NOT_APPLICABLE,
        )
        with self.assertRaises(TurnKernelFailure) as genesis:
            self.kernel.validate_state_delta(
                prepared, (base,), authorized_evidence_ids=(evidence_id,)
            )
        self.assertIs(
            genesis.exception.envelope.error_code,
            ErrorCode.REASONER_CONTRACT_INVALID,
        )
        protected = replace(
            base,
            mutation_target=StateMutationTarget.BRANCH_OVERLAY,
            subject_ids=(self.ted,),
            protected_user_provenance=ProtectedUserProvenance.MODEL_INFERRED,
        )
        with self.assertRaises(TurnKernelFailure) as user:
            self.kernel.validate_state_delta(
                prepared, (protected,), authorized_evidence_ids=(evidence_id,)
            )
        self.assertIn("protected-user", user.exception.envelope.message)


if __name__ == "__main__":
    unittest.main()
