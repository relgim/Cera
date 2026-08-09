#!/usr/bin/env python3
"""Isolated ordinary typed-turn route over the shared Continuous V3 pipeline."""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import ctypes
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
from threading import Event, Lock, Thread
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cera.continuous import (
    ContinuousIngressAuthorityStore,
    ContinuousSillyTavernShadowRequestBridge,
    PreparedContinuousIngressBridge,
    ValidatorFinalizationPackageV1,
    build_default_prepared_classifier_registry,
)
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.codex_stored import CodexContinuousStoredSessionPort
from cera.continuous.prompting import (
    PLANNER_STABLE_INSTRUCTIONS,
    VALIDATOR_STABLE_INSTRUCTIONS,
)
from cera.continuous.scripted_job4 import ScriptedJob4FixtureRuntime
from cera.continuous.sessions import (
    ContinuousSessionCompatibilityV1,
    ContinuousSessionCoordinator,
    ContinuousSessionHandleV1,
    ContinuousSessionRole,
    ContinuousSessionSnapshotStore,
)
from cera.continuous.world import ContinuousWorldStore, WorldPromotionReceiptV1
from cera.active_runtime_validation import active_runtime_status
from cera.creator_review import CreatorReviewAction
from cera.evidence import EvidenceWorldMode
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind, TypedId, deterministic_id
from cera.ingress import RawTurnEnvelope, RawTurnIngressFacade
from cera.kernel import PreflightAuthority, RequestedContentClass, TurnKernel
from cera.reasoner import SeedDossierAssembler
from cera.providers.codex_worker import _BASE_INSTRUCTIONS_BY_ROLE
from cera.provider_dispatch_guard import assert_provider_dispatch_allowed
from cera.reasoner_session import OpenAICodexStoredThreadBackend
from cera.runtime import HanezawaContinuousManualWorld
from cera.schema import from_mapping
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    text_sha256,
    to_primitive,
)
from cera.sillytavern.continuous_manual import (
    AcceptedContinuousManualTurn,
    CONTINUOUS_V3_MANUAL_PORT,
    CONTINUOUS_V3_MANUAL_PROFILE_ID,
    CONTINUOUS_V3_MANUAL_SERVICE,
    ContinuousManualStateStore,
    ContinuousSillyTavernManualAdapter,
    PreparedContinuousManualTurn,
    RejectedContinuousManualTurn,
)
from cera.sillytavern.models import (
    CERA_CONTINUOUS_V3_MANUAL_MODEL,
    SillyTavernChatRequest,
)
from cera.sillytavern.manual_routes import (
    PROVIDER_BACKED_MANUAL_ROUTE,
    PROVIDER_FREE_MANUAL_ROUTE,
    ContinuousManualRoute,
    validate_manual_profile,
)
from cera.sillytavern.provider_authority import validate_provider_activation
from cera.sillytavern.server import CeraSillyTavernServerConfig, build_server

from scripts.run_continuous_planner_validator_job4 import (
    JobHarness,
    compatibility,
    read_world_revision,
    seed_world,
    source_character_summary,
)
from scripts.run_sillytavern_continuous_v3_campaign import (
    validate_authority as validate_provider_cycle_authority,
)


_ALL_CAST = re.compile(
    r"\b(?:everyone|the whole family|all (?:the )?girls|the sisters)\b",
    re.IGNORECASE,
)
_HANA_ALIAS = re.compile(r"\b(?:mom|mother|hana)\b", re.IGNORECASE)


