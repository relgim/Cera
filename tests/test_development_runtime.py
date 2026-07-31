from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from cera.composer import ArtifactPublicationMode
from cera.evaluation import RealGenesisSandbox
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.genesis.models import GenesisRecordType
from cera.ids import IdKind, TypedId
from cera.registry import build_schema_registry
from cera.runtime import (
    DevelopmentOrdinaryTurnPreparer,
    DevelopmentTurnSpec,
    OrdinaryApplicationRequestV2,
    RequiredSeedRecord,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json


ROOT = Path(__file__).resolve().parents[1]


class DevelopmentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = RealGenesisSandbox.create(ROOT, revision="v1_2")
        self.addCleanup(self.sandbox.close)
        relationships = {
            character_id: next(
                record.record_id
                for record in self.sandbox.compiled.records
                if record.record_type is GenesisRecordType.RELATIONSHIP_EDGE
                and record.relationship_from_id == character_id
                and record.relationship_to_id == self.sandbox.protected_user_id()
            )
            for character_id in CHARACTER_IDS.values()
        }
        self.preparer = DevelopmentOrdinaryTurnPreparer(
            store=self.sandbox.store,
            evidence_service=self.sandbox.service,
            world_id=self.sandbox.world_id,
            genesis_revision_id=self.sandbox.revision_id,
            protected_user_id=self.sandbox.protected_user_id(),
            access_scope=self.sandbox.system_scope(),
            relationship_record_ids=relationships,
        )

    def test_raw_turn_preparation_binds_ingress_seed_and_application_v2(self) -> None:
        hana = CHARACTER_IDS["Hana"]
        memory = self.sandbox.record_with_payload_value("memory_id", "H-M08")
        prepared = self.preparer.prepare(
            DevelopmentTurnSpec(
                schema_version=DevelopmentTurnSpec.SCHEMA_VERSION,
                turn_key="provider-free-hana-memory",
                raw_message=(
                    "Ted asks how Hana kept faith through years without an answer."
                ),
                session_id=TypedId(IdKind.SESSION, "development-test"),
                branch_id=self.sandbox.branch_id,
                present_character_ids=(self.sandbox.protected_user_id(), hana),
                eligible_responder_ids=(hana,),
                scene_anchors=("Hana and Ted are speaking in the entry hall.",),
                additional_seed_records=(
                    RequiredSeedRecord(
                        record_id=memory.record_id,
                        sections=(
                            "remembered_content",
                            "learned_meaning_or_belief_pressure",
                            "possible_retrieval_cues",
                        ),
                        reason="Resolve the indirect farewell-memory cue.",
                    ),
                ),
            )
        )
        request = prepared.application_request
        self.assertIsInstance(request, OrdinaryApplicationRequestV2)
        self.assertEqual(
            request.ingress_evidence.reasoner_request_sha256,
            request.reasoner_request.request_sha256,
        )
        self.assertEqual(
            request.ingress_evidence.seed_receipt.exact_evidence_ids,
            tuple(
                value.evidence_id
                for value in request.reasoner_request.seed_dossier.exact_seed_evidence
            ),
        )
        self.assertEqual(len(request.ingress_evidence.seed_lookup_receipts), 2)
        self.assertFalse(request.composer_plan.creator_event_coverage_required)
        self.assertIn(
            "already visible",
            " ".join(request.reasoner_request.hard_boundaries),
        )
        decoded = build_schema_registry().decode(
            __import__("json").loads(canonical_json(request))
        )
        self.assertEqual(decoded, request)

    def test_regeneration_topology_uses_current_head_only(self) -> None:
        hana = CHARACTER_IDS["Hana"]
        spec = DevelopmentTurnSpec(
            schema_version=DevelopmentTurnSpec.SCHEMA_VERSION,
            turn_key="empty-regeneration",
            raw_message="Ted asks for a different response.",
            session_id=TypedId(IdKind.SESSION, "development-test"),
            branch_id=self.sandbox.branch_id,
            present_character_ids=(self.sandbox.protected_user_id(), hana),
            eligible_responder_ids=(hana,),
            scene_anchors=("The branch is still at story start.",),
            publication_mode=ArtifactPublicationMode.REGENERATE,
        )
        with self.assertRaisesRegex(Exception, "cannot regenerate an empty branch"):
            self.preparer.prepare(spec)

    def test_ingress_evidence_cannot_be_rebound_to_another_source(self) -> None:
        hana = CHARACTER_IDS["Hana"]
        prepared = self.preparer.prepare(
            DevelopmentTurnSpec(
                schema_version=DevelopmentTurnSpec.SCHEMA_VERSION,
                turn_key="source-binding",
                raw_message="Ted greets Hana at the doorway.",
                session_id=TypedId(IdKind.SESSION, "development-test"),
                branch_id=self.sandbox.branch_id,
                present_character_ids=(self.sandbox.protected_user_id(), hana),
                eligible_responder_ids=(hana,),
                scene_anchors=("Ted has just entered the doorway.",),
            )
        )
        with self.assertRaises(Exception):
            replace(
                prepared.application_request,
                ingress_evidence=replace(
                    prepared.ingress_evidence,
                    source_sha256="0" * 64,
                ),
            )


if __name__ == "__main__":
    unittest.main()
