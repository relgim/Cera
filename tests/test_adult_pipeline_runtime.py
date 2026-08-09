from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from cera.adult_pipeline import (
    AdultAcceptedPromotionReceiptV1,
    AdultCodexProjectionV2,
    AdultContextFactV1,
    AdultCraftExcerptV1,
    AdultCraftMode,
    AdultCraftQueryV1,
    AdultCraftSelectionV1,
    AdultCurrentDataUseV1,
    AdultDecisionStepV1,
    AdultEntryReason,
    AdultFilterConflictClass,
    AdultFilterConflictV1,
    AdultFilterDecisionV1,
    AdultFilterInvocationV1,
    AdultFilterPassV1,
    AdultFilterVerdict,
    AdultNextRoute,
    AdultPipeline,
    AdultPipelineInputV1,
    AdultProjectionEffectV1,
    AdultProjectionEventV1,
    AdultProjectionPresenceChangeV1,
    AdultProtectedEventV1,
    AdultProtectedFullRecordV1,
    AdultProviderReceiptV1,
    AdultProviderRole,
    AdultRouteStateSnapshotV1,
    AdultRouteTransitionV1,
    AdultSceneInvocationV1,
    AdultSceneOutputV1,
    AdultSceneRequestV1,
    AdultSessionScope,
    BoundAdultPromotionV1,
    promote_passed_adult_candidate,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.serialization import canonical_json, canonical_sha256, text_sha256

PROTECTED_PROSE = "PROTECTED_SCENE_SENTINEL: the private scene reaches its endpoint."
PRIVATE_FACT = "PRIVATE_CONTEXT_SENTINEL"
PROTECTED_CONTINUITY = "PROTECTED_CONTINUITY_SENTINEL"


def scene_request(
    *,
    entry_reason: AdultEntryReason = AdultEntryReason.CODEX_ADULT_HANDOFF,
    craft_mode: AdultCraftMode = AdultCraftMode.ON,
) -> AdultSceneRequestV1:
    query = AdultCraftQueryV1(
        schema_version=AdultCraftQueryV1.SCHEMA_VERSION,
        mode=craft_mode,
        concept_keys=() if craft_mode is AdultCraftMode.OFF else ("pacing",),
        keyword_keys=() if craft_mode is AdultCraftMode.OFF else ("scene",),
    )
    covered_axes = {
        AdultCraftMode.OFF: (),
        AdultCraftMode.ON: ("direct_vocabulary", "clarity"),
        AdultCraftMode.EX: (
            "direct_vocabulary",
            "clarity",
            "buildup",
            "physiology",
            "continuity",
            "sound",
            "climax",
            "aftermath",
        ),
    }[craft_mode]
    excerpts = (
        ()
        if craft_mode is AdultCraftMode.OFF
        else (
            AdultCraftExcerptV1(
                craft_ref="craft:pacing",
                concept_keys=("pacing",),
                excerpt="Build the requested scene coherently and stop at its next boundary.",
            ),
        )
    )
    return AdultSceneRequestV1(
        schema_version=AdultSceneRequestV1.SCHEMA_VERSION,
        entry_reason=entry_reason,
        adult_handoff=(
            "Continue under the adult logic owner from this boundary."
            if entry_reason is AdultEntryReason.CODEX_ADULT_HANDOFF
            else None
        ),
        exact_current_source="Continue the established private scene.",
        accepted_safe_continuity="The characters are together in the current room.",
        accepted_protected_continuity=(
            PROTECTED_CONTINUITY
            if entry_reason is AdultEntryReason.ACCEPTED_ADULT_CONTINUATION
            else None
        ),
        autonomy_mode="both",
        depth_mode="auto",
        current_context=(
            AdultContextFactV1(
                evidence_ref="evidence:public",
                subject_id="character:hana",
                authoritative_fact="Hana is present in the room.",
                visibility="public",
            ),
            AdultContextFactV1(
                evidence_ref="evidence:private",
                subject_id="character:hana",
                authoritative_fact=PRIVATE_FACT,
                visibility="adult_role_private",
            ),
        ),
        retrieved_craft=AdultCraftSelectionV1(
            schema_version=AdultCraftSelectionV1.SCHEMA_VERSION,
            mode=craft_mode,
            query_sha256=canonical_sha256(query),
            covered_axes=covered_axes,
            excerpts=excerpts,
        ),
        hard_boundaries=("Preserve accepted world and character authority.",),
    )


def scene_output(*, next_route: AdultNextRoute = AdultNextRoute.ORDINARY) -> AdultSceneOutputV1:
    return AdultSceneOutputV1(
        schema_version=AdultSceneOutputV1.SCHEMA_VERSION,
        logic_owner="deepseek_adult_scene",
        decision_path=(
            AdultDecisionStepV1(
                decision_key="decision_one",
                character_id="character:hana",
                concise_decision="Hana makes the next character-consistent choice.",
                evidence_refs=("evidence:public", "evidence:private"),
            ),
        ),
        exact_story_prose=PROTECTED_PROSE,
        resulting_state="The private sequence reaches a stable stopping point.",
        unresolved_threads=("The next conversation remains open.",),
        next_route=next_route,
        next_route_reason=(
            "The next prompt returns to ordinary character logic."
            if next_route is AdultNextRoute.ORDINARY
            else "The accepted scene remains within adult continuity."
        ),
    )


def pass_decision(output: AdultSceneOutputV1) -> AdultFilterDecisionV1:
    full = AdultProtectedFullRecordV1(
        schema_version=AdultProtectedFullRecordV1.SCHEMA_VERSION,
        scene_output_sha256=canonical_sha256(output),
        exact_story_prose=output.exact_story_prose,
        exact_story_prose_sha256=text_sha256(output.exact_story_prose),
        decision_path=output.decision_path,
        events=(
            AdultProtectedEventV1(
                event_key="decision_one",
                protected_summary="The complete protected event is recorded.",
                character_ids=("character:hana",),
                durable_effects=("The scene changes Hana's immediate outlook.",),
                knowledge_owner_ids=("character:hana",),
            ),
        ),
        current_data_uses=(
            AdultCurrentDataUseV1(
                evidence_ref="evidence:public",
                decision_key="decision_one",
                concise_use="Current public state informed the decision.",
            ),
            AdultCurrentDataUseV1(
                evidence_ref="evidence:private",
                decision_key="decision_one",
                concise_use="Current private character context informed the decision.",
            ),
        ),
        resulting_protected_state="Protected continuity is available to the adult role.",
        unresolved_threads=output.unresolved_threads,
    )
    projection = AdultCodexProjectionV2(
        schema_version=AdultCodexProjectionV2.SCHEMA_VERSION,
        protected_full_record_sha256=canonical_sha256(full),
        events=(
            AdultProjectionEventV1(
                event_key="decision_one",
                non_explicit_summary="A private interaction reached a stable boundary.",
                lasting_story_meaning="Hana's immediate outlook changed.",
            ),
        ),
        presence_changes=(
            AdultProjectionPresenceChangeV1(
                character_id="character:hana",
                direction="leave",
                effective_after_event_key="decision_one",
            ),
        ),
        durable_effects=(
            AdultProjectionEffectV1(
                effect_key="hana_outlook",
                effect_kind="character_development",
                source_event_key="decision_one",
                subject_ids=("character:hana",),
                non_explicit_effect="Hana privately reassesses the interaction.",
                target_key="development:hana_private_outlook",
                visibility="character_private",
                knowledge_owner_id="character:hana",
            ),
        ),
        resulting_public_state="The room is quiet after the private interaction.",
        unresolved_threads=output.unresolved_threads,
    )
    transition = AdultRouteTransitionV1(
        schema_version=AdultRouteTransitionV1.SCHEMA_VERSION,
        current_route=AdultNextRoute.ADULT,
        next_route=output.next_route,
        return_to_codex=output.next_route is AdultNextRoute.ORDINARY,
        concise_reason=output.next_route_reason,
    )
    return AdultFilterDecisionV1(
        schema_version=AdultFilterDecisionV1.SCHEMA_VERSION,
        verdict=AdultFilterVerdict.PASS,
        passed=AdultFilterPassV1(
            protected_full_record=full,
            codex_projection=projection,
            route_transition=transition,
        ),
        conflict=None,
    )


def reject_decision() -> AdultFilterDecisionV1:
    return AdultFilterDecisionV1(
        schema_version=AdultFilterDecisionV1.SCHEMA_VERSION,
        verdict=AdultFilterVerdict.REJECT,
        passed=None,
        conflict=AdultFilterConflictV1(
            conflict_class=AdultFilterConflictClass.LOGIC_NOT_REALIZED,
            concise_explanation="The only material decision was not realized.",
            decision_key="decision_one",
        ),
    )


def receipt(
    *,
    role: AdultProviderRole,
    request_sha256: str,
    output: object,
    session: str,
    terminalized: bool,
) -> AdultProviderReceiptV1:
    return AdultProviderReceiptV1(
        schema_version=AdultProviderReceiptV1.SCHEMA_VERSION,
        role=role,
        session_scope=(
            AdultSessionScope.ACCEPTED_BRANCH
            if role is AdultProviderRole.SCENE
            else AdultSessionScope.CANDIDATE
        ),
        provider="offline-fake",
        model="offline-fake",
        session_id_sha256=text_sha256(session),
        request_sha256=request_sha256,
        output_sha256=canonical_sha256(output),
        provider_operations=1,
        finish_status="complete",
        session_terminalized=terminalized,
    )


class FakeAdultScene:
    external_provider_boundary = False

    def __init__(self, output: AdultSceneOutputV1) -> None:
        self.output = output
        self.calls = 0

    def generate_adult_scene(self, request: AdultSceneRequestV1) -> AdultSceneInvocationV1:
        self.calls += 1
        return AdultSceneInvocationV1(
            output=self.output,
            receipt=receipt(
                role=AdultProviderRole.SCENE,
                request_sha256=canonical_sha256(request),
                output=self.output,
                session="scene-session",
                terminalized=False,
            ),
        )


class FakeAdultFilter:
    external_provider_boundary = False

    def __init__(
        self,
        decision: AdultFilterDecisionV1 | None = None,
        *,
        session: str = "filter-session",
    ) -> None:
        self.decision = decision
        self.session = session
        self.calls = 0

    def validate_and_stage(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        decision = self.decision or pass_decision(request.scene_output)
        return AdultFilterInvocationV1(
            decision=decision,
            receipt=receipt(
                role=AdultProviderRole.FILTER,
                request_sha256=canonical_sha256(request),
                output=decision,
                session=self.session,
                terminalized=True,
            ),
        )


class ExternalScene(FakeAdultScene):
    external_provider_boundary = True


class ExternalFilter(FakeAdultFilter):
    external_provider_boundary = True


class FakeAtomicAdultStore:
    def __init__(self) -> None:
        self.promotions = 0
        self.route = AdultNextRoute.ORDINARY

    def current_logic_route(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AdultRouteStateSnapshotV1:
        return AdultRouteStateSnapshotV1(
            schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
            world_id=world_id,
            branch_id=branch_id,
            accepted_head_sha256=None,
            current_logic_route=self.route,
            source_promotion_sha256=None,
        )

    def promote_adult_acceptance(self, bundle):  # type: ignore[no-untyped-def]
        self.promotions += 1
        self.route = bundle.next_route
        receipt_value = AdultAcceptedPromotionReceiptV1(
            schema_version=AdultAcceptedPromotionReceiptV1.SCHEMA_VERSION,
            accepted_turn_id="accepted:adult-1",
            world_id=bundle.world_id,
            branch_id=bundle.branch_id,
            accepted_head_before_sha256=bundle.accepted_head_before_sha256,
            accepted_head_after_sha256=text_sha256("accepted-after"),
            promotion_bundle_sha256=canonical_sha256(bundle),
            current_logic_route=bundle.next_route,
            return_to_codex=bundle.return_to_codex,
        )
        return BoundAdultPromotionV1(bundle=bundle, receipt=receipt_value)


class AdultPipelineRuntimeTests(unittest.TestCase):
    def prepared(self, request: AdultSceneRequestV1 | None = None) -> AdultPipelineInputV1:
        return AdultPipelineInputV1(
            request_id="request:adult-1",
            candidate_id="candidate:adult-1",
            world_id="world:adult-1",
            branch_id="branch:adult-1",
            accepted_head_sha256=text_sha256("accepted-head"),
            scene_request=request or scene_request(),
        )

    def test_pass_stages_complete_atomic_bundle_before_acceptance(self) -> None:
        result = AdultPipeline(
            scene=FakeAdultScene(scene_output()),
            filter_port=FakeAdultFilter(),
        ).run(self.prepared())

        self.assertTrue(result.eligible_for_atomic_acceptance)
        self.assertFalse(result.requires_post_accept_recorder)
        self.assertEqual(result.logic_owner, "deepseek_adult_scene")
        self.assertEqual(result.next_route, AdultNextRoute.ORDINARY)
        self.assertTrue(result.return_to_codex)
        bundle = result.promotion_bundle()
        self.assertEqual(bundle.logic_owner, "deepseek_adult_scene")
        self.assertTrue(bundle.return_to_codex)
        self.assertEqual(bundle.next_route, AdultNextRoute.ORDINARY)
        self.assertEqual(bundle.exact_story_prose, PROTECTED_PROSE)
        filter_wire = canonical_json(result.filtered.request)
        self.assertNotIn("candidate:adult-1", filter_wire)
        self.assertNotIn("world:adult-1", filter_wire)
        self.assertNotIn("branch:adult-1", filter_wire)

    def test_codex_projection_is_v2_and_excludes_protected_and_private_bytes(self) -> None:
        result = AdultPipeline(
            scene=FakeAdultScene(scene_output()),
            filter_port=FakeAdultFilter(),
        ).run(self.prepared())

        safe = canonical_json(result.codex_readable_projection())
        self.assertIn(AdultCodexProjectionV2.SCHEMA_VERSION, safe)
        self.assertNotIn(PROTECTED_PROSE, safe)
        self.assertNotIn(PRIVATE_FACT, safe)
        self.assertNotIn(PROTECTED_CONTINUITY, safe)
        self.assertIn("presence_changes", safe)
        self.assertIn("target_key", safe)

    def test_atomic_promotion_seam_persists_next_route_with_all_staged_artifacts(self) -> None:
        result = AdultPipeline(
            scene=FakeAdultScene(scene_output(next_route=AdultNextRoute.ADULT)),
            filter_port=FakeAdultFilter(),
        ).run(self.prepared())
        store = FakeAtomicAdultStore()

        accepted = promote_passed_adult_candidate(result, store)

        self.assertEqual(store.promotions, 1)
        self.assertEqual(accepted.receipt.current_logic_route, AdultNextRoute.ADULT)
        self.assertFalse(accepted.receipt.return_to_codex)
        self.assertEqual(
            store.current_logic_route(
                world_id="world:adult-1",
                branch_id="branch:adult-1",
            ).current_logic_route,
            AdultNextRoute.ADULT,
        )

    def test_rejection_has_no_promotable_or_codex_visible_artifacts(self) -> None:
        result = AdultPipeline(
            scene=FakeAdultScene(scene_output()),
            filter_port=FakeAdultFilter(reject_decision()),
        ).run(self.prepared())

        self.assertFalse(result.eligible_for_atomic_acceptance)
        self.assertIsNone(result.next_route)
        self.assertFalse(result.return_to_codex)
        with self.assertRaisesRegex(StateConflictError, "no promotable"):
            result.promotion_bundle()
        with self.assertRaisesRegex(StateConflictError, "no Codex projection"):
            result.codex_readable_projection()
        store = FakeAtomicAdultStore()
        with self.assertRaisesRegex(StateConflictError, "no promotable"):
            promote_passed_adult_candidate(result, store)
        self.assertEqual(store.promotions, 0)

    def test_accepted_adult_state_can_enter_without_codex_handoff(self) -> None:
        request = scene_request(entry_reason=AdultEntryReason.ACCEPTED_ADULT_CONTINUATION)
        result = AdultPipeline(
            scene=FakeAdultScene(scene_output(next_route=AdultNextRoute.ADULT)),
            filter_port=FakeAdultFilter(),
        ).run(self.prepared(request))

        self.assertEqual(result.next_route, AdultNextRoute.ADULT)
        self.assertFalse(result.return_to_codex)

    def test_craft_mode_changes_context_breadth_but_never_route_ownership(self) -> None:
        for mode in AdultCraftMode:
            with self.subTest(mode=mode):
                result = AdultPipeline(
                    scene=FakeAdultScene(scene_output(next_route=AdultNextRoute.ORDINARY)),
                    filter_port=FakeAdultFilter(),
                ).run(self.prepared(scene_request(craft_mode=mode)))
                self.assertEqual(result.logic_owner, "deepseek_adult_scene")
                self.assertEqual(result.next_route, AdultNextRoute.ORDINARY)
                self.assertTrue(result.return_to_codex)

    def test_provider_guard_stops_external_scene_before_dispatch(self) -> None:
        scene = ExternalScene(scene_output())
        filter_port = FakeAdultFilter()
        with patch.dict(os.environ, {PROVIDER_DISPATCH_DISABLED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(StateConflictError, "external provider dispatch"):
                AdultPipeline(scene=scene, filter_port=filter_port).run(self.prepared())
        self.assertEqual(scene.calls, 0)
        self.assertEqual(filter_port.calls, 0)

    def test_provider_guard_stops_external_filter_after_one_scene_dispatch(self) -> None:
        scene = FakeAdultScene(scene_output())
        filter_port = ExternalFilter()
        with patch.dict(os.environ, {PROVIDER_DISPATCH_DISABLED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(StateConflictError, "external provider dispatch"):
                AdultPipeline(scene=scene, filter_port=filter_port).run(self.prepared())
        self.assertEqual(scene.calls, 1)
        self.assertEqual(filter_port.calls, 0)

    def test_scene_and_filter_cannot_share_a_provider_session(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "sessions are not isolated"):
            AdultPipeline(
                scene=FakeAdultScene(scene_output()),
                filter_port=FakeAdultFilter(session="scene-session"),
            ).run(self.prepared())

    def test_filter_cannot_rewrite_scene_prose(self) -> None:
        output = scene_output()
        decision = pass_decision(output)
        assert decision.passed is not None
        changed_full = replace(
            decision.passed.protected_full_record,
            exact_story_prose="A rewritten candidate.",
            exact_story_prose_sha256=text_sha256("A rewritten candidate."),
        )
        changed = replace(
            decision,
            passed=replace(decision.passed, protected_full_record=changed_full),
        )
        with self.assertRaisesRegex(ContractValidationError, "rewrote the exact Scene prose"):
            AdultPipeline(
                scene=FakeAdultScene(output),
                filter_port=FakeAdultFilter(changed),
            ).run(self.prepared())

    def test_filter_cannot_leak_exact_prose_into_codex_projection(self) -> None:
        output = scene_output()
        decision = pass_decision(output)
        assert decision.passed is not None
        projection = replace(
            decision.passed.codex_projection,
            resulting_public_state=PROTECTED_PROSE,
        )
        changed = replace(decision, passed=replace(decision.passed, codex_projection=projection))
        with self.assertRaisesRegex(ContractValidationError, "exact protected prose"):
            AdultPipeline(
                scene=FakeAdultScene(output),
                filter_port=FakeAdultFilter(changed),
            ).run(self.prepared())

    def test_filter_cannot_leak_private_context_into_codex_projection(self) -> None:
        output = scene_output()
        decision = pass_decision(output)
        assert decision.passed is not None
        projection = replace(
            decision.passed.codex_projection,
            resulting_public_state=PRIVATE_FACT,
        )
        changed = replace(decision, passed=replace(decision.passed, codex_projection=projection))
        with self.assertRaisesRegex(ContractValidationError, "private context"):
            AdultPipeline(
                scene=FakeAdultScene(output),
                filter_port=FakeAdultFilter(changed),
            ).run(self.prepared())

    def test_filter_cannot_leak_prior_protected_continuity_into_projection(self) -> None:
        request = scene_request(entry_reason=AdultEntryReason.ACCEPTED_ADULT_CONTINUATION)
        output = scene_output()
        decision = pass_decision(output)
        assert decision.passed is not None
        projection = replace(
            decision.passed.codex_projection,
            resulting_public_state=PROTECTED_CONTINUITY,
        )
        changed = replace(decision, passed=replace(decision.passed, codex_projection=projection))
        with self.assertRaisesRegex(ContractValidationError, "protected adult continuity"):
            AdultPipeline(
                scene=FakeAdultScene(output),
                filter_port=FakeAdultFilter(changed),
            ).run(self.prepared(request))

    def test_filter_must_validate_every_scene_current_data_reference(self) -> None:
        output = scene_output()
        decision = pass_decision(output)
        assert decision.passed is not None
        changed_full = replace(
            decision.passed.protected_full_record,
            current_data_uses=decision.passed.protected_full_record.current_data_uses[:1],
        )
        changed = replace(
            decision,
            passed=replace(decision.passed, protected_full_record=changed_full),
        )
        with self.assertRaisesRegex(ContractValidationError, "every decision current-data"):
            AdultPipeline(
                scene=FakeAdultScene(output),
                filter_port=FakeAdultFilter(changed),
            ).run(self.prepared())

    def test_filter_cannot_change_scene_route_transition(self) -> None:
        output = scene_output(next_route=AdultNextRoute.ORDINARY)
        decision = pass_decision(output)
        assert decision.passed is not None
        changed_transition = AdultRouteTransitionV1(
            schema_version=AdultRouteTransitionV1.SCHEMA_VERSION,
            current_route=AdultNextRoute.ADULT,
            next_route=AdultNextRoute.ADULT,
            return_to_codex=False,
            concise_reason="The adult route remains active.",
        )
        changed = replace(
            decision,
            passed=replace(decision.passed, route_transition=changed_transition),
        )
        with self.assertRaisesRegex(ContractValidationError, "next-route decision"):
            AdultPipeline(
                scene=FakeAdultScene(output),
                filter_port=FakeAdultFilter(changed),
            ).run(self.prepared())


if __name__ == "__main__":
    unittest.main()
