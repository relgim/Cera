"""Retained sequence-first Codex Planner binding for the lean Pi route.

Python projects only explicit, bounded current-scene material into the existing
sequence-first semantic contract.  Accepted Pi state is evidence; the retained
Codex thread is a reasoning aid and never becomes branch authority.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol

from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import canonical_json, text_sha256, to_primitive
from cera.sequence_first.contracts import (
    ApprovedTargetV1,
    CharacterDeltaV1,
    EvidenceRecordV1,
    ProtectedSourceClaimV1,
    SequenceDraftV1,
    SequenceFirstTurnSemanticInputV1,
    Visibility,
)

from .runtime import PlannerTurnInputV1, PlannerTurnOutputV1
from .readable_debug import ReadablePiSceneDebugLog


class SequencePlannerSessionPort(Protocol):
    def plan(self, semantic_input: SequenceFirstTurnSemanticInputV1) -> SequenceDraftV1: ...


class RetainedCodexPlannerAdapter:
    """Adapt one retained Planner session to the lean coordinator contract."""

    def __init__(
        self,
        session: SequencePlannerSessionPort,
        *,
        operation_evidence: ProviderOperationEvidenceStoreV1 | None = None,
        readable_debug: ReadablePiSceneDebugLog | None = None,
    ) -> None:
        self.session = session
        self.operation_evidence = operation_evidence
        self.readable_debug = readable_debug
        self.last_semantic_input: SequenceFirstTurnSemanticInputV1 | None = None
        self._turn_index = 0

    def plan(self, request: PlannerTurnInputV1) -> PlannerTurnOutputV1:
        self._turn_index += 1
        if self.operation_evidence is not None:
            self.operation_evidence.begin_turn(
                ":".join(
                    (
                        request.world_id,
                        request.branch_id,
                        f"planner-{self._turn_index:04d}",
                        text_sha256(request.exact_user_source)[:12],
                    )
                )
            )
        semantic_input = _semantic_input(request)
        sequence = self.session.plan(semantic_input)
        semantic_input.validate_intended(sequence)
        self.last_semantic_input = semantic_input
        if self.readable_debug is not None:
            self.readable_debug.write(
                stage="codex-planner",
                identity=f"{request.world_id}-{request.branch_id}-planner-{self._turn_index:04d}",
                sections={
                    "Exact user input": request.exact_user_source,
                    "Python semantic input sent to Codex Planner": semantic_input,
                    "Codex Planner structured output": sequence,
                },
            )
        return PlannerTurnOutputV1(
            sequence=to_primitive(sequence),
            provider_operations=1,
        )


def _semantic_input(request: PlannerTurnInputV1) -> SequenceFirstTurnSemanticInputV1:
    present = _character_ids(
        request.current_state.get("accepted_present_character_ids"),
        field_name="accepted_present_character_ids",
    )
    remote = _character_ids(
        request.current_state.get("explicitly_authorized_remote_character_ids", ()),
        field_name="explicitly_authorized_remote_character_ids",
    )
    known = tuple(
        dict.fromkeys(
            (
                "character:ted",
                *request.characters.keys(),
                *present,
                *remote,
            )
        )
    )
    _character_ids(known, field_name="known_character_ids")
    public_state = request.current_state.get("public_scene_state")
    if not isinstance(public_state, str) or not public_state.strip():
        raise ContractValidationError("lean Planner requires public_scene_state")

    current_source_key = "source:current"
    current_claim = ProtectedSourceClaimV1(
        claim_key="current_request",
        exact_text=request.exact_user_source,
    )
    prior = _immediate_prior_ordinary_sequence(request.accepted_records)
    evidence = _accepted_evidence(request.accepted_records)
    evidence += _mapping_evidence("relationship", request.relationships)
    evidence += _mapping_evidence("memory", request.relevant_memories)

    unresolved = _text_tuple(
        request.current_state.get("unresolved_threads", ()),
        field_name="unresolved_threads",
    )
    hard_boundaries = tuple(
        dict.fromkeys(
            (
                "Do not invent Ted speech or dialogue.",
                "Do not invent Ted thoughts or feelings.",
                *_text_tuple(
                    request.current_state.get("hard_boundaries", ()),
                    field_name="hard_boundaries",
                ),
            )
        )
    )
    approved_targets = tuple(
        from_mapping(ApprovedTargetV1, value)
        for value in _mapping_sequence(
            request.current_state.get("approved_targets", ()),
            field_name="approved_targets",
        )
    )
    semantics = SequenceFirstTurnSemanticInputV1(
        exact_current_source=request.exact_user_source,
        current_source_key=current_source_key,
        protected_source_claims=(current_claim,),
        known_character_ids=known,
        accepted_present_character_ids=present,
        explicitly_authorized_remote_character_ids=remote,
        current_public_scene_state=public_state,
        prior_realized_sequence=prior,
        character_deltas=tuple(
            CharacterDeltaV1(
                character_id=character_id,
                concise_delta=_bounded_json(value, f"character {character_id}"),
            )
            for character_id, value in request.characters.items()
        ),
        evidence_records=evidence,
        approved_targets=approved_targets,
        unresolved_threads=unresolved,
        hard_boundaries=hard_boundaries,
        scene_reinitialization=bool(
            request.current_state.get("scene_reinitialization", False)
        ),
    )
    return semantics


def _immediate_prior_ordinary_sequence(
    accepted_records: tuple[Mapping[str, Any], ...],
) -> SequenceDraftV1 | None:
    if not accepted_records:
        return None
    receipt = accepted_records[-1].get("receipt")
    if not isinstance(receipt, Mapping) or receipt.get("route") != "ordinary":
        return None
    authority = receipt.get("primary_authority_json")
    if not isinstance(authority, str):
        raise ContractValidationError("accepted ordinary receipt omitted its sequence")
    try:
        payload = json.loads(authority)
    except json.JSONDecodeError as exc:
        raise ContractValidationError("accepted ordinary sequence is invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise ContractValidationError("accepted ordinary sequence is not an object")
    return from_mapping(SequenceDraftV1, payload)


def _accepted_evidence(
    accepted_records: tuple[Mapping[str, Any], ...],
) -> tuple[EvidenceRecordV1, ...]:
    records: list[EvidenceRecordV1] = []
    for index, value in enumerate(accepted_records, start=1):
        receipt = value.get("receipt")
        if not isinstance(receipt, Mapping):
            raise ContractValidationError("accepted evidence omitted its receipt")
        generation = receipt.get("generation")
        if type(generation) is not int or generation < 1:
            raise ContractValidationError("accepted evidence generation is invalid")
        exact_prose = receipt.get("exact_accepted_prose")
        primary_authority = receipt.get("primary_authority_json")
        if not isinstance(exact_prose, str) or not isinstance(primary_authority, str):
            raise ContractValidationError("accepted evidence custody fields are invalid")
        public_value = {
            "receipt": {
                "accepted_turn_id": receipt.get("accepted_turn_id"),
                "generation": generation,
                "route": receipt.get("route"),
                "exact_user_source": receipt.get("exact_user_source"),
                "exact_accepted_prose_sha256": text_sha256(exact_prose),
                "primary_authority_sha256": text_sha256(primary_authority),
            },
            "ordinary_record": value.get("ordinary_record"),
            "adult_projection": value.get("adult_projection"),
        }
        records.append(
            EvidenceRecordV1(
                evidence_key=f"evidence:accepted:{generation:08d}:{index:02d}",
                subject_id=f"accepted:turn:{generation:08d}",
                visibility=Visibility.PUBLIC,
                exact_content=_bounded_json(public_value, "accepted evidence"),
            )
        )
    return tuple(records)


def _mapping_evidence(
    namespace: str,
    values: Mapping[str, Mapping[str, Any]],
) -> tuple[EvidenceRecordV1, ...]:
    output: list[EvidenceRecordV1] = []
    for index, (key, value) in enumerate(values.items(), start=1):
        digest = text_sha256(key)[:16]
        output.append(
            EvidenceRecordV1(
                evidence_key=f"evidence:{namespace}:{digest}:{index:02d}",
                subject_id=f"{namespace}:{digest}",
                visibility=Visibility.PUBLIC,
                exact_content=_bounded_json(value, f"{namespace} evidence"),
            )
        )
    return tuple(output)


def _bounded_json(value: Mapping[str, Any], field_name: str) -> str:
    text = canonical_json(value)
    if len(text) > 4_000:
        raise ContractValidationError(f"{field_name} exceeds the Planner delta bound")
    return text


def _character_ids(value: object, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ContractValidationError(f"{field_name} must be an ordered list")
    result = tuple(value)
    if any(
        not isinstance(item, str) or not item.startswith("character:")
        for item in result
    ):
        raise ContractValidationError(f"{field_name} contains an invalid character")
    if len(result) != len(set(result)):
        raise ContractValidationError(f"{field_name} contains duplicates")
    return result


def _text_tuple(value: object, *, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ContractValidationError(f"{field_name} must be an ordered list")
    result = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in result):
        raise ContractValidationError(f"{field_name} contains invalid text")
    return result


def _mapping_sequence(
    value: object,
    *,
    field_name: str,
) -> tuple[Mapping[str, Any], ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ContractValidationError(f"{field_name} must be an ordered list")
    result = tuple(value)
    if any(not isinstance(item, Mapping) for item in result):
        raise ContractValidationError(f"{field_name} contains a non-object")
    return result
