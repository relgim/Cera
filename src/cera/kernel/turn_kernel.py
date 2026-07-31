"""Deterministic, provider-free Phase 4 Turn Kernel."""

from __future__ import annotations

import json

from cera.contracts import ErrorEnvelope, SourceUnit, TurnRequest
from cera.evidence import EvidenceAccessScope, EvidenceFetchRequest, EvidenceService
from cera.errors import ErrorCode, EvidenceServiceError, RetryMode
from cera.ids import IdKind, TypedId, deterministic_id
from cera.serialization import canonical_json, domain_sha256, text_sha256
from cera.storage import SourceRecord

from .models import (
    PreparedTurn,
    ProposedStateRecord,
    ProtectedUserProvenance,
    RequestedContentClass,
    StateDeltaValidationReceipt,
    StateMutationTarget,
    TurnIntakeCommand,
    TurnKernelFailure,
    TurnRoute,
)


class TurnKernel:
    def __init__(self, evidence_service: EvidenceService) -> None:
        self.evidence_service = evidence_service

    def prepare_turn(
        self,
        command: TurnIntakeCommand,
        *,
        access_scope: EvidenceAccessScope,
    ) -> PreparedTurn:
        generation_id = deterministic_id(
            IdKind.GENERATION,
            "cera.turn.request.v1",
            str(command.request_id),
        )
        try:
            snapshot = self.evidence_service.open_snapshot(
                request_id=command.request_id,
                world_id=command.world_id,
                branch_id=command.branch_id,
                access_scope=access_scope,
                world_mode=command.world_mode,
            )
        except EvidenceServiceError as exc:
            self._fail(
                command,
                generation_id,
                exc.code,
                str(exc),
                "snapshot",
            )
        assert snapshot is not None
        if (
            snapshot.generation != command.expected_generation
            or snapshot.branch_head_artifact_id != command.expected_parent_artifact_id
        ):
            self._fail(
                command,
                generation_id,
                ErrorCode.STATE_CONFLICT,
                "request branch generation or parent artifact is stale",
                "branch_resolution",
            )
        if snapshot.genesis_revision_id != command.genesis_revision_id:
            self._fail(
                command,
                generation_id,
                ErrorCode.STATE_CONFLICT,
                "request Genesis revision does not match immutable world binding",
                "branch_resolution",
            )
        present = set(command.present_character_ids)
        responders = set(command.requested_responding_npc_ids)
        if command.protected_user_id not in present:
            self._fail(
                command,
                generation_id,
                ErrorCode.INTAKE_INVALID,
                "protected user must be represented in the present cast",
                "intake",
            )
        if command.protected_user_id in responders:
            self._fail(
                command,
                generation_id,
                ErrorCode.INTAKE_INVALID,
                "protected user cannot be selected as a responding NPC",
                "protected_user_validation",
            )
        if not responders.issubset(present):
            self._fail(
                command,
                generation_id,
                ErrorCode.INTAKE_INVALID,
                "requested responder is outside the present cast",
                "cast_validation",
            )
        if command.preflight_authority.blocked_nonconsensual_crossing_established:
            boundary = command.preflight_authority.blocker_boundary_unit_index
            if boundary is None or boundary >= len(command.source_units):
                self._fail(
                    command,
                    generation_id,
                    ErrorCode.INTAKE_INVALID,
                    "blocked boundary does not identify a source unit",
                    "preflight",
                )
            self._fail(
                command,
                generation_id,
                ErrorCode.BLOCKED_NONCONSENSUAL_EVENT,
                "physical or sexual non-consensual event reached the blocked boundary",
                "preflight_blocker",
            )
        if command.preflight_authority.requested_content_class is RequestedContentClass.ADULT:
            participant_ids = {
                value.participant_id
                for value in command.preflight_authority.adult_participants
            }
            if not participant_ids or not participant_ids.issubset(present):
                self._fail(
                    command,
                    generation_id,
                    ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                    "adult participant authority is incomplete or outside the present cast",
                    "adult_authority",
                )
            if not all(
                value.consent_valid
                for value in command.preflight_authority.adult_participants
            ):
                self._fail(
                    command,
                    generation_id,
                    ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                    "adult identity, consent, capacity, pressure, or freedom evidence is unresolved",
                    "adult_authority",
                )
            adult_evidence_ids = tuple(
                evidence_id
                for participant in command.preflight_authority.adult_participants
                for evidence_id in participant.evidence_ids
            )
            try:
                identity_evidence = self.evidence_service.fetch_evidence(
                    snapshot,
                    EvidenceFetchRequest(
                        evidence_ids=adult_evidence_ids,
                        sections=("route_flags",),
                    ),
                )
            except EvidenceServiceError:
                self._fail(
                    command,
                    generation_id,
                    ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                    "adult identity evidence is unavailable under the turn snapshot",
                    "adult_authority",
                )
            adult_by_subject: set[TypedId] = set()
            for record in identity_evidence.exact_records:
                route_flags = json.loads(record.sections_json)["route_flags"]
                if (
                    route_flags.get("adult_eligibility")
                    == "confirmed_identity_eligible"
                ):
                    adult_by_subject.update(record.subject_ids)
            if not participant_ids.issubset(adult_by_subject):
                self._fail(
                    command,
                    generation_id,
                    ErrorCode.ADULT_AUTHORITY_UNRESOLVED,
                    "participant lacks snapshot-authorized confirmed-adult identity evidence",
                    "adult_authority",
                )
            lookup_receipt_ids = (identity_evidence.receipt.lookup_receipt_id,)
            route = TurnRoute.CONSENT_VALID_ADULT
        else:
            if command.preflight_authority.adult_participants:
                self._fail(
                    command,
                    generation_id,
                    ErrorCode.INTAKE_INVALID,
                    "ordinary route cannot carry unused adult authority",
                    "preflight",
                )
            route = TurnRoute.ORDINARY
            lookup_receipt_ids = ()
        protected_payload = {
            "source_units": tuple(
                {
                    "classification": value.classification.value,
                    "text": value.text,
                }
                for value in command.source_units
            )
        }
        protected_json = canonical_json(protected_payload)
        raw_source_ref = deterministic_id(
            IdKind.PROTECTED_SOURCE,
            "cera.turn.protected_source.v1",
            f"{command.request_id}|{text_sha256(protected_json)}",
        )
        source_units = tuple(
            SourceUnit(
                source_unit_id=deterministic_id(
                    IdKind.SOURCE_UNIT,
                    "cera.turn.source_unit.v1",
                    f"{command.request_id}|{index}|{value.classification.value}|{text_sha256(value.text)}",
                ),
                classification=value.classification,
                text_ref=deterministic_id(
                    IdKind.PROTECTED_SOURCE_SEGMENT,
                    "cera.turn.protected_segment.v1",
                    f"{raw_source_ref}|{index}",
                ),
                sha256=text_sha256(value.text),
            )
            for index, value in enumerate(command.source_units)
        )
        request = TurnRequest(
            schema_version=TurnRequest.SCHEMA_VERSION,
            world_id=command.world_id,
            request_id=command.request_id,
            session_id=command.session_id,
            branch_id=command.branch_id,
            parent_artifact_id=command.expected_parent_artifact_id,
            generation_id=generation_id,
            snapshot_token=snapshot.snapshot_token,
            genesis_revision_id=snapshot.genesis_revision_id,
            protected_user_id=command.protected_user_id,
            raw_source_ref=raw_source_ref,
            source_sha256=text_sha256(protected_json),
            source_units=source_units,
            requested_route_hints=command.requested_route_hints,
            idempotency_key=command.idempotency_key,
        )
        source_record = SourceRecord.from_payload(
            source_id=deterministic_id(
                IdKind.SOURCE,
                "cera.turn.source_record.v1",
                str(command.request_id),
            ),
            request_id=command.request_id,
            branch_id=command.branch_id,
            payload={
                "protected_source_ref": str(raw_source_ref),
                "source_sha256": request.source_sha256,
                "source_units": tuple(str(value.source_unit_id) for value in source_units),
            },
        )
        return PreparedTurn(
            schema_version=PreparedTurn.SCHEMA_VERSION,
            request=request,
            source_record=source_record,
            evidence_snapshot=snapshot,
            route=route,
            present_character_ids=command.present_character_ids,
            eligible_responding_npc_ids=command.requested_responding_npc_ids,
            adult_authority=command.preflight_authority.adult_participants,
            lookup_receipt_ids=lookup_receipt_ids,
            provider_calls_made=0,
            story_state_committed=False,
        )

    def validate_state_delta(
        self,
        prepared: PreparedTurn,
        proposals: tuple[ProposedStateRecord, ...],
        *,
        authorized_evidence_ids: tuple[TypedId, ...],
    ) -> StateDeltaValidationReceipt:
        self.evidence_service._validate_snapshot(prepared.evidence_snapshot)
        authorized = set(authorized_evidence_ids)
        present = set(prepared.present_character_ids)
        for proposal in proposals:
            if proposal.branch_id != prepared.request.branch_id:
                self._delta_fail(prepared, "state proposal belongs to another branch")
            if proposal.mutation_target is StateMutationTarget.GENESIS:
                self._delta_fail(prepared, "runtime state proposal cannot modify Genesis")
            if not set(proposal.evidence_ids).issubset(authorized):
                self._delta_fail(prepared, "state proposal cites unauthorized evidence")
            character_subjects = {
                value for value in proposal.subject_ids if value.kind is IdKind.CHARACTER
            }
            if not character_subjects.issubset(present):
                self._delta_fail(prepared, "state proposal includes an absent character")
            if prepared.request.protected_user_id in character_subjects:
                if (
                    proposal.protected_user_provenance
                    is not ProtectedUserProvenance.EXPLICIT_USER_SOURCE
                ):
                    self._delta_fail(
                        prepared,
                        "model-inferred protected-user state is prohibited",
                    )
        return StateDeltaValidationReceipt(
            validation_receipt_id=deterministic_id(
                IdKind.VALIDATION,
                "cera.state_delta.validation.v1",
                f"{prepared.request.request_id}|{domain_sha256('cera.state_delta.v1', proposals)}",
            ),
            request_id=prepared.request.request_id,
            branch_id=prepared.request.branch_id,
            accepted_record_ids=tuple(value.record_id for value in proposals),
            status="validated_advisory_only",
            story_state_committed=False,
        )

    def _delta_fail(self, prepared: PreparedTurn, message: str) -> None:
        raise TurnKernelFailure(
            self._error(
                request_id=prepared.request.request_id,
                branch_id=prepared.request.branch_id,
                generation_id=prepared.request.generation_id,
                code=ErrorCode.REASONER_CONTRACT_INVALID,
                message=message,
                stage="state_delta_validation",
            )
        )

    def _fail(
        self,
        command: TurnIntakeCommand,
        generation_id: TypedId,
        code: ErrorCode,
        message: str,
        stage: str,
    ) -> None:
        raise TurnKernelFailure(
            self._error(
                request_id=command.request_id,
                branch_id=command.branch_id,
                generation_id=generation_id,
                code=code,
                message=message,
                stage=stage,
            )
        )

    @staticmethod
    def _error(
        *,
        request_id: TypedId,
        branch_id: TypedId,
        generation_id: TypedId,
        code: ErrorCode,
        message: str,
        stage: str,
    ) -> ErrorEnvelope:
        return ErrorEnvelope(
            schema_version=ErrorEnvelope.SCHEMA_VERSION,
            error_code=code,
            message=message,
            trace_id=deterministic_id(
                IdKind.TRACE,
                "cera.turn.failure.v1",
                f"{request_id}|{stage}|{code.value}",
            ),
            request_id=request_id,
            branch_id=branch_id,
            generation_id=generation_id,
            stage=stage,
            story_state_committed=False,
            retry_mode=RetryMode.MANUAL_AFTER_REVIEW,
            details=(),
        )
