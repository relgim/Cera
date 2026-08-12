"""Request-bound named retrieval tools for CERA logic-owner providers.

The semantic retrieval service remains useful to Python callers.  This module
adds the provider-facing custody envelope: one exact request, accepted head,
role, private-character scope, and bounded result ledger.  Returned evidence
keys are allocated by Python from exact source files and are valid only for the
request registry that created them.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar

from cera.cognition import (
    CognitionCitationClass,
    CognitionDynamicEvidenceV1,
    CognitionEvidenceVisibility,
)
from cera.continuous.evidence import (
    EvidenceBindingKind,
    EvidenceVisibility,
    RequestEvidenceBindingRegistry,
    RequestEvidenceBindingV1,
)
from cera.errors import ContractValidationError, ProviderToolRequestError, StateConflictError
from cera.sequence_first.contracts import PROTECTED_USER_ID
from cera.serialization import canonical_sha256, domain_sha256, re_is_sha256, text_sha256

from ._world_workspace_files import read_json_object
from .retrieval import (
    COGNITION_CONTEXT_ONLY_VISIBILITY_NOT_CITABLE,
    COGNITION_ELIGIBLE_AFTER_EXACT_FETCH,
    DOSSIER_INDEX_SCHEMA,
    MAX_RETRIEVAL_CALLS,
    MAX_SEARCH_RESULTS,
    BranchRetrievalService,
    cognition_exact_record_projection,
)

NAMED_RETRIEVAL_TOOLS = (
    "get_turn_context",
    "get_character_context",
    "search_evidence",
    "get_exact_record",
    "get_relationship_context",
    "get_memory_context",
    "get_thread_context",
    "get_voice_examples",
    "get_craft_context",
)
MAX_NAMED_RETRIEVAL_RETURNED_BYTES = 1_048_576
NAMED_RETRIEVAL_PROVIDER_REQUEST_FAILURE_POLICY_ID = (
    "cera.pi_scene.named_retrieval_provider_request_failures.v4"
)
COGNITION_NAMED_RETRIEVAL_PROVIDER_CONTRACT_ID = (
    "cera.pi_scene.cognition_named_retrieval_provider_contract.v1"
)


@dataclass(frozen=True, slots=True)
class _CognitionExactLocatorV1:
    record_id: str
    evidence_ref: str
    binding_sha256: str
    relative_path: str
    source_sha256: str


class RetrievalProviderRole(StrEnum):
    PLANNER = "planner"
    COGNITION_PLANNER = "cognition_planner"
    VALIDATOR = "validator"
    ADULT_SCENE = "adult_scene"
    ADULT_FILTER = "adult_filter"
    RECORDER = "recorder"


_ROLE_TOOLS: dict[RetrievalProviderRole, frozenset[str]] = {
    RetrievalProviderRole.PLANNER: frozenset(NAMED_RETRIEVAL_TOOLS),
    RetrievalProviderRole.COGNITION_PLANNER: frozenset(NAMED_RETRIEVAL_TOOLS),
    RetrievalProviderRole.VALIDATOR: frozenset(
        {
            "get_turn_context",
            "get_character_context",
            "search_evidence",
            "get_exact_record",
            "get_relationship_context",
            "get_memory_context",
            "get_thread_context",
        }
    ),
    RetrievalProviderRole.ADULT_SCENE: frozenset(NAMED_RETRIEVAL_TOOLS),
    RetrievalProviderRole.ADULT_FILTER: frozenset(
        {
            "get_turn_context",
            "get_character_context",
            "search_evidence",
            "get_exact_record",
            "get_relationship_context",
            "get_memory_context",
            "get_thread_context",
        }
    ),
    RetrievalProviderRole.RECORDER: frozenset(
        {
            "get_turn_context",
            "get_character_context",
            "get_exact_record",
            "get_relationship_context",
            "get_memory_context",
            "get_thread_context",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class NamedRetrievalRequestBindingV1:
    """Immutable server-side authority for one provider retrieval operation."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.named_retrieval_request.v1"

    schema_version: str
    request_id: str
    world_id: str
    branch_id: str
    accepted_head_sha256: str | None
    role: RetrievalProviderRole
    private_character_ids: tuple[str, ...]
    maximum_calls: int = MAX_RETRIEVAL_CALLS
    maximum_returned_bytes: int = MAX_NAMED_RETRIEVAL_RETURNED_BYTES

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("named retrieval request schema changed")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (self.request_id, self.world_id, self.branch_id)
        ):
            raise ContractValidationError("named retrieval request identity is incomplete")
        if self.accepted_head_sha256 is not None and (
            not isinstance(self.accepted_head_sha256, str)
            or len(self.accepted_head_sha256) != 64
            or any(value not in "0123456789abcdef" for value in self.accepted_head_sha256)
        ):
            raise ContractValidationError("named retrieval accepted head is invalid")
        if len(self.private_character_ids) != len(set(self.private_character_ids)):
            raise ContractValidationError("named retrieval private scope has duplicates")
        if any(
            not isinstance(value, str) or not value.startswith("character:")
            for value in self.private_character_ids
        ):
            raise ContractValidationError("named retrieval private scope is invalid")
        if (
            self.role is RetrievalProviderRole.COGNITION_PLANNER
            and PROTECTED_USER_ID in self.private_character_ids
        ):
            raise PermissionError(
                "cognition retrieval cannot receive protected-user private authority"
            )
        if not 1 <= self.maximum_calls <= MAX_RETRIEVAL_CALLS:
            raise ContractValidationError("named retrieval call ceiling is invalid")
        if not 4_096 <= self.maximum_returned_bytes <= MAX_NAMED_RETRIEVAL_RETURNED_BYTES:
            raise ContractValidationError("named retrieval byte ceiling is invalid")

    @property
    def binding_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)


