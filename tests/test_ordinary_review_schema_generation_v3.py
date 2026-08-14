from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from cera.generated.ordinary_review_contracts_v3 import (
    OrdinaryReviewContractError,
    validate_ordinary_review_v3,
    validate_schema_version,
)
from cera.pi_scene.ordinary_rejection_policy import (
    ORDINARY_STANDING_CREATOR_POLICY_ID,
    ORDINARY_STANDING_CREATOR_POLICY_SHA256,
    ORDINARY_STANDING_CREATOR_POLICY_TEXT_SHA256,
    ORDINARY_STANDING_CREATOR_POLICY_VERSION,
    ordinary_standing_creator_policy,
)
from cera.serialization import canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "scripts" / "generate_ordinary_review_contracts_v3.py"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "generated"
POSITIVE_FIXTURES = FIXTURE_ROOT / "ordinary_review_v3_positive.json"
NEGATIVE_FIXTURES = FIXTURE_ROOT / "ordinary_review_v3_negative.json"


def _fixture_cases(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_bytes())
    assert isinstance(payload, dict)
    cases = payload["cases"]
    assert isinstance(cases, list)
    return cast(list[dict[str, object]], cases)


def _case(path: Path, case_id: str) -> dict[str, object]:
    return next(case for case in _fixture_cases(path) if case["case_id"] == case_id)


def _successor_validator(value: object) -> object:
    if not isinstance(value, dict):
        raise ValueError("successor is not an object")
    return copy.deepcopy(value)


def _external_validators(
    version: object,
) -> Mapping[str, Callable[[object], object]] | None:
    if version == "cera.pi_scene.review_decision.v3":
        return {"ordinary_successor": _successor_validator}
    return None


class OrdinaryReviewSchemaGenerationV3Tests(unittest.TestCase):
    def test_generated_v3_artifacts_are_current(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join((str(ROOT / "src"), str(ROOT)))
        completed = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            cwd=ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_python_projection_accepts_all_positive_and_rejects_all_negative(self) -> None:
        for case in _fixture_cases(POSITIVE_FIXTURES):
            with self.subTest(case=case["case_id"]):
                self.assertEqual(
                    validate_schema_version(
                        str(case["contract_schema_version"]),
                        case["value"],
                        external_validators=_external_validators(case["contract_schema_version"]),
                    ),
                    case["value"],
                )
        for case in _fixture_cases(NEGATIVE_FIXTURES):
            with self.subTest(case=case["case_id"]):
                with self.assertRaises(OrdinaryReviewContractError):
                    validate_schema_version(
                        str(case["contract_schema_version"]),
                        case["value"],
                        external_validators=_external_validators(case["contract_schema_version"]),
                    )

    def test_standing_policy_projection_is_distinct_and_hash_bound(self) -> None:
        review = cast(
            dict[str, object],
            _case(
                POSITIVE_FIXTURES,
                "review.schema.positive.accepted_standing_policy",
            )["value"],
        )
        accepted = validate_ordinary_review_v3(review)
        acceptance = cast(dict[str, object], accepted["acceptance"])
        authority = cast(dict[str, object], acceptance["standing_policy"])
        self.assertEqual(accepted["gate_status"], "reject")
        self.assertEqual(acceptance["mode"], "standing_policy")
        self.assertEqual(acceptance["canon_status"], "provisional")
        self.assertEqual(authority["authority_kind"], "standing_creator_policy")
        self.assertEqual(authority["policy_id"], ORDINARY_STANDING_CREATOR_POLICY_ID)
        self.assertEqual(authority["policy_version"], ORDINARY_STANDING_CREATOR_POLICY_VERSION)
        self.assertEqual(authority["policy_sha256"], ORDINARY_STANDING_CREATOR_POLICY_SHA256)
        self.assertEqual(authority["tolerated_reason_codes"], ["luna:omitted_decision"])

        for field in ("policy_sha256", "audit_sha256"):
            tampered = copy.deepcopy(review)
            tampered_acceptance = cast(dict[str, object], tampered["acceptance"])
            tampered_authority = cast(dict[str, object], tampered_acceptance["standing_policy"])
            tampered_authority[field] = "a" * 64
            with self.subTest(field=field), self.assertRaises(OrdinaryReviewContractError):
                validate_ordinary_review_v3(tampered)

        for field, value in (
            ("candidate_sha256", "a" * 64),
            ("tolerated_reason_codes", ["luna:capability_restriction"]),
        ):
            tampered = copy.deepcopy(review)
            tampered_acceptance = cast(dict[str, object], tampered["acceptance"])
            tampered_authority = cast(dict[str, object], tampered_acceptance["standing_policy"])
            tampered_authority[field] = value
            tampered_authority["audit_sha256"] = canonical_sha256(
                {key: item for key, item in tampered_authority.items() if key != "audit_sha256"}
            )
            with self.subTest(field=field), self.assertRaises(OrdinaryReviewContractError):
                validate_ordinary_review_v3(tampered)

    def test_failure_source_roles_and_policy_text_are_pinned(self) -> None:
        review = cast(
            dict[str, object],
            copy.deepcopy(
                _case(
                    POSITIVE_FIXTURES,
                    "review.schema.positive.automatic_luna_reject",
                )["value"]
            ),
        )
        checks = cast(dict[str, object], review["checks"])
        luna = cast(dict[str, object], checks["luna"])
        failure = cast(list[dict[str, object]], luna["failures"])[0]
        failure["source_kind"] = "reader_issue"
        failure["feedback_scope"] = "exact_quote"
        with self.assertRaises(OrdinaryReviewContractError):
            validate_ordinary_review_v3(review)

        policy = ordinary_standing_creator_policy()
        self.assertEqual(policy.policy_sha256, ORDINARY_STANDING_CREATOR_POLICY_SHA256)
        self.assertEqual(policy.policy_text_sha256, ORDINARY_STANDING_CREATOR_POLICY_TEXT_SHA256)


if __name__ == "__main__":
    unittest.main()
