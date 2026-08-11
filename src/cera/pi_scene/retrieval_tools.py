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

from cera.continuous.evidence import (
    EvidenceVisibility,
    RequestEvidenceBindingRegistry,
    RequestEvidenceBindingV1,
)
from cera.errors import ContractValidationError, ProviderToolRequestError, StateConflictError
from cera.serialization import canonical_sha256, domain_sha256, text_sha256

from ._world_workspace_files import read_json_object
from .retrieval import (
    DOSSIER_INDEX_SCHEMA,
    MAX_RETRIEVAL_CALLS,
    MAX_SEARCH_RESULTS,
    BranchRetrievalService,
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


class RetrievalProviderRole(StrEnum):
    PLANNER = "planner"
    VALIDATOR = "validator"
    ADULT_SCENE = "adult_scene"
    ADULT_FILTER = "adult_filter"
    RECORDER = "recorder"


_ROLE_TOOLS: dict[RetrievalProviderRole, frozenset[str]] = {
    RetrievalProviderRole.PLANNER: frozenset(NAMED_RETRIEVAL_TOOLS),
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
        self._advertised_exact_record_ids: set[str] = set()

    @property
    def binding_sha256(self) -> str:
        return self.binding.binding_sha256

    @property
    def returned_bytes(self) -> int:
        return self._returned_bytes

    def invoke(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool_name not in self.tool_names or not isinstance(arguments, dict):
            raise ContractValidationError("named retrieval request is invalid")
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
        self._authorize_exact_result(tool_name, raw)
        source_paths = self._source_paths(tool_name, arguments, raw)
        bindings = tuple(self._allocate_source(path) for path in source_paths)
        result: dict[str, Any] = {
            "schema_version": "cera.pi_scene.named_retrieval_result.v1",
            "tool": tool_name,
            "data": self._semantic_data(tool_name, raw, bindings),
            "evidence_refs": [value.binding_key for value in bindings],
            "source_revisions": {
                value.binding_key: value.record_revision for value in bindings
            },
        }
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
            self._advertised_exact_record_ids.update(
                str(value["record_id"])
                for value in raw.get("records", ())
                if isinstance(value, Mapping)
                and isinstance(value.get("record_id"), str)
            )
        return result

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
            return self.service.search_evidence(
                terms,
                character_id=character_id,
                limit=limit,
            )
        if tool_name == "get_exact_record":
            record_id = arguments.get("record_id")
            if not isinstance(record_id, str):
                raise ContractValidationError("exact-record identity is invalid")
            if (
                "search_evidence" in _ROLE_TOOLS[self.binding.role]
                and record_id not in self._advertised_exact_record_ids
            ):
                raise ProviderToolRequestError(
                    "exact record was not advertised by a prior successful search"
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

    @staticmethod
    def _semantic_data(
        tool_name: str,
        raw: object,
        bindings: Sequence[RequestEvidenceBindingV1],
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
                    "evidence_ref": evidence_ref,
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
                "evidence_ref": bindings[0].binding_key,
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
                        "evidence_ref": binding.binding_key,
                    }
                    for row, binding in zip(records, bindings, strict=True)
                ],
                "truncated": bool(raw.get("truncated", False)),
            }
        if tool_name == "get_exact_record" and isinstance(raw, Mapping):
            return {
                "record_id": raw.get("record_id"),
                "record_type": raw.get("record_type"),
                "record": raw.get("record"),
                "evidence_ref": bindings[0].binding_key,
            }
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
