from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import patch

from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultFilterConflictClass,
    AdultNextRoute,
    AdultRouteStateSnapshotV1,
)
from cera.adult_pipeline.craft_catalog import CatalogAdultCraftRetrieval
from cera.adult_pipeline.integration import AdultPipelineIntegrationV1
from cera.adult_pipeline.pi_roles import (
    AdultRoleViewContextV1,
    PiDeepSeekAdultFilterPort,
    PiDeepSeekAdultScenePort,
)
from cera.adult_pipeline.pipeline import AdultPipeline
from cera.adult_pipeline.preparation import build_adult_turn_preparation_builder
from cera.pi_scene.adult_full_model_review import (
    AdultAutomaticRepairReviewRequiredV1,
    AdultFullModelReviewService,
    AdultPromotedReviewOutcomeV1,
    AdultRejectedReviewActionV1,
    ProtectedAdultExecutor,
)
from cera.pi_scene.adult_operation_contracts import AdultOperationReviewState
from cera.pi_scene.adult_operation_store import ProtectedAdultOperationStore
from cera.pi_scene.adult_orchestration import (
    AdultRouteOperationOutcomeV1,
    AutomaticAdultRouteOrchestrator,
    PreparedAdultRouteOperationV1,
    RejectedAdultRouteOperationV1,
    classify_prepared_adult_execution,
)
from cera.pi_scene.adult_review import (
    AdultAutomaticRepairDisposition,
    AdultProvisionalAcceptanceDisposition,
    AdultRejectedReviewSafeSummaryV1,
)
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.writer_view import WriterViewMaterializer
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.serialization import canonical_json

from .test_adult_pipeline_pi_integration import _FakeStructuredTransport
from .test_adult_turn_preparation import CATALOG_ROOT, _plan, _turn
from .test_pi_scene_adult_orchestration import _RouteState
from .test_pi_scene_adult_review import _ConflictTransport


def _current_facts() -> tuple[AdultContextFactV1, ...]:
    return (
        AdultContextFactV1(
            evidence_ref="evidence:public",
            subject_id="character:hana",
            authoritative_fact="Hana is present in the current room.",
            visibility="public",
        ),
        AdultContextFactV1(
            evidence_ref="evidence:private",
            subject_id="character:hana",
            authoritative_fact="Hana retains private current context.",
            visibility="adult_role_private",
        ),
    )


class AdultFullModelReviewServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scene_store = LeanSceneStore(self.root / "world")
        self.operation_store = ProtectedAdultOperationStore(self.root / "protected")
        self.service = AdultFullModelReviewService(
            scene_store=self.scene_store,
            operation_store=self.operation_store,
        )
        self.turn = _turn(craft_mode="off")
        self.initial_route = AdultRouteStateSnapshotV1(
            schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
            world_id=self.turn.world_id,
            branch_id=self.turn.branch_id,
            accepted_head_sha256=None,
            current_logic_route=AdultNextRoute.ORDINARY,
            source_promotion_sha256=None,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _integration(
        self,
        *,
        candidate_id: str,
        conflict: AdultFilterConflictClass | None,
        suffix: str,
    ) -> AdultPipelineIntegrationV1:
        transport = _FakeStructuredTransport() if conflict is None else _ConflictTransport(conflict)
        context = AdultRoleViewContextV1(
            world_id=self.turn.world_id,
            branch_id=self.turn.branch_id,
            scene_id=self.turn.scene_id,
            turn_id=f"turn:{suffix}",
            candidate_id=candidate_id,
            current_state=dict(self.turn.current_state),
            characters={key: dict(value) for key, value in self.turn.characters.items()},
        )
        materializer = WriterViewMaterializer(self.root / "views" / suffix)
        session_root = self.root / "sessions" / suffix
        scene = PiDeepSeekAdultScenePort(
            transport=transport,
            materializer=materializer,
            context=context,
            session_root=session_root,
            accepted_parent_session=None,
        )
        filter_port = PiDeepSeekAdultFilterPort(
            transport=transport,
            materializer=materializer,
            context=context,
            session_root=session_root,
        )
        return AdultPipelineIntegrationV1(
            pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
            scene_port=scene,
            filter_port=filter_port,
            craft_retrieval=CatalogAdultCraftRetrieval(CATALOG_ROOT),
        )

    def _prepared_and_outcome(
        self,
        *,
        request_id: str,
        candidate_id: str,
        conflict: AdultFilterConflictClass | None,
        suffix: str,
    ) -> tuple[PreparedAdultRouteOperationV1, AdultRouteOperationOutcomeV1]:
        integration = self._integration(
            candidate_id=candidate_id,
            conflict=conflict,
            suffix=suffix,
        )
        orchestrator = AutomaticAdultRouteOrchestrator(
            route_state=_RouteState(self.initial_route),
            preparation_builder=build_adult_turn_preparation_builder(integration.craft_retrieval),
            pipeline=integration,
        )
        prepared = orchestrator.prepare(
            request_id=request_id,
            candidate_id=candidate_id,
            turn_input=self.turn,
            accepted_safe_projection="The accepted public scene remains current.",
            protected_adult_continuity=None,
            current_facts=_current_facts(),
            product_story_boundaries=("Preserve accepted branch authority.",),
            cognition_handoff_factory=_plan,
        )
        self.service.bind_prepared_turn(prepared=prepared, turn_input=self.turn)
        with patch.dict(
            os.environ,
            {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
            clear=False,
        ):
            outcome = orchestrator.execute_prepared(prepared)
        self.operation_store.begin(prepared)
        self.operation_store.record_execution(outcome)
        return prepared, outcome

    def _execute_for(
        self,
        *,
        conflict: AdultFilterConflictClass | None,
        suffix: str,
    ) -> ProtectedAdultExecutor:
        def execute(prepared: PreparedAdultRouteOperationV1) -> AdultRouteOperationOutcomeV1:
            # Prepared custody must be durable before provider execution is reachable.
            record = self.operation_store.lookup(
                request_id=prepared.request_id,
                candidate_id=prepared.candidate_id,
            )
            self.assertEqual(record.state, AdultOperationReviewState.PREPARED)
            integration = self._integration(
                candidate_id=prepared.candidate_id,
                conflict=conflict,
                suffix=suffix,
            )
            with patch.dict(
                os.environ,
                {PROVIDER_DISPATCH_DISABLED_ENV: "1"},
                clear=False,
            ):
                protected = integration.execute(
                    request_id=prepared.request_id,
                    candidate_id=prepared.candidate_id,
                    world_id=prepared.route_state.world_id,
                    branch_id=prepared.route_state.branch_id,
                    accepted_head_sha256=prepared.route_state.accepted_head_sha256,
                    scene_request=prepared.scene_request,
                )
            return classify_prepared_adult_execution(prepared, protected)

        return execute

    def _registered_rejection(
        self,
        *,
        conflict: AdultFilterConflictClass = AdultFilterConflictClass.LOGIC_NOT_REALIZED,
        identity_suffix: str = "full-review",
    ) -> AdultRejectedReviewSafeSummaryV1:
        _, outcome = self._prepared_and_outcome(
            request_id=f"request:{identity_suffix}",
            candidate_id=f"candidate:{identity_suffix}",
            conflict=conflict,
            suffix=f"initial-rejection-{identity_suffix}",
        )
        assert isinstance(outcome, RejectedAdultRouteOperationV1)
        return self.service.register_rejection(
            outcome=outcome,
            turn_input=self.turn,
            planner_provider_operations=1,
        )

    def test_safe_review_restart_decline_and_provisional_block(self) -> None:
        review = self._registered_rejection()
        restarted = AdultFullModelReviewService(
            scene_store=LeanSceneStore(self.root / "world"),
            operation_store=ProtectedAdultOperationStore(self.root / "protected"),
        )
        self.assertEqual(restarted.get_review(review.review_id), review)
        self.assertNotIn(self.turn.exact_user_source, canonical_json(review))

        blocked = restarted.provisional_acceptance(review.review_id)
        self.assertEqual(
            blocked.disposition,
            AdultProvisionalAcceptanceDisposition.REPROJECTION_REQUIRED,
        )
        self.assertFalse(blocked.accepted_effect_created)
        self.assertIsNone(
            self.scene_store.load_head(
                world_id=self.turn.world_id,
                branch_id=self.turn.branch_id,
            ).receipt
        )

        first = restarted.decline(review.review_id)
        second = restarted.decline(review.review_id)
        self.assertEqual(first, second)
        self.assertEqual(first.operation.state, AdultOperationReviewState.DECLINED)

    def test_rejected_regenerate_passes_once_and_replays_without_dispatch(self) -> None:
        review = self._registered_rejection()
        calls = 0
        execute = self._execute_for(conflict=None, suffix="rejected-successor")

        def counted(prepared: PreparedAdultRouteOperationV1) -> AdultRouteOperationOutcomeV1:
            nonlocal calls
            calls += 1
            original = self.operation_store.lookup(
                request_id="request:full-review",
                candidate_id="candidate:full-review",
            ).prepared
            self.assertEqual(prepared.scene_request, original.scene_request)
            self.assertEqual(prepared.route_state, original.route_state)
            return execute(prepared)

        first = self.service.regenerate_rejected(review.review_id, execute=counted)
        replay = self.service.regenerate_rejected(
            review.review_id,
            execute=lambda _prepared: (_ for _ in ()).throw(
                AssertionError("completed adult Regenerate redispatched")
            ),
        )

        self.assertEqual(calls, 1)
        self.assertIsInstance(first, AdultRejectedReviewActionV1)
        self.assertIsNotNone(first.accepted)
        self.assertIsNotNone(replay.accepted)
        self.assertFalse(first.replayed)
        self.assertTrue(replay.replayed)
        self.assertEqual(
            self.scene_store.load_head(
                world_id=self.turn.world_id,
                branch_id=self.turn.branch_id,
            ).generation,
            1,
        )

    def test_automatic_repair_is_critical_only_and_single_complete_attempt(self) -> None:
        quality = self._registered_rejection()
        skipped = self.service.automatic_repair_if_critical(
            quality.review_id,
            execute=lambda _prepared: (_ for _ in ()).throw(
                AssertionError("quality-only rejection auto-repaired")
            ),
        )
        self.assertIsInstance(skipped, AdultAutomaticRepairReviewRequiredV1)
        assert isinstance(skipped, AdultAutomaticRepairReviewRequiredV1)
        self.assertEqual(
            skipped.disposition,
            AdultAutomaticRepairDisposition.CREATOR_REVIEW_REQUIRED,
        )

        critical = self._registered_rejection(
            conflict=AdultFilterConflictClass.CURRENT_DATA_CONFLICT,
            identity_suffix="critical-review",
        )
        repaired = self.service.automatic_repair_if_critical(
            critical.review_id,
            execute=self._execute_for(conflict=None, suffix="critical-successor"),
        )
        self.assertIsInstance(repaired, AdultRejectedReviewActionV1)
        assert isinstance(repaired, AdultRejectedReviewActionV1)
        self.assertEqual(repaired.action, "automatic_repair")
        self.assertIsNotNone(repaired.accepted)

    def test_accepted_regenerate_reuses_exact_capsule_and_preserves_old_sibling(self) -> None:
        prepared, outcome = self._prepared_and_outcome(
            request_id="request:accepted-original",
            candidate_id="candidate:accepted-original",
            conflict=None,
            suffix="accepted-original",
        )
        assert not isinstance(outcome, RejectedAdultRouteOperationV1)
        original = self.service._promote_passed(  # noqa: SLF001 - setup exact public seam
            outcome,
            turn_input=self.turn,
            replacement_base=None,
            replayed=False,
        )
        original_receipt = self.scene_store.load_accepted_turn_by_receipt_sha256(
            world_id=self.turn.world_id,
            branch_id=self.turn.branch_id,
            receipt_sha256=original.promotion.receipt.accepted_head_after_sha256,
        )
        original_dir = self.scene_store._accepted_turn_dir(original_receipt)
        original_bytes = {
            path.relative_to(original_dir).as_posix(): path.read_bytes()
            for path in original_dir.rglob("*")
            if path.is_file()
        }

        calls = 0
        execute = self._execute_for(conflict=None, suffix="accepted-replacement")

        def checked(value: PreparedAdultRouteOperationV1) -> AdultRouteOperationOutcomeV1:
            nonlocal calls
            calls += 1
            self.assertEqual(value.scene_request, prepared.scene_request)
            self.assertEqual(value.route_state, prepared.route_state)
            return execute(value)

        replacement = self.service.regenerate_accepted(
            action_request_id="request:accepted-regenerate-one",
            world_id=self.turn.world_id,
            branch_id=self.turn.branch_id,
            execute=checked,
        )
        self.assertIsInstance(replacement, AdultPromotedReviewOutcomeV1)
        assert isinstance(replacement, AdultPromotedReviewOutcomeV1)
        self.assertEqual(calls, 1)
        self.assertEqual(replacement.envelope.accepted_turn_id, original.envelope.accepted_turn_id)
        self.assertEqual(replacement.envelope.generation, original.envelope.generation)
        self.assertEqual(replacement.envelope.exact_current_source, self.turn.exact_user_source)
        self.assertEqual(
            replacement.envelope.primary_handoff_json,
            original.envelope.primary_handoff_json,
        )
        self.assertNotEqual(
            replacement.promotion.receipt.accepted_head_after_sha256,
            original.promotion.receipt.accepted_head_after_sha256,
        )
        self.assertEqual(
            {
                path.relative_to(original_dir).as_posix(): path.read_bytes()
                for path in original_dir.rglob("*")
                if path.is_file()
            },
            original_bytes,
        )
        replay = self.service.regenerate_accepted(
            action_request_id="request:accepted-regenerate-one",
            world_id=self.turn.world_id,
            branch_id=self.turn.branch_id,
            execute=lambda _prepared: (_ for _ in ()).throw(
                AssertionError("accepted adult Regenerate replay redispatched")
            ),
        )
        self.assertEqual(replay, replace(replacement, replayed=True))

    def test_rejected_accepted_regenerate_keeps_original_until_successor_passes(self) -> None:
        _, outcome = self._prepared_and_outcome(
            request_id="request:accepted-reject-base",
            candidate_id="candidate:accepted-reject-base",
            conflict=None,
            suffix="accepted-reject-base",
        )
        assert not isinstance(outcome, RejectedAdultRouteOperationV1)
        original = self.service._promote_passed(  # noqa: SLF001 - setup exact public seam
            outcome,
            turn_input=self.turn,
            replacement_base=None,
            replayed=False,
        )
        old_head = original.promotion.receipt.accepted_head_after_sha256
        rejected = self.service.regenerate_accepted(
            action_request_id="request:accepted-regenerate-rejected",
            world_id=self.turn.world_id,
            branch_id=self.turn.branch_id,
            execute=self._execute_for(
                conflict=AdultFilterConflictClass.LOGIC_NOT_REALIZED,
                suffix="accepted-rejected-alternative",
            ),
        )
        self.assertNotIsInstance(rejected, AdultPromotedReviewOutcomeV1)
        rejected_review = cast(AdultRejectedReviewSafeSummaryV1, rejected)
        self.assertEqual(
            self.scene_store.load_head(
                world_id=self.turn.world_id,
                branch_id=self.turn.branch_id,
            ).accepted_head_sha256,
            old_head,
        )
        promoted = self.service.regenerate_rejected(
            rejected_review.review_id,
            execute=self._execute_for(conflict=None, suffix="accepted-second-alternative"),
        )
        self.assertIsNotNone(promoted.accepted)
        assert promoted.accepted is not None
        self.assertEqual(promoted.accepted.replaced_receipt_sha256, old_head)
        self.assertNotEqual(
            self.scene_store.load_head(
                world_id=self.turn.world_id,
                branch_id=self.turn.branch_id,
            ).accepted_head_sha256,
            old_head,
        )


if __name__ == "__main__":
    unittest.main()
