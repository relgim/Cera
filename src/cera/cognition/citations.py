"""Typed cognition citation classes and provider-turn evidence handoff.

This module is deliberately provider and Pi-Scene neutral.  It classifies the
finite citation namespaces available to a cognition plan, enforces private
knowledge ownership, and materializes only exact facts that independent
validation may consume after the provider turn.  Retrieval adapters construct
the dynamic DTOs; they do not become citation authority merely by returning
context to a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.sequence_first.contracts import (
    SequenceFirstTurnSemanticInputV1,
    Visibility,
)
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, text_sha256

from .contracts import CognitionPlanV1

MAX_COGNITION_CITABLE_RECORD_CHARACTERS = 4_000

_IDENTITY = re.compile(r"[a-z][a-z0-9_.:-]{0,191}\Z")
_TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{0,95}\Z")


class CognitionCitationClass(StrEnum):
    """Closed citation taxonomy shared by validation and evidence selection."""

    SOURCE_CURRENT = "source_current"
    DRAFT_LOCAL_SEQUENCE_ITEM = "draft_local_sequence_item"
    STATIC_VALIDATION_EVIDENCE = "static_validation_evidence"
    DYNAMIC_CONTEXT_ONLY_BINDING = "dynamic_context_only_binding"
    DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE = "dynamic_exact_record_validation_evidence"


@dataclass(frozen=True, slots=True)
class CognitionStaticCitationScopeV1:
    """Fact-free provider projection of Python-classified static references."""

    citable_static_evidence_refs: tuple[str, ...]
    context_only_static_evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for refs in (
            self.citable_static_evidence_refs,
            self.context_only_static_evidence_refs,
        ):
            if refs != tuple(sorted(refs)) or len(refs) != len(set(refs)):
                raise ContractValidationError("static cognition citation scope is not canonical")
            if any(_IDENTITY.fullmatch(value) is None for value in refs):
                raise ContractValidationError(
                    "static cognition citation scope contains an invalid ref"
                )
        if set(self.citable_static_evidence_refs).intersection(
            self.context_only_static_evidence_refs
        ):
            raise ContractValidationError("static cognition citation scopes overlap")

    def to_payload(self) -> dict[str, list[str]]:
        return {
            "citable_static_evidence_refs": list(self.citable_static_evidence_refs),
            "context_only_static_evidence_refs": list(self.context_only_static_evidence_refs),
        }


def cognition_static_citation_scope(
    turn: SequenceFirstTurnSemanticInputV1,
) -> CognitionStaticCitationScopeV1:
    """Partition existing static refs by Python character count without facts."""

    return CognitionStaticCitationScopeV1(
        citable_static_evidence_refs=tuple(
            sorted(
                record.evidence_key
                for record in turn.evidence_records
                if len(record.exact_content) <= MAX_COGNITION_CITABLE_RECORD_CHARACTERS
            )
        ),
        context_only_static_evidence_refs=tuple(
            sorted(
                record.evidence_key
                for record in turn.evidence_records
                if len(record.exact_content) > MAX_COGNITION_CITABLE_RECORD_CHARACTERS
            )
        ),
    )


class CognitionEvidenceVisibility(StrEnum):
    PUBLIC = "public"
    CHARACTER_PRIVATE = "character_private"


@dataclass(frozen=True, slots=True)
class CognitionDynamicEvidenceV1:
    """One request-local dynamic reference with complete retrieval custody."""

    SCHEMA_VERSION: ClassVar[str] = "cera.cognition.dynamic_validation_evidence.v1"
    OPERATION_SCHEMA: ClassVar[str] = "cera.cognition.dynamic_evidence_operation.v1"

    schema_version: str
    evidence_ref: str
    citation_class: CognitionCitationClass
    world_id: str
    branch_id: str
    request_id: str
    binding_sha256: str
    relative_path: str
    source_sha256: str
    source_read_operation_sha256: str
    tool_name: str
    call_index: int
    provider_request_sha256: str
    retrieval_operation_sha256: str
    visibility: CognitionEvidenceVisibility
    knowledge_owner_id: str | None
    record_id: str | None
    concise_authoritative_fact: str | None
    authoritative_fact_sha256: str | None

    @classmethod
    def create(
        cls,
        *,
        evidence_ref: str,
        citation_class: CognitionCitationClass,
        world_id: str,
        branch_id: str,
        request_id: str,
        binding_sha256: str,
        relative_path: str,
        source_sha256: str,
        source_read_operation_sha256: str,
        tool_name: str,
        call_index: int,
        provider_request_sha256: str,
        visibility: CognitionEvidenceVisibility,
        knowledge_owner_id: str | None,
        record_id: str | None,
        concise_authoritative_fact: str | None,
    ) -> CognitionDynamicEvidenceV1:
        fact_sha256 = (
            None if concise_authoritative_fact is None else text_sha256(concise_authoritative_fact)
        )
        operation_payload = {
            "evidence_ref": evidence_ref,
            "citation_class": citation_class.value,
            "world_id": world_id,
            "branch_id": branch_id,
            "request_id": request_id,
            "binding_sha256": binding_sha256,
            "relative_path": relative_path,
            "source_sha256": source_sha256,
            "source_read_operation_sha256": source_read_operation_sha256,
            "tool_name": tool_name,
            "call_index": call_index,
            "provider_request_sha256": provider_request_sha256,
            "visibility": visibility.value,
            "knowledge_owner_id": knowledge_owner_id,
            "record_id": record_id,
            "authoritative_fact_sha256": fact_sha256,
        }
        return cls(
            schema_version=cls.SCHEMA_VERSION,
            evidence_ref=evidence_ref,
            citation_class=citation_class,
            world_id=world_id,
            branch_id=branch_id,
            request_id=request_id,
            binding_sha256=binding_sha256,
            relative_path=relative_path,
            source_sha256=source_sha256,
            source_read_operation_sha256=source_read_operation_sha256,
            tool_name=tool_name,
            call_index=call_index,
            provider_request_sha256=provider_request_sha256,
            retrieval_operation_sha256=domain_sha256(
                cls.OPERATION_SCHEMA,
                operation_payload,
            ),
            visibility=visibility,
            knowledge_owner_id=knowledge_owner_id,
            record_id=record_id,
            concise_authoritative_fact=concise_authoritative_fact,
            authoritative_fact_sha256=fact_sha256,
        )

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("cognition dynamic-evidence schema changed")
        if _IDENTITY.fullmatch(self.evidence_ref) is None:
            raise ContractValidationError("cognition dynamic evidence reference is invalid")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.world_id, self.branch_id, self.request_id)
        ):
            raise ContractValidationError("cognition dynamic evidence scope is incomplete")
        if any(
            not re_is_sha256(value)
            for value in (
                self.binding_sha256,
                self.source_sha256,
                self.source_read_operation_sha256,
                self.provider_request_sha256,
                self.retrieval_operation_sha256,
            )
        ):
            raise ContractValidationError("cognition dynamic evidence hash is invalid")
        path = PurePosixPath(self.relative_path)
        if (
            not self.relative_path
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in self.relative_path
        ):
            raise ContractValidationError("cognition dynamic evidence path is invalid")
        if _TOOL_NAME.fullmatch(self.tool_name) is None:
            raise ContractValidationError("cognition dynamic evidence tool is invalid")
        if type(self.call_index) is not int or self.call_index < 1:
            raise ContractValidationError("cognition dynamic evidence call index is invalid")
        if self.record_id is not None and (
            not isinstance(self.record_id, str) or _IDENTITY.fullmatch(self.record_id) is None
        ):
            raise ContractValidationError("cognition dynamic record identity is invalid")
        if self.visibility is CognitionEvidenceVisibility.PUBLIC:
            if self.knowledge_owner_id is not None:
                raise ContractValidationError("public cognition evidence has a private owner")
        elif not isinstance(self.knowledge_owner_id, str) or not self.knowledge_owner_id.startswith(
            "character:"
        ):
            raise ContractValidationError("private cognition evidence lacks one owner")
        if self.citation_class is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING:
            if (
                self.concise_authoritative_fact is not None
                or self.authoritative_fact_sha256 is not None
            ):
                raise ContractValidationError(
                    "context-only cognition binding carries a validation fact"
                )
        elif self.citation_class is CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE:
            fact = self.concise_authoritative_fact
            if self.tool_name != "get_exact_record" or self.record_id is None:
                raise ContractValidationError(
                    "citable cognition evidence is not an exact-record fetch"
                )
            if (
                not isinstance(fact, str)
                or not fact.strip()
                or len(fact) > MAX_COGNITION_CITABLE_RECORD_CHARACTERS
                or self.authoritative_fact_sha256 != text_sha256(fact)
            ):
                raise ContractValidationError("citable cognition exact-record fact changed")
        else:
            raise ContractValidationError("cognition dynamic evidence class is invalid")
        if self.retrieval_operation_sha256 != self.expected_retrieval_operation_sha256:
            raise ContractValidationError("cognition dynamic retrieval operation changed")

    @property
    def expected_retrieval_operation_sha256(self) -> str:
        return domain_sha256(
            self.OPERATION_SCHEMA,
            {
                "evidence_ref": self.evidence_ref,
                "citation_class": self.citation_class.value,
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "request_id": self.request_id,
                "binding_sha256": self.binding_sha256,
                "relative_path": self.relative_path,
                "source_sha256": self.source_sha256,
                "source_read_operation_sha256": self.source_read_operation_sha256,
                "tool_name": self.tool_name,
                "call_index": self.call_index,
                "provider_request_sha256": self.provider_request_sha256,
                "visibility": self.visibility.value,
                "knowledge_owner_id": self.knowledge_owner_id,
                "record_id": self.record_id,
                "authoritative_fact_sha256": self.authoritative_fact_sha256,
            },
        )

    @property
    def evidence_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CognitionCitationEntryV1:
    evidence_ref: str
    citation_class: CognitionCitationClass
    concise_authoritative_fact: str | None
    authoritative_fact_sha256: str | None
    visibility: CognitionEvidenceVisibility | None
    knowledge_owner_id: str | None
    dynamic_evidence: CognitionDynamicEvidenceV1 | None = None
    static_context_only: bool = False

    def __post_init__(self) -> None:
        if type(self.static_context_only) is not bool:
            raise ContractValidationError("static context-only marker is invalid")
        if _IDENTITY.fullmatch(self.evidence_ref) is None:
            raise ContractValidationError("cognition citation entry is invalid")
        emits_fact = self.citation_class in {
            CognitionCitationClass.STATIC_VALIDATION_EVIDENCE,
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
        }
        if emits_fact:
            fact = self.concise_authoritative_fact
            if (
                not isinstance(fact, str)
                or not fact.strip()
                or len(fact) > MAX_COGNITION_CITABLE_RECORD_CHARACTERS
                or self.authoritative_fact_sha256 != text_sha256(fact)
                or self.visibility is None
            ):
                raise ContractValidationError("cognition citation fact changed")
            if self.visibility is CognitionEvidenceVisibility.PUBLIC:
                if self.knowledge_owner_id is not None:
                    raise ContractValidationError(
                        "public cognition citation entry has a private owner"
                    )
            elif not isinstance(
                self.knowledge_owner_id, str
            ) or not self.knowledge_owner_id.startswith("character:"):
                raise ContractValidationError("private cognition citation entry lacks one owner")
        elif any(
            value is not None
            for value in (
                self.concise_authoritative_fact,
                self.authoritative_fact_sha256,
                self.visibility,
                self.knowledge_owner_id,
            )
        ):
            # Dynamic context keeps visibility inside its custody DTO, but it
            # never exposes a fact-bearing citation entry.
            if not (
                self.citation_class is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
                and self.concise_authoritative_fact is None
                and self.authoritative_fact_sha256 is None
                and self.visibility is None
                and self.knowledge_owner_id is None
            ):
                raise ContractValidationError(
                    "non-emitting cognition citation carries validation evidence"
                )
        if self.dynamic_evidence is None:
            if (
                self.citation_class
                is CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE
            ):
                raise ContractValidationError("dynamic cognition citation lacks custody")
            if (
                self.citation_class is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
                and not self.static_context_only
            ):
                raise ContractValidationError("dynamic cognition citation lacks custody")
        elif (
            self.static_context_only
            or self.dynamic_evidence.evidence_ref != self.evidence_ref
            or self.dynamic_evidence.citation_class is not self.citation_class
        ):
            raise ContractValidationError("dynamic cognition citation custody changed")
        if self.static_context_only and self.citation_class is not (
            CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
        ):
            raise ContractValidationError("static context-only citation class changed")


@dataclass(frozen=True, slots=True)
class CognitionCitationCatalogV1:
    entries: tuple[CognitionCitationEntryV1, ...]

    def __post_init__(self) -> None:
        refs = tuple(value.evidence_ref for value in self.entries)
        if len(refs) != len(set(refs)):
            raise ContractValidationError("cognition citation namespaces collide")

    def entry(self, evidence_ref: str) -> CognitionCitationEntryV1:
        matches = tuple(value for value in self.entries if value.evidence_ref == evidence_ref)
        if len(matches) != 1:
            raise ContractValidationError("decision cites unavailable evidence")
        return matches[0]


@dataclass(frozen=True, slots=True)
class CognitionClassifiedCitationUseV1:
    evidence_ref: str
    decision_key: str
    decision_owner_id: str
    location: str
    entry: CognitionCitationEntryV1


@dataclass(frozen=True, slots=True)
class CognitionSelectedEvidenceV1:
    """One exact fact selected by classified provider citations."""

    SCHEMA_VERSION: ClassVar[str] = "cera.cognition.selected_validation_evidence.v1"

    schema_version: str
    evidence_ref: str
    citation_class: CognitionCitationClass
    concise_authoritative_fact: str
    authoritative_fact_sha256: str
    visibility: CognitionEvidenceVisibility
    knowledge_owner_id: str | None
    cited_decision_owner_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("cognition selected-evidence schema changed")
        if self.citation_class not in {
            CognitionCitationClass.STATIC_VALIDATION_EVIDENCE,
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
        }:
            raise ContractValidationError("cognition selected evidence is not citable")
        if (
            _IDENTITY.fullmatch(self.evidence_ref) is None
            or not self.concise_authoritative_fact.strip()
            or len(self.concise_authoritative_fact) > MAX_COGNITION_CITABLE_RECORD_CHARACTERS
            or self.authoritative_fact_sha256 != text_sha256(self.concise_authoritative_fact)
        ):
            raise ContractValidationError("cognition selected evidence changed")
        if (
            not self.cited_decision_owner_ids
            or self.cited_decision_owner_ids != tuple(sorted(self.cited_decision_owner_ids))
            or len(self.cited_decision_owner_ids) != len(set(self.cited_decision_owner_ids))
            or any(not value.startswith("character:") for value in self.cited_decision_owner_ids)
        ):
            raise ContractValidationError("cognition selected evidence owners are invalid")
        if self.visibility is CognitionEvidenceVisibility.PUBLIC:
            if self.knowledge_owner_id is not None:
                raise ContractValidationError("public selected evidence has a private owner")
        elif self.knowledge_owner_id is None or set(self.cited_decision_owner_ids) != {
            self.knowledge_owner_id
        }:
            raise ContractValidationError(
                "private selected evidence does not match its decision owner"
            )


@dataclass(frozen=True, slots=True)
class CognitionProviderTurnHandoffV1:
    """Request- and plan-bound result finalized before provider acceptance."""

    SCHEMA_VERSION: ClassVar[str] = "cera.cognition.provider_turn_handoff.v1"

    schema_version: str
    turn_semantic_sha256: str
    plan_semantic_sha256: str
    provider_thread_sha256: str
    selected_evidence: tuple[CognitionSelectedEvidenceV1, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("cognition provider-turn handoff schema changed")
        if any(
            not re_is_sha256(value)
            for value in (
                self.turn_semantic_sha256,
                self.plan_semantic_sha256,
                self.provider_thread_sha256,
            )
        ):
            raise ContractValidationError("cognition provider-turn handoff hash is invalid")
        refs = tuple(value.evidence_ref for value in self.selected_evidence)
        if refs != tuple(sorted(refs)) or len(refs) != len(set(refs)):
            raise ContractValidationError("cognition provider-turn evidence is not canonical")

    @property
    def handoff_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


@dataclass(frozen=True, slots=True)
class CognitionProviderCompletedTurnV1:
    """Internal ledger-finalization value; not a provider output schema."""

    plan: CognitionPlanV1
    handoff: CognitionProviderTurnHandoffV1

    def __post_init__(self) -> None:
        if self.plan.semantic_sha256 != self.handoff.plan_semantic_sha256:
            raise ContractValidationError("cognition completed-turn plan changed")


def cognition_citation_catalog(
    *,
    plan: CognitionPlanV1,
    turn: SequenceFirstTurnSemanticInputV1,
    dynamic_evidence: tuple[CognitionDynamicEvidenceV1, ...] = (),
) -> CognitionCitationCatalogV1:
    static_scope = cognition_static_citation_scope(turn)
    citable_static_refs = set(static_scope.citable_static_evidence_refs)
    entries: list[CognitionCitationEntryV1] = [
        CognitionCitationEntryV1(
            evidence_ref=turn.current_source_key,
            citation_class=CognitionCitationClass.SOURCE_CURRENT,
            concise_authoritative_fact=None,
            authoritative_fact_sha256=None,
            visibility=None,
            knowledge_owner_id=None,
        )
    ]
    entries.extend(
        CognitionCitationEntryV1(
            evidence_ref=item.item_key,
            citation_class=CognitionCitationClass.DRAFT_LOCAL_SEQUENCE_ITEM,
            concise_authoritative_fact=None,
            authoritative_fact_sha256=None,
            visibility=None,
            knowledge_owner_id=None,
        )
        for item in plan.sequence.items
    )
    entries.extend(
        CognitionCitationEntryV1(
            evidence_ref=record.evidence_key,
            citation_class=(
                CognitionCitationClass.STATIC_VALIDATION_EVIDENCE
                if record.evidence_key in citable_static_refs
                else CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
            ),
            concise_authoritative_fact=(
                record.exact_content if record.evidence_key in citable_static_refs else None
            ),
            authoritative_fact_sha256=(
                text_sha256(record.exact_content)
                if record.evidence_key in citable_static_refs
                else None
            ),
            visibility=(
                (
                    CognitionEvidenceVisibility.PUBLIC
                    if record.visibility is Visibility.PUBLIC
                    else CognitionEvidenceVisibility.CHARACTER_PRIVATE
                )
                if record.evidence_key in citable_static_refs
                else None
            ),
            knowledge_owner_id=(
                record.knowledge_owner_id if record.evidence_key in citable_static_refs else None
            ),
            static_context_only=(record.evidence_key not in citable_static_refs),
        )
        for record in turn.evidence_records
    )
    entries.extend(
        CognitionCitationEntryV1(
            evidence_ref=value.evidence_ref,
            citation_class=value.citation_class,
            concise_authoritative_fact=value.concise_authoritative_fact,
            authoritative_fact_sha256=value.authoritative_fact_sha256,
            visibility=(
                value.visibility
                if value.citation_class
                is CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE
                else None
            ),
            knowledge_owner_id=(
                value.knowledge_owner_id
                if value.citation_class
                is CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE
                else None
            ),
            dynamic_evidence=value,
        )
        for value in dynamic_evidence
    )
    return CognitionCitationCatalogV1(entries=tuple(entries))


def classify_cognition_plan_citations(
    *,
    plan: CognitionPlanV1,
    turn: SequenceFirstTurnSemanticInputV1,
    dynamic_evidence: tuple[CognitionDynamicEvidenceV1, ...] = (),
) -> tuple[CognitionCitationCatalogV1, tuple[CognitionClassifiedCitationUseV1, ...]]:
    """Classify every decision citation and enforce exact private ownership."""

    catalog = cognition_citation_catalog(
        plan=plan,
        turn=turn,
        dynamic_evidence=dynamic_evidence,
    )
    uses: list[CognitionClassifiedCitationUseV1] = []
    for decision in plan.decision_records:
        locations = (
            ("causal_trigger_refs", decision.causal_trigger_refs),
            ("decisive_factor_refs", decision.decisive_factor_refs),
            (
                "observer_frame.directly_perceived",
                tuple(value.source_ref for value in decision.observer_frame.directly_perceived),
            ),
            (
                "material_pressures.evidence_refs",
                tuple(
                    reference
                    for pressure in decision.material_pressures
                    for reference in pressure.evidence_refs
                ),
            ),
        )
        for location, references in locations:
            for reference in references:
                entry = catalog.entry(reference)
                if entry.citation_class is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING:
                    raise ContractValidationError("decision cites context-only evidence")
                if (
                    entry.visibility is CognitionEvidenceVisibility.CHARACTER_PRIVATE
                    and entry.knowledge_owner_id != decision.owner_id
                ):
                    raise ContractValidationError(
                        "decision cites private evidence owned by another character"
                    )
                uses.append(
                    CognitionClassifiedCitationUseV1(
                        evidence_ref=reference,
                        decision_key=decision.decision_key,
                        decision_owner_id=decision.owner_id,
                        location=location,
                        entry=entry,
                    )
                )
    return catalog, tuple(uses)


def cognition_provider_turn_handoff(
    *,
    plan: CognitionPlanV1,
    turn: SequenceFirstTurnSemanticInputV1,
    provider_thread_sha256: str,
    dynamic_evidence: tuple[CognitionDynamicEvidenceV1, ...] = (),
) -> CognitionProviderTurnHandoffV1:
    _catalog, uses = classify_cognition_plan_citations(
        plan=plan,
        turn=turn,
        dynamic_evidence=dynamic_evidence,
    )
    by_ref: dict[str, list[CognitionClassifiedCitationUseV1]] = {}
    for use in uses:
        if use.entry.citation_class in {
            CognitionCitationClass.STATIC_VALIDATION_EVIDENCE,
            CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE,
        }:
            by_ref.setdefault(use.evidence_ref, []).append(use)
    selected: list[CognitionSelectedEvidenceV1] = []
    for evidence_ref in sorted(by_ref):
        grouped = by_ref[evidence_ref]
        entry = grouped[0].entry
        fact = entry.concise_authoritative_fact
        fact_sha256 = entry.authoritative_fact_sha256
        visibility = entry.visibility
        assert fact is not None and fact_sha256 is not None and visibility is not None
        selected.append(
            CognitionSelectedEvidenceV1(
                schema_version=CognitionSelectedEvidenceV1.SCHEMA_VERSION,
                evidence_ref=evidence_ref,
                citation_class=entry.citation_class,
                concise_authoritative_fact=fact,
                authoritative_fact_sha256=fact_sha256,
                visibility=visibility,
                knowledge_owner_id=entry.knowledge_owner_id,
                cited_decision_owner_ids=tuple(
                    sorted({value.decision_owner_id for value in grouped})
                ),
            )
        )
    return CognitionProviderTurnHandoffV1(
        schema_version=CognitionProviderTurnHandoffV1.SCHEMA_VERSION,
        turn_semantic_sha256=canonical_sha256(turn),
        plan_semantic_sha256=plan.semantic_sha256,
        provider_thread_sha256=provider_thread_sha256,
        selected_evidence=tuple(selected),
    )
