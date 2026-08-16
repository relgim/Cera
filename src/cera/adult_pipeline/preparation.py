"""Privacy-safe construction of protected adult-turn preparations.

The builder has two explicit entry points.  An ordinary Cognition plan may
hand off once at its declared adult boundary, while an already-accepted adult
branch continues without another Codex operation.  Adult craft retrieval is a
bounded presentation aid; it never participates in either route decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cera.adult_craft.catalog import AdultCraftCatalog
from cera.cognition.contracts import CognitionPlanV1, LogicRoute
from cera.errors import ContractValidationError
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV2
from cera.pi_scene.review_store import LeanSceneTurnInputV1
from cera.sequence_first.contracts import ItemKind
from cera.serialization import canonical_json, text_sha256, to_primitive

from .contracts import (
    AdultContextFactV1,
    AdultCraftMode,
    AdultEntryReason,
)
from .craft_catalog import CatalogAdultCraftRetrieval
from .integration import AdultScenePreparationV1

_TOKEN = re.compile(r"[a-z0-9]+")
_MAX_QUERY_KEYS = 16
_MAX_HANDOFF_BYTES = 20_000
_MAX_SAFE_CONTINUITY_BYTES = 40_000


@dataclass(frozen=True, slots=True)
class _IndexedQueryKey:
    key: str
    aliases: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class _CraftQueryIndex:
    concepts: tuple[_IndexedQueryKey, ...]
    keywords: tuple[_IndexedQueryKey, ...]

    @classmethod
    def from_catalog(cls, catalog: AdultCraftCatalog) -> _CraftQueryIndex:
        concept_aliases: dict[str, set[tuple[str, ...]]] = {}
        keyword_aliases: dict[str, set[tuple[str, ...]]] = {}

        for fragment in catalog.fragments:
            concept_values = {
                *(value.value for value in fragment.coverage_concepts),
                *(value.value for value in fragment.families),
                *(value.value for value in fragment.axes),
                *(value.value for value in fragment.channels),
                *(value.concept.value for value in fragment.vocabulary_groups),
            }
            for value in concept_values:
                key = _normalize_key(value)
                if not key:
                    continue
                aliases = concept_aliases.setdefault(key, set())
                _add_key_aliases(aliases, key)

            for group in fragment.vocabulary_groups:
                concept_key = _normalize_key(group.concept.value)
                concept_bucket = concept_aliases.setdefault(concept_key, set())
                for term in group.terms:
                    keyword_key = _normalize_key(term)
                    tokens = _token_tuple(term)
                    if not keyword_key or not tokens:
                        continue
                    concept_bucket.add(tokens)
                    keyword_aliases.setdefault(keyword_key, set()).add(tokens)

        return cls(
            concepts=_freeze_index(concept_aliases),
            keywords=_freeze_index(keyword_aliases),
        )

    def extract(self, text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
        tokens = _token_tuple(text)
        return (
            _matched_keys(self.concepts, tokens),
            _matched_keys(self.keywords, tokens),
        )


class AdultTurnPreparationBuilder:
    """Build one adult request input without choosing the story route."""

    def __init__(self, *, craft_catalog: AdultCraftCatalog) -> None:
        self._craft_index = _CraftQueryIndex.from_catalog(craft_catalog)

    @classmethod
    def from_catalog_root(cls, catalog_root: Path) -> AdultTurnPreparationBuilder:
        """Load and verify the immutable local catalog used for query keys."""

        return cls(craft_catalog=AdultCraftCatalog.load(catalog_root))

    @classmethod
    def from_craft_retrieval(
        cls,
        craft_retrieval: CatalogAdultCraftRetrieval,
    ) -> AdultTurnPreparationBuilder:
        """Reuse the exact catalog already owned by the adult integration."""

        return cls(craft_catalog=craft_retrieval.catalog)

    def from_cognition_handoff(
        self,
        *,
        turn_input: LeanSceneTurnInputV1,
        cognition_plan: CognitionPlanV1,
        accepted_safe_projection: str,
        protected_adult_continuity: str | None,
        current_facts: tuple[AdultContextFactV1, ...],
        product_story_boundaries: tuple[str, ...],
    ) -> AdultScenePreparationV1:
        """Preserve one exact non-graphic Codex plan through its adult boundary."""

        transition = cognition_plan.route_transition
        if transition is None:
            raise ContractValidationError("adult Cognition handoff requires a route transition")
        if (
            transition.from_route is not LogicRoute.ORDINARY
            or transition.to_route is not LogicRoute.ADULT
        ):
            raise ContractValidationError(
                "adult Cognition handoff must transition from ordinary to adult"
            )
        sequence_items = cognition_plan.sequence.items
        boundary_index = next(
            index
            for index, item in enumerate(sequence_items)
            if item.item_key == transition.boundary_item_key
        )
        trailing_items = sequence_items[boundary_index + 1 :]
        terminal_stop_marker = (
            len(trailing_items) == 1
            and trailing_items[0].kind is ItemKind.STOPPING_BOUNDARY
            and trailing_items[0].causal_parent_item_key == transition.boundary_item_key
            and trailing_items[0].owner_id is None
            and trailing_items[0].owner_response_semantics is None
            and not trailing_items[0].evidence_keys
            and not trailing_items[0].protected_user_claim_keys
            and not trailing_items[0].protected_user_exact_quotes
            and not trailing_items[0].durable_change_keys
            and not trailing_items[0].planner_item_keys
        )
        if trailing_items and not terminal_stop_marker:
            raise ContractValidationError(
                "adult Cognition handoff contains sequence items after its route boundary"
            )

        # This is the complete provider-authored plan, including the ordered
        # sequence, every material decision effect, and the boundary proposal.
        # It is neither summarized nor reconstructed by Python.
        exact_handoff = canonical_json(cognition_plan)
        if len(exact_handoff) > _MAX_HANDOFF_BYTES:
            raise ContractValidationError("exact adult Cognition handoff exceeds its budget")

        self._validate_common(
            accepted_safe_projection=accepted_safe_projection,
            protected_adult_continuity=protected_adult_continuity,
            adult_handoff=exact_handoff,
            current_facts=current_facts,
            product_story_boundaries=product_story_boundaries,
        )
        craft_mode = _craft_mode(turn_input)
        concept_keys, keyword_keys = self._craft_keys(
            craft_mode,
            query_text=_query_text(turn_input.exact_user_source, cognition_plan),
        )
        return _preparation(
            turn_input=turn_input,
            entry_reason=AdultEntryReason.CODEX_ADULT_HANDOFF,
            adult_handoff=exact_handoff,
            accepted_safe_projection=accepted_safe_projection,
            protected_adult_continuity=protected_adult_continuity,
            current_facts=current_facts,
            craft_mode=craft_mode,
            concept_keys=concept_keys,
            keyword_keys=keyword_keys,
            product_story_boundaries=product_story_boundaries,
        )

    def from_accepted_adult_continuation(
        self,
        *,
        turn_input: LeanSceneTurnInputV1,
        accepted_safe_projection: str,
        protected_adult_continuity: str,
        current_facts: tuple[AdultContextFactV1, ...],
        product_story_boundaries: tuple[str, ...],
    ) -> AdultScenePreparationV1:
        """Continue an accepted adult branch without invoking ordinary Codex."""

        self._validate_common(
            accepted_safe_projection=accepted_safe_projection,
            protected_adult_continuity=protected_adult_continuity,
            adult_handoff=None,
            current_facts=current_facts,
            product_story_boundaries=product_story_boundaries,
        )
        craft_mode = _craft_mode(turn_input)
        concept_keys, keyword_keys = self._craft_keys(
            craft_mode,
            # The safe accepted projection can guide continuity retrieval.  The
            # exact protected continuation is deliberately absent here.
            query_text=f"{turn_input.exact_user_source}\n{accepted_safe_projection}",
        )
        return _preparation(
            turn_input=turn_input,
            entry_reason=AdultEntryReason.ACCEPTED_ADULT_CONTINUATION,
            adult_handoff=None,
            accepted_safe_projection=accepted_safe_projection,
            protected_adult_continuity=protected_adult_continuity,
            current_facts=current_facts,
            craft_mode=craft_mode,
            concept_keys=concept_keys,
            keyword_keys=keyword_keys,
            product_story_boundaries=product_story_boundaries,
        )

    def _craft_keys(
        self,
        mode: AdultCraftMode,
        *,
        query_text: str,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if mode is AdultCraftMode.OFF:
            return (), ()
        return self._craft_index.extract(query_text)

    @staticmethod
    def _validate_common(
        *,
        accepted_safe_projection: str,
        protected_adult_continuity: str | None,
        adult_handoff: str | None,
        current_facts: tuple[AdultContextFactV1, ...],
        product_story_boundaries: tuple[str, ...],
    ) -> None:
        _bounded_text(
            accepted_safe_projection,
            "accepted adult safe projection",
            maximum=_MAX_SAFE_CONTINUITY_BYTES,
        )
        if protected_adult_continuity is not None:
            _bounded_text(
                protected_adult_continuity,
                "protected adult continuity",
                maximum=100_000,
            )
        if type(current_facts) is not tuple:
            raise ContractValidationError("adult current facts must be an immutable tuple")
        refs = tuple(value.evidence_ref for value in current_facts)
        if len(refs) != len(set(refs)):
            raise ContractValidationError("adult current facts contain duplicate evidence refs")
        for fact in current_facts:
            if fact.visibility == "adult_role_private" and not fact.subject_id.startswith(
                "character:"
            ):
                raise ContractValidationError(
                    "adult-role private facts must preserve their character owner"
                )
        if type(product_story_boundaries) is not tuple:
            raise ContractValidationError("adult product/story boundaries must be a tuple")
        for boundary in product_story_boundaries:
            _bounded_text(boundary, "adult product/story boundary", maximum=2_000)
        if len(product_story_boundaries) != len(set(product_story_boundaries)):
            raise ContractValidationError("adult product/story boundaries contain duplicates")

        if protected_adult_continuity is None:
            return
        safe_values = (
            accepted_safe_projection,
            *(value.authoritative_fact for value in current_facts),
            *product_story_boundaries,
            *((adult_handoff,) if adult_handoff is not None else ()),
        )
        if any(protected_adult_continuity in value for value in safe_values):
            raise ContractValidationError(
                "exact protected adult continuity escaped its dedicated field"
            )


def build_adult_turn_preparation_builder(
    craft_retrieval: CatalogAdultCraftRetrieval,
) -> AdultTurnPreparationBuilder:
    """Narrow runtime factory sharing the integration's verified catalog."""

    return AdultTurnPreparationBuilder.from_craft_retrieval(craft_retrieval)


