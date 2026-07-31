"""Adapter from the existing AdultCraftSelector result to prompt craft items."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cera.adult_craft.models import AdultCraftAssetClass, AdultCraftMode
from cera.contracts import AdultRenderingMode
from cera.errors import ContractValidationError

from .models import PromptCraftItem

if TYPE_CHECKING:
    from cera.adult_craft.selector import AdultCraftSelectionResult


def prompt_craft_items_from_selection(
    selection: AdultCraftSelectionResult,
) -> tuple[PromptCraftItem, ...]:
    """Preserve existing selector order, hashes, provenance, and variable count."""

    selected_mode = selection.receipt.selected_modes
    if len(selected_mode) != 1:
        raise ContractValidationError("adult craft selection must bind exactly one mode")
    fragment_ids = tuple(value.fragment_id for value in selection.fragments)
    fragment_hashes = tuple(value.fragment_sha256 for value in selection.fragments)
    if fragment_ids != selection.receipt.selected_fragment_ids:
        raise ContractValidationError("adult craft selection order differs from receipt")
    if fragment_hashes != selection.receipt.selected_fragment_sha256:
        raise ContractValidationError("adult craft fragment hashes differ from receipt")
    rendering_mode = {
        AdultCraftMode.ON: AdultRenderingMode.ON,
        AdultCraftMode.EX: AdultRenderingMode.EX,
    }[selected_mode[0]]
    covered = set(selection.receipt.covered_requirements)
    items = []
    for order, fragment in enumerate(selection.fragments):
        declarations = _coverage_declarations(fragment)
        reasons = sorted(covered.intersection(declarations))
        reason = "coverage:" + (",".join(reasons) if reasons else "selector_ordered_support")
        source = fragment.source
        provenance = (
            f"adult/catalog/{fragment.catalog_version}|"
            f"{source.source_relative_path}|{'/'.join(source.heading_path)}|"
            f"lines:{source.line_start}-{source.line_end}|source_sha256:{source.source_sha256}"
        )
        items.append(
            PromptCraftItem(
                fragment_id=str(fragment.fragment_id),
                version=fragment.catalog_version,
                fragment_sha256=fragment.fragment_sha256,
                allowed_adult_rendering_modes=(rendering_mode,),
                deterministic_order=order,
                text=fragment.craft_text,
                source_provenance=provenance,
                selection_reason=reason,
                noncanonical=True,
                noncopyable=True,
                is_example=fragment.asset_class is AdultCraftAssetClass.MICRO_EXAMPLE,
            )
        )
    return tuple(items)


def _coverage_declarations(fragment) -> set[str]:
    result = {f"family:{value.value}" for value in fragment.families}
    result.update(f"axis:{value.value}" for value in fragment.axes)
    result.update(f"channel:{value.value}" for value in fragment.channels)
    result.update(f"concept:{value.value}" for value in fragment.coverage_concepts)
    result.update(f"concept:{value.concept.value}" for value in fragment.vocabulary_groups)
    result.update(f"lexicon:{value.concept.value}" for value in fragment.vocabulary_groups)
    return result
