"""Compact creator-visible projections of model-authored decision records.

These projections expose the explicit audit contract CERA asks providers to
author.  They never expose hidden reasoning, provider conversation state, or
unstructured chain-of-thought.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast

from cera.cognition.contracts import CognitionPlanV1
from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.serialization import to_primitive


def cognition_creator_trace(primary_authority_json: str) -> dict[str, Any]:
    """Decode one canonical cognition plan into its creator audit surface."""

    try:
        raw = json.loads(primary_authority_json)
    except json.JSONDecodeError as exc:
        raise ContractValidationError("creator trace authority is not valid JSON") from exc
    if not isinstance(raw, Mapping):
        raise ContractValidationError("creator trace authority must be an object")
    try:
        plan = cast(CognitionPlanV1, from_mapping(CognitionPlanV1, raw))
    except (ContractValidationError, TypeError, ValueError) as exc:
        raise ContractValidationError("creator trace cognition plan is invalid") from exc

    return {
        "schema_version": "cera.pi_scene.creator_cognition_trace.v1",
        "logic_owner": "codex_cognition",
        "decision_records": [to_primitive(value) for value in plan.decision_records],
        "decision_item_links": [to_primitive(value) for value in plan.decision_item_links],
        "autonomy_application": [
            {
                "decision_key": value.decision_key,
                "owner_id": value.owner_id,
                **to_primitive(value.autonomy_application),
            }
            for value in plan.decision_records
        ],
        "route_transition": (
            None if plan.route_transition is None else to_primitive(plan.route_transition)
        ),
        "provisional_dependencies": [
            to_primitive(value) for value in plan.provisional_dependencies
        ],
    }
