"""Bounded OFF/ON/EX retrieval over the repository adult-craft catalog.

This adapter is deliberately mechanical.  The caller supplies normalized
concept and keyword keys; the adapter ranks immutable craft fragments and
fills the mode's craft-axis coverage.  It cannot select or change the story
route and it never reads outside the verified catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from cera.adult_craft.catalog import AdultCraftCatalog
from cera.adult_craft.models import AdultCraftFragment
from cera.errors import ContractValidationError
from cera.serialization import canonical_sha256

from .contracts import (
    AdultCraftExcerptV1,
    AdultCraftMode,
    AdultCraftQueryV1,
    AdultCraftSelectionV1,
)

_CATALOG_MODE = {
    AdultCraftMode.ON: "adult_on",
    AdultCraftMode.EX: "adult_ex",
}
_REQUIRED_AXES = {
    AdultCraftMode.ON: frozenset({"direct_vocabulary", "clarity"}),
    AdultCraftMode.EX: frozenset(
        {
            "direct_vocabulary",
            "clarity",
            "buildup",
            "physiology",
            "continuity",
            "sound",
            "climax",
            "aftermath",
        }
    ),
}
_AXIS_PROJECTION = {
    "direct_vocabulary": frozenset({"direct_vocabulary", "clarity"}),
    "realism_agency": frozenset({"clarity"}),
    "buildup": frozenset({"buildup"}),
    "causal_physiology": frozenset({"physiology"}),
    "material_continuity": frozenset({"continuity"}),
    "action_bound_sound": frozenset({"sound"}),
    "vocalization": frozenset({"sound"}),
    "climax": frozenset({"climax"}),
    "aftermath": frozenset({"aftermath"}),
}
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class _IndexedFragment:
    fragment: AdultCraftFragment
    keys: frozenset[str]
    projected_axes: frozenset[str]
    size_bytes: int


class CatalogAdultCraftRetrieval:
    """Verify once, then return deterministic, bounded craft selections."""

    external_provider_boundary = False

    def __init__(
        self,
        catalog_root: Path,
        *,
        maximum_excerpts: int = 12,
        maximum_total_bytes: int = 32_768,
    ) -> None:
        if type(maximum_excerpts) is not int or not 1 <= maximum_excerpts <= 12:
            raise ContractValidationError("adult craft excerpt budget must be 1..12")
        if type(maximum_total_bytes) is not int or not 1_024 <= maximum_total_bytes <= 131_072:
            raise ContractValidationError("adult craft byte budget must be 1024..131072")
        self.catalog = AdultCraftCatalog.load(catalog_root)
        self.maximum_excerpts = maximum_excerpts
        self.maximum_total_bytes = maximum_total_bytes
        self._indexed = tuple(_index(value) for value in self.catalog.fragments)

    def retrieve_adult_craft(self, query: AdultCraftQueryV1) -> AdultCraftSelectionV1:
        query_sha256 = canonical_sha256(query)
        if query.mode is AdultCraftMode.OFF:
            return AdultCraftSelectionV1(
                schema_version=AdultCraftSelectionV1.SCHEMA_VERSION,
                mode=query.mode,
                query_sha256=query_sha256,
                covered_axes=(),
                excerpts=(),
            )

        catalog_mode = _CATALOG_MODE[query.mode]
        requested = frozenset((*query.concept_keys, *query.keyword_keys))
        required_axes = _REQUIRED_AXES[query.mode]
        candidates = tuple(
            value
            for value in self._indexed
            if catalog_mode in {mode.value for mode in value.fragment.modes}
        )
        chosen: list[_IndexedFragment] = []
        covered: set[str] = set()
        used_bytes = 0

        # Query matches are preferred.  The coverage fill below is independent
        # so a narrow query cannot accidentally remove the basic mode contract.
        ranked_matches = sorted(
            (
                value
                for value in candidates
                if not requested or _match_score(value, requested)
            ),
            key=lambda value: (
                -_match_score(value, requested),
                -len(value.projected_axes.intersection(required_axes)),
                value.size_bytes,
                str(value.fragment.fragment_id),
            ),
        )
        for value in ranked_matches:
            if len(chosen) >= self.maximum_excerpts:
                break
            if used_bytes + value.size_bytes > self.maximum_total_bytes:
                continue
            # Retain only candidates that add an exact query match or coverage.
            if requested and not _match_score(value, requested):
                continue
            chosen.append(value)
            covered.update(value.projected_axes)
            used_bytes += value.size_bytes
            if _covered_requested(chosen, requested) == requested and required_axes.issubset(
                covered
            ):
                break

        while not required_axes.issubset(covered):
            uncovered = required_axes.difference(covered)
            options = [
                value
                for value in candidates
                if value not in chosen
                and value.projected_axes.intersection(uncovered)
                and used_bytes + value.size_bytes <= self.maximum_total_bytes
            ]
            if not options or len(chosen) >= self.maximum_excerpts:
                raise ContractValidationError(
                    "adult craft catalog cannot satisfy the selected mode within budget"
                )
            value = min(
                options,
                key=lambda item: (
                    -len(item.projected_axes.intersection(uncovered)),
                    -_match_score(item, requested),
                    item.size_bytes,
                    str(item.fragment.fragment_id),
                ),
            )
            chosen.append(value)
            covered.update(value.projected_axes)
            used_bytes += value.size_bytes

        excerpts = tuple(
            AdultCraftExcerptV1(
                craft_ref=str(value.fragment.fragment_id),
                concept_keys=_excerpt_concepts(value, requested),
                excerpt=value.fragment.craft_text,
            )
            for value in chosen
        )
        return AdultCraftSelectionV1(
            schema_version=AdultCraftSelectionV1.SCHEMA_VERSION,
            mode=query.mode,
            query_sha256=query_sha256,
            covered_axes=tuple(sorted(required_axes.intersection(covered))),
            excerpts=excerpts,
        )


def _index(fragment: AdultCraftFragment) -> _IndexedFragment:
    metadata = {
        *(value.value for value in fragment.axes),
        *(value.value for value in fragment.channels),
        *(value.value for value in fragment.coverage_concepts),
        *(value.value for value in fragment.families),
        *(value.concept.value for value in fragment.vocabulary_groups),
    }
    text_tokens = {value for value in _TOKEN.findall(fragment.craft_text.casefold()) if value}
    keys = frozenset(_normalize(value) for value in (*metadata, *text_tokens))
    axes: set[str] = set()
    for value in fragment.axes:
        axes.update(_AXIS_PROJECTION.get(value.value, ()))
    return _IndexedFragment(
        fragment=fragment,
        keys=keys,
        projected_axes=frozenset(axes),
        size_bytes=len(fragment.craft_text.encode("utf-8")),
    )


def _normalize(value: str) -> str:
    return "_".join(_TOKEN.findall(value.casefold()))


def _match_score(indexed: _IndexedFragment, requested: frozenset[str]) -> int:
    score = 0
    for value in requested:
        if value in indexed.keys:
            score += 4
            continue
        components = frozenset(value.split("_"))
        if components and components.issubset(indexed.keys):
            score += 2
    return score


def _covered_requested(
    chosen: list[_IndexedFragment],
    requested: frozenset[str],
) -> frozenset[str]:
    return frozenset(
        value
        for value in requested
        if any(_match_score(indexed, frozenset({value})) for indexed in chosen)
    )


def _excerpt_concepts(
    indexed: _IndexedFragment,
    requested: frozenset[str],
) -> tuple[str, ...]:
    concepts = {
        value.value for value in indexed.fragment.coverage_concepts
    }.union(indexed.keys.intersection(requested))
    if not concepts:
        concepts.update(indexed.projected_axes)
    return tuple(sorted(_normalize(value) for value in concepts if _normalize(value)))