class ManualJobHarness(JobHarness):
    """Adapt arbitrary prepared ingress onto the accepted JobHarness ports."""

    def __init__(
        self,
        *args,
        authority_world: HanezawaContinuousManualWorld,
        execution_identity_sha256: str,
        profile_id: str | None = None,
        provider_authority_guard=None,
        provider_family_limits: Mapping[str, int] | None = None,
        **kwargs,
    ) -> None:
        self.authority_world = authority_world
        self.execution_identity_sha256 = execution_identity_sha256
        self.profile_id = profile_id or CONTINUOUS_V3_MANUAL_PROFILE_ID
        self.provider_authority_guard = provider_authority_guard
        self.provider_family_limits = (
            None if provider_family_limits is None else dict(provider_family_limits)
        )
        world_id = authority_world.world_id.value
        branch_id = authority_world.branch_id.value
        registry = build_default_prepared_classifier_registry()
        lifecycle_root = Path(kwargs["lifecycle_root"])
        authority = ContinuousIngressAuthorityStore(
            lifecycle_root / "ingress_authority",
            prepared_classifier_registry=registry,
        )
        kwargs.update(
            {
                "world_id": world_id,
                "branch_id": branch_id,
                "ingress_authority": authority,
            }
        )
        super().__init__(*args, **kwargs)
        self.shadow_ingress = ContinuousSillyTavernShadowRequestBridge(
            raw_ingress=RawTurnIngressFacade(
                turn_kernel=TurnKernel(authority_world.service),
                seed_assembler=SeedDossierAssembler(authority_world.service),
            ),
            prepared_ingress=PreparedContinuousIngressBridge(
                authority=authority,
                classifier_registry=registry,
            ),
        )
        self._manual_pending: dict[
            int, tuple[Any, PreparedContinuousManualTurn]
        ] = {}

    def _provider_family_consumed(self, family: str) -> int:
        invoked = {
            event["call_id"]
            for event in self.call_ledger.events
            if event["state"] == "transport_invoked"
            and (
                "deepseek" if event["owner"] == "composer" else "codex_family"
            )
            == family
        }
        last_by_call: dict[str, dict[str, Any]] = {}
        for event in self.call_ledger.events:
            last_by_call[str(event["call_id"])] = event
        unresolved = {
            call_id
            for call_id, event in last_by_call.items()
            if event["state"]
            in {
                "prepared_not_invoked",
                "worker_started_not_invoked",
                "worker_preflight_not_invoked",
            }
            and (
                "deepseek" if event["owner"] == "composer" else "codex_family"
            )
            == family
        }
        return len(invoked | unresolved)

    def provider_call(self, label: str, owner: str, operation):
        if self.provider_family_limits is not None:
            family = "deepseek" if owner == "composer" else "codex_family"
            if (
                self._provider_family_consumed(family)
                >= self.provider_family_limits[family]
            ):
                raise RuntimeError(
                    f"manual {family} provider authority is exhausted"
                )
        return super().provider_call(label, owner, operation)

    def restore_accepted_pairs(self, turn_ids: tuple[str, ...]) -> None:
        if self.accepted_pairs:
            raise RuntimeError("continuous manual accepted pairs were already loaded")
        if turn_ids:
            self.accepted_pairs.extend(
                self.world.accepted_turn_pairs(
                    self.world_id,
                    self.branch_id,
                    turn_ids,
                )
            )

    def prepare_process_stop(self) -> dict[str, Any]:
        """Close the transient Validator while retaining Planner restart state."""

        validator = self.validator_session.archive_and_verify_terminal(
            "continuous_manual_process_stop_validator"
        )
        planner_handle = self.planner_session.handle
        if planner_handle is None:
            raise RuntimeError("continuous manual Planner handle disappeared")
        planner_selectable = (
            self.planner_session.port.selectable_as_active_or_accepted_ancestry(
                planner_handle
            )
        )
        if not validator.verified or not planner_selectable:
            raise RuntimeError(
                "continuous manual process-stop thread lifecycle failed"
            )
        return {
            "schema_version": "cera.continuous_manual_process_stop_threads.v1",
            "validator_archive_evidence": to_primitive(validator),
            "validator_archive_verified": validator.verified,
            "planner_restart_state_retained": planner_selectable,
            "planner_thread_sha256": planner_handle.provider_thread_id_sha256,
            "external_provider_calls": (
                0
                if self.scripted_provider_free
                else self.call_ledger.dispatched_call_count
            ),
        }

    def prepare_manual_http_turn(
        self,
        request: SillyTavernChatRequest,
        turn_number: int,
        accepted_generation: int,
        scene_number: int,
        current_scene_turn_ids: tuple[str, ...],
        current_scene_character_ids: tuple[str, ...],
        process_instance_id: str,
    ) -> PreparedContinuousManualTurn:
        if self.provider_authority_guard is not None:
            self.provider_authority_guard()
        if self.provider_family_limits is not None:
            required_codex = 3 if request.cera_scene_change else 2
            if (
                self._provider_family_consumed("codex_family")
                + required_codex
                > self.provider_family_limits["codex_family"]
                or self._provider_family_consumed("deepseek") + 1
                > self.provider_family_limits["deepseek"]
            ):
                raise RuntimeError(
                    "provider authority cannot cover the complete manual turn"
                )
        if turn_number in self._manual_pending:
            raise RuntimeError("continuous manual turn is already pending")
        if accepted_generation != len(self.accepted_pairs) + 1:
            raise RuntimeError("continuous manual accepted generation changed")
        self._active_turn_number = turn_number
        self._active_turn_id = f"turn-{turn_number:03d}"
        self._active_validator_label = None
        before_calls = len(self.call_records)
        character_ids = self._candidate_character_ids(
            request.latest_user_content,
            scene_change=request.cera_scene_change,
            current_scene_character_ids=current_scene_character_ids,
        )
        summaries = tuple(
            source_character_summary(
                self.source_root,
                name.casefold(),
                world=self.world,
                world_file_revision=read_world_revision(
                    self.world,
                    name,
                    world_id=self.world_id,
                    branch_id=self.branch_id,
                ),
                world_id=self.world_id,
                branch_id=self.branch_id,
            )
            for name, character_id in CHARACTER_IDS.items()
            if character_id in character_ids
        )
        scene_id = f"scene-{scene_number:03d}"
        envelope = self._raw_envelope(
            request=request,
            turn_id=self._active_turn_id,
            present_npc_ids=character_ids,
        )
        shadow = self.shadow_ingress.prepare(
            chat_request=request,
            envelope=envelope,
            scene_id=scene_id,
            turn_id=self._active_turn_id,
            character_summaries=summaries,
        )
        continuous_request = replace(
            shadow.request,
            planner_requested_character_ids=tuple(
                str(character_id) for character_id in character_ids
            ),
        )
        if request.cera_scene_change:
            if not current_scene_turn_ids:
                raise RuntimeError(
                    "continuous manual Scene Change lacks accepted current-scene turns"
                )
            self._active_validator_label = (
                f"scene-{scene_number - 1}-validator-summary"
            )
            summary = self.coordinator.prepare_scene_change_summary(
                continuous_request,
                completed_scene_id=f"scene-{scene_number - 1:03d}",
                accepted_turn_ids=current_scene_turn_ids,
            )
            self.scene_change_envelope = summary.scene_change_envelope
            self._active_validator_label = None
            candidate = self.coordinator.prepare_after_validated_scene_change(
                continuous_request,
                scene_change_envelope=self.scene_change_envelope,
                summary_provider_calls=1,
            )
        else:
            candidate = self.coordinator.prepare(continuous_request)
        package = candidate.validator_package
        assessment = package.creator_review
        if assessment is None:
            raise RuntimeError("continuous manual candidate lacks creator review")
        calls = len(self.call_records) - before_calls
        expected_calls = 4 if request.cera_scene_change else 3
        if calls != expected_calls:
            raise RuntimeError("continuous manual provider schedule changed")
        prepared = PreparedContinuousManualTurn(
            schema_version=PreparedContinuousManualTurn.SCHEMA_VERSION,
            turn_number=turn_number,
            turn_id=self._active_turn_id,
            scene_number=scene_number,
            scene_id=scene_id,
            scene_change=request.cera_scene_change,
            active_character_ids=tuple(str(value) for value in character_ids),
            accepted_generation_after=accepted_generation,
            candidate_text=candidate.deepseek_story_text,
            candidate_sha256=candidate.candidate_sha256,
            candidate_text_sha256=text_sha256(candidate.deepseek_story_text),
            sequence_beats=tuple(
                beat.observable_action_or_dialogue_direction
                for beat in candidate.planner_sequence.beats
            ),
            sequence_plan_sha256=candidate.planner_sequence.sequence_sha256,
            validator_package_id=package.package_id,
            validator_package_sha256=package.package_sha256,
            validator_semantic_status=package.semantic_status.value,
            assessment_schema_version=assessment.schema_version,
            assessment_severity=assessment.severity,
            assessment_publication_eligibility=assessment.publication_eligibility,
            assessment_issue_owner=assessment.issue_owner,
            assessment_reason_codes=assessment.reason_codes,
            assessment_creator_reason=assessment.creator_reason,
            assessment_verifier_status=assessment.verifier_status,
            assessment_receipt_sha256=assessment.assessment_sha256,
            provider_calls=calls,
            accept_allowed=package.permits_disposable_acceptance(
                CreatorReviewAction.ACCEPT
            ),
            raw_source_sha256=text_sha256(request.latest_user_content),
            conversation_sha256=request.conversation_sha256,
            ingress_receipt_id=continuous_request.ingress_receipt_id,
            ingress_receipt_sha256=continuous_request.ingress_receipt_sha256,
            authority_context_sha256=candidate.authority_context_sha256,
            profile_id=self.profile_id,
            execution_identity_sha256=self.execution_identity_sha256,
            process_instance_id=process_instance_id,
        )
        self._manual_pending[turn_number] = (candidate, prepared)
        return prepared

    def accept_manual_http_turn(
        self, prepared: PreparedContinuousManualTurn
    ) -> AcceptedContinuousManualTurn:
        candidate, bound = self._pending_exact(prepared)
        package = candidate.validator_package
        assessment = package.creator_review
        if assessment is None or not package.permits_disposable_acceptance(
            CreatorReviewAction.ACCEPT
        ):
            raise RuntimeError("continuous manual candidate is not Accept eligible")
        if self._candidate_binding(candidate) != self._prepared_binding(prepared):
            raise RuntimeError("continuous manual review binding changed before Accept")
        receipt = self.coordinator.apply_creator_action(
            prepared.turn_id, CreatorReviewAction.ACCEPT
        )
        pair = self.world.accepted_turn_pairs(
            self.world_id,
            self.branch_id,
            (prepared.turn_id,),
        )[0]
        self.accepted_pairs.append(pair)
        self._manual_pending.pop(prepared.turn_number)
        return AcceptedContinuousManualTurn(
            turn_number=prepared.turn_number,
            turn_id=prepared.turn_id,
            generation=prepared.accepted_generation_after,
            artifact_id=receipt.receipt_sha256,
            promotion_receipt_sha256=receipt.receipt_sha256,
            review_binding_sha256=prepared.review_binding_sha256,
        )

    def reject_manual_http_turn(
        self,
        prepared: PreparedContinuousManualTurn,
        recovered_after_restart: bool,
    ) -> RejectedContinuousManualTurn:
        if recovered_after_restart:
            receipt = self._reject_recovered_candidate(prepared)
        else:
            _candidate, _bound = self._pending_exact(prepared)
            receipt = self.coordinator.apply_creator_action(
                prepared.turn_id, CreatorReviewAction.DECLINE
            )
            self._manual_pending.pop(prepared.turn_number)
        if receipt.accepted or receipt.active_before_sha256 != receipt.active_after_sha256:
            raise RuntimeError("continuous manual rejection changed active state")
        return RejectedContinuousManualTurn(
            turn_number=prepared.turn_number,
            turn_id=prepared.turn_id,
            discard_receipt_sha256=receipt.receipt_sha256,
            review_binding_sha256=prepared.review_binding_sha256,
            recovered_after_restart=recovered_after_restart,
        )

    def recover_manual_decision(
        self,
        prepared: PreparedContinuousManualTurn,
        action: CreatorReviewAction,
    ) -> AcceptedContinuousManualTurn | RejectedContinuousManualTurn | None:
        self.world.recover_pending_promotions(self.world_id, self.branch_id)
        candidate_root = (
            self.world.branch_root(self.world_id, self.branch_id)
            / "CANDIDATES"
            / prepared.turn_id
        )
        filename = (
            "PROMOTION_RECEIPT.json"
            if action is CreatorReviewAction.ACCEPT
            else "CREATOR_ACTION.json"
        )
        receipt_path = candidate_root / filename
        if not receipt_path.is_file() or receipt_path.is_symlink():
            return None
        receipt = from_mapping(
            WorldPromotionReceiptV1,
            json.loads(receipt_path.read_text(encoding="utf-8")),
        )
        if (
            receipt.world_id != self.world_id
            or receipt.branch_id != self.branch_id
            or receipt.turn_id != prepared.turn_id
            or receipt.creator_action is not action
            or receipt.package_sha256 != prepared.validator_package_sha256
            or receipt.candidate_sha256 != prepared.candidate_sha256
            or receipt.authority_context_sha256 != prepared.authority_context_sha256
        ):
            raise RuntimeError("continuous manual recovered decision binding changed")
        if action is CreatorReviewAction.ACCEPT:
            if not receipt.accepted or not receipt.planner_append_required:
                raise RuntimeError("continuous manual recovered acceptance is incomplete")
            if prepared.turn_id in self.world.pending_acceptance_synchronization(
                self.world_id, self.branch_id
            ):
                raise RuntimeError(
                    "continuous manual recovered acceptance synchronization is incomplete"
                )
            synchronization = self.world.acceptance_synchronization_record(
                self.world_id, self.branch_id, prepared.turn_id
            )
            if (
                synchronization.get("model_injection_state") != "synchronized"
                or synchronization.get("planner_snapshot_state") != "persisted"
                or synchronization.get("stable_reference_state") != "persisted"
                or synchronization.get("promotion_receipt_payload")
                != to_primitive(receipt)
            ):
                raise RuntimeError(
                    "continuous manual recovered acceptance evidence is incomplete"
                )
            envelope = self.world.accepted_final_envelope(
                self.world_id, self.branch_id, prepared.turn_id
            )
            if envelope.acceptance_receipt_sha256 != receipt.receipt_sha256:
                raise RuntimeError(
                    "continuous manual recovered acceptance receipt changed"
                )
            return AcceptedContinuousManualTurn(
                turn_number=prepared.turn_number,
                turn_id=prepared.turn_id,
                generation=prepared.accepted_generation_after,
                artifact_id=receipt.receipt_sha256,
                promotion_receipt_sha256=receipt.receipt_sha256,
                review_binding_sha256=prepared.review_binding_sha256,
            )
        if receipt.accepted or receipt.planner_append_required:
            raise RuntimeError("continuous manual recovered rejection changed state")
        return RejectedContinuousManualTurn(
            turn_number=prepared.turn_number,
            turn_id=prepared.turn_id,
            discard_receipt_sha256=receipt.receipt_sha256,
            review_binding_sha256=prepared.review_binding_sha256,
            recovered_after_restart=True,
        )

    def _raw_envelope(
        self,
        *,
        request: SillyTavernChatRequest,
        turn_id: str,
        present_npc_ids: tuple[TypedId, ...],
    ) -> RawTurnEnvelope:
        identity = canonical_sha256(
            {
                "session_id": request.cera_session_id,
                "turn_id": turn_id,
                "conversation_sha256": request.conversation_sha256,
                "execution_identity_sha256": self.execution_identity_sha256,
            }
        )
        return RawTurnEnvelope(
            schema_version=RawTurnEnvelope.SCHEMA_VERSION,
            world_id=self.authority_world.world_id,
            request_id=deterministic_id(
                IdKind.REQUEST,
                "cera.continuous_manual_request.v1",
                identity,
            ),
            session_id=deterministic_id(
                IdKind.SESSION,
                "cera.continuous_manual_session.v1",
                str(request.cera_session_id),
            ),
            branch_id=self.authority_world.branch_id,
            expected_generation=0,
            expected_parent_artifact_id=None,
            genesis_revision_id=self.authority_world.revision_id,
            protected_user_id=self.authority_world.protected_user_id,
            present_character_ids=(
                self.authority_world.protected_user_id,
                *present_npc_ids,
            ),
            eligible_responder_ids=present_npc_ids,
            raw_message=request.latest_user_content,
            idempotency_key=f"continuous-manual-{identity}",
            world_mode=EvidenceWorldMode.REAL,
            access_scope=self.authority_world.system_scope,
            preflight_authority=PreflightAuthority(RequestedContentClass.ORDINARY),
            hard_boundaries=(
                "Do not author the protected user beyond exact supplied source.",
                "Do not transfer owner-private evidence between characters.",
                "Use only the active-cast candidate scope exposed by Python.",
            ),
        )

    def _candidate_character_ids(
        self,
        raw_message: str,
        *,
        scene_change: bool,
        current_scene_character_ids: tuple[str, ...],
    ) -> tuple[TypedId, ...]:
        explicit = (
            set(CHARACTER_IDS.values())
            if _ALL_CAST.search(raw_message)
            else {
                character_id
                for name, character_id in CHARACTER_IDS.items()
                if re.search(
                    rf"\b{re.escape(name)}\b", raw_message, re.IGNORECASE
                )
            }
        )
        if _HANA_ALIAS.search(raw_message):
            explicit.add(CHARACTER_IDS["Hana"])
        current_cast = {
            character_id
            for character_id in CHARACTER_IDS.values()
            if str(character_id) in current_scene_character_ids
        }
        if {str(value) for value in current_cast} != set(
            current_scene_character_ids
        ):
            raise RuntimeError("continuous manual current-scene cast changed")
        if scene_change:
            selected = explicit
        else:
            selected = explicit | current_cast
        if not selected:
            if not self.accepted_pairs and not current_scene_character_ids:
                selected.add(CHARACTER_IDS["Sakura"])
            else:
                raise RuntimeError("continuous manual active cast cannot be resolved")
        return tuple(
            character_id
            for character_id in CHARACTER_IDS.values()
            if character_id in selected
        )

    def _pending_exact(
        self, prepared: PreparedContinuousManualTurn
    ) -> tuple[Any, PreparedContinuousManualTurn]:
        try:
            candidate, bound = self._manual_pending[prepared.turn_number]
        except KeyError as exc:
            raise RuntimeError("continuous manual pending candidate is unavailable") from exc
        if bound != prepared:
            raise RuntimeError("continuous manual review record is stale or substituted")
        return candidate, bound

    @staticmethod
    def _candidate_binding(candidate: Any) -> dict[str, object]:
        package = candidate.validator_package
        assessment = package.creator_review
        if assessment is None:
            raise RuntimeError("continuous manual assessment disappeared")
        return {
            "candidate_text": candidate.deepseek_story_text,
            "candidate_sha256": candidate.candidate_sha256,
            "candidate_text_sha256": text_sha256(candidate.deepseek_story_text),
            "sequence_plan_sha256": candidate.planner_sequence.sequence_sha256,
            "validator_package_id": package.package_id,
            "validator_package_sha256": package.package_sha256,
            "validator_semantic_status": package.semantic_status.value,
            "assessment_receipt_sha256": assessment.assessment_sha256,
            "authority_context_sha256": candidate.authority_context_sha256,
            "accept_allowed": package.permits_disposable_acceptance(
                CreatorReviewAction.ACCEPT
            ),
        }

    @staticmethod
    def _prepared_binding(prepared: PreparedContinuousManualTurn) -> dict[str, object]:
        return {
            "candidate_text": prepared.candidate_text,
            "candidate_sha256": prepared.candidate_sha256,
            "candidate_text_sha256": prepared.candidate_text_sha256,
            "sequence_plan_sha256": prepared.sequence_plan_sha256,
            "validator_package_id": prepared.validator_package_id,
            "validator_package_sha256": prepared.validator_package_sha256,
            "validator_semantic_status": prepared.validator_semantic_status,
            "assessment_receipt_sha256": prepared.assessment_receipt_sha256,
            "authority_context_sha256": prepared.authority_context_sha256,
            "accept_allowed": prepared.accept_allowed,
        }

    def _reject_recovered_candidate(self, prepared: PreparedContinuousManualTurn):
        candidate_root = (
            self.world.branch_root(self.world_id, self.branch_id)
            / "CANDIDATES"
            / prepared.turn_id
        )
        package_path = candidate_root / "VALIDATOR_PACKAGE.json"
        authority_path = candidate_root / "CANDIDATE_AUTHORITY.json"
        if not package_path.is_file() or not authority_path.is_file():
            raise RuntimeError("continuous manual recovered candidate is incomplete")
        package = from_mapping(
            ValidatorFinalizationPackageV1,
            json.loads(package_path.read_text(encoding="utf-8")),
        )
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        if (
            package.package_sha256 != prepared.validator_package_sha256
            or authority.get("candidate_sha256") != prepared.candidate_sha256
            or authority.get("authority_context_sha256")
            != prepared.authority_context_sha256
            or authority.get("package_sha256") != prepared.validator_package_sha256
        ):
            raise RuntimeError("continuous manual recovered candidate binding changed")
        return self.world.apply_creator_action(
            world_id=self.world_id,
            branch_id=self.branch_id,
            turn_id=prepared.turn_id,
            action=CreatorReviewAction.DECLINE,
            package=package,
            candidate_sha256=prepared.candidate_sha256,
            authority_context_sha256=prepared.authority_context_sha256,
        )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SILLYTAVERN_ROOT = Path(
    os.environ.get("CERA_SILLYTAVERN_ROOT", r"E:\AIChatBot\SillyTavern")
).resolve()
DEFAULT_MANUAL_ROOT = PROJECT_ROOT / "runtime" / "manual" / "continuous_v3"
DEFAULT_MANUAL_SESSION_ID = "cera-continuous-manual"
_ROOT_SCHEMA = "cera.continuous_manual_root.v1"
_EXECUTION_SCHEMA = "cera.continuous_manual_execution_manifest.v1"
_SCRIPTED_SESSION_SCHEMA = "cera.file_backed_scripted_session_port.v1"
_LOCAL_LAUNCHED_CHILDREN: dict[int, subprocess.Popen] = {}
_ACTIVE_ROUTE = PROVIDER_FREE_MANUAL_ROUTE
_ACTIVE_TRANSPORT_MODE = "scripted_provider_free"
_ACTIVE_PROVIDER_ACTIVATION: Path | None = None
_ACTIVE_PROVIDER_CYCLE_AUTHORITY: dict[str, Any] | None = None


