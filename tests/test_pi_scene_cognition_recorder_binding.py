from __future__ import annotations

import json
import unittest

from cera.errors import ContractValidationError
from cera.pi_scene.contracts import primary_item_keys


def _sequence() -> dict[str, object]:
    return {
        "schema_version": "cera.sequence_first.sequence_draft.v5",
        "items": [
            {
                "item_key": "hana_answers",
                "owner_id": "character:hana_hanezawa",
                "kind": "dialogue",
                "summary": "Hana answers.",
                "planner_beat_keys": ["beat_one"],
                "durable_change_keys": [],
            }
        ],
        "durable_changes": [],
        "presence_changes": [],
        "resulting_public_state": "Hana has answered.",
        "unresolved_threads": [],
        "stopping_boundary": "Stop before Ted responds.",
    }


class CognitionRecorderBindingTests(unittest.TestCase):
    def test_production_cognition_wrapper_exposes_its_exact_nested_sequence_items(self) -> None:
        sequence = _sequence()
        sequence.pop("schema_version")
        authority = {
            "sequence": sequence,
            "decision_records": [],
            "decision_item_links": [],
            "provisional_dependencies": [],
            "route_transition": None,
        }
        self.assertEqual(
            primary_item_keys(json.dumps(authority)),
            ("hana_answers",),
        )

    def test_cognition_wrapper_exposes_its_exact_nested_sequence_items(self) -> None:
        authority = {
            "schema_version": "cera.cognition.plan.v1",
            "sequence": _sequence(),
            "decision_records": [],
            "decision_item_links": [],
            "provisional_dependencies": [],
            "route_transition": None,
        }
        self.assertEqual(
            primary_item_keys(json.dumps(authority)),
            ("hana_answers",),
        )

    def test_unrecognized_nested_alias_fails_closed(self) -> None:
        authority = {
            "schema_version": "cera.cognition.plan.v1",
            "sequence": _sequence(),
            "decision_records": [],
            "decision_item_links": [],
            "provisional_dependencies": [],
            "route_transition": None,
            "items": _sequence()["items"],
        }
        with self.assertRaisesRegex(ContractValidationError, "shape changed"):
            primary_item_keys(json.dumps(authority))

    def test_production_wrapper_rejects_a_noncanonical_nested_sequence(self) -> None:
        sequence = _sequence()
        sequence.pop("schema_version")
        sequence["unexpected"] = []
        authority = {
            "sequence": sequence,
            "decision_records": [],
            "decision_item_links": [],
            "provisional_dependencies": [],
            "route_transition": None,
        }
        with self.assertRaisesRegex(ContractValidationError, "shape changed"):
            primary_item_keys(json.dumps(authority))


if __name__ == "__main__":
    unittest.main()
