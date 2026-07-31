from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

from cera.composer import DeepSeekCompositionDraftV5, RealizationKind
from cera.composer.deepseek import _draft_has_structural_core_v4
from cera.composer.obligations import (
    ComposerOutputDiagnosticCode as Diagnostic,
    CoverageObligationMode,
    build_composer_output_obligations,
    evaluate_draft_obligations,
    typed_decoding_diagnostic,
)
from cera.errors import ContractValidationError
from cera.ids import IdKind, TypedId
from cera.schema import from_mapping
import tests.test_composer as composer_support
from tests.structural_v2_fixtures import (
    composition_draft_v5_from_submission,
)


class ComposerOutputObligationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = composer_support.ComposerTests("runTest")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)

    def _request(self, suffix: str, **kwargs):
        return self.base.request(suffix, **kwargs)

    def _payload(self, request, suffix: str = "draft"):
        return composition_draft_v5_from_submission(
            self.base.submission(request, suffix)
        )

    @staticmethod
    def _draft(payload):
        return from_mapping(DeepSeekCompositionDraftV5, payload)

    def test_one_canonical_object_binds_associations_and_tagged_coverage(
        self,
    ) -> None:
        request = self._request(
            "canonical",
            source_texts=("First supplied unit.", "Second supplied unit."),
            classifications=(
                composer_support.SourceUnitClassification.EVENT,
                composer_support.SourceUnitClassification.MESSAGE,
            ),
            coverage=True,
        )
        obligations = build_composer_output_obligations(request)
        provider = obligations.to_provider_dict()
        self.assertEqual(
            provider["obligation_sha256"],
            obligations.obligation_sha256,
        )
        self.assertEqual(
            obligations.source_coverage.mode,
            CoverageObligationMode.EXACT_SEQUENCE,
        )
        self.assertEqual(
            provider["source_coverage"]["mode"],
            "exact_sequence",
        )
        self.assertEqual(
            provider["source_coverage"]["values"],
            [
                str(value.source_unit_id)
                for value in request.source_packet.units
            ],
        )
        self.assertEqual(
            provider["specificity_coverage"]["mode"],
            "must_be_empty",
        )
        self.assertEqual(provider["specificity_coverage"]["values"], [])
        self.assertTrue(
            provider["source_coverage"][
                "each_returned_entry_requires_one_or_more_existing_segment_keys"
            ]
        )
        self.assertEqual(
            provider["required_beat_authority_owner_associations"],
            [
                {
                    "authority_id": str(value.beat_id),
                    "owner_id": str(value.actor_id),
                }
                for value in (
                    request.reasoner_outcome.decision.current_segment
                    .ordered_beats
                )
            ],
        )

    def test_source_sequence_missing_extra_and_reorder_are_rejected(
        self,
    ) -> None:
        request = self._request(
            "source-sequence",
            source_texts=("First supplied unit.", "Second supplied unit."),
            classifications=(
                composer_support.SourceUnitClassification.EVENT,
                composer_support.SourceUnitClassification.MESSAGE,
            ),
            coverage=True,
        )
        obligations = build_composer_output_obligations(request)
        base = self._payload(request)

        mutations = {}
        missing = json.loads(json.dumps(base))
        missing["source_coverage"] = missing["source_coverage"][:-1]
        mutations["missing"] = missing
        reordered = json.loads(json.dumps(base))
        reordered["source_coverage"].reverse()
        mutations["reordered"] = reordered
        extra = json.loads(json.dumps(base))
        extra["source_coverage"].append(
            {
                "source_unit_id": str(
                    TypedId(IdKind.SOURCE_UNIT, "unadvertised")
                ),
                "segment_keys": ["core"],
            }
        )
        mutations["extra"] = extra

        for label, payload in mutations.items():
            with self.subTest(label=label):
                diagnostics = evaluate_draft_obligations(
                    self._draft(payload),
                    obligations,
                )
                self.assertIn(
                    Diagnostic.SOURCE_COVERAGE_SEQUENCE_MISMATCH,
                    diagnostics,
                )

    def test_empty_coverage_is_an_explicit_obligation(self) -> None:
        request = self._request("empty-coverage")
        obligations = build_composer_output_obligations(request)
        payload = self._payload(request)
        payload["source_coverage"] = [
            {
                "source_unit_id": str(
                    request.source_packet.units[0].source_unit_id
                ),
                "segment_keys": ["core"],
            }
        ]
        diagnostics = evaluate_draft_obligations(
            self._draft(payload),
            obligations,
        )
        self.assertIn(Diagnostic.SOURCE_COVERAGE_NOT_EMPTY, diagnostics)

    def test_authority_owner_pairs_cannot_be_swapped(self) -> None:
        request = self._request(
            "association-swap",
            responders=(self.base.alpha, self.base.beta),
        )
        obligations = build_composer_output_obligations(request)
        payload = self._payload(request)
        realizations = payload["realization_segments"]
        self.assertGreaterEqual(len(realizations), 2)
        first_owner = realizations[0]["owner_id"]
        realizations[0]["owner_id"] = realizations[1]["owner_id"]
        realizations[1]["owner_id"] = first_owner
        diagnostics = evaluate_draft_obligations(
            self._draft(payload),
            obligations,
        )
        self.assertIn(
            Diagnostic.REALIZATION_AUTHORITY_OWNER_MISMATCH,
            diagnostics,
        )
        self.assertIn(
            Diagnostic.BEAT_AUTHORITY_RELATION_MISMATCH,
            diagnostics,
        )

    def test_missing_selected_owner_is_atomic_and_distinct_realizations_may_share_authority(
        self,
    ) -> None:
        request = self._request(
            "owner-and-duplicate",
            responders=(self.base.alpha, self.base.beta),
        )
        obligations = build_composer_output_obligations(request)
        draft = self._draft(self._payload(request))
        without_second = SimpleNamespace(
            **{
                field: getattr(draft, field)
                for field in (
                    "story_segments",
                    "source_coverage",
                    "specificity_coverage",
                    "terminal_segment_key",
                )
            },
            realization_segments=tuple(
                value
                for value in draft.realization_segments
                if value.owner_id != request.selected_npc_ids[1]
            ),
        )
        self.assertIn(
            Diagnostic.MISSING_SELECTED_NPC_OWNER,
            evaluate_draft_obligations(without_second, obligations),
        )
        distinct_realizations_same_authority = SimpleNamespace(
            story_segments=draft.story_segments,
            source_coverage=draft.source_coverage,
            specificity_coverage=draft.specificity_coverage,
            terminal_segment_key=draft.terminal_segment_key,
            realization_segments=(
                *draft.realization_segments,
                replace(
                    draft.realization_segments[0],
                    kind=(
                        RealizationKind.DIALOGUE
                        if draft.realization_segments[0].kind
                        is not RealizationKind.DIALOGUE
                        else RealizationKind.ACTION
                    ),
                ),
            ),
        )
        self.assertNotIn(
            Diagnostic.REALIZATION_ASSOCIATION_DUPLICATE,
            evaluate_draft_obligations(
                distinct_realizations_same_authority,
                obligations,
            ),
        )

    def test_typed_duplicate_and_terminal_failures_map_to_value_free_codes(
        self,
    ) -> None:
        request = self._request("typed-safe-codes")
        duplicate = self._payload(request)
        duplicate["story_segments"].append(
            dict(duplicate["story_segments"][0])
        )
        with self.assertRaises(ContractValidationError) as caught:
            self._draft(duplicate)
        self.assertEqual(
            typed_decoding_diagnostic(caught.exception),
            Diagnostic.STORY_SEGMENT_DUPLICATE,
        )

        duplicate_realization = self._payload(request)
        duplicate_realization["realization_segments"].append(
            dict(duplicate_realization["realization_segments"][0])
        )
        with self.assertRaises(ContractValidationError) as caught:
            self._draft(duplicate_realization)
        self.assertEqual(
            typed_decoding_diagnostic(caught.exception),
            Diagnostic.REALIZATION_ASSOCIATION_DUPLICATE,
        )

        empty_source_segments = self._payload(
            self._request(
                "typed-empty-source-segments",
                source_texts=("Supplied event.",),
                classifications=(
                    composer_support.SourceUnitClassification.EVENT,
                ),
                coverage=True,
            )
        )
        empty_source_segments["source_coverage"][0][
            "segment_keys"
        ] = []
        with self.assertRaises(ContractValidationError) as caught:
            self._draft(empty_source_segments)
        self.assertEqual(
            typed_decoding_diagnostic(caught.exception),
            Diagnostic.SOURCE_COVERAGE_SEGMENT_KEYS_EMPTY,
        )

        bad_terminal = self._payload(request)
        bad_terminal["story_segments"].append(
            {"segment_key": "final", "text": "A final response."}
        )
        with self.assertRaises(ContractValidationError) as caught:
            self._draft(bad_terminal)
        self.assertEqual(
            typed_decoding_diagnostic(caught.exception),
            Diagnostic.TERMINAL_SEGMENT_RULE_FAILED,
        )

    def test_atomic_predicates_and_composite_complete_core_are_equivalent(
        self,
    ) -> None:
        request = self._request("equivalence")
        obligations = build_composer_output_obligations(request)
        valid = self._draft(self._payload(request))
        self.assertEqual(
            _draft_has_structural_core_v4(request, valid),
            not evaluate_draft_obligations(valid, obligations),
        )

        invalid = SimpleNamespace(
            story_segments=valid.story_segments,
            source_coverage=valid.source_coverage,
            realization_segments=(),
            specificity_coverage=valid.specificity_coverage,
            terminal_segment_key=valid.terminal_segment_key,
        )
        self.assertEqual(
            _draft_has_structural_core_v4(request, invalid),
            not evaluate_draft_obligations(invalid, obligations),
        )


if __name__ == "__main__":
    unittest.main()