def _preparation(
    *,
    turn_input: LeanSceneTurnInputV1,
    entry_reason: AdultEntryReason,
    adult_handoff: str | None,
    accepted_safe_projection: str,
    protected_adult_continuity: str | None,
    current_facts: tuple[AdultContextFactV1, ...],
    craft_mode: AdultCraftMode,
    concept_keys: tuple[str, ...],
    keyword_keys: tuple[str, ...],
    product_story_boundaries: tuple[str, ...],
) -> AdultScenePreparationV1:
    autonomy_mode, depth_mode = _semantic_controls(turn_input)
    current_source_fact = AdultContextFactV1(
        evidence_ref="source:current",
        subject_id="source:current",
        authoritative_fact=(
            "The exact current source is available in exact_current_source and is bound by "
            f"SHA-256 {text_sha256(turn_input.exact_user_source)}."
        ),
        visibility="adult_role_private",
    )
    return AdultScenePreparationV1(
        entry_reason=entry_reason,
        adult_handoff=adult_handoff,
        exact_current_source=turn_input.exact_user_source,
        accepted_safe_continuity=accepted_safe_projection,
        accepted_protected_continuity=protected_adult_continuity,
        autonomy_mode=autonomy_mode,
        depth_mode=depth_mode,
        current_context=(*current_facts, current_source_fact),
        craft_mode=craft_mode,
        craft_concept_keys=concept_keys,
        craft_keyword_keys=keyword_keys,
        # These are copied exactly.  Python adds no provider policy or model
        # safety boundary to the creator-supplied product/story boundaries.
        hard_boundaries=product_story_boundaries,
    )