def configure_manual_execution(
    route: ContinuousManualRoute,
    *,
    transport_mode: str,
    provider_activation: Path | None = None,
    provider_cycle_authority: Mapping[str, Any] | None = None,
) -> None:
    """Select one process-wide route before touching any managed root."""

    if route is PROVIDER_FREE_MANUAL_ROUTE:
        if (
            transport_mode != "scripted_provider_free"
            or provider_activation is not None
            or provider_cycle_authority is not None
        ):
            raise RuntimeError("provider-free manual route rejects provider authority")
    elif route is PROVIDER_BACKED_MANUAL_ROUTE:
        if transport_mode not in {"non_network_fake_ports", "external_provider"}:
            raise RuntimeError("provider-backed manual transport mode is invalid")
        if transport_mode == "external_provider" and (
            provider_activation is None or provider_cycle_authority is None
        ):
            raise RuntimeError(
                "external provider manual mode requires one activation receipt"
            )
        if transport_mode == "non_network_fake_ports" and (
            provider_activation is not None or provider_cycle_authority is not None
        ):
            raise RuntimeError("fake provider ports reject provider authority")
    else:
        raise RuntimeError("unknown continuous manual route")
    global _ACTIVE_ROUTE, _ACTIVE_TRANSPORT_MODE, _ACTIVE_PROVIDER_ACTIVATION
    global _ACTIVE_PROVIDER_CYCLE_AUTHORITY
    global CONTINUOUS_V3_MANUAL_PROFILE_ID, CONTINUOUS_V3_MANUAL_PORT
    global CONTINUOUS_V3_MANUAL_SERVICE, CERA_CONTINUOUS_V3_MANUAL_MODEL
    global DEFAULT_MANUAL_ROOT, DEFAULT_MANUAL_SESSION_ID
    _ACTIVE_ROUTE = route
    _ACTIVE_TRANSPORT_MODE = transport_mode
    _ACTIVE_PROVIDER_ACTIVATION = (
        None if provider_activation is None else provider_activation.resolve()
    )
    _ACTIVE_PROVIDER_CYCLE_AUTHORITY = (
        None
        if provider_cycle_authority is None
        else dict(provider_cycle_authority)
    )
    CONTINUOUS_V3_MANUAL_PROFILE_ID = route.profile_id
    CONTINUOUS_V3_MANUAL_PORT = route.port
    CONTINUOUS_V3_MANUAL_SERVICE = route.service
    CERA_CONTINUOUS_V3_MANUAL_MODEL = route.model
    DEFAULT_MANUAL_ROOT = (
        PROJECT_ROOT / "runtime" / "manual" / route.default_root_name
    )
    DEFAULT_MANUAL_SESSION_ID = route.default_session_id


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(canonical_bytes(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _signed_record(
    value: Mapping[str, Any], *, hash_field: str
) -> dict[str, Any]:
    if hash_field in value:
        raise RuntimeError(f"signed record already contains {hash_field}")
    payload = dict(value)
    return {**payload, hash_field: canonical_sha256(payload)}


def _read_signed_record(
    path: Path, *, hash_field: str, label: str
) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    supplied = value.pop(hash_field, None)
    if supplied != canonical_sha256(value):
        raise RuntimeError(f"{label} integrity changed")
    return {**value, hash_field: supplied}


class FileBackedScriptedContinuousSessionPort:
    """Provider-free stored-thread seam that survives a real process restart."""

    external_provider_boundary = False

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self._lock = Lock()
        self.provider_calls = 0
        if not self.path.exists():
            self._write(
                {
                    "schema_version": _SCRIPTED_SESSION_SCHEMA,
                    "counter": 0,
                    "valid": [],
                    "parents": {},
                    "model_visible_context": {},
                    "base_instructions": {},
                    "operations": [],
                }
            )

    @property
    def operations(self) -> list[tuple[str, str]]:
        return [tuple(value) for value in self._read()["operations"]]

    def create(
        self,
        compatibility: ContinuousSessionCompatibilityV1,
        *,
        base_instructions: str = "",
    ) -> ContinuousSessionHandleV1:
        with self._lock:
            state = self._read()
            counter = int(state["counter"]) + 1
            thread_id = (
                f"file-continuous-{compatibility.role.value}-{counter}"
            )
            state["counter"] = counter
            state["valid"].append(thread_id)
            state["parents"][thread_id] = None
            state["model_visible_context"][thread_id] = []
            state["base_instructions"][thread_id] = base_instructions
            self._operation(state, "create", text_sha256(thread_id))
            self._write_without_hash(state)
            return self._handle(thread_id, counter)

    def resume(self, handle: ContinuousSessionHandleV1) -> bool:
        with self._lock:
            state = self._read()
            result = handle.provider_thread_id in state["valid"]
            self._operation(
                state,
                "resume" if result else "resume_missing",
                handle.provider_thread_id_sha256,
            )
            self._write_without_hash(state)
            return result

    def fork_branch(
        self,
        parent: ContinuousSessionHandleV1,
        compatibility: ContinuousSessionCompatibilityV1,
    ) -> ContinuousSessionHandleV1:
        with self._lock:
            state = self._read()
            if parent.provider_thread_id not in state["valid"]:
                raise RuntimeError("scripted manual parent session is unavailable")
            counter = int(state["counter"]) + 1
            thread_id = (
                f"file-continuous-{compatibility.role.value}-{counter}"
            )
            state["counter"] = counter
            state["valid"].append(thread_id)
            state["parents"][thread_id] = parent.provider_thread_id
            state["model_visible_context"][thread_id] = list(
                state["model_visible_context"].get(parent.provider_thread_id, [])
            )
            state["base_instructions"][thread_id] = state[
                "base_instructions"
            ].get(parent.provider_thread_id, "")
            self._operation(state, "fork_branch", text_sha256(thread_id))
            self._write_without_hash(state)
            return self._handle(thread_id, counter)

    def append_context(
        self, handle: ContinuousSessionHandleV1, text: str
    ) -> None:
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("scripted manual appended context is empty")
        with self._lock:
            state = self._read()
            if handle.provider_thread_id not in state["valid"]:
                raise RuntimeError("scripted manual session is unavailable")
            state["model_visible_context"][handle.provider_thread_id].append(text)
            self._operation(state, "append_context", text_sha256(text))
            self._write_without_hash(state)

    def archive(self, handle: ContinuousSessionHandleV1, reason: str) -> None:
        del reason
        with self._lock:
            state = self._read()
            state["valid"] = [
                value
                for value in state["valid"]
                if value != handle.provider_thread_id
            ]
            self._operation(state, "archive", handle.provider_thread_id_sha256)
            self._write_without_hash(state)

    def selectable_as_active_or_accepted_ancestry(
        self, handle: ContinuousSessionHandleV1
    ) -> bool:
        with self._lock:
            state = self._read()
            result = handle.provider_thread_id in state["valid"]
            self._operation(
                state,
                "selectable" if result else "not_selectable",
                handle.provider_thread_id_sha256,
            )
            self._write_without_hash(state)
            return result

    def _read(self) -> dict[str, Any]:
        value = json.loads(self.path.read_text(encoding="utf-8"))
        supplied = value.pop("state_sha256", None)
        if (
            value.get("schema_version") != _SCRIPTED_SESSION_SCHEMA
            or supplied != canonical_sha256(value)
        ):
            raise RuntimeError("scripted manual session-port state changed")
        value["state_sha256"] = supplied
        return value

    def _write_without_hash(self, state: dict[str, Any]) -> None:
        state.pop("state_sha256", None)
        self._write(state)

    def _write(self, state: dict[str, Any]) -> None:
        payload = dict(state)
        payload["state_sha256"] = canonical_sha256(state)
        _atomic_json(self.path, payload)

    @staticmethod
    def _operation(
        state: dict[str, Any], operation: str, identity_sha256: str
    ) -> None:
        state["operations"].append([operation, identity_sha256])

    @staticmethod
    def _handle(thread_id: str, counter: int) -> ContinuousSessionHandleV1:
        return ContinuousSessionHandleV1(
            schema_version=ContinuousSessionHandleV1.SCHEMA_VERSION,
            provider_session_id=f"file-scripted-epoch-{counter}",
            provider_thread_id=thread_id,
            provider_thread_id_sha256=text_sha256(thread_id),
        )


def _execution_source_paths() -> tuple[Path, ...]:
    roots = (
        PROJECT_ROOT / "src" / "cera" / "continuous",
        PROJECT_ROOT / "src" / "cera" / "sillytavern",
    )
    paths = {
        path.resolve()
        for root in roots
        for path in root.rglob("*.py")
    }
    paths.update(
        {
            (PROJECT_ROOT / "src" / "cera" / "runtime" / "human_test.py").resolve(),
            (
                PROJECT_ROOT
                / "scripts"
                / "run_continuous_planner_validator_job4.py"
            ).resolve(),
            Path(__file__).resolve(),
            (
                PROJECT_ROOT
                / "scripts"
                / "run_cera_sillytavern_continuous_manual_readiness.py"
            ).resolve(),
            (
                PROJECT_ROOT
                / "integrations"
                / "sillytavern"
                / "continuous_v3_manual_profile.json"
            ).resolve(),
            (
                PROJECT_ROOT
                / "integrations"
                / "sillytavern"
                / "continuous_v3_provider_manual_profile.json"
            ).resolve(),
            (
                PROJECT_ROOT
                / "scripts"
                / "run_cera_sillytavern_continuous_provider_manual.py"
            ).resolve(),
            (
                PROJECT_ROOT
                / "src"
                / "cera"
                / "sillytavern"
                / "manual_routes.py"
            ).resolve(),
            (
                PROJECT_ROOT
                / "src"
                / "cera"
                / "sillytavern"
                / "provider_authority.py"
            ).resolve(),
        }
    )
    genesis = PROJECT_ROOT / "genesis" / "packages" / "hanezawa_core_v1_2"
    paths.update(path.resolve() for path in genesis.rglob("*.json"))
    return tuple(sorted(paths, key=lambda value: value.as_posix()))


def _validate_manual_sillytavern_profile(profile: object) -> None:
    if _ACTIVE_ROUTE is PROVIDER_BACKED_MANUAL_ROUTE:
        if not isinstance(profile, Mapping):
            raise RuntimeError("provider-backed manual profile is not an object")
        try:
            validate_manual_profile(profile, route=_ACTIVE_ROUTE)
        except Exception as exc:
            raise RuntimeError(
                "provider-backed manual SillyTavern profile changed"
            ) from exc
        return
    expected_profile = {
        "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
        "production": False,
        "endpoint": f"http://127.0.0.1:{CONTINUOUS_V3_MANUAL_PORT}/v1",
        "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
        "stream": False,
        "route": "continuous_v3_manual",
        "provider_mode": "scripted_provider_free",
        "external_provider_calls_authorized": 0,
        "creator_review_required": True,
        "automatic_accept": False,
        "automatic_retry": False,
        "fallback": False,
        "automatic_false_positive": False,
        "database_policy": "isolated_resettable_manual_root",
        "lan_binding_allowed": False,
        "sillytavern_custom_endpoint": {
            "chat_completion_source": "custom",
            "custom_include_body_yaml": (
                "cera_session_id: cera-continuous-manual\n"
                "cera_profile_id: cera.continuous_v3.manual.v1\n"
                "cera_scene_change: false"
            ),
            "scene_change_one_shot_yaml": (
                "cera_session_id: cera-continuous-manual\n"
                "cera_profile_id: cera.continuous_v3.manual.v1\n"
                "cera_scene_change: true"
            ),
            "review_workflow": "repository_cli_on_port_5114",
            "installed_review_relay_changed": False,
        },
        "notes": (
            "Provider-free manual qualification profile. It is not an installed "
            "or active SillyTavern preset."
        ),
    }
    if profile != expected_profile:
        raise RuntimeError("continuous manual SillyTavern profile changed")


def _manual_provider_activation() -> dict[str, Any] | None:
    if _ACTIVE_TRANSPORT_MODE != "external_provider":
        if (
            _ACTIVE_PROVIDER_ACTIVATION is not None
            or _ACTIVE_PROVIDER_CYCLE_AUTHORITY is not None
        ):
            raise RuntimeError("fake manual route rejects provider activation")
        return None
    path = _ACTIVE_PROVIDER_ACTIVATION
    expected = _ACTIVE_PROVIDER_CYCLE_AUTHORITY
    if path is None or not path.is_file() or expected is None:
        raise RuntimeError("provider-backed manual route lacks activation authority")
    required = {
        "cycle_directory",
        "expected_checkpoint_sha",
        "expected_cycle_id",
        "expected_cycle_sequence",
        "expected_job4_task_id",
        "expected_authorization_sha256",
    }
    if set(expected) != required:
        raise RuntimeError("provider-backed manual cycle authority is incomplete")
    cycle_authority = validate_provider_cycle_authority(
        Path(expected["cycle_directory"]).resolve(),
        expected_checkpoint_sha=str(expected["expected_checkpoint_sha"]),
        expected_authorization_sha256=str(
            expected["expected_authorization_sha256"]
        ),
        expected_cycle_id=str(expected["expected_cycle_id"]),
        expected_cycle_sequence=expected["expected_cycle_sequence"],
        expected_task_id=str(expected["expected_job4_task_id"]),
    )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RuntimeError("provider-backed manual activation is malformed")
    activation = validate_provider_activation(
        raw,
        expected_cycle_id=str(expected["expected_cycle_id"]),
        expected_cycle_sequence=expected["expected_cycle_sequence"],
        expected_job4_task_id=str(expected["expected_job4_task_id"]),
        expected_job4_authorization_sha256=str(
            expected["expected_authorization_sha256"]
        ),
        expected_route_profile_id=_ACTIVE_ROUTE.profile_id,
        maximum_codex_family_calls=799,
        maximum_deepseek_calls=800,
    )
    if activation["authority_source_sha256"] != cycle_authority[
        "cycle_manifest_sha256"
    ]:
        raise RuntimeError("manual provider activation source is not the bound cycle")
    return activation


def manual_execution_manifest() -> dict[str, Any]:
    activation = _manual_provider_activation()
    git_pathspecs = (
        "src/cera",
        "scripts/run_continuous_planner_validator_job4.py",
        "scripts/run_cera_sillytavern_continuous_manual.py",
        "scripts/run_cera_sillytavern_continuous_manual_readiness.py",
        "genesis/packages/hanezawa_core_v1_2",
        "integrations/sillytavern/continuous_v3_manual_profile.json",
        "integrations/sillytavern/continuous_v3_provider_manual_profile.json",
        "scripts/run_cera_sillytavern_continuous_provider_manual.py",
    )
    git_execution_source_commit = subprocess.run(
        ["git", "log", "-1", "--format=%H", "--", *git_pathspecs],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    git_execution_source_status = [
        line
        for line in subprocess.run(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--",
                *git_pathspecs,
            ],
            cwd=PROJECT_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.splitlines()
        if line
    ]
    files = {
        path.relative_to(PROJECT_ROOT).as_posix(): bytes_sha256(path.read_bytes())
        for path in _execution_source_paths()
    }
    profile_path = _ACTIVE_ROUTE.profile_path(PROJECT_ROOT)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    _validate_manual_sillytavern_profile(profile)
    payload = {
        "schema_version": _EXECUTION_SCHEMA,
        "git_execution_source_commit": git_execution_source_commit,
        "git_execution_source_worktree_status": git_execution_source_status,
        "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
        "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
        "service": CONTINUOUS_V3_MANUAL_SERVICE,
        "host": "127.0.0.1",
        "port": CONTINUOUS_V3_MANUAL_PORT,
        "provider_mode": _ACTIVE_TRANSPORT_MODE,
        "external_provider_calls_authorized": (
            0
            if activation is None
            else activation["maximum_codex_family_calls"]
            + activation["maximum_deepseek_calls"]
        ),
        "route_profile": {
            "path": profile_path.relative_to(PROJECT_ROOT).as_posix(),
            "sha256": bytes_sha256(profile_path.read_bytes()),
            "profile": profile,
        },
        "execution_critical_files": files,
    }
    if _ACTIVE_ROUTE is PROVIDER_BACKED_MANUAL_ROUTE:
        payload.update(
            {
                "provider_activation_path": (
                    None
                    if _ACTIVE_PROVIDER_ACTIVATION is None
                    else str(_ACTIVE_PROVIDER_ACTIVATION)
                ),
                "provider_activation_file_sha256": (
                    None
                    if _ACTIVE_PROVIDER_ACTIVATION is None
                    else bytes_sha256(_ACTIVE_PROVIDER_ACTIVATION.read_bytes())
                ),
                "provider_activation_receipt_sha256": (
                    None if activation is None else activation["activation_sha256"]
                ),
                "provider_cycle_authority": _ACTIVE_PROVIDER_CYCLE_AUTHORITY,
                "provider_cycle_authority_sha256": (
                    None
                    if _ACTIVE_PROVIDER_CYCLE_AUTHORITY is None
                    else canonical_sha256(_ACTIVE_PROVIDER_CYCLE_AUTHORITY)
                ),
            }
        )
    return {**payload, "execution_identity_sha256": canonical_sha256(payload)}


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.2)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _file_inventory(root: Path, relative_paths: tuple[str, ...]) -> dict[str, Any]:
    files: dict[str, Any] = {}
    for relative in relative_paths:
        path = root / Path(relative)
        files[relative.replace("\\", "/")] = {
            "exists": path.is_file(),
            "size": path.stat().st_size if path.is_file() else None,
            "sha256": bytes_sha256(path.read_bytes()) if path.is_file() else None,
        }
    return files


def isolation_inventory() -> dict[str, Any]:
    persistent = (
        PROJECT_ROOT
        / "runtime"
        / "development"
        / "hanezawa_human_test_v1_2.sqlite3"
    )
    installed_paths = (
        "config.yaml",
        "data/default-user/settings.json",
        "data/default-user/characters/Hanezawa Family - Cera v1.0.png",
        "public/scripts/openai.js",
        "src/endpoints/backends/chat-completions.js",
        "src/endpoints/chats.js",
        "plugins/cera-review-proxy/index.js",
        "plugins/cera-review-proxy/package.json",
        "public/scripts/extensions/third-party/cera-creator-review/index.js",
        "public/scripts/extensions/third-party/cera-creator-review/style.css",
        "public/scripts/extensions/third-party/cera-creator-review/manifest.json",
    )
    normalized_active_runtime = json.loads(canonical_bytes(active_runtime_status()))
    return {
        "schema_version": "cera.continuous_manual_isolation_inventory.v1",
        "active_runtime": normalized_active_runtime,
        "persistent_human_test_database": {
            "path": persistent.relative_to(PROJECT_ROOT).as_posix(),
            "exists": persistent.is_file(),
            "sha256": (
                bytes_sha256(persistent.read_bytes()) if persistent.is_file() else None
            ),
        },
        "installed_sillytavern": {
            "root": str(SILLYTAVERN_ROOT),
            "root_exists": SILLYTAVERN_ROOT.is_dir(),
            "files": _file_inventory(SILLYTAVERN_ROOT, installed_paths),
        },
        "loopback_ports": {
            str(port): _port_open(port)
            for port in (8000, 5101, 5113, CONTINUOUS_V3_MANUAL_PORT)
        },
    }


def _state_identity(
    manifest: Mapping[str, Any], *, session_id: str
) -> dict[str, Any]:
    return {
        "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
        "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
        "port": CONTINUOUS_V3_MANUAL_PORT,
        "service": CONTINUOUS_V3_MANUAL_SERVICE,
        "world_id": HanezawaContinuousManualWorld.WORLD_ID.value,
        "branch_id": HanezawaContinuousManualWorld.BRANCH_ID.value,
        "session_id": session_id,
        "execution_identity_sha256": manifest["execution_identity_sha256"],
    }


def _validate_managed_root(root: Path, *, must_exist: bool) -> Path:
    resolved = root.resolve()
    allowed_parent = (PROJECT_ROOT / "runtime" / "manual").resolve()
    if allowed_parent not in resolved.parents:
        raise RuntimeError("manual root must remain under runtime/manual")
    if resolved == allowed_parent or len(resolved.relative_to(allowed_parent).parts) < 1:
        raise RuntimeError("manual root is too broad")
    if os.name == "nt" and len(str(resolved)) > 90:
        raise RuntimeError(
            "manual root is too long for bounded Windows transaction paths"
        )
    identity_path = resolved / "ROOT_IDENTITY.json"
    if must_exist:
        if not identity_path.is_file():
            raise RuntimeError("manual root lacks its managed identity")
        identity = _read_signed_record(
            identity_path,
            hash_field="root_identity_sha256",
            label="continuous manual root identity",
        )
        if (
            identity.get("schema_version") != _ROOT_SCHEMA
            or Path(identity.get("root", "")).resolve() != resolved
            or identity.get("model") != CERA_CONTINUOUS_V3_MANUAL_MODEL
            or identity.get("profile_id") != CONTINUOUS_V3_MANUAL_PROFILE_ID
            or identity.get("port") != CONTINUOUS_V3_MANUAL_PORT
        ):
            raise RuntimeError("manual root identity changed")
    return resolved


def reset_manual_root(
    root: Path | None = None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    root = DEFAULT_MANUAL_ROOT if root is None else root
    session_id = DEFAULT_MANUAL_SESSION_ID if session_id is None else session_id
    root = _validate_managed_root(root, must_exist=False)
    if _port_open(CONTINUOUS_V3_MANUAL_PORT):
        raise RuntimeError("refusing to reset while the manual port is occupied")
    if root.exists():
        _validate_managed_root(root, must_exist=True)
        process_path = root / "process" / "ACTIVE_PROCESS.json"
        if process_path.is_file():
            raise RuntimeError("refusing to reset a root with an active process record")
    staging = root.with_name(f"{root.name}.building-{os.getpid()}")
    if staging.exists():
        if staging.parent != root.parent or not staging.name.startswith(root.name + ".building-"):
            raise RuntimeError("manual staging path is unsafe")
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        manifest = manual_execution_manifest()
        authority = HanezawaContinuousManualWorld.initialize(
            PROJECT_ROOT,
            staging / "authority" / "hanezawa_continuous_manual.sqlite3",
            replace=True,
        )
        world = ContinuousWorldStore(staging / "world")
        seed_world(
            world,
            PROJECT_ROOT,
            world_id=authority.world_id.value,
            branch_id=authority.branch_id.value,
        )
        for relative in (
            "provider_workspaces",
            "evidence",
            "process",
            "logs",
        ):
            (staging / relative).mkdir()
        _atomic_json(staging / "EXECUTION_MANIFEST.json", manifest)
        _atomic_json(staging / "ISOLATION_BASELINE.json", isolation_inventory())
        root_identity = _signed_record(
            {
                "schema_version": _ROOT_SCHEMA,
                "root": str(root),
                "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                "port": CONTINUOUS_V3_MANUAL_PORT,
                "session_id": session_id,
                "execution_identity_sha256": manifest[
                    "execution_identity_sha256"
                ],
            },
            hash_field="root_identity_sha256",
        )
        _atomic_json(staging / "ROOT_IDENTITY.json", root_identity)
        ContinuousManualStateStore(
            staging / "state",
            identity=_state_identity(manifest, session_id=session_id),
        )
        if root.exists():
            shutil.rmtree(root)
        os.replace(staging, root)
        return {
            "status": "reset",
            "root": str(root),
            "generation": 0,
            "execution_identity_sha256": manifest[
                "execution_identity_sha256"
            ],
            "external_provider_calls": 0,
        }
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _load_root(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _validate_managed_root(root, must_exist=True)
    stored = json.loads((root / "EXECUTION_MANIFEST.json").read_text(encoding="utf-8"))
    current = manual_execution_manifest()
    if stored != current:
        raise RuntimeError("continuous manual execution identity drifted; reset required")
    identity = _read_signed_record(
        root / "ROOT_IDENTITY.json",
        hash_field="root_identity_sha256",
        label="continuous manual root identity",
    )
    if identity.get("execution_identity_sha256") != stored.get(
        "execution_identity_sha256"
    ):
        raise RuntimeError("continuous manual root execution binding changed")
    return stored, identity


def build_scripted_manual_adapter(
    root: Path,
    *,
    process_instance_id: str,
) -> tuple[ContinuousSillyTavernManualAdapter, ManualJobHarness]:
    manifest, identity = _load_root(root)
    root = root.resolve()
    authority = HanezawaContinuousManualWorld.open(
        PROJECT_ROOT,
        root / "authority" / "hanezawa_continuous_manual.sqlite3",
    )
    world = ContinuousWorldStore(root / "world")
    world_id = authority.world_id.value
    branch_id = authority.branch_id.value
    session_port = FileBackedScriptedContinuousSessionPort(
        root / "evidence" / "SCRIPTED_SESSION_PORT.json"
    )
    snapshot_store = ContinuousSessionSnapshotStore(
        world.branch_root(world_id, branch_id)
    )
    snapshot_path = snapshot_store.path_for(ContinuousSessionRole.PLANNER)
    if snapshot_path.is_file():
        snapshot = snapshot_store.load(ContinuousSessionRole.PLANNER)
        planner = ContinuousSessionCoordinator.resume_compatible(
            snapshot,
            session_port,
            expected_compatibility=snapshot.compatibility,
            base_instructions=PLANNER_STABLE_INSTRUCTIONS,
        )
    else:
        planner = ContinuousSessionCoordinator(
            compatibility(
                world,
                ContinuousSessionRole.PLANNER,
                world_id=world_id,
                branch_id=branch_id,
            ),
            session_port,
        )
        planner.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
    validator = ContinuousSessionCoordinator(
        compatibility(
            world,
            ContinuousSessionRole.VALIDATOR,
            world_id=world_id,
            branch_id=branch_id,
        ),
        session_port,
    )
    planner_handle = planner.ensure_session().provider_thread_id
    validator_handle = validator.ensure_session().provider_thread_id
    fixture = ScriptedJob4FixtureRuntime(world_id=world_id, branch_id=branch_id)
    harness = ManualJobHarness(
        source_root=PROJECT_ROOT,
        cycle=root / "cycle",
        world=world,
        planner_session=planner,
        validator_session=validator,
        planner_handle=planner_handle,
        validator_handle=validator_handle,
        lifecycle_root=root / "provider_workspaces",
        call_ledger=ContinuousProviderCallLedger(
            root / "evidence" / "PROVIDER_CALL_LEDGER.jsonl",
            maximum_calls=(
                10 if _ACTIVE_ROUTE is PROVIDER_FREE_MANUAL_ROUTE else 1599
            ),
        ),
        planner_transport_factory=fixture.planner_transport,
        validator_transport_factory=fixture.validator_transport,
        composer_transport_factory=fixture.composer_transport,
        scripted_provider_free=True,
        authority_world=authority,
        execution_identity_sha256=manifest["execution_identity_sha256"],
    )
    fixture.bind(harness)
    state = ContinuousManualStateStore(
        root / "state",
        identity=_state_identity(manifest, session_id=identity["session_id"]),
    )
    adapter = ContinuousSillyTavernManualAdapter(
        session_id=identity["session_id"],
        profile_id=CONTINUOUS_V3_MANUAL_PROFILE_ID,
        model=CERA_CONTINUOUS_V3_MANUAL_MODEL,
        execution_identity_sha256=manifest["execution_identity_sha256"],
        process_instance_id=process_instance_id,
        state_store=state,
        prepare_turn=harness.prepare_manual_http_turn,
        accept_turn=harness.accept_manual_http_turn,
        reject_turn=harness.reject_manual_http_turn,
        recover_decision=harness.recover_manual_decision,
        route_identity={
            "port": CONTINUOUS_V3_MANUAL_PORT,
            "provider_mode": _ACTIVE_TRANSPORT_MODE,
            "external_provider_calls_authorized": 0,
        },
    )
    harness.restore_accepted_pairs(state.accepted_turn_ids)
    return adapter, harness


def build_provider_backed_manual_adapter(
    root: Path,
    *,
    process_instance_id: str,
) -> tuple[ContinuousSillyTavernManualAdapter, ManualJobHarness, ExitStack]:
    """Construct the provider profile through fake or authority-bound ports."""

    if _ACTIVE_ROUTE is not PROVIDER_BACKED_MANUAL_ROUTE:
        raise RuntimeError("provider-backed adapter rejects the provider-free profile")
    if _ACTIVE_TRANSPORT_MODE == "non_network_fake_ports":
        adapter, harness = build_scripted_manual_adapter(
            root, process_instance_id=process_instance_id
        )
        return adapter, harness, ExitStack()

    assert_provider_dispatch_allowed(
        "scripts.continuous_manual.provider_runtime",
        external_provider_boundary=True,
    )
    activation = _manual_provider_activation()
    if activation is None:
        raise RuntimeError("provider-backed adapter lacks activation authority")
    manifest, identity = _load_root(root)
    if (
        manifest.get("provider_activation_receipt_sha256")
        != activation["activation_sha256"]
        or manifest.get("provider_activation_file_sha256")
        != bytes_sha256(_ACTIVE_PROVIDER_ACTIVATION.read_bytes())
    ):
        raise RuntimeError("provider-backed root activation binding changed")
    root = root.resolve()
    authority = HanezawaContinuousManualWorld.open(
        PROJECT_ROOT,
        root / "authority" / "hanezawa_continuous_manual.sqlite3",
    )
    world = ContinuousWorldStore(root / "world")
    world_id = authority.world_id.value
    branch_id = authority.branch_id.value
    lifecycle_root = root / "provider_workspaces"
    stack = ExitStack()
    try:
        from openai_codex import Codex, CodexConfig

        codex = stack.enter_context(
            Codex(CodexConfig(config_overrides=("mcp_servers={}",), env={}))
        )
        account = codex.account()
        if account.account is None:
            raise RuntimeError("ChatGPT Codex session is unavailable")
        planner_backend = OpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-sol",
            cwd=str(lifecycle_root),
            base_instructions=(
                _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
                + "\n\n"
                + PLANNER_STABLE_INSTRUCTIONS
            ),
            service_name="cera_manual_provider_planner",
        )
        validator_backend = OpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-terra",
            cwd=str(lifecycle_root),
            base_instructions=(
                _BASE_INSTRUCTIONS_BY_ROLE["scene_realization_verifier"]
                + "\n\n"
                + VALIDATOR_STABLE_INSTRUCTIONS
            ),
            service_name="cera_manual_provider_validator",
        )
        planner_port = CodexContinuousStoredSessionPort(planner_backend)
        validator_port = CodexContinuousStoredSessionPort(validator_backend)
        snapshot_store = ContinuousSessionSnapshotStore(
            world.branch_root(world_id, branch_id)
        )
        snapshot_path = snapshot_store.path_for(ContinuousSessionRole.PLANNER)
        if snapshot_path.is_file():
            snapshot = snapshot_store.load(ContinuousSessionRole.PLANNER)
            planner = ContinuousSessionCoordinator.resume_compatible(
                snapshot,
                planner_port,
                expected_compatibility=snapshot.compatibility,
                base_instructions=planner_backend.base_instructions,
            )
        else:
            planner = ContinuousSessionCoordinator(
                compatibility(
                    world,
                    ContinuousSessionRole.PLANNER,
                    world_id=world_id,
                    branch_id=branch_id,
                ),
                planner_port,
                base_instructions=planner_backend.base_instructions,
            )
        validator = ContinuousSessionCoordinator(
            compatibility(
                world,
                ContinuousSessionRole.VALIDATOR,
                world_id=world_id,
                branch_id=branch_id,
            ),
            validator_port,
            base_instructions=validator_backend.base_instructions,
        )
        planner_handle = planner.ensure_session().provider_thread_id
        validator_handle = validator.ensure_session().provider_thread_id

        def authority_guard() -> None:
            current = _manual_provider_activation()
            if (
                current is None
                or current["activation_sha256"] != activation["activation_sha256"]
                or bytes_sha256(_ACTIVE_PROVIDER_ACTIVATION.read_bytes())
                != manifest["provider_activation_file_sha256"]
            ):
                raise RuntimeError(
                    "provider activation changed before manual dispatch"
                )

        harness = ManualJobHarness(
            source_root=PROJECT_ROOT,
            cycle=root / "cycle",
            world=world,
            planner_session=planner,
            validator_session=validator,
            planner_handle=planner_handle,
            validator_handle=validator_handle,
            lifecycle_root=lifecycle_root,
            call_ledger=ContinuousProviderCallLedger(
                root / "evidence" / "PROVIDER_CALL_LEDGER.jsonl",
                maximum_calls=(
                    activation["maximum_codex_family_calls"]
                    + activation["maximum_deepseek_calls"]
                ),
            ),
            authority_world=authority,
            execution_identity_sha256=manifest["execution_identity_sha256"],
            profile_id=_ACTIVE_ROUTE.profile_id,
            provider_authority_guard=authority_guard,
            provider_family_limits={
                "codex_family": activation["maximum_codex_family_calls"],
                "deepseek": activation["maximum_deepseek_calls"],
            },
        )
        state = ContinuousManualStateStore(
            root / "state",
            identity=_state_identity(manifest, session_id=identity["session_id"]),
        )
        adapter = ContinuousSillyTavernManualAdapter(
            session_id=identity["session_id"],
            profile_id=_ACTIVE_ROUTE.profile_id,
            model=_ACTIVE_ROUTE.model,
            execution_identity_sha256=manifest["execution_identity_sha256"],
            process_instance_id=process_instance_id,
            state_store=state,
            prepare_turn=harness.prepare_manual_http_turn,
            accept_turn=harness.accept_manual_http_turn,
            reject_turn=harness.reject_manual_http_turn,
            recover_decision=harness.recover_manual_decision,
            route_identity={
                "port": _ACTIVE_ROUTE.port,
                "provider_mode": _ACTIVE_TRANSPORT_MODE,
                "external_provider_calls_authorized": manifest[
                    "external_provider_calls_authorized"
                ],
                "provider_activation_receipt_sha256": activation[
                    "activation_sha256"
                ],
            },
        )
        harness.restore_accepted_pairs(state.accepted_turn_ids)
        return adapter, harness, stack
    except BaseException:
        stack.close()
        raise


def build_selected_manual_adapter(
    root: Path, *, process_instance_id: str
) -> tuple[ContinuousSillyTavernManualAdapter, ManualJobHarness, ExitStack]:
    if _ACTIVE_ROUTE is PROVIDER_FREE_MANUAL_ROUTE:
        adapter, harness = build_scripted_manual_adapter(
            root, process_instance_id=process_instance_id
        )
        return adapter, harness, ExitStack()
    return build_provider_backed_manual_adapter(
        root, process_instance_id=process_instance_id
    )


def _http_json(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    port: int | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    active_port = CONTINUOUS_V3_MANUAL_PORT if port is None else port
    request = Request(
        f"http://127.0.0.1:{active_port}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def _process_path(root: Path) -> Path:
    return root.resolve() / "process" / "ACTIVE_PROCESS.json"


def _stop_request_path(root: Path) -> Path:
    return root.resolve() / "process" / "STOP_REQUEST.json"


def _read_bound_process_record(root: Path) -> dict[str, Any]:
    root = _validate_managed_root(root, must_exist=True)
    manifest, _identity = _load_root(root)
    record = _read_signed_record(
        _process_path(root),
        hash_field="process_record_sha256",
        label="continuous manual process record",
    )
    identity = _read_signed_record(
        root / "ROOT_IDENTITY.json",
        hash_field="root_identity_sha256",
        label="continuous manual root identity",
    )
    process_instance_id = record.get("process_instance_id")
    expected_fields = {
        "schema_version",
        "pid",
        "executable",
        "root",
        "host",
        "port",
        "model",
        "profile_id",
        "process_instance_id",
        "process_instance_sha256",
        "execution_identity_sha256",
        "provider_mode",
        "external_provider_calls_authorized",
        "started_unix_ns",
        "process_record_sha256",
    }
    if (
        set(record) != expected_fields
        or record.get("schema_version") != "cera.continuous_manual_process.v1"
        or record.get("root") != str(root)
        or record.get("executable") != str(Path(sys.executable).resolve())
        or record.get("host") != "127.0.0.1"
        or record.get("port") != CONTINUOUS_V3_MANUAL_PORT
        or record.get("model") != CERA_CONTINUOUS_V3_MANUAL_MODEL
        or record.get("profile_id") != CONTINUOUS_V3_MANUAL_PROFILE_ID
        or record.get("provider_mode") != _ACTIVE_TRANSPORT_MODE
        or record.get("external_provider_calls_authorized")
        != manifest.get("external_provider_calls_authorized")
        or not isinstance(record.get("pid"), int)
        or int(record["pid"]) <= 0
        or not isinstance(record.get("started_unix_ns"), int)
        or int(record["started_unix_ns"]) <= 0
        or not isinstance(process_instance_id, str)
        or not process_instance_id
        or record.get("process_instance_sha256")
        != text_sha256(process_instance_id)
        or record.get("execution_identity_sha256")
        != identity.get("execution_identity_sha256")
    ):
        raise RuntimeError("continuous manual process identity changed")
    return record


def _read_bound_stop_request(
    path: Path, process_record: Mapping[str, Any]
) -> dict[str, Any]:
    request = _read_signed_record(
        path,
        hash_field="stop_request_sha256",
        label="continuous manual stop request",
    )
    if (
        set(request)
        != {
            "schema_version",
            "requested_by_pid",
            "requested_unix_ns",
            "process_instance_sha256",
            "execution_identity_sha256",
            "stop_request_sha256",
        }
        or request.get("schema_version")
        != "cera.continuous_manual_stop_request.v1"
        or not isinstance(request.get("requested_by_pid"), int)
        or int(request["requested_by_pid"]) <= 0
        or not isinstance(request.get("requested_unix_ns"), int)
        or int(request["requested_unix_ns"]) <= 0
        or request.get("process_instance_sha256")
        != process_record.get("process_instance_sha256")
        or request.get("execution_identity_sha256")
        != process_record.get("execution_identity_sha256")
    ):
        raise RuntimeError("continuous manual stop request identity changed")
    return request


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            ):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _record_process_stop_failure(
    root: Path,
    process_record: Mapping[str, Any],
    stop_request_path: Path,
    error: BaseException,
) -> dict[str, Any]:
    process_instance_sha256 = str(process_record["process_instance_sha256"])
    rejected_input_path = (
        root
        / "process"
        / f"STOP_FAILED_INPUT_{process_instance_sha256}.json"
    )
    stop_request_bytes_sha256 = None
    if stop_request_path.is_file():
        stop_request_bytes_sha256 = bytes_sha256(stop_request_path.read_bytes())
        if rejected_input_path.exists():
            raise RuntimeError(
                "continuous manual failed stop input identity was already used"
            )
        os.replace(stop_request_path, rejected_input_path)
    failure = _signed_record(
        {
            "schema_version": "cera.continuous_manual_process_stop_failure.v1",
            "process_record_sha256": process_record["process_record_sha256"],
            "process_instance_sha256": process_instance_sha256,
            "execution_identity_sha256": process_record[
                "execution_identity_sha256"
            ],
            "error_type": type(error).__name__,
            "error_message_sha256": text_sha256(str(error)),
            "stop_request_bytes_sha256": stop_request_bytes_sha256,
            "rejected_input_path": (
                rejected_input_path.name
                if stop_request_bytes_sha256 is not None
                else None
            ),
            "failed_unix_ns": time.time_ns(),
            "external_provider_calls": 0,
        },
        hash_field="process_stop_failure_sha256",
    )
    _atomic_json(
        root
        / "process"
        / f"STOP_FAILURE_{process_instance_sha256}.json",
        failure,
    )
    return failure


def serve_manual_root(
    root: Path,
    *,
    process_instance_id: str,
) -> int:
    root = _validate_managed_root(root, must_exist=True)
    if _port_open(CONTINUOUS_V3_MANUAL_PORT):
        raise RuntimeError("continuous manual port is already occupied")
    process_path = _process_path(root)
    if process_path.exists():
        raise RuntimeError("continuous manual active-process record already exists")
    stop_request_path = _stop_request_path(root)
    if stop_request_path.exists():
        raise RuntimeError("continuous manual stop request requires recovery")
    manifest, _identity = _load_root(root)
    adapter, harness, provider_stack = build_selected_manual_adapter(
        root,
        process_instance_id=process_instance_id,
    )
    server = build_server(
        adapter,
        CeraSillyTavernServerConfig(
            host="127.0.0.1",
            port=CONTINUOUS_V3_MANUAL_PORT,
            model=CERA_CONTINUOUS_V3_MANUAL_MODEL,
            service=CONTINUOUS_V3_MANUAL_SERVICE,
        ),
    )
    process_record = _signed_record(
        {
            "schema_version": "cera.continuous_manual_process.v1",
            "pid": os.getpid(),
            "executable": str(Path(sys.executable).resolve()),
            "root": str(root),
            "host": "127.0.0.1",
            "port": CONTINUOUS_V3_MANUAL_PORT,
            "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
            "profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
            "process_instance_id": process_instance_id,
            "process_instance_sha256": text_sha256(process_instance_id),
            "execution_identity_sha256": adapter.execution_identity_sha256,
            "provider_mode": _ACTIVE_TRANSPORT_MODE,
            "external_provider_calls_authorized": manifest[
                "external_provider_calls_authorized"
            ],
            "started_unix_ns": time.time_ns(),
        },
        hash_field="process_record_sha256",
    )
    _atomic_json(process_path, process_record)

    watcher_done = Event()
    stop_terminalized = Event()
    watcher_errors: list[BaseException] = []
    watcher_failure_records: list[dict[str, Any]] = []

    def watch_stop_request() -> None:
        while not watcher_done.wait(0.1):
            if not stop_request_path.is_file():
                continue
            try:
                _read_bound_stop_request(stop_request_path, process_record)
                thread_evidence = _signed_record(
                    harness.prepare_process_stop(),
                    hash_field="process_stop_threads_sha256",
                )
                thread_evidence_path = (
                    root
                    / "process"
                    / f"THREADS_{process_record['process_instance_sha256']}.json"
                )
                _atomic_json(thread_evidence_path, thread_evidence)
                consumed_path = (
                    root
                    / "process"
                    / f"STOP_CONSUMED_{process_record['process_instance_sha256']}.json"
                )
                os.replace(stop_request_path, consumed_path)
                stop_terminalized.set()
            except BaseException as error:
                watcher_errors.append(error)
                failure = _record_process_stop_failure(
                    root, process_record, stop_request_path, error
                )
                watcher_failure_records.append(failure)
            finally:
                server.shutdown()
            return

    watcher = Thread(target=watch_stop_request, daemon=True)
    watcher.start()

    def stop_server(_signum=None, _frame=None) -> None:
        Thread(target=server.shutdown, daemon=True).start()

    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signum, stop_server)
        except (OSError, ValueError):
            pass
    try:
        server.serve_forever()
        return 0
    finally:
        watcher_done.set()
        watcher.join(timeout=2)
        server.server_close()
        provider_stack.close()
        terminal_payload = dict(process_record)
        terminal_payload.pop("process_record_sha256")
        terminal_status = (
            "failed_stop_terminalization"
            if watcher_errors
            else "stopped"
            if stop_terminalized.is_set()
            else "uncontrolled_shutdown"
        )
        terminal = _signed_record(
            {
                **terminal_payload,
                "schema_version": "cera.continuous_manual_process_terminal.v1",
                "process_record_sha256": process_record["process_record_sha256"],
                "terminal_status": terminal_status,
                "process_stop_failure_sha256": (
                    watcher_failure_records[0]["process_stop_failure_sha256"]
                    if watcher_failure_records
                    else None
                ),
                "stopped_unix_ns": time.time_ns(),
                "external_provider_calls": harness.provider_calls,
            },
            hash_field="terminal_record_sha256",
        )
        _atomic_json(
            root
            / "process"
            / f"TERMINAL_{process_record['process_instance_sha256']}.json",
            terminal,
        )
        if process_path.is_file():
            observed = _read_signed_record(
                process_path,
                hash_field="process_record_sha256",
                label="continuous manual process record",
            )
            if observed == process_record:
                process_path.unlink()


def manual_status(
    root: Path, *, verify_execution: bool = True
) -> dict[str, Any]:
    root = _validate_managed_root(root, must_exist=True)
    execution_identity_ok = True
    execution_error = None
    if verify_execution:
        try:
            _load_root(root)
        except RuntimeError as error:
            execution_identity_ok = False
            execution_error = str(error)
    process_path = _process_path(root)
    if not process_path.is_file():
        return {
            "status": (
                "stopped" if execution_identity_ok else "stale_or_conflicting"
            ),
            "process_record": False,
            "port_open": _port_open(CONTINUOUS_V3_MANUAL_PORT),
            "execution_identity_ok": execution_identity_ok,
            "execution_error": execution_error,
        }
    record = _read_bound_process_record(root)
    pid_alive = _pid_alive(int(record.get("pid", 0)))
    try:
        http_status, health = _http_json("GET", "/health", timeout=1.0)
    except (OSError, URLError, TimeoutError, ValueError):
        http_status, health = 0, {}
    identity_ok = (
        http_status == 200
        and execution_identity_ok
        and health.get("model") == CERA_CONTINUOUS_V3_MANUAL_MODEL
        and health.get("reasoner_session", {}).get("profile_id")
        == CONTINUOUS_V3_MANUAL_PROFILE_ID
        and health.get("reasoner_session", {}).get("process_instance_sha256")
        == record.get("process_instance_sha256")
        and health.get("reasoner_session", {}).get("execution_identity_sha256")
        == record.get("execution_identity_sha256")
    )
    return {
        "status": "running" if pid_alive and identity_ok else "stale_or_conflicting",
        "process_record": True,
        "pid": record.get("pid"),
        "pid_alive": pid_alive,
        "port_open": _port_open(CONTINUOUS_V3_MANUAL_PORT),
        "health_status": http_status,
        "identity_ok": identity_ok,
        "execution_identity_ok": execution_identity_ok,
        "execution_error": execution_error,
        "process_instance_sha256": record.get("process_instance_sha256"),
        "execution_identity_sha256": record.get("execution_identity_sha256"),
    }


def _require_running_manual_root(root: Path) -> dict[str, Any]:
    status = manual_status(root)
    if status.get("status") != "running":
        raise RuntimeError(
            "continuous manual service for the requested root is not running"
        )
    return status


def start_manual_root(root: Path) -> dict[str, Any]:
    root = _validate_managed_root(root, must_exist=True)
    _load_root(root)
    status = manual_status(root, verify_execution=False)
    if status["status"] != "stopped" or status["port_open"]:
        raise RuntimeError("continuous manual service is not cleanly stopped")
    assert_provider_dispatch_allowed(
        "scripts.continuous_manual.child_process",
        external_provider_boundary=(
            _ACTIVE_ROUTE is PROVIDER_BACKED_MANUAL_ROUTE
            and _ACTIVE_TRANSPORT_MODE == "external_provider"
        ),
    )
    process_instance_id = canonical_sha256(
        {
            "root": str(root),
            "parent_pid": os.getpid(),
            "start_nonce": time.time_ns(),
        }
    )
    stdout_path = root / "logs" / f"server-{text_sha256(process_instance_id)[:16]}.out.log"
    stderr_path = root / "logs" / f"server-{text_sha256(process_instance_id)[:16]}.err.log"
    creationflags = 0
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        module_name = "scripts.run_cera_sillytavern_continuous_manual"
        route_arguments: list[str] = []
        if _ACTIVE_ROUTE is PROVIDER_BACKED_MANUAL_ROUTE:
            module_name = (
                "scripts.run_cera_sillytavern_continuous_provider_manual"
            )
            route_arguments = ["--transport-mode", _ACTIVE_TRANSPORT_MODE]
            if _ACTIVE_PROVIDER_ACTIVATION is not None:
                route_arguments.extend(
                    [
                        "--provider-activation",
                        str(_ACTIVE_PROVIDER_ACTIVATION),
                    ]
                )
            if _ACTIVE_PROVIDER_CYCLE_AUTHORITY is not None:
                authority = _ACTIVE_PROVIDER_CYCLE_AUTHORITY
                route_arguments.extend(
                    [
                        "--cycle-directory",
                        str(authority["cycle_directory"]),
                        "--expected-checkpoint-sha",
                        str(authority["expected_checkpoint_sha"]),
                        "--expected-cycle-id",
                        str(authority["expected_cycle_id"]),
                        "--expected-cycle-sequence",
                        str(authority["expected_cycle_sequence"]),
                        "--expected-job4-task-id",
                        str(authority["expected_job4_task_id"]),
                        "--expected-authorization-sha256",
                        str(authority["expected_authorization_sha256"]),
                    ]
                )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                module_name,
                *route_arguments,
                "serve",
                "--root",
                str(root),
                "--process-instance-id",
                process_instance_id,
            ],
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creationflags,
        )
    deadline = time.monotonic() + 20.0
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        if process.poll() is not None:
            break
        try:
            latest = manual_status(root, verify_execution=False)
        except Exception:
            latest = {}
        if latest.get("status") == "running":
            _LOCAL_LAUNCHED_CHILDREN[process.pid] = process
            return {
                **latest,
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "external_provider_calls": 0,
            }
        time.sleep(0.1)
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=5)
    raise RuntimeError(
        "continuous manual service failed startup: "
        + json.dumps(latest, sort_keys=True)
    )


