from __future__ import annotations

import base64
import unittest

from cera.errors import ContractValidationError
from cera.pi_scene.provider_stage_retry import ProviderStage
from cera.pi_scene.provider_stage_retry_packets import (
    FrozenProviderStageResultV1,
    ImmutableRetrievalSnapshotIdentityV1,
    ProviderStageConfigurationV1,
    freeze_adult_filter_stage_packet,
    freeze_adult_scene_stage_packet,
    freeze_planner_stage_packet,
    freeze_recorder_stage_packet,
    freeze_semantic_validator_stage_packet,
    freeze_writer_stage_packet,
)
from cera.serialization import text_sha256


def _configuration(stage: ProviderStage) -> ProviderStageConfigurationV1:
    reasoning = {
        ProviderStage.PLANNER: "medium",
        ProviderStage.SEMANTIC_VALIDATOR: "extra_high",
    }.get(stage, "non_thinking")
    return ProviderStageConfigurationV1.create(
        stage=stage,
        model_id=f"model-for-{stage.value}",
        reasoning_mode=reasoning,
        routing={"route": "ordinary" if "adult" not in stage.value else "adult"},
        content_policy_route="protected" if "adult" in stage.value else "ordinary",
        stage_configuration={"maximum_output_tokens": 4096},
    )


class ProviderStageRetryPacketTests(unittest.TestCase):
    def test_all_six_stage_packets_are_exact_and_stage_bound(self) -> None:
        mutable_plan = {"beats": ["one"]}
        retrieval = ImmutableRetrievalSnapshotIdentityV1(
            schema_version=ImmutableRetrievalSnapshotIdentityV1.SCHEMA_VERSION,
            snapshot_id="planner-snapshot-7",
            snapshot_sha256=text_sha256("retrieval snapshot bytes"),
            immutability_evidence_sha256=text_sha256("immutable receipt"),
        )
        planner = freeze_planner_stage_packet(
            normalized_messages=[{"role": "user", "content": "hello"}],
            accepted_state={"head": "accepted-7"},
            retrieval_snapshot=retrieval,
            tool_result_bundle={"results": [{"id": "tool-1", "value": "safe"}]},
            configuration=_configuration(ProviderStage.PLANNER),
        )
        writer = freeze_writer_stage_packet(
            sequence_plan=mutable_plan,
            realization_context={"cast": ["npc-1"]},
            configuration=_configuration(ProviderStage.WRITER),
        )
        validator = freeze_semantic_validator_stage_packet(
            sequence_plan=mutable_plan,
            candidate={"prose": "candidate"},
            validation_context={"constraints": ["c1"]},
            configuration=_configuration(ProviderStage.SEMANTIC_VALIDATOR),
        )
        recorder = freeze_recorder_stage_packet(
            accepted_story={"prose": "accepted"},
            accepted_story_receipt={"receipt": "r1"},
            recording_context={"branch": "branch-1"},
            configuration=_configuration(ProviderStage.RECORDER),
        )
        adult_scene = freeze_adult_scene_stage_packet(
            scene_request={"request": "protected"},
            evidence_bundle={"evidence": ["protected-source"]},
            configuration=_configuration(ProviderStage.ADULT_SCENE),
        )
        scene_result = FrozenProviderStageResultV1.create(
            source_stage=ProviderStage.ADULT_SCENE,
            exact_result=b"\x00protected adult scene result\xff",
        )
        adult_filter = freeze_adult_filter_stage_packet(
            scene_result=scene_result,
            filter_context={"policy": "consensual-adult"},
            configuration=_configuration(ProviderStage.ADULT_FILTER),
        )

        packets = (planner, validator, writer, recorder, adult_scene, adult_filter)
        self.assertEqual(
            tuple(packet.stage for packet in packets),
            tuple(ProviderStage),
        )
        frozen_writer = writer.exact_bytes
        mutable_plan["beats"].append("mutated-after-freeze")
        self.assertEqual(writer.exact_bytes, frozen_writer)

        planner_payload = planner.to_payload()
        planner_input = planner_payload["semantic_input"]
        assert isinstance(planner_input, dict)
        self.assertEqual(
            planner_input["retrieval_snapshot"]["snapshot_sha256"],
            retrieval.snapshot_sha256,
        )
        self.assertIn("tool_result_bundle", planner_input)

        filter_payload = adult_filter.to_payload()
        filter_input = filter_payload["semantic_input"]
        assert isinstance(filter_input, dict)
        scene_binding = filter_input["frozen_scene_result"]
        assert isinstance(scene_binding, dict)
        self.assertEqual(scene_binding["result_sha256"], scene_result.result_sha256)
        self.assertEqual(
            base64.b64decode(scene_binding["exact_result_base64"]),
            scene_result.exact_result,
        )

    def test_configuration_cannot_cross_stage(self) -> None:
        with self.assertRaises(ContractValidationError):
            freeze_writer_stage_packet(
                sequence_plan={},
                realization_context={},
                configuration=_configuration(ProviderStage.PLANNER),
            )

    def test_adult_filter_refuses_non_scene_result(self) -> None:
        wrong = FrozenProviderStageResultV1.create(
            source_stage=ProviderStage.WRITER,
            exact_result=b"writer output",
        )
        with self.assertRaises(ContractValidationError):
            freeze_adult_filter_stage_packet(
                scene_result=wrong,
                filter_context={},
                configuration=_configuration(ProviderStage.ADULT_FILTER),
            )


if __name__ == "__main__":
    unittest.main()
