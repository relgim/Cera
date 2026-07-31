from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from pathlib import Path
import unittest

from cera.config import Environment, FoundationConfig
from cera.errors import CanonicalizationError, ConfigurationError, ContractValidationError, IdentityError
from cera.ids import IdKind, TypedId, deterministic_id, new_id, require_kind
from cera.registry import build_schema_registry
from cera.schema import SchemaRegistry, from_mapping
from cera.serialization import canonical_json, canonical_sha256, domain_sha256, text_sha256, to_primitive
from cera.contracts import SourceUnit, SourceUnitClassification, TurnRequest

from tests.support import HASH_A, HASH_B, tid


class FoundationTests(unittest.TestCase):
    def test_typed_id_round_trip_and_kind(self) -> None:
        value = new_id(IdKind.REQUEST)
        self.assertEqual(TypedId.parse(str(value), IdKind.REQUEST), value)
        with self.assertRaises(IdentityError):
            TypedId.parse(str(value), IdKind.BRANCH)
        with self.assertRaises(IdentityError):
            TypedId.parse("request:not valid")
        with self.assertRaises(IdentityError):
            require_kind(value, IdKind.BRANCH, "branch_id")

    def test_deterministic_id_is_stable_and_namespaced(self) -> None:
        first = deterministic_id(IdKind.EVENT, "source", "42")
        second = deterministic_id(IdKind.EVENT, "source", "42")
        other = deterministic_id(IdKind.MEMORY, "source", "42")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)

    def test_canonical_json_is_stable_and_strict(self) -> None:
        left = {"z": [2, 1], "a": {"id": tid(IdKind.BRANCH)}}
        right = {"a": {"id": tid(IdKind.BRANCH)}, "z": (2, 1)}
        self.assertEqual(canonical_json(left), canonical_json(right))
        self.assertEqual(canonical_sha256(left), canonical_sha256(right))
        self.assertNotEqual(domain_sha256("artifact", left), domain_sha256("source", left))
        self.assertEqual(text_sha256("é"), text_sha256("é"))
        with self.assertRaises(CanonicalizationError):
            canonical_json({"bad": math.inf})
        with self.assertRaises(CanonicalizationError):
            canonical_json({1: "non-string key"})
        with self.assertRaises(CanonicalizationError):
            to_primitive({"unsupported": {"a", "set"}})

    def test_strict_nested_schema_decode(self) -> None:
        payload = {
            "schema_version": TurnRequest.SCHEMA_VERSION,
            "world_id": str(tid(IdKind.WORLD)),
            "request_id": str(tid(IdKind.REQUEST)),
            "session_id": str(tid(IdKind.SESSION)),
            "branch_id": str(tid(IdKind.BRANCH)),
            "parent_artifact_id": None,
            "generation_id": str(tid(IdKind.GENERATION)),
            "snapshot_token": str(tid(IdKind.SNAPSHOT)),
            "genesis_revision_id": str(tid(IdKind.GENESIS_REVISION)),
            "protected_user_id": "character:ted",
            "raw_source_ref": str(tid(IdKind.PROTECTED_SOURCE)),
            "source_sha256": HASH_A,
            "source_units": [
                {
                    "source_unit_id": "source_unit:S01",
                    "classification": "message",
                    "text_ref": "protected_source_segment:S01",
                    "sha256": HASH_B,
                }
            ],
            "requested_route_hints": [],
            "idempotency_key": "turn-1",
        }
        request = from_mapping(TurnRequest, payload)
        self.assertIsInstance(request.source_units[0], SourceUnit)
        self.assertIs(request.source_units[0].classification, SourceUnitClassification.MESSAGE)
        with self.assertRaises(ContractValidationError):
            from_mapping(TurnRequest, {**payload, "unknown": True})
        with self.assertRaises(ContractValidationError):
            from_mapping(TurnRequest, {**payload, "request_id": "bad-id"})
        with self.assertRaises(ContractValidationError):
            from_mapping(TurnRequest, {key: value for key, value in payload.items() if key != "branch_id"})

    def test_config_is_phase_safe(self) -> None:
        config = FoundationConfig(
            schema_version=FoundationConfig.SCHEMA_VERSION,
            environment=Environment.TEST,
            project_root=str(Path("D:/AIChatBot/Cera")),
        )
        self.assertFalse(config.live_provider_calls_enabled)
        with self.assertRaises(ConfigurationError):
            FoundationConfig(
                schema_version=FoundationConfig.SCHEMA_VERSION,
                environment=Environment.TEST,
                project_root=str(Path("D:/AIChatBot/Cera")),
                live_provider_calls_enabled=True,
            )

    def test_default_registry_is_versioned_and_closed(self) -> None:
        registry = build_schema_registry()
        self.assertIn(TurnRequest.SCHEMA_VERSION, registry.versions)
        with self.assertRaises(ContractValidationError):
            registry.decode({"schema_version": "cera.unknown.v1"})
        duplicate = SchemaRegistry()
        duplicate.register(TurnRequest)
        with self.assertRaises(ContractValidationError):
            duplicate.register(TurnRequest)


if __name__ == "__main__":
    unittest.main()