def stop_manual_root(root: Path) -> dict[str, Any]:
    root = _validate_managed_root(root, must_exist=True)
    status = manual_status(root, verify_execution=False)
    if status["status"] != "running":
        raise RuntimeError("continuous manual service identity is not safely stoppable")
    record = _read_bound_process_record(root)
    pid = int(record["pid"])
    stop_path = _stop_request_path(root)
    if stop_path.exists():
        raise RuntimeError("continuous manual stop request is already pending")
    stop_request = _signed_record(
        {
            "schema_version": "cera.continuous_manual_stop_request.v1",
            "requested_by_pid": os.getpid(),
            "requested_unix_ns": time.time_ns(),
            "process_instance_sha256": record["process_instance_sha256"],
            "execution_identity_sha256": record["execution_identity_sha256"],
        },
        hash_field="stop_request_sha256",
    )
    _atomic_json(stop_path, stop_request)
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if not _pid_alive(pid) and not _port_open(CONTINUOUS_V3_MANUAL_PORT):
            break
        time.sleep(0.1)
    if _pid_alive(pid) or _port_open(CONTINUOUS_V3_MANUAL_PORT):
        raise RuntimeError("continuous manual service did not stop cleanly")
    local_child = _LOCAL_LAUNCHED_CHILDREN.pop(pid, None)
    if local_child is not None:
        local_child.wait(timeout=2)
    if _process_path(root).exists() or _stop_request_path(root).exists():
        raise RuntimeError("continuous manual service left active control state")
    terminal_path = (
        root
        / "process"
        / f"TERMINAL_{record['process_instance_sha256']}.json"
    )
    terminal = _read_signed_record(
        terminal_path,
        hash_field="terminal_record_sha256",
        label="continuous manual terminal record",
    )
    if terminal.get("terminal_status") != "stopped":
        failure_sha256 = terminal.get("process_stop_failure_sha256")
        if failure_sha256 is not None:
            failure = _read_signed_record(
                root
                / "process"
                / f"STOP_FAILURE_{record['process_instance_sha256']}.json",
                hash_field="process_stop_failure_sha256",
                label="continuous manual process-stop failure",
            )
            if failure.get("process_stop_failure_sha256") != failure_sha256:
                raise RuntimeError(
                    "continuous manual process-stop failure evidence changed"
                )
        raise RuntimeError(
            "continuous manual service stopped without successful terminalization"
        )
    consumed_path = (
        root
        / "process"
        / f"STOP_CONSUMED_{record['process_instance_sha256']}.json"
    )
    consumed = _read_bound_stop_request(consumed_path, record)
    thread_path = (
        root
        / "process"
        / f"THREADS_{record['process_instance_sha256']}.json"
    )
    thread_evidence = _read_signed_record(
        thread_path,
        hash_field="process_stop_threads_sha256",
        label="continuous manual process-stop thread evidence",
    )
    if (
        terminal.get("terminal_status") != "stopped"
        or terminal.get("process_record_sha256")
        != record.get("process_record_sha256")
        or consumed.get("process_instance_sha256")
        != record.get("process_instance_sha256")
        or thread_evidence.get("validator_archive_verified") is not True
        or thread_evidence.get("planner_restart_state_retained") is not True
    ):
        raise RuntimeError("continuous manual terminal evidence changed")
    return {
        "status": "stopped",
        "pid": pid,
        "process_instance_sha256": record["process_instance_sha256"],
        "terminal_record_sha256": terminal["terminal_record_sha256"],
        "stop_request_sha256": consumed["stop_request_sha256"],
        "process_stop_threads_sha256": thread_evidence[
            "process_stop_threads_sha256"
        ],
        "port_open": False,
        "external_provider_calls": 0,
    }


