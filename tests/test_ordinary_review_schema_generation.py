from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from cera.generated.ordinary_review_contracts_v2 import (
    OrdinaryReviewContractError,
    normalize_ordinary_review_decision_v2,
    normalize_ordinary_review_v2,
    validate_ordinary_review_checks_v1,
    validate_ordinary_review_decision_v2,
    validate_ordinary_review_lifecycle_v1,
    validate_schema_version,
)
from cera.generated.provider_stage_retry_contracts_v1 import (
    validate_provider_stage_retry_status_envelope_v1,
)

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_ordinary_review_contracts.py"
PROVIDER_GENERATOR = ROOT / "scripts" / "generate_provider_stage_retry_contracts.py"
SCHEMA_ROOT = ROOT / "schemas" / "pi_scene" / "ordinary_review" / "v2"
POSITIVE_FIXTURES = ROOT / "tests" / "fixtures" / "generated" / "ordinary_review_v2_positive.json"
NEGATIVE_FIXTURES = ROOT / "tests" / "fixtures" / "generated" / "ordinary_review_v2_negative.json"
JAVASCRIPT_CONTRACTS = (
    ROOT / "integrations" / "sillytavern" / "generated" / "ordinary-review-contracts-v2.mjs"
)
STAGED_JAVASCRIPT_CONTRACTS = (
    ROOT
    / "integrations"
    / "sillytavern"
    / "creator-review-extension"
    / "generated"
    / "ordinary-review-contracts-v2.mjs",
    ROOT
    / "integrations"
    / "sillytavern"
    / "cera-review-proxy-plugin"
    / "generated"
    / "ordinary-review-contracts-v2.mjs",
)
DECISION_VERSION = "cera.pi_scene.review_decision.v2"
SUCCESSOR_VERSION = "cera.pi_scene.ordinary_successor_fixture.v1"


