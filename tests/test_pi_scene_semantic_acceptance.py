from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.store import LeanSceneStore
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticConflictClass,
    SemanticConflictV1,
    SemanticValidationCustodyV1,
    SemanticValidationRequestV1,
    SemanticValidationVerdictV1,
    SemanticVerdict,
)
from cera.serialization import canonical_json, canonical_sha256, text_sha256, to_primitive

from .test_cognition_contracts import _plan
from .test_pi_scene_store_quality import _candidate, _turn_dir


def _qualified_candidate():
    candidate = _candidate()
    authority_json = canonical_json(to_primitive(_plan()))
    return replace(
        candidate,
        primary_authority_kind="codex_cognition_plan",
        primary_authority_json=authority_json,
        primary_authority_sha256=text_sha256(authority_json),
    )


def _validation(candidate, *, verdict: SemanticVerdict = SemanticVerdict.PASS):
    request = SemanticValidationRequestV1(
        schema_version=SemanticValidationRequestV1.SCHEMA_VERSION,
        cognition_plan=_plan(),
        exact_current_source=candidate.exact_user_source,
        exact_candidate_prose=candidate.story_text,
        current_public_state="The conversation remains open.",
        hard_boundaries=("Do not invent Ted dialogue or private state.",),
        selected_evidence=(),
    )
    result = SemanticValidationVerdictV1(
        schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
        verdict=verdict,
        conflict=(
            None
            if verdict is SemanticVerdict.PASS
            else SemanticConflictV1(
                conflict_class=SemanticConflictClass.OMITTED_DECISION,
                concise_explanation="The planned response is missing.",
                exact_quote=None,
                decision_key="sakura_door_response",
            )
        ),
    )
    custody = SemanticValidationCustodyV1(
        request_id="request:validation-1",
        candidate_id=candidate.candidate_id,
        world_id=candidate.world_id,
        branch_id=candidate.branch_id,
        accepted_head_sha256=candidate.accepted_head_before_sha256,
        cognition_plan_sha256=canonical_sha256(request.cognition_plan),
        candidate_prose_sha256=text_sha256(request.exact_candidate_prose),
        validation_request_sha256=canonical_sha256(request),
    )
    return BoundSemanticValidationV1(
        request=request,
        custody=custody,
        verdict=result,
    )


class PiSceneSemanticAcceptanceTests(unittest.TestCase):
    def test_cognition_accept_requires_passing_exact_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = LeanSceneStore(Path(temporary))
            candidate = _qualified_candidate()
            with self.assertRaisesRegex(
                ContractValidationError,
                "requires a passing semantic validation",
            ):
                store.accept(candidate)
            with self.assertRaisesRegex(
                ContractValidationError,
                "rejected semantic validation",
            ):
                store.accept(
                    candidate,
                    semantic_validation=_validation(
                        candidate,
                        verdict=SemanticVerdict.REJECT,
                    ),
                )
            accepted = store.accept(
                candidate,
                semantic_validation=_validation(candidate),
            )
            self.assertEqual(
                store.load_head(
                    world_id=candidate.world_id,
                    branch_id=candidate.branch_id,
                ).receipt,
                accepted,
            )
            self.assertTrue((_turn_dir(Path(temporary)) / "SEMANTIC_VALIDATION.json").is_file())

    def test_validation_artifact_tamper_or_deletion_fails_restart(self) -> None:
        for mutation in ("delete", "tamper"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                store = LeanSceneStore(root)
                candidate = _qualified_candidate()
                store.accept(candidate, semantic_validation=_validation(candidate))
                path = _turn_dir(root) / "SEMANTIC_VALIDATION.json"
                if mutation == "delete":
                    path.unlink()
                else:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["validation"]["verdict"]["verdict"] = "reject"
                    path.write_text(canonical_json(payload), encoding="utf-8")
                with self.assertRaisesRegex(
                    StateConflictError,
                    "semantic-validation",
                ):
                    LeanSceneStore(root).load_head(
                        world_id=candidate.world_id,
                        branch_id=candidate.branch_id,
                    )

    def test_legacy_sequence_accept_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = LeanSceneStore(Path(temporary))
            candidate = _candidate()
            accepted = store.accept(candidate)
            self.assertEqual(accepted.primary_authority_kind, "codex_sequence")
            self.assertFalse((_turn_dir(Path(temporary)) / "SEMANTIC_VALIDATION.json").exists())


if __name__ == "__main__":
    unittest.main()