def recover_stale_process_record(root: Path) -> dict[str, Any]:
    root = _validate_managed_root(root, must_exist=True)
    status = manual_status(root)
    if status["status"] != "stale_or_conflicting":
        raise RuntimeError("continuous manual process recovery is not required")
    record_path = _process_path(root)
    record = _read_bound_process_record(root)
    if _pid_alive(int(record.get("pid", 0))) or _port_open(CONTINUOUS_V3_MANUAL_PORT):
        raise RuntimeError("continuous manual stale process cannot be proven absent")
    stop_path = _stop_request_path(root)
    if stop_path.is_file():
        _read_bound_stop_request(stop_path, record)
    recovered_payload = dict(record)
    recovered_payload.pop("process_record_sha256")
    recovered = _signed_record(
        {
            **recovered_payload,
            "schema_version": "cera.continuous_manual_process_recovery.v1",
            "process_record_sha256": record["process_record_sha256"],
            "recovered_unix_ns": time.time_ns(),
            "recovery_reason": "dead_pid_and_closed_bound_port",
        },
        hash_field="recovery_record_sha256",
    )
    _atomic_json(
        root
        / "process"
        / f"RECOVERED_{record['process_instance_sha256']}.json",
        recovered,
    )
    record_path.unlink()
    if stop_path.is_file():
        os.replace(
            stop_path,
            root
            / "process"
            / f"STOP_RECOVERED_{record['process_instance_sha256']}.json",
        )
    return {"status": "recovered", "external_provider_calls": 0}