class BoundNamedRetrievalTools:
    """Typed named operations over one branch-local retrieval service."""

    tool_names: tuple[str, ...] = NAMED_RETRIEVAL_TOOLS
    provider_request_failure_policy_id = (
        NAMED_RETRIEVAL_PROVIDER_REQUEST_FAILURE_POLICY_ID
    )

    def __init__(
        self,
        service: BranchRetrievalService,
        *,
        binding: NamedRetrievalRequestBindingV1,
        evidence_registry: RequestEvidenceBindingRegistry,
    ) -> None:
        if (service.workspace.world_id, service.workspace.branch_id) != (
            binding.world_id,
            binding.branch_id,
        ):
            raise StateConflictError("named retrieval service changed branch scope")
        if (
            evidence_registry.world_id,
            evidence_registry.branch_id,
            evidence_registry.turn_id,
        ) != (binding.world_id, binding.branch_id, binding.request_id):
            raise StateConflictError("named retrieval registry changed request scope")
        self.service = service
        self.binding = binding
        self.evidence_registry = evidence_registry
        self._returned_bytes = 0
        self._complete_dossier_character_ids: set[str] = set()
        self._advertised_exact_record_locators: dict[
            str, _CognitionExactLocatorV1 | None
        ] = {}
        self._fetched_exact_record_ids: set[str] = set()
        self._cognition_dynamic_by_ref: dict[str, CognitionDynamicEvidenceV1] = {}
        self._cognition_expected_sha256_by_ref: dict[str, str] = {}
        self._successful_provider_calls: dict[int, tuple[str, str]] = {}
        self._direct_provider_call_index = 0
        self._provider_turn_finalized = False
        if self.cognition_mode and PROTECTED_USER_ID in binding.private_character_ids:
            raise PermissionError(
                "cognition retrieval cannot receive protected-user private authority"
            )

    @property
    def cognition_mode(self) -> bool:
        return self.binding.role is RetrievalProviderRole.COGNITION_PLANNER

    @property
    def provider_contract_id(self) -> str | None:
        return (
            COGNITION_NAMED_RETRIEVAL_PROVIDER_CONTRACT_ID
            if self.cognition_mode
            else None
        )

    def finalize_provider_turn(
        self,
        provider_calls: tuple[object, ...] | None = None,
    ) -> tuple[CognitionDynamicEvidenceV1, ...]:
        """Return typed transient evidence without adding facts to debug receipts."""

        if not self.cognition_mode:
            return ()
        if self._provider_turn_finalized:
            raise StateConflictError("cognition retrieval finalization is one-shot")
        self._provider_turn_finalized = True
        try:
            selected = tuple(
                self._cognition_dynamic_by_ref[key]
                for key in sorted(self._cognition_dynamic_by_ref)
            )
            self._validate_cognition_finalization(
                selected,
                provider_calls=provider_calls,
            )
            return selected
        finally:
            self._clear_cognition_transient_state()

    def abort_provider_turn(self) -> None:
        if not self.cognition_mode:
            return
        self._provider_turn_finalized = True
        self._clear_cognition_transient_state()

    def _clear_cognition_transient_state(self) -> None:
        self._cognition_dynamic_by_ref.clear()
        self._cognition_expected_sha256_by_ref.clear()
        self._advertised_exact_record_locators.clear()
        self._fetched_exact_record_ids.clear()
        self._complete_dossier_character_ids.clear()
        self._successful_provider_calls.clear()

    @property
    def binding_sha256(self) -> str:
        return self.binding.binding_sha256

    @property
    def returned_bytes(self) -> int:
        return self._returned_bytes

    def invoke(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        provider_call_index: int | None = None,
        provider_request_sha256: str | None = None,
    ) -> dict[str, Any]:
        if self.cognition_mode and self._provider_turn_finalized:
            raise StateConflictError("cognition retrieval request is already finalized")
        if tool_name not in self.tool_names or not isinstance(arguments, dict):
            raise ContractValidationError("named retrieval request is invalid")
        expected_request_sha256 = canonical_sha256(
            {"tool": tool_name, "arguments": arguments}
        )
        if provider_call_index is None:
            self._direct_provider_call_index += 1
            provider_call_index = self._direct_provider_call_index
        if type(provider_call_index) is not int or provider_call_index < 1:
            raise ContractValidationError("named retrieval provider call index is invalid")
        if provider_request_sha256 is None:
            provider_request_sha256 = expected_request_sha256
        if provider_request_sha256 != expected_request_sha256:
            raise StateConflictError("named retrieval provider request hash changed")
        assert provider_request_sha256 is not None
        if tool_name not in _ROLE_TOOLS[self.binding.role]:
            raise PermissionError("named retrieval tool is not authorized for this role")
        self._assert_current_binding()
        # Private scope authorization happens before any character-private
        # bytes are searched or fetched.  This prevents yes/no term probing.
        self._authorize_private_arguments(tool_name, arguments)
        if tool_name in {
            "get_character_context",
            "get_relationship_context",
            "get_memory_context",
        } and (
            isinstance(arguments.get("character_id"), str)
            and arguments["character_id"] in self._complete_dossier_character_ids
        ):
            raise ProviderToolRequestError(
                "named retrieval rejected a redundant complete-dossier request"
            )
        raw = self._dispatch(tool_name, arguments)
        search_record_ids = (
            self._cognition_search_record_ids(raw)
            if self.cognition_mode and tool_name == "search_evidence"
            else ()
        )
        self._authorize_exact_result(tool_name, raw)
        source_paths = self._source_paths(tool_name, arguments, raw)
        bindings = tuple(self._allocate_source(path) for path in source_paths)
        if self.cognition_mode and tool_name == "get_exact_record":
            self._validate_exact_locator(raw, bindings)
        candidates = self._cognition_candidates(
            tool_name,
            raw,
            bindings,
            provider_call_index=provider_call_index,
            provider_request_sha256=provider_request_sha256,
        )
        evidence_refs = tuple(
            value.evidence_ref
            for value in candidates
            if value.citation_class
            is CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE
        )
        context_refs = tuple(
            value.evidence_ref
            for value in candidates
            if value.citation_class
            is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
        )
        if set(evidence_refs).intersection(context_refs):
            raise StateConflictError("cognition retrieval result ref classes overlap")
        result: dict[str, Any] = {
            "schema_version": (
                "cera.pi_scene.named_retrieval_result.v2"
                if self.cognition_mode
                else "cera.pi_scene.named_retrieval_result.v1"
            ),
            "tool": tool_name,
            "data": self._semantic_data(
                tool_name,
                raw,
                bindings,
                cognition_mode=self.cognition_mode,
                evidence_refs=frozenset(evidence_refs),
            ),
            "source_revisions": {
                value.binding_key: value.record_revision for value in bindings
            },
        }
        if self.cognition_mode:
            result.update(
                {
                    "context_refs": list(context_refs),
                    "evidence_refs": list(evidence_refs),
                }
            )
        else:
            result["evidence_refs"] = [value.binding_key for value in bindings]
        result["budget"] = {
            "calls_remaining": self.binding.maximum_calls - self.service.call_count,
            "bytes_remaining_before_call": (
                self.binding.maximum_returned_bytes - self._returned_bytes
            ),
        }
        encoded = json.dumps(
            result, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if self._returned_bytes + len(encoded) > self.binding.maximum_returned_bytes:
            raise StateConflictError("named retrieval reached its returned-byte ceiling")
        self._returned_bytes += len(encoded)
        self._assert_current_binding()
        if tool_name == "get_turn_context" and isinstance(raw, Mapping):
            dossiers = raw.get("character_dossiers", ())
            if isinstance(dossiers, list | tuple):
                self._complete_dossier_character_ids.update(
                    str(value["character_id"])
                    for value in dossiers
                    if isinstance(value, Mapping)
                    and isinstance(value.get("character_id"), str)
                )
        elif tool_name == "get_character_context":
            self._complete_dossier_character_ids.add(_character_argument(arguments))
        elif tool_name == "search_evidence" and isinstance(raw, Mapping):
            if self.cognition_mode:
                for record_id, binding in zip(
                    search_record_ids,
                    bindings,
                    strict=True,
                ):
                    if binding.relative_path is None:
                        raise StateConflictError(
                            "cognition search locator lacks an exact path"
                        )
                    self._advertised_exact_record_locators[record_id] = (
                        _CognitionExactLocatorV1(
                            record_id=record_id,
                            evidence_ref=binding.binding_key,
                            binding_sha256=binding.binding_sha256,
                            relative_path=binding.relative_path,
                            source_sha256=binding.source_sha256,
                        )
                    )
            else:
                self._advertised_exact_record_locators.update(
                    {
                        str(value["record_id"]): None
                        for value in raw.get("records", ())
                        if isinstance(value, Mapping)
                        and isinstance(value.get("record_id"), str)
                    }
                )
        elif tool_name == "get_exact_record" and isinstance(raw, Mapping):
            exact_record_id = raw.get("record_id")
            if isinstance(exact_record_id, str):
                self._fetched_exact_record_ids.add(exact_record_id)
        for candidate in candidates:
            prior = self._cognition_dynamic_by_ref.get(candidate.evidence_ref)
            if prior is None:
                self._cognition_dynamic_by_ref[candidate.evidence_ref] = candidate
                self._cognition_expected_sha256_by_ref[candidate.evidence_ref] = (
                    candidate.evidence_sha256
                )
                continue
            if (
                candidate.citation_class
                is CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE
                and prior.citation_class
                is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
                and (
                    candidate.relative_path,
                    candidate.source_sha256,
                    candidate.binding_sha256,
                )
                == (
                    prior.relative_path,
                    prior.source_sha256,
                    prior.binding_sha256,
                )
            ):
                # A search locator is promoted only by a successful exact read
                # of the same immutable request/path/hash binding.
                self._cognition_dynamic_by_ref[candidate.evidence_ref] = candidate
                self._cognition_expected_sha256_by_ref[candidate.evidence_ref] = (
                    candidate.evidence_sha256
                )
                continue
            if (
                (candidate.relative_path, candidate.source_sha256, candidate.binding_sha256)
                == (prior.relative_path, prior.source_sha256, prior.binding_sha256)
                and (
                    candidate.citation_class
                    is CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
                    or prior.citation_class
                    is CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE
                )
            ):
                # Repeated locators do not mint a second identity, and a later
                # broad result cannot downgrade an already exact promotion.
                continue
            if candidate != prior:
                raise StateConflictError("cognition retrieval evidence binding collided")
        prior_call = self._successful_provider_calls.get(provider_call_index)
        call_custody = (tool_name, provider_request_sha256)
        if prior_call is not None and prior_call != call_custody:
            raise StateConflictError("cognition provider call custody collided")
        self._successful_provider_calls[provider_call_index] = call_custody
        return result

    def _cognition_search_record_ids(self, raw: object) -> tuple[str, ...]:
        if not isinstance(raw, Mapping):
            raise StateConflictError("cognition search result changed shape")
        records = raw.get("records", ())
        if not isinstance(records, list | tuple):
            raise StateConflictError("cognition search records changed shape")
        record_ids = tuple(
            value.get("record_id") if isinstance(value, Mapping) else None
            for value in records
        )
        if any(not isinstance(value, str) or not value for value in record_ids):
            raise StateConflictError("cognition search record identity changed")
        typed = tuple(str(value) for value in record_ids)
        if len(typed) != len(set(typed)) or set(typed).intersection(
            self._advertised_exact_record_locators
        ):
            raise StateConflictError("cognition search returned a duplicate record identity")
        return typed

    def _validate_exact_locator(
        self,
        raw: object,
        bindings: Sequence[RequestEvidenceBindingV1],
    ) -> None:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("record_id"), str):
            raise StateConflictError("exact cognition result changed identity")
        record_id = str(raw["record_id"])
        locator = self._advertised_exact_record_locators.get(record_id)
        if locator is None or len(bindings) != 1:
            raise StateConflictError("exact cognition result lacks prior locator custody")
        binding = bindings[0]
        if (
            binding.binding_key != locator.evidence_ref
            or binding.binding_sha256 != locator.binding_sha256
            or binding.relative_path != locator.relative_path
            or binding.source_sha256 != locator.source_sha256
        ):
            raise StateConflictError("exact cognition record changed after its locator")

    def _validate_cognition_finalization(
        self,
        selected: tuple[CognitionDynamicEvidenceV1, ...],
        *,
        provider_calls: tuple[object, ...] | None,
    ) -> None:
        if set(self._cognition_expected_sha256_by_ref) != {
            value.evidence_ref for value in selected
        }:
            raise StateConflictError("cognition final evidence catalog changed")
        bindings = {value.binding_key: value for value in self.evidence_registry.bindings}
        observed_calls = dict(self._successful_provider_calls)
        if provider_calls is not None:
            reconciled: dict[int, tuple[str, str]] = {}
            for call in provider_calls:
                call_index = getattr(call, "call_index", None)
                tool_name = getattr(call, "tool_name", None)
                request_sha256 = getattr(call, "request_sha256", None)
                success = getattr(call, "success", None)
                if (
                    type(call_index) is not int
                    or call_index < 1
                    or not isinstance(tool_name, str)
                    or not isinstance(request_sha256, str)
                    or not re_is_sha256(request_sha256)
                    or type(success) is not bool
                ):
                    raise StateConflictError("cognition provider call ledger changed shape")
                if success:
                    reconciled[call_index] = (tool_name, request_sha256)
            if any(reconciled.get(key) != value for key, value in observed_calls.items()):
                raise StateConflictError("cognition provider call ledger changed custody")
            observed_calls = reconciled
        for value in selected:
            binding = bindings.get(value.evidence_ref)
            if binding is None or binding.kind is not EvidenceBindingKind.WORLD_RECORD:
                raise StateConflictError("cognition evidence lacks its request binding")
            expected_visibility = (
                CognitionEvidenceVisibility.PUBLIC
                if binding.visibility is EvidenceVisibility.PUBLIC
                else CognitionEvidenceVisibility.CHARACTER_PRIVATE
            )
            expected_read_operation = canonical_sha256(
                {
                    "schema_version": "cera.pi_scene.named_retrieval_exact_read.v1",
                    "request_id": self.binding.request_id,
                    "relative_path": binding.relative_path,
                    "source_sha256": binding.source_sha256,
                }
            )
            if (
                self._cognition_expected_sha256_by_ref.get(value.evidence_ref)
                != value.evidence_sha256
                or value.world_id != self.binding.world_id
                or value.branch_id != self.binding.branch_id
                or value.request_id != self.binding.request_id
                or value.binding_sha256 != binding.binding_sha256
                or value.relative_path != binding.relative_path
                or value.source_sha256 != binding.source_sha256
                or value.source_read_operation_sha256
                != binding.exact_read_operation_sha256
                or value.source_read_operation_sha256 != expected_read_operation
                or value.visibility is not expected_visibility
                or value.knowledge_owner_id != binding.knowledge_owner_id
                or observed_calls.get(value.call_index)
                != (value.tool_name, value.provider_request_sha256)
            ):
                raise StateConflictError("cognition dynamic evidence changed custody")

    def _dispatch(self, tool_name: str, arguments: dict[str, Any]) -> object:
        if tool_name == "get_turn_context":
            character_ids = arguments.get("character_ids", ())
            if character_ids is None:
                character_ids = ()
            if not isinstance(character_ids, list | tuple):
                raise ContractValidationError("turn-context character IDs are invalid")
            selected = tuple(dict.fromkeys(character_ids))
            if len(selected) > 1:
                raise ProviderToolRequestError(
                    "turn-context explicit selection exceeds one distinct character"
                )
            return self.service.get_turn_context(selected)
        if tool_name == "get_character_context":
            return self.service.get_character_context(_character_argument(arguments))
        if tool_name == "search_evidence":
            terms = arguments.get("terms")
            if not isinstance(terms, list):
                raise ContractValidationError("evidence-search terms are invalid")
            character_id = arguments.get("character_id")
            limit = arguments.get("limit", MAX_SEARCH_RESULTS)
            if character_id is not None and not isinstance(character_id, str):
                raise ContractValidationError("evidence-search character scope is invalid")
            if type(limit) is not int:
                raise ContractValidationError("evidence-search limit is invalid")
            if self.cognition_mode:
                return self.service.search_cognition_evidence(
                    terms,
                    character_id=character_id,
                    limit=limit,
                )
            return self.service.search_evidence(terms, character_id=character_id, limit=limit)
        if tool_name == "get_exact_record":
            record_id = arguments.get("record_id")
            if not isinstance(record_id, str):
                raise ContractValidationError("exact-record identity is invalid")
            if "search_evidence" in _ROLE_TOOLS[self.binding.role] and (
                record_id not in self._advertised_exact_record_locators
            ):
                if self.cognition_mode:
                    raise StateConflictError(
                        "exact cognition record lacks prior locator custody"
                    )
                raise ProviderToolRequestError(
                    "exact record was not advertised by a prior successful search"
                )
            if self.cognition_mode and record_id in self._fetched_exact_record_ids:
                raise ProviderToolRequestError(
                    "exact record was already fetched in this cognition request"
                )
            return self.service.get_exact_record(record_id)
        if tool_name == "get_relationship_context":
            return dict(self.service.get_relationship_context(_character_argument(arguments)))
        if tool_name == "get_memory_context":
            return dict(self.service.get_memory_context(_character_argument(arguments)))
        if tool_name == "get_thread_context":
            if arguments:
                raise ContractValidationError("thread-context arguments are invalid")
            return list(self.service.get_thread_context())
        if tool_name == "get_voice_examples":
            return self.service.get_voice_examples(_character_argument(arguments))
        if tool_name == "get_craft_context":
            if arguments:
                raise ContractValidationError("craft-context arguments are invalid")
            return dict(self.service.get_craft_context())
        raise AssertionError("closed named retrieval dispatch changed")

    def _authorize_private_arguments(
        self, tool_name: str, arguments: Mapping[str, Any]
    ) -> None:
        character_ids: list[object] = []
        if tool_name == "get_turn_context":
            raw = arguments.get("character_ids", ())
            if raw is not None:
                if not isinstance(raw, list | tuple):
                    raise ContractValidationError("turn-context character IDs are invalid")
                character_ids.extend(raw)
        elif tool_name in {
            "get_character_context",
            "get_relationship_context",
            "get_memory_context",
            "get_voice_examples",
        }:
            character_ids.append(arguments.get("character_id"))
        elif tool_name == "search_evidence" and arguments.get("character_id") is not None:
            character_ids.append(arguments.get("character_id"))
        allowed = set(self.binding.private_character_ids)
        for value in character_ids:
            if not isinstance(value, str) or value not in allowed:
                raise PermissionError("character-private retrieval scope is not authorized")

    def _authorize_exact_result(self, tool_name: str, raw: object) -> None:
        if tool_name != "get_exact_record" or not isinstance(raw, Mapping):
            return
        if self.cognition_mode:
            visibility, owner, eligibility, _fact = cognition_exact_record_projection(raw)
            if eligibility == COGNITION_CONTEXT_ONLY_VISIBILITY_NOT_CITABLE:
                raise PermissionError(
                    "exact cognition record visibility is not authorized"
                )
            if (
                visibility == "character_private"
                and owner not in self.binding.private_character_ids
            ):
                raise PermissionError(
                    "character-private exact record is not authorized"
                )
            return
        visibility = str(raw.get("visibility", "public")).casefold()
        owner = raw.get("knowledge_owner_id")
        if visibility in {"creator_private", "creator-only"}:
            raise PermissionError("creator-private record is not role-authorized")
        if visibility != "public" and owner not in self.binding.private_character_ids:
            raise PermissionError("character-private exact record is not authorized")

    def _assert_current_binding(self) -> None:
        identity = read_json_object(
            self.service.workspace.branch_root / "BRANCH_IDENTITY.json",
            "named retrieval branch identity",
        )
        if identity != {
            "world_id": self.binding.world_id,
            "branch_id": self.binding.branch_id,
        }:
            raise PermissionError("named retrieval branch identity changed")
        index = read_json_object(
            self.service.workspace.branch_root
            / "DERIVED"
            / "CurrentCharacterDossiers"
            / "INDEX.json",
            "named retrieval dossier index",
        )
        if (
            index.get("schema_version") != DOSSIER_INDEX_SCHEMA
            or index.get("world_id") != self.binding.world_id
            or index.get("branch_id") != self.binding.branch_id
            or index.get("accepted_head_sha256") != self.binding.accepted_head_sha256
        ):
            raise StateConflictError("named retrieval accepted checkpoint changed")

    def _source_paths(
        self,
        tool_name: str,
        arguments: Mapping[str, Any],
        raw: object,
    ) -> tuple[Path, ...]:
        root = self.service.workspace.branch_root
        if tool_name == "get_turn_context":
            assert isinstance(raw, Mapping)
            paths = [
                root / "ACTIVE" / "WORLD_STATE.json",
                root / "DERIVED" / "CurrentCharacterDossiers" / "INDEX.json",
            ]
            for dossier in raw.get("character_dossiers", ()):
                if isinstance(dossier, Mapping) and isinstance(
                    dossier.get("character_id"), str
                ):
                    paths.append(self._dossier_path(str(dossier["character_id"])))
            return _unique_paths(paths)
        if tool_name in {
            "get_character_context",
            "get_relationship_context",
            "get_memory_context",
        }:
            return (self._dossier_path(_character_argument(arguments)),)
        if tool_name == "search_evidence":
            assert isinstance(raw, Mapping)
            paths = []
            for row in raw.get("records", ()):
                if not isinstance(row, Mapping):
                    raise StateConflictError("evidence-search result changed shape")
                relative = row.get("path")
                if isinstance(relative, str):
                    paths.append(root.joinpath(*Path(relative).parts))
                elif isinstance(row.get("character_id"), str):
                    paths.append(self._dossier_path(str(row["character_id"])))
                else:
                    raise StateConflictError("evidence-search result lacks exact source")
            return _unique_paths(paths)
        if tool_name == "get_exact_record":
            assert isinstance(raw, Mapping) and isinstance(raw.get("path"), str)
            return (root.joinpath(*Path(str(raw["path"])).parts),)
        if tool_name in {"get_thread_context", "get_voice_examples", "get_craft_context"}:
            return (root / "DERIVED" / "ContextCatalogs" / "CURRENT.json",)
        raise AssertionError("closed named retrieval source mapping changed")

    def _dossier_path(self, character_id: str) -> Path:
        index = read_json_object(
            self.service.workspace.branch_root
            / "DERIVED"
            / "CurrentCharacterDossiers"
            / "INDEX.json",
            "named retrieval dossier index",
        )
        characters = index.get("characters")
        row = characters.get(character_id) if isinstance(characters, Mapping) else None
        if not isinstance(row, Mapping) or not isinstance(row.get("path"), str):
            raise FileNotFoundError("named retrieval dossier source is unavailable")
        return self.service.workspace.branch_root.joinpath(*Path(str(row["path"])).parts)

    def _available_dossier_ids(self) -> frozenset[str]:
        index = read_json_object(
            self.service.workspace.branch_root
            / "DERIVED"
            / "CurrentCharacterDossiers"
            / "INDEX.json",
            "named retrieval dossier index",
        )
        characters = index.get("characters")
        if not isinstance(characters, Mapping):
            raise StateConflictError("named retrieval dossier catalog changed shape")
        return frozenset(str(value) for value in characters)

    def _allocate_source(self, path: Path) -> RequestEvidenceBindingV1:
        root = self.service.workspace.branch_root
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or resolved.is_symlink() or not resolved.is_file():
            raise PermissionError("named retrieval source escaped its branch")
        relative = resolved.relative_to(root).as_posix()
        text = resolved.read_text(encoding="utf-8")
        payload: object = json.loads(text) if resolved.suffix.casefold() == ".json" else text
        revision = payload.get("_cera_revision") if isinstance(payload, Mapping) else None
        visibility, owner = _source_visibility(relative, payload)
        if visibility is EvidenceVisibility.CREATOR_PRIVATE:
            raise PermissionError("creator-private source is not provider-visible")
        if visibility is EvidenceVisibility.CHARACTER_PRIVATE and (
            owner not in self.binding.private_character_ids
        ):
            raise PermissionError("character-private source is outside request scope")
        source_sha256 = text_sha256(text)
        return self.evidence_registry.allocate_world_record(
            relative_path=relative,
            source_sha256=source_sha256,
            record_revision=revision if type(revision) is int else None,
            record_type=_record_type(relative),
            visibility=visibility,
            knowledge_owner_id=owner,
            exact_read_operation_sha256=canonical_sha256(
                {
                    "schema_version": "cera.pi_scene.named_retrieval_exact_read.v1",
                    "request_id": self.binding.request_id,
                    "relative_path": relative,
                    "source_sha256": source_sha256,
                }
            ),
        )

    def _cognition_candidates(
        self,
        tool_name: str,
        raw: object,
        bindings: Sequence[RequestEvidenceBindingV1],
        *,
        provider_call_index: int,
        provider_request_sha256: str,
    ) -> tuple[CognitionDynamicEvidenceV1, ...]:
        if not self.cognition_mode:
            return ()
        record_ids: list[str | None] = [None] * len(bindings)
        fact: str | None = None
        eligibility: str | None = None
        exact_visibility: str | None = None
        exact_owner: str | None = None
        if tool_name == "search_evidence" and isinstance(raw, Mapping):
            records = tuple(
                value for value in raw.get("records", ()) if isinstance(value, Mapping)
            )
            if len(records) != len(bindings):
                raise StateConflictError("cognition search binding count changed")
            record_ids = [
                str(value["record_id"])
                if isinstance(value.get("record_id"), str)
                else None
                for value in records
            ]
        elif tool_name == "get_exact_record" and isinstance(raw, Mapping):
            exact_visibility, exact_owner, eligibility, fact = (
                cognition_exact_record_projection(raw)
            )
            record_id = raw.get("record_id")
            if not isinstance(record_id, str):
                raise StateConflictError("exact cognition record identity changed")
            record_ids = [record_id]
        output: list[CognitionDynamicEvidenceV1] = []
        for index, binding in enumerate(bindings):
            if (
                binding.relative_path is None
                or binding.exact_read_operation_sha256 is None
            ):
                raise StateConflictError("cognition retrieval binding lacks exact custody")
            is_exact_evidence = (
                tool_name == "get_exact_record"
                and eligibility == COGNITION_ELIGIBLE_AFTER_EXACT_FETCH
            )
            visibility = (
                exact_visibility
                if tool_name == "get_exact_record"
                else binding.visibility.value
            )
            owner = (
                exact_owner
                if tool_name == "get_exact_record"
                else binding.knowledge_owner_id
            )
            if visibility not in {"public", "character_private"}:
                raise PermissionError(
                    "cognition retrieval visibility is not representable"
                )
            output.append(
                CognitionDynamicEvidenceV1.create(
                    evidence_ref=binding.binding_key,
                    citation_class=(
                        CognitionCitationClass.DYNAMIC_EXACT_RECORD_VALIDATION_EVIDENCE
                        if is_exact_evidence
                        else CognitionCitationClass.DYNAMIC_CONTEXT_ONLY_BINDING
                    ),
                    world_id=binding.world_id,
                    branch_id=binding.branch_id,
                    request_id=binding.turn_id,
                    binding_sha256=binding.binding_sha256,
                    relative_path=binding.relative_path,
                    source_sha256=binding.source_sha256,
                    source_read_operation_sha256=(
                        binding.exact_read_operation_sha256
                    ),
                    tool_name=tool_name,
                    call_index=provider_call_index,
                    provider_request_sha256=provider_request_sha256,
                    visibility=CognitionEvidenceVisibility(visibility),
                    knowledge_owner_id=owner,
                    record_id=record_ids[index],
                    concise_authoritative_fact=(fact if is_exact_evidence else None),
                )
            )
        return tuple(output)

    @staticmethod
    def _semantic_data(
        tool_name: str,
        raw: object,
        bindings: Sequence[RequestEvidenceBindingV1],
        *,
        cognition_mode: bool,
        evidence_refs: frozenset[str],
    ) -> object:
        if tool_name == "get_turn_context" and isinstance(raw, Mapping):
            private_refs = {
                value.knowledge_owner_id: value.binding_key
                for value in bindings
                if value.knowledge_owner_id is not None
            }

            def dossier_with_evidence_ref(value: Mapping[str, Any]) -> dict[str, object]:
                character_id = value.get("character_id")
                evidence_ref = (
                    private_refs.get(character_id)
                    if isinstance(character_id, str)
                    else None
                )
                return {
                    **_semantic_dossier(value),
                    (
                        "context_ref" if cognition_mode else "evidence_ref"
                    ): evidence_ref,
                }

            return {
                "scene_id": raw.get("scene_id"),
                "character_dossiers": [
                    dossier_with_evidence_ref(value)
                    for value in raw.get("character_dossiers", ())
                    if isinstance(value, Mapping)
                ],
                "omitted_character_ids": raw.get("omitted_character_ids", []),
            }
        if tool_name == "get_character_context" and isinstance(raw, Mapping):
            return {
                **_semantic_dossier(raw),
                (
                    "context_ref" if cognition_mode else "evidence_ref"
                ): bindings[0].binding_key,
            }
        if tool_name == "search_evidence" and isinstance(raw, Mapping):
            records = tuple(
                value for value in raw.get("records", ()) if isinstance(value, Mapping)
            )
            return {
                "terms": raw.get("terms", []),
                "character_scope": raw.get("character_scope"),
                "records": [
                    {
                        **{
                            key: value
                            for key, value in row.items()
                            if key not in {"path", "content_sha256", "dossier_sha256"}
                        },
                        (
                            "context_ref" if cognition_mode else "evidence_ref"
                        ): binding.binding_key,
                    }
                    for row, binding in zip(records, bindings, strict=True)
                ],
                "truncated": bool(raw.get("truncated", False)),
            }
        if tool_name == "get_exact_record" and isinstance(raw, Mapping):
            result = {
                "record_id": raw.get("record_id"),
                "record_type": raw.get("record_type"),
                "record": raw.get("record"),
            }
            if cognition_mode:
                _visibility, _owner, eligibility, _fact = (
                    cognition_exact_record_projection(raw)
                )
                result["citation_eligibility"] = eligibility
                result[
                    "evidence_ref"
                    if bindings[0].binding_key in evidence_refs
                    else "context_ref"
                ] = bindings[0].binding_key
            else:
                result["evidence_ref"] = bindings[0].binding_key
            return result
        return raw