def _payload(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def _fixture_cases(path: Path) -> list[dict[str, object]]:
    cases = _payload(path)["cases"]
    assert isinstance(cases, list)
    return cast(list[dict[str, object]], cases)


def _case(path: Path, case_id: str) -> dict[str, object]:
    return next(case for case in _fixture_cases(path) if case["case_id"] == case_id)


def _successor_validator(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "completion_sha256",
    }:
        raise ValueError("successor is not the exact fixture completion")
    if value.get("schema_version") != SUCCESSOR_VERSION:
        raise ValueError("successor version changed")
    digest = value.get("completion_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("successor digest changed")
    if any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("successor digest is not lowercase hexadecimal")
    return copy.deepcopy(value)


def _external_validators(
    version: object,
) -> Mapping[str, Callable[[object], object]] | None:
    if version == DECISION_VERSION:
        return {"ordinary_successor": _successor_validator}
    return None


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class OrdinaryReviewSchemaGenerationTests(unittest.TestCase):
    def test_generated_artifacts_and_provider_engine_are_current(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
        for generator in (GENERATOR, PROVIDER_GENERATOR):
            with self.subTest(generator=generator.name):
                completed = subprocess.run(
                    [sys.executable, str(generator), "--check"],
                    cwd=ROOT,
                    env=environment,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )

    def test_python_projection_accepts_every_positive_fixture(self) -> None:
        for case in _fixture_cases(POSITIVE_FIXTURES):
            with self.subTest(case=case["case_id"]):
                value = case["value"]
                result = validate_schema_version(
                    str(case["contract_schema_version"]),
                    value,
                    external_validators=_external_validators(case["contract_schema_version"]),
                )
                self.assertEqual(result, value)
                self.assertIsNot(result, value)

    def test_python_projection_rejects_every_negative_fixture(self) -> None:
        for case in _fixture_cases(NEGATIVE_FIXTURES):
            with self.subTest(case=case["case_id"]):
                with self.assertRaises(OrdinaryReviewContractError):
                    validate_schema_version(
                        str(case["contract_schema_version"]),
                        case["value"],
                        external_validators=_external_validators(case["contract_schema_version"]),
                    )

    def test_public_roots_are_exact_and_ordinary_only(self) -> None:
        review = _payload(SCHEMA_ROOT / "review.schema.json")
        review_properties = cast(dict[str, object], review["properties"])
        self.assertEqual(len(review_properties), 21)
        self.assertEqual(set(cast(list[str], review["required"])), set(review_properties))
        self.assertEqual(
            cast(dict[str, object], review_properties["route"])["const"],
            "ordinary",
        )
        self.assertEqual(
            cast(dict[str, object], review_properties["primary_authority_kind"])["const"],
            "codex_cognition_plan",
        )
        self.assertTrue(
            cast(
                str,
                cast(dict[str, object], review_properties["request_controls"])["$ref"],
            ).endswith("#/$defs/request_controls_v3")
        )

        lifecycle = _payload(SCHEMA_ROOT / "review_lifecycle.schema.json")
        lifecycle_properties = cast(dict[str, object], lifecycle["properties"])
        self.assertEqual(len(lifecycle_properties), 10)
        self.assertEqual(
            set(cast(list[str], lifecycle["required"])),
            set(lifecycle_properties),
        )
        self.assertEqual(
            cast(dict[str, object], lifecycle_properties["state"])["const"],
            "checks_pending",
        )
        self.assertEqual(
            cast(dict[str, object], lifecycle_properties["terminal_decision"])["type"],
            "null",
        )

    def test_check_roles_and_full_provider_status_envelopes_are_closed(self) -> None:
        blocked_luna = cast(
            dict[str, object],
            copy.deepcopy(
                _case(
                    POSITIVE_FIXTURES,
                    "review_checks.schema.positive.luna_blocked_ambiguous",
                )["value"]
            ),
        )
        luna = cast(dict[str, object], blocked_luna["luna"])
        envelope = cast(dict[str, object], luna["provider_stage_retry_status"])
        status = cast(dict[str, object], envelope["status"])
        self.assertEqual(
            (status["stage"], status["provider"], status["model_family"]),
            ("semantic_validator", "codex", "luna"),
        )
        del envelope["actions"]
        with self.assertRaises(OrdinaryReviewContractError):
            validate_ordinary_review_checks_v1(blocked_luna)

        blocked_reader = cast(
            dict[str, object],
            copy.deepcopy(
                _case(
                    POSITIVE_FIXTURES,
                    "review_checks.schema.positive.reader_blocked_ambiguous",
                )["value"]
            ),
        )
        reader = cast(dict[str, object], blocked_reader["reader"])
        reader_envelope = cast(dict[str, object], reader["provider_stage_retry_status"])
        reader_status = cast(dict[str, object], reader_envelope["status"])
        self.assertEqual(
            (
                reader_status["stage"],
                reader_status["provider"],
                reader_status["model_family"],
            ),
            ("reader", "codex", "sol"),
        )
        python_lane = cast(dict[str, object], blocked_reader["python"])
        self.assertIsNone(python_lane["provider_stage_retry_status"])
        negative_ids = {case["case_id"] for case in _fixture_cases(NEGATIVE_FIXTURES)}
        self.assertTrue(
            {
                "review_checks.schema.negative.pending_lane_contains_retry_status",
                "review_checks.schema.negative.inconclusive_lane_contains_succeeded_retry_status",
                "review_checks.schema.negative.reader_lane_contains_succeeded_retry_status",
            }.issubset(negative_ids)
        )

        nested_cases = [
            cast(dict[str, object], case["value"])
            for case in _fixture_cases(POSITIVE_FIXTURES)
            if case["contract_schema_version"] == "cera.pi_scene.review_checks.v1"
        ]
        observed: set[tuple[str, str, tuple[str, ...]]] = set()
        for checks in nested_cases:
            for lane_name in ("luna", "reader"):
                lane = cast(dict[str, object], checks[lane_name])
                retry = lane["provider_stage_retry_status"]
                if retry is None:
                    continue
                normalized_envelope = validate_provider_stage_retry_status_envelope_v1(retry)
                status = normalized_envelope["status"]
                actions = normalized_envelope["actions"]
                observed.add(
                    (
                        lane_name,
                        cast(str, status["state"]),
                        tuple(cast(str, action["action_kind"]) for action in actions),
                    )
                )
        self.assertTrue(
            {
                ("luna", "eligible", ("provider_retry",)),
                ("reader", "eligible", ("provider_retry",)),
                ("luna", "blocked_ambiguous", ("check_status",)),
                ("reader", "blocked_ambiguous", ("check_status",)),
                ("luna", "attempts_exhausted", ()),
                ("reader", "attempts_exhausted", ()),
            }.issubset(observed)
        )

        for case_id, lane_name in (
            (
                "review_checks.schema.negative.inconclusive_lane_contains_succeeded_retry_status",
                "luna",
            ),
            (
                "review_checks.schema.negative.reader_lane_contains_succeeded_retry_status",
                "reader",
            ),
        ):
            checks = cast(
                dict[str, object],
                _case(NEGATIVE_FIXTURES, case_id)["value"],
            )
            lane = cast(dict[str, object], checks[lane_name])
            succeeded = lane["provider_stage_retry_status"]
            self.assertIsNotNone(validate_provider_stage_retry_status_envelope_v1(succeeded))
            with self.assertRaises(OrdinaryReviewContractError):
                validate_ordinary_review_checks_v1(checks)

    def test_manual_pass_and_resolved_choices_preserve_the_completed_gate(self) -> None:
        manual = cast(
            dict[str, object],
            _case(
                POSITIVE_FIXTURES,
                "review.schema.positive.manual_pass_review_ready",
            )["value"],
        )
        self.assertEqual(manual["gate_status"], "pass")
        self.assertEqual(
            cast(dict[str, object], manual["request_controls"])["schema_version"],
            "cera.pi_scene.request_controls.v3",
        )
        self.assertEqual(
            cast(dict[str, object], manual["actions"]),
            {
                "accept_enabled": True,
                "regenerate_enabled": True,
                "decline_enabled": True,
                "replan_enabled": False,
                "auditable_override_enabled": False,
                "auditable_override_action": None,
                "repair_recording_enabled": False,
            },
        )
        for suffix in (
            "declined_qualified_manual_candidate",
            "regenerated_qualified_manual_candidate",
        ):
            resolved = cast(
                dict[str, object],
                _case(POSITIVE_FIXTURES, f"review.schema.positive.{suffix}")["value"],
            )
            self.assertEqual(resolved["gate_status"], "pass")
            attempts = cast(list[dict[str, object]], resolved["provider_attempts"])
            self.assertEqual(attempts[-1]["disposition"], "checks_passed")

    def test_known_reject_remains_non_actionable_until_the_peer_join(self) -> None:
        partial = cast(
            dict[str, object],
            _case(
                POSITIVE_FIXTURES,
                "review.schema.positive.checks_pending_known_luna_reject",
            )["value"],
        )
        self.assertEqual((partial["state"], partial["gate_status"]), ("checks_pending", "reject"))
        self.assertTrue(
            all(
                value is False or value is None
                for value in cast(dict[str, object], partial["actions"]).values()
            )
        )
        attempts = cast(list[dict[str, object]], partial["provider_attempts"])
        self.assertEqual(attempts[-1]["disposition"], "luna_rejected")
        negative_ids = {case["case_id"] for case in _fixture_cases(NEGATIVE_FIXTURES)}
        self.assertIn(
            "review.schema.negative.checks_pending_reject_enables_creator_action",
            negative_ids,
        )

    def test_recorder_repair_is_backend_authority_not_client_inference(self) -> None:
        projection_pending = cast(
            dict[str, object],
            _case(
                POSITIVE_FIXTURES,
                "review.schema.positive.accepted_automatic_projection_pending",
            )["value"],
        )
        self.assertEqual(projection_pending["recording_status"], "projection_pending")
        self.assertIs(
            cast(dict[str, object], projection_pending["actions"])["repair_recording_enabled"],
            False,
        )
        repairable = cast(
            dict[str, object],
            _case(
                POSITIVE_FIXTURES,
                "review.schema.positive.accepted_manual_pending_repair_with_action",
            )["value"],
        )
        self.assertEqual(repairable["recording_status"], "pending_repair")
        self.assertIs(
            cast(dict[str, object], repairable["actions"])["repair_recording_enabled"],
            True,
        )

    def test_creator_guidance_is_content_free_and_privacy_closed(self) -> None:
        positive = _fixture_cases(POSITIVE_FIXTURES)
        guidance_values = [
            cast(dict[str, object], cast(dict[str, object], case["value"])["creator_guidance"])
            for case in positive
            if case["contract_schema_version"] == "cera.pi_scene.review.v2"
            and cast(dict[str, object], case["value"])["creator_guidance"] is not None
        ]
        self.assertTrue(guidance_values)
        for guidance in guidance_values:
            self.assertEqual(
                set(guidance),
                {"schema_version", "action", "text_sha256"},
            )
            self.assertNotIn("text", guidance)
        negative_ids = {case["case_id"] for case in _fixture_cases(NEGATIVE_FIXTURES)}
        self.assertIn("review.schema.negative.raw_creator_guidance_text", negative_ids)
        self.assertIn("review.schema.negative.legacy_sequence_authority", negative_ids)
        self.assertTrue(
            {
                "review.schema.negative.request_controls_are_null",
                "review.schema.negative.legacy_request_controls_v1",
                "review.schema.negative.legacy_request_controls_v2",
            }.issubset(negative_ids)
        )

    def test_decision_successor_callback_and_detached_hash_are_exact(self) -> None:
        regenerate = cast(
            dict[str, object],
            copy.deepcopy(
                _case(
                    POSITIVE_FIXTURES,
                    "review_decision.schema.positive.positive_05",
                )["value"]
            ),
        )
        with self.assertRaises(OrdinaryReviewContractError):
            validate_ordinary_review_decision_v2(regenerate)
        self.assertIsNone(normalize_ordinary_review_decision_v2(regenerate))

        with self.assertRaises(OrdinaryReviewContractError):
            validate_ordinary_review_decision_v2(
                regenerate,
                successor_validator=lambda _value: (_ for _ in ()).throw(
                    ValueError("invalid successor")
                ),
            )

        normalized_successor = {
            "schema_version": SUCCESSOR_VERSION,
            "completion_sha256": "d" * 64,
        }
        normalized_decision = copy.deepcopy(regenerate)
        normalized_decision["successor"] = copy.deepcopy(normalized_successor)
        detached = copy.deepcopy(normalized_decision)
        cast(dict[str, object], detached["review"])["terminal_decision"] = None
        terminal = cast(
            dict[str, object],
            cast(dict[str, object], regenerate["review"])["terminal_decision"],
        )
        terminal["decision_sha256"] = _canonical_sha256(detached)
        result = validate_ordinary_review_decision_v2(
            regenerate,
            successor_validator=lambda _value: copy.deepcopy(normalized_successor),
        )
        self.assertEqual(result["successor"], normalized_successor)

        decline = _case(
            POSITIVE_FIXTURES,
            "review_decision.schema.positive.positive_04",
        )["value"]
        self.assertIsNotNone(validate_ordinary_review_decision_v2(decline))

    def test_named_decision_regressions_cover_link_and_action_identity(self) -> None:
        negative_ids = {case["case_id"] for case in _fixture_cases(NEGATIVE_FIXTURES)}
        required = {
            "review_decision.schema.negative.automatic_accept_uses_manual_action",
            "review_decision.schema.negative.manual_accept_uses_automatic_action",
            "review_decision.schema.negative.override_uses_manual_action",
            "review_decision.schema.negative.terminal_link_url_mismatch",
            "review_decision.schema.negative.terminal_link_hash_mismatch",
            "review_decision.schema.negative.terminal_hash_uses_linked_payload",
        }
        self.assertTrue(required.issubset(negative_ids))

    def test_initial_lifecycle_contains_no_completed_work_or_terminal_decision(self) -> None:
        lifecycle = _case(
            POSITIVE_FIXTURES,
            "review_lifecycle.schema.positive.base_01",
        )["value"]
        normalized = validate_ordinary_review_lifecycle_v1(lifecycle)
        self.assertEqual(normalized["state"], "checks_pending")
        self.assertEqual(normalized["gate_status"], "pending")
        self.assertIsNone(normalized["terminal_decision"])
        checks = cast(dict[str, dict[str, object]], normalized["checks"])
        self.assertEqual(
            {checks[name]["status"] for name in ("luna", "reader", "python")},
            {"pending"},
        )

    def test_normalizer_returns_a_copy_or_none(self) -> None:
        value = _case(POSITIVE_FIXTURES, "review.schema.positive.base_01")["value"]
        normalized = normalize_ordinary_review_v2(value)
        self.assertEqual(normalized, value)
        self.assertIsNot(normalized, value)
        invalid = copy.deepcopy(value)
        cast(dict[str, object], invalid)["route"] = "adult"
        self.assertIsNone(normalize_ordinary_review_v2(invalid))

    def test_staged_javascript_copies_are_byte_identical(self) -> None:
        expected = JAVASCRIPT_CONTRACTS.read_bytes()
        for staged in STAGED_JAVASCRIPT_CONTRACTS:
            with self.subTest(staged=staged):
                self.assertEqual(staged.read_bytes(), expected)

    @unittest.skipUnless(shutil.which("node"), "Node.js is unavailable")
    def test_javascript_projection_matches_generated_fixtures(self) -> None:
        unicode_decision = cast(
            dict[str, object],
            copy.deepcopy(
                _case(
                    POSITIVE_FIXTURES,
                    "review_decision.schema.positive.positive_04",
                )["value"]
            ),
        )
        unicode_review = cast(dict[str, object], unicode_decision["review"])
        unicode_review["story_text"] = "Hana—雪🙂 leaves the choice open."
        detached = copy.deepcopy(unicode_decision)
        cast(dict[str, object], detached["review"])["terminal_decision"] = None
        cast(dict[str, object], unicode_review["terminal_decision"])["decision_sha256"] = (
            _canonical_sha256(detached)
        )
        validate_ordinary_review_decision_v2(unicode_decision)
        script = f"""
import fs from 'node:fs';
import * as contracts from {json.dumps(JAVASCRIPT_CONTRACTS.resolve().as_uri())};
const positive = JSON.parse(fs.readFileSync({json.dumps(str(POSITIVE_FIXTURES))}, 'utf8'));
const negative = JSON.parse(fs.readFileSync({json.dumps(str(NEGATIVE_FIXTURES))}, 'utf8'));
const successorValidator = value => {{
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new TypeError('invalid successor');
  if (Object.keys(value).sort().join(',') !== 'completion_sha256,schema_version') throw new TypeError('invalid successor keys');
  if (value.schema_version !== {json.dumps(SUCCESSOR_VERSION)}) throw new TypeError('invalid successor version');
  if (!/^[a-f0-9]{{64}}$/.test(value.completion_sha256)) throw new TypeError('invalid successor hash');
  return structuredClone(value);
}};
for (const item of positive.cases) {{
  const externalValidators = item.contract_schema_version === {json.dumps(DECISION_VERSION)}
    ? {{ ordinary_successor: successorValidator }}
    : {{}};
  contracts.validateSchemaVersion(item.contract_schema_version, item.value, {{ externalValidators }});
}}
for (const item of negative.cases) {{
  const externalValidators = item.contract_schema_version === {json.dumps(DECISION_VERSION)}
    ? {{ ordinary_successor: successorValidator }}
    : {{}};
  let rejected = false;
  try {{
    contracts.validateSchemaVersion(item.contract_schema_version, item.value, {{ externalValidators }});
  }} catch (error) {{
    if (error instanceof contracts.OrdinaryReviewContractError) rejected = true;
    else throw error;
  }}
  if (!rejected) throw new Error(`negative fixture accepted: ${{item.case_id}}`);
}}
const regenerate = positive.cases.find(item => item.value?.creator_action === 'regenerate').value;
if (contracts.normalizeOrdinaryReviewDecisionV2(regenerate) !== null) {{
  throw new Error('non-null successor validated without its exact callback');
}}
const decline = positive.cases.find(item => item.value?.creator_action === 'decline').value;
contracts.validateOrdinaryReviewDecisionV2(decline);
contracts.validateOrdinaryReviewDecisionV2({json.dumps(unicode_decision, ensure_ascii=False)});
"""
        completed = subprocess.run(
            [str(shutil.which("node")), "--input-type=module", "--eval", script],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