def verify_isolation(root: Path) -> dict[str, Any]:
    root = _validate_managed_root(root, must_exist=True)
    baseline = json.loads((root / "ISOLATION_BASELINE.json").read_text(encoding="utf-8"))
    current = isolation_inventory()
    persistent_unchanged = (
        baseline["persistent_human_test_database"]
        == current["persistent_human_test_database"]
    )
    active_profile_unchanged = baseline["active_runtime"] == current["active_runtime"]
    installed_sillytavern_unchanged = (
        baseline["installed_sillytavern"] == current["installed_sillytavern"]
    )
    background_ports_unchanged = all(
        baseline["loopback_ports"][str(port)]
        == current["loopback_ports"][str(port)]
        for port in (8000, 5101, 5113)
    )
    status = manual_status(root)
    no_orphan = status["status"] == "stopped" and not status["port_open"]
    result = {
        "persistent_database_unchanged": persistent_unchanged,
        "active_profile_unchanged": active_profile_unchanged,
        "installed_sillytavern_unchanged": installed_sillytavern_unchanged,
        "background_ports_unchanged": background_ports_unchanged,
        "manual_service_stopped": no_orphan,
        "external_provider_calls": 0,
    }
    result["passed"] = all(
        result[key]
        for key in (
            "persistent_database_unchanged",
            "active_profile_unchanged",
            "installed_sillytavern_unchanged",
            "background_ports_unchanged",
            "manual_service_stopped",
        )
    )
    return result