def current_dossier_accepted_head(service: BranchRetrievalService) -> str | None:
    """Capture the exact accepted checkpoint before a request bridge starts."""

    index = read_json_object(
        service.workspace.branch_root
        / "DERIVED"
        / "CurrentCharacterDossiers"
        / "INDEX.json",
        "named retrieval dossier index",
    )
    if (
        index.get("schema_version") != DOSSIER_INDEX_SCHEMA
        or index.get("world_id") != service.workspace.world_id
        or index.get("branch_id") != service.workspace.branch_id
    ):
        raise StateConflictError("named retrieval dossier index changed branch scope")
    head = index.get("accepted_head_sha256")
    if head is not None and not isinstance(head, str):
        raise StateConflictError("named retrieval accepted head changed shape")
    return head


def _character_argument(arguments: Mapping[str, Any]) -> str:
    value = arguments.get("character_id")
    if not isinstance(value, str):
        raise ContractValidationError("character-context identity is invalid")
    return value


def _unique_paths(paths: Sequence[Path]) -> tuple[Path, ...]:
    output: list[Path] = []
    seen: set[Path] = set()
    for value in paths:
        resolved = value.resolve()
        if resolved not in seen:
            seen.add(resolved)
            output.append(resolved)
    return tuple(output)


def _semantic_dossier(value: Mapping[str, Any]) -> dict[str, Any]:
    custody = {
        "schema_version",
        "_cera_revision",
        "world_id",
        "branch_id",
        "accepted_head_sha256",
        "accepted_state_checkpoint_sha256",
        "visibility",
        "knowledge_owner_id",
        "dossier_sha256",
    }
    return {key: item for key, item in value.items() if key not in custody}


