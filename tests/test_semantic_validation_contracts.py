from __future__ import annotations

import unittest

from cera.errors import ContractValidationError
from cera.schema import from_mapping
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticConflictClass,
    SemanticConflictV1,
    SemanticReviewFlagV1,
    SemanticValidationCustodyV1,
    SemanticValidationRequestV1,
    SemanticValidationVerdictV1,
    SemanticVerdict,
    ValidationEvidenceV1,
    semantic_verdict_json_schema,
)
from cera.serialization import canonical_sha256, text_sha256, to_primitive

from .test_cognition_contracts import _plan


def _request() -> SemanticValidationRequestV1:
    return SemanticValidationRequestV1(
        schema_version=SemanticValidationRequestV1.SCHEMA_VERSION,
        cognition_plan=_plan(),
        exact_current_source="Ted asks Sakura to open the door.",
        exact_candidate_prose=('Sakura kept one hand off the latch. "Who is outside?" she asked.'),
        current_public_state="Ted and Sakura are inside by the closed door.",
        hard_boundaries=("Do not invent Ted dialogue or private state.",),
        selected_evidence=(
            ValidationEvidenceV1(
                evidence_ref="source:current",
                concise_authoritative_fact="Ted asked Sakura to open the door.",
            ),
        ),
    )


def _custody(request: SemanticValidationRequestV1) -> SemanticValidationCustodyV1:
    return SemanticValidationCustodyV1(
        request_id="request:validation-1",
        candidate_id="candidate:one",
        world_id="world:test",
        branch_id="branch:main",
        accepted_head_sha256=None,
        cognition_plan_sha256=canonical_sha256(request.cognition_plan),
        candidate_prose_sha256=text_sha256(request.exact_candidate_prose),
        validation_request_sha256=canonical_sha256(request),
    )


class SemanticValidationContractTests(unittest.TestCase):
    def test_pass_round_trip_and_python_custody(self) -> None:
        request = _request()
        verdict = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.PASS,
            conflict=None,
            review_flags=(
                SemanticReviewFlagV1(
                    flag_code="minor_style_variation",
                    concise_explanation="Wording differs without changing meaning.",
                ),
            ),
        )
        decoded = from_mapping(
            SemanticValidationVerdictV1,
            to_primitive(verdict),
        )
        self.assertEqual(decoded, verdict)
        bound = BoundSemanticValidationV1(
            request=request,
            custody=_custody(request),
            verdict=verdict,
        )
        self.assertEqual(len(bound.binding_sha256), 64)

    def test_conflict_quote_and_decision_are_exactly_bound(self) -> None:
        request = _request()
        verdict = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.REJECT,
            conflict=SemanticConflictV1(
                conflict_class=SemanticConflictClass.CONTRADICTED_DECISION,
                concise_explanation="The prose opens the door instead of verifying.",
                exact_quote="opened the door",
                decision_key="sakura_door_response",
            ),
        )
        with self.assertRaisesRegex(ContractValidationError, "quote is not exact"):
            BoundSemanticValidationV1(
                request=request,
                custody=_custody(request),
                verdict=verdict,
            )

        bad_decision = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.REJECT,
            conflict=SemanticConflictV1(
                conflict_class=SemanticConflictClass.OMITTED_DECISION,
                concise_explanation="A required decision is omitted.",
                exact_quote=None,
                decision_key="unknown_decision",
            ),
        )
        with self.assertRaisesRegex(ContractValidationError, "unknown decision"):
            BoundSemanticValidationV1(
                request=request,
                custody=_custody(request),
                verdict=bad_decision,
            )

    def test_repair_policy_is_python_derived(self) -> None:
        repairable = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.REJECT,
            conflict=SemanticConflictV1(
                conflict_class=SemanticConflictClass.OMITTED_DECISION,
                concise_explanation="Required response is missing.",
                exact_quote=None,
                decision_key="sakura_door_response",
            ),
        )
        self.assertTrue(repairable.automatic_repair_eligible)
        ambiguous = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.REJECT,
            conflict=SemanticConflictV1(
                conflict_class=SemanticConflictClass.AUTHORITY_AMBIGUITY,
                concise_explanation="The supplied authority conflicts internally.",
                exact_quote=None,
                decision_key="sakura_door_response",
            ),
        )
        self.assertFalse(ambiguous.automatic_repair_eligible)

    def test_schema_has_closed_binary_branches_and_no_custody(self) -> None:
        schema = semantic_verdict_json_schema(decision_keys=("sakura_door_response",))
        branches = schema["properties"]["result"]["oneOf"]
        self.assertEqual(
            [branch["properties"]["verdict"]["const"] for branch in branches],
            ["pass", "reject"],
        )
        rendered = repr(schema)
        self.assertIn("sakura_door_response", rendered)
        exact_quote = branches[1]["properties"]["conflict"]["properties"]["exact_quote"]
        self.assertIn("verbatim contiguous substring", exact_quote["description"])
        self.assertIn("Never paraphrase", exact_quote["description"])
        self.assertIn("add ellipses", exact_quote["description"])
        for forbidden in (
            "candidate_id",
            "world_id",
            "branch_id",
            "accepted_head_sha256",
            "automatic_repair_eligible",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_spliced_quote_with_ellipsis_still_fails_exact_binding(self) -> None:
        request = _request()
        verdict = SemanticValidationVerdictV1(
            schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
            verdict=SemanticVerdict.REJECT,
            conflict=SemanticConflictV1(
                conflict_class=SemanticConflictClass.PRESENCE_VIOLATION,
                concise_explanation="Two real fragments were incorrectly joined.",
                exact_quote='Sakura kept one hand off the latch. ... "Who is outside?"',
                decision_key="sakura_door_response",
            ),
        )
        with self.assertRaisesRegex(ContractValidationError, "quote is not exact"):
            BoundSemanticValidationV1(
                request=request,
                custody=_custody(request),
                verdict=verdict,
            )

    def test_closed_decoder_rejects_coercion_and_unknown_fields(self) -> None:
        payload = {
            "schema_version": SemanticValidationVerdictV1.SCHEMA_VERSION,
            "verdict": "pass",
            "conflict": None,
            "review_flags": [],
            "repair": True,
        }
        with self.assertRaisesRegex(ContractValidationError, "unknown fields"):
            from_mapping(SemanticValidationVerdictV1, payload)


if __name__ == "__main__":
    unittest.main()