def pending_review_status(root: Path) -> dict[str, Any]:
    _require_running_manual_root(root)
    manifest, identity = _load_root(root)
    state = ContinuousManualStateStore(
        root.resolve() / "state",
        identity=_state_identity(manifest, session_id=identity["session_id"]),
    )
    review_id = state.current_review_id
    if review_id is None:
        return {"status": "none", "unresolved_review": False}
    status, body = _http_json("GET", f"/v1/cera/reviews/{review_id}")
    return {
        "http_status": status,
        "unresolved_review": True,
        "review_id": str(review_id),
        "body": body,
    }


def _print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    def root_argument(command):
        command.add_argument("--root", type=Path, default=DEFAULT_MANUAL_ROOT)

    reset = commands.add_parser("reset")
    root_argument(reset)
    reset.add_argument("--session-id", default=DEFAULT_MANUAL_SESSION_ID)
    reset.add_argument("--confirm-reset", action="store_true")

    serve = commands.add_parser("serve")
    root_argument(serve)
    serve.add_argument("--process-instance-id", required=True)

    for name in (
        "start",
        "stop",
        "status",
        "recover-process",
        "verify-isolation",
        "pending-review",
    ):
        command = commands.add_parser(name)
        root_argument(command)

    submit = commands.add_parser("submit")
    root_argument(submit)
    submit.add_argument("--message", required=True)
    submit.add_argument("--scene-change", action="store_true")

    review = commands.add_parser("review")
    root_argument(review)
    review.add_argument("--review-id", required=True)

    decide = commands.add_parser("decide")
    root_argument(decide)
    decide.add_argument("--review-id", required=True)
    decide.add_argument("--action", choices=("accept", "decline"), required=True)
    decide.add_argument("--feedback")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "reset":
        if not args.confirm_reset:
            raise RuntimeError("reset requires --confirm-reset")
        result = reset_manual_root(args.root, session_id=args.session_id)
    elif args.command == "serve":
        return serve_manual_root(
            args.root,
            process_instance_id=args.process_instance_id,
        )
    elif args.command == "start":
        result = start_manual_root(args.root)
    elif args.command == "stop":
        result = stop_manual_root(args.root)
    elif args.command == "status":
        result = manual_status(args.root)
    elif args.command == "recover-process":
        result = recover_stale_process_record(args.root)
    elif args.command == "verify-isolation":
        result = verify_isolation(args.root)
    elif args.command == "pending-review":
        result = pending_review_status(args.root)
    elif args.command == "submit":
        assert_provider_dispatch_allowed(
            "scripts.continuous_manual.sillytavern_completion",
            external_provider_boundary=(
                _ACTIVE_ROUTE is PROVIDER_BACKED_MANUAL_ROUTE
                and _ACTIVE_TRANSPORT_MODE == "external_provider"
            ),
        )
        _require_running_manual_root(args.root)
        _manifest, identity = _load_root(args.root)
        status, result = _http_json(
            "POST",
            "/v1/chat/completions",
            {
                "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                "stream": False,
                "cera_session_id": identity["session_id"],
                "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                "cera_scene_change": args.scene_change,
                "messages": [{"role": "user", "content": args.message}],
            },
        )
        result = {"http_status": status, "body": result}
    elif args.command == "review":
        _require_running_manual_root(args.root)
        _load_root(args.root)
        status, result = _http_json(
            "GET", f"/v1/cera/reviews/{args.review_id}"
        )
        result = {"http_status": status, "body": result}
    elif args.command == "decide":
        _require_running_manual_root(args.root)
        _load_root(args.root)
        payload: dict[str, Any] = {"action": args.action}
        if args.feedback is not None:
            payload["feedback"] = args.feedback
        status, result = _http_json(
            "POST",
            f"/v1/cera/reviews/{args.review_id}/decision",
            payload,
        )
        result = {"http_status": status, "body": result}
    else:
        raise RuntimeError("unknown continuous manual command")
    _print_json(result)
    return 0 if not isinstance(result, dict) or result.get("passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