def _source_visibility(
    relative: str, payload: object
) -> tuple[EvidenceVisibility, str | None]:
    raw_visibility = "public"
    owner: object = None
    if isinstance(payload, Mapping):
        raw_visibility = str(payload.get("visibility", "public")).casefold()
        owner = payload.get("knowledge_owner_id") or payload.get("character_id")
        record = payload.get("record")
        if isinstance(record, Mapping):
            raw_visibility = str(
                payload.get("visibility", record.get("visibility", raw_visibility))
            ).casefold()
            owner = owner or record.get("owner_id")
            owners = record.get("knowledge_owner_ids")
            if owner is None and isinstance(owners, list) and len(owners) == 1:
                owner = owners[0]
    if raw_visibility in {"creator_private", "creator-only"}:
        return EvidenceVisibility.CREATOR_PRIVATE, str(owner) if owner else None
    if raw_visibility in {
        "private",
        "character_private",
        "owner_private",
    } or _record_type(relative) == "current_character_dossiers":
        if not isinstance(owner, str) or not owner.startswith("character:"):
            raise StateConflictError("character-private source lacks one knowledge owner")
        return EvidenceVisibility.CHARACTER_PRIVATE, owner
    return EvidenceVisibility.PUBLIC, None


def _record_type(relative: str) -> str:
    parts = relative.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] in {"ACTIVE", "DERIVED"}:
        return parts[1].casefold()
    return parts[0].casefold()