def _semantic_controls(turn_input: LeanSceneTurnInputV1) -> tuple[str, str]:
    controls = turn_input.request_controls
    if controls is None:
        return "both", "auto"
    return controls.character_autonomy, controls.scene_depth


def _craft_mode(turn_input: LeanSceneTurnInputV1) -> AdultCraftMode:
    controls = turn_input.request_controls
    if not isinstance(controls, LeanSceneRequestControlsV2):
        return AdultCraftMode.OFF
    return AdultCraftMode(controls.adult_craft_mode)


def _query_text(source: str, plan: CognitionPlanV1) -> str:
    strings = _primitive_strings(to_primitive(plan))
    return "\n".join((source, *strings))


def _primitive_strings(value: object) -> tuple[str, ...]:
    found: list[str] = []

    def visit(item: object) -> None:
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, list):
            for child in item:
                visit(child)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)

    visit(value)
    return tuple(found)


def _freeze_index(values: dict[str, set[tuple[str, ...]]]) -> tuple[_IndexedQueryKey, ...]:
    return tuple(
        _IndexedQueryKey(key=key, aliases=tuple(sorted(aliases)))
        for key, aliases in sorted(values.items())
        if key and aliases and len(key) <= 96
    )


def _add_key_aliases(aliases: set[tuple[str, ...]], key: str) -> None:
    components = tuple(value for value in key.split("_") if value)
    if components:
        aliases.add(components)
    for component in components:
        if len(component) >= 4 and component != "general":
            aliases.add((component,))


def _matched_keys(
    index: tuple[_IndexedQueryKey, ...],
    tokens: tuple[str, ...],
) -> tuple[str, ...]:
    matches: list[tuple[int, str]] = []
    for value in index:
        positions = (
            position
            for alias in value.aliases
            if (position := _phrase_position(tokens, alias)) is not None
        )
        first = min(positions, default=None)
        if first is not None:
            matches.append((first, value.key))
    matches.sort(key=lambda value: (value[0], value[1]))
    return tuple(key for _, key in matches[:_MAX_QUERY_KEYS])


def _phrase_position(
    tokens: tuple[str, ...],
    phrase: tuple[str, ...],
) -> int | None:
    if not phrase or len(phrase) > len(tokens):
        return None
    width = len(phrase)
    for index in range(len(tokens) - width + 1):
        if tokens[index : index + width] == phrase:
            return index
    return None


def _normalize_key(value: str) -> str:
    return "_".join(_token_tuple(value))


def _token_tuple(value: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(value.casefold()))


def _bounded_text(value: str, field: str, *, maximum: int) -> None:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ContractValidationError(f"{field} must be non-empty and bounded")
