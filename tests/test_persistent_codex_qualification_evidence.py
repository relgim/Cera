from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from cera.errors import ContractValidationError
from cera.providers import (
    codex_realization_verifier_candidate,
    load_persistent_codex_completion_registration_qualification,
    load_persistent_codex_qualification,
    load_persistent_codex_rotation_qualification,
    load_persistent_codex_tree_cleanup_qualification,
    load_persistent_codex_verifier_epoch_qualification,
    load_relational_boundary_qualification,
)
from cera.providers.codex_sdk_compat import (
    CODEX_SDK_COMPATIBILITY_ID,
    CODEX_SDK_COMPATIBILITY_SOURCE_SHA256,
)
from cera.serialization import bytes_sha256

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v1"
    / "summary.json"
)
SUMMARY_SHA256 = "f92a08e5cda04c701c74b3d1ddd98fe1abc0da733959f56545c0eb43cd07f5f3"
ROTATION_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v2"
    / "summary.json"
)
ROTATION_SUMMARY_SHA256 = (
    "c4bbe45d98649a14e817380524b71e1a2703c72433b162386f16ea37f06ca2d9"
)
TREE_CLEANUP_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v3"
    / "summary.json"
)
TREE_CLEANUP_SUMMARY_SHA256 = (
    "d477cade6580dcc3d5df9e445047f57a8c9b74e6f24bb21b2a109afb0d4124d8"
)
VERIFIER_EPOCH_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v4"
    / "summary.json"
)
VERIFIER_EPOCH_SUMMARY_SHA256 = (
    "d08a18bc970c788ab0f9f8bdcdf647c42bd5c42e7fef3e20d51ed25ab2c84b9b"
)
COMPLETION_REGISTRATION_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "persistent_codex_transport_probe_2026-07-29_v5"
    / "summary.json"
)
COMPLETION_REGISTRATION_SUMMARY_SHA256 = (
    "1e5f7f09f0ed6b29211fe40c8ac998e6344103b84fe1ef0f4bd0b496c40c56f1"
)
RELATIONAL_BOUNDARY_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "deepseek_relational_boundary_probe_2026-07-29_v1"
    / "summary.json"
)
RELATIONAL_BOUNDARY_SUMMARY_SHA256 = (
    "0e6e3a70a2b17990340e7426e252c798ec511d84379f2a6f3c771d16fd6b2202"
)
ACTIVE_RELATIONAL_BOUNDARY_SUMMARY = (
    ROOT
    / "evaluation"
    / "evidence"
    / "deepseek_relational_boundary_probe_2026-07-29_v4"
    / "summary.json"
)
ACTIVE_RELATIONAL_BOUNDARY_SUMMARY_SHA256 = (
    "d18e1397eafe1e845e2722100268a7fa0cc7b514be30d710a02d76637981940c"
)


class PersistentCodexQualificationEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.payload = json.loads(SUMMARY.read_text(encoding="utf-8"))
        cls.rotation_payload = json.loads(
            ROTATION_SUMMARY.read_text(encoding="utf-8")
        )
        cls.tree_cleanup_payload = json.loads(
            TREE_CLEANUP_SUMMARY.read_text(encoding="utf-8")
        )
        cls.verifier_epoch_payload = json.loads(
            VERIFIER_EPOCH_SUMMARY.read_text(encoding="utf-8")
        )
        cls.completion_registration_payload = json.loads(
            COMPLETION_REGISTRATION_SUMMARY.read_text(encoding="utf-8")
        )
        cls.relational_boundary_payload = json.loads(
            RELATIONAL_BOUNDARY_SUMMARY.read_text(encoding="utf-8")
        )
        cls.active_relational_boundary_payload = json.loads(
            ACTIVE_RELATIONAL_BOUNDARY_SUMMARY.read_text(
                encoding="utf-8"
            )
        )

    def test_passed_live_evidence_qualifies_exact_role_process_reuse(self) -> None:
        evidence = load_persistent_codex_qualification(
            SUMMARY,
            expected_summary_sha256=SUMMARY_SHA256,
            expected_model="gpt-5.6-sol",
        )
        self.assertEqual(evidence.summary_sha256, SUMMARY_SHA256)
        self.assertEqual(evidence.reasoner_calls, 2)
        self.assertEqual(evidence.verifier_calls, 2)

    def test_changed_evidence_fails_hash_binding_before_activation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            path.write_bytes(SUMMARY.read_bytes() + b"\n")
            with self.assertRaisesRegex(ContractValidationError, "hash does not match"):
                load_persistent_codex_qualification(
                    path,
                    expected_summary_sha256=SUMMARY_SHA256,
                    expected_model="gpt-5.6-sol",
                )

    def test_rehashed_semantic_mutations_still_fail_closed(self) -> None:
        mutations = {
            "nonterminal status": lambda value: value.update(status="running"),
            "two process launches": lambda value: value["role_sessions"][
                "scene_reasoner"
            ].update(process_launch_count=2),
            "prompt retention": lambda value: value["calls"][0][
                "provider_receipt"
            ].update(retains_prompt=True),
            "duplicate provider request": lambda value: value["calls"][1][
                "provider_receipt"
            ].update(
                provider_request_id_sha256=value["calls"][0]["provider_receipt"][
                    "provider_request_id_sha256"
                ]
            ),
            "wrong role": lambda value: value["calls"][2].update(
                role="scene_reasoner"
            ),
            "wrong model": lambda value: value["calls"][3][
                "provider_receipt"
            ].update(returned_model="gpt-5.6-terra"),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                payload = deepcopy(self.payload)
                mutate(payload)
                raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
                path = Path(directory) / "summary.json"
                path.write_bytes(raw)
                with self.assertRaises(ContractValidationError):
                    load_persistent_codex_qualification(
                        path,
                        expected_summary_sha256=bytes_sha256(raw),
                        expected_model="gpt-5.6-sol",
                    )

    def test_passed_rotation_evidence_qualifies_two_verifier_epochs(self) -> None:
        evidence = load_persistent_codex_rotation_qualification(
            ROTATION_SUMMARY,
            expected_summary_sha256=ROTATION_SUMMARY_SHA256,
            expected_model="gpt-5.6-sol",
        )
        self.assertEqual(evidence.summary_sha256, ROTATION_SUMMARY_SHA256)
        self.assertEqual(evidence.verifier_calls, 4)
        self.assertEqual(evidence.process_launches, 2)
        self.assertEqual(evidence.maximum_requests_per_process, 2)

    def test_rehashed_rotation_mutations_fail_closed(self) -> None:
        mutations = {
            "third call kept first process": lambda value: value["calls"][2].update(
                process_launch_count_after_call=1
            ),
            "hidden retry": lambda value: value["calls"][1].update(
                automatic_retry_count=1
            ),
            "wrong total launches": lambda value: value.update(
                process_launch_count=3
            ),
            "prompt retention": lambda value: value["calls"][0][
                "provider_receipt"
            ].update(retains_prompt=True),
            "duplicate provider request": lambda value: value["calls"][3][
                "provider_receipt"
            ].update(
                provider_request_id_sha256=value["calls"][0][
                    "provider_receipt"
                ]["provider_request_id_sha256"]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                payload = deepcopy(self.rotation_payload)
                mutate(payload)
                raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
                path = Path(directory) / "summary.json"
                path.write_bytes(raw)
                with self.assertRaises(ContractValidationError):
                    load_persistent_codex_rotation_qualification(
                        path,
                        expected_summary_sha256=bytes_sha256(raw),
                        expected_model="gpt-5.6-sol",
                    )

    def test_passed_tree_cleanup_evidence_qualifies_replacement_order(self) -> None:
        evidence = load_persistent_codex_tree_cleanup_qualification(
            TREE_CLEANUP_SUMMARY,
            expected_summary_sha256=TREE_CLEANUP_SUMMARY_SHA256,
            expected_model="gpt-5.6-sol",
        )
        self.assertEqual(evidence.summary_sha256, TREE_CLEANUP_SUMMARY_SHA256)
        self.assertEqual(evidence.verifier_calls, 4)
        self.assertEqual(evidence.process_launches, 2)
        self.assertEqual(
            evidence.cleanup_policy,
            "force_full_tree_before_replacement",
        )

    def test_tree_cleanup_policy_mutations_fail_closed(self) -> None:
        mutations = {
            "graceful parent only": lambda value: value.update(
                process_tree_cleanup_policy="close_parent_stdin"
            ),
            "replacement does not require confirmation": lambda value: value.update(
                replacement_dispatch_requires_cleanup_confirmation=False
            ),
            "wrong launch sequence": lambda value: value["calls"][2].update(
                process_launch_count_after_call=1
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                payload = deepcopy(self.tree_cleanup_payload)
                mutate(payload)
                raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
                path = Path(directory) / "summary.json"
                path.write_bytes(raw)
                with self.assertRaises(ContractValidationError):
                    load_persistent_codex_tree_cleanup_qualification(
                        path,
                        expected_summary_sha256=bytes_sha256(raw),
                        expected_model="gpt-5.6-sol",
                    )

    def test_verifier_epoch_evidence_binds_shim_and_real_verifier_contract(
        self,
    ) -> None:
        evidence = load_persistent_codex_verifier_epoch_qualification(
            VERIFIER_EPOCH_SUMMARY,
            expected_summary_sha256=VERIFIER_EPOCH_SUMMARY_SHA256,
            expected_model="gpt-5.6-sol",
        )
        self.assertEqual(
            evidence.summary_sha256,
            VERIFIER_EPOCH_SUMMARY_SHA256,
        )
        self.assertEqual(evidence.verifier_calls, 6)
        self.assertEqual(evidence.process_launches, 3)
        self.assertEqual(
            evidence.sdk_compatibility_id,
            "cera.codex_sdk_early_completion_buffer.v1",
        )

    def test_rehashed_verifier_epoch_mutations_fail_closed(self) -> None:
        mutations = {
            "shim source drift": lambda value: value["sdk_compatibility"].update(
                route_notification_source_sha256="0" * 64
            ),
            "activation not validated": lambda value: value["calls"][4].update(
                transport_compatibility_activation_validated=False
            ),
            "wrong third epoch": lambda value: value["calls"][4].update(
                process_launch_count_after_call=2
            ),
            "semantic rejection": lambda value: value["calls"][2][
                "verification_receipt"
            ].update(status="rejected"),
            "request mismatch": lambda value: value["calls"][1][
                "verification_receipt"
            ].update(verification_request_sha256="0" * 64),
            "duplicate provider request": lambda value: value["calls"][5][
                "provider_receipt"
            ].update(
                provider_request_id_sha256=value["calls"][0][
                    "provider_receipt"
                ]["provider_request_id_sha256"]
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                payload = deepcopy(self.verifier_epoch_payload)
                mutate(payload)
                raw = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
                path = Path(directory) / "summary.json"
                path.write_bytes(raw)
                with self.assertRaises(ContractValidationError):
                    load_persistent_codex_verifier_epoch_qualification(
                        path,
                        expected_summary_sha256=bytes_sha256(raw),
                        expected_model="gpt-5.6-sol",
                    )

    def test_completion_registration_evidence_binds_five_epochs(self) -> None:
        current_route = codex_realization_verifier_candidate()
        evidence_route_sha256 = self.completion_registration_payload[
            "route_sha256"
        ]
        evidence = (
            load_persistent_codex_completion_registration_qualification(
                COMPLETION_REGISTRATION_SUMMARY,
                expected_summary_sha256=(
                    COMPLETION_REGISTRATION_SUMMARY_SHA256
                ),
                expected_model="gpt-5.6-sol",
                expected_route_sha256=evidence_route_sha256,
            )
        )
        self.assertEqual(evidence.verifier_calls, 10)
        self.assertEqual(evidence.process_launches, 5)
        self.assertEqual(
            evidence.sdk_compatibility_id,
            "cera.codex_sdk_completion_registration.v2",
        )
        self.assertEqual(
            evidence.route_notification_source_sha256,
            "8fd316aa949d03812e935b0928e3767d0faa1d79701f599d97e66c0e46c679d1",
        )
        self.assertEqual(evidence.route_sha256, evidence_route_sha256)
        self.assertNotEqual(evidence.route_sha256, current_route.route_sha256)

    def test_rehashed_completion_registration_mutations_fail_closed(
        self,
    ) -> None:
        evidence_route_sha256 = self.completion_registration_payload[
            "route_sha256"
        ]
        mutations = {
            "pre-registration omitted": lambda value: value.update(
                returned_turn_pre_registration_required=False
            ),
            "fifth epoch omitted": lambda value: value.update(
                process_launch_count=4
            ),
            "wrong final launch": lambda value: value["calls"][9].update(
                process_launch_count_after_call=4
            ),
            "hidden retry": lambda value: value["calls"][8].update(
                automatic_retry_count=1
            ),
            "route drift": lambda value: value.update(
                route_sha256="0" * 64
            ),
            "retroactive current compatibility promotion": lambda value: value[
                "sdk_compatibility"
            ].update(
                compatibility_id=CODEX_SDK_COMPATIBILITY_ID,
                route_notification_source_sha256=(
                    CODEX_SDK_COMPATIBILITY_SOURCE_SHA256
                ),
            ),
            "duplicate provider request": lambda value: value["calls"][9][
                "provider_receipt"
            ].update(
                provider_request_id_sha256=value["calls"][0][
                    "provider_receipt"
                ]["provider_request_id_sha256"]
            ),
        }
        for label, mutate in mutations.items():
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                payload = deepcopy(self.completion_registration_payload)
                mutate(payload)
                raw = (
                    json.dumps(payload, sort_keys=True) + "\n"
                ).encode("utf-8")
                path = Path(directory) / "summary.json"
                path.write_bytes(raw)
                with self.assertRaises(ContractValidationError):
                    load_persistent_codex_completion_registration_qualification(
                        path,
                        expected_summary_sha256=bytes_sha256(raw),
                        expected_model="gpt-5.6-sol",
                        expected_route_sha256=evidence_route_sha256,
                    )

    def test_relational_boundary_evidence_binds_unchanged_two_stage_probe(
        self,
    ) -> None:
        evidence = load_relational_boundary_qualification(
            RELATIONAL_BOUNDARY_SUMMARY,
            expected_summary_sha256=RELATIONAL_BOUNDARY_SUMMARY_SHA256,
            expected_composer_prompt_version=(
                "cera.deepseek_scene_composer_prompt.v10"
            ),
            expected_composer_dto_version=(
                "cera.deepseek_composition_draft.v5"
            ),
            expected_composer_model="deepseek-v4-pro",
            expected_verifier_model="gpt-5.6-sol",
        )
        self.assertEqual(
            evidence.summary_sha256,
            RELATIONAL_BOUNDARY_SUMMARY_SHA256,
        )
        self.assertEqual(evidence.deepseek_calls, 1)
        self.assertEqual(evidence.sol_calls, 1)
        self.assertEqual(
            evidence.candidate_sha256,
            self.relational_boundary_payload[
                "composer_candidate_sha256"
            ],
        )

    def test_active_relational_boundary_v4_binds_canonical_obligation_probe(
        self,
    ) -> None:
        evidence = load_relational_boundary_qualification(
            ACTIVE_RELATIONAL_BOUNDARY_SUMMARY,
            expected_summary_sha256=(
                ACTIVE_RELATIONAL_BOUNDARY_SUMMARY_SHA256
            ),
            expected_composer_prompt_version=(
                "cera.deepseek_scene_composer_prompt.v12"
            ),
            expected_composer_dto_version=(
                "cera.deepseek_composition_draft.v5"
            ),
            expected_composer_model="deepseek-v4-pro",
            expected_verifier_model="gpt-5.6-sol",
            expected_probe_schema=(
                "cera.deepseek_relational_boundary_probe.v4"
            ),
            expected_qualification_id=(
                "deepseek-relational-protected-user-boundary-v4"
            ),
        )
        self.assertEqual(
            evidence.summary_sha256,
            ACTIVE_RELATIONAL_BOUNDARY_SUMMARY_SHA256,
        )
        self.assertEqual(evidence.deepseek_calls, 1)
        self.assertEqual(evidence.sol_calls, 1)

    def test_rehashed_relational_boundary_mutations_fail_closed(self) -> None:
        mutations = {
            "candidate changed": lambda value: value.update(
                verifier_candidate_sha256="0" * 64
            ),
            "verification rejected": lambda value: value[
                "provider_stages"
            ][1]["verification_receipt"].update(status="rejected"),
            "boundary omitted": lambda value: value["provider_stages"][1][
                "verification_receipt"
            ].update(verified_boundary_checks=[]),
            "hidden second dispatch": lambda value: value.update(
                deepseek_dispatches=2
            ),
            "prompt drift": lambda value: value.update(
                prompt_version="cera.deepseek_scene_composer_prompt.v9"
            ),
            "story retained": lambda value: value["provider_stages"][0][
                "provider_receipt"
            ].update(retains_story_prose=True),
        }
        for label, mutate in mutations.items():
            with (
                self.subTest(label=label),
                tempfile.TemporaryDirectory() as directory,
            ):
                payload = deepcopy(self.relational_boundary_payload)
                mutate(payload)
                raw = (
                    json.dumps(payload, sort_keys=True) + "\n"
                ).encode("utf-8")
                path = Path(directory) / "summary.json"
                path.write_bytes(raw)
                with self.assertRaises(ContractValidationError):
                    load_relational_boundary_qualification(
                        path,
                        expected_summary_sha256=bytes_sha256(raw),
                        expected_composer_prompt_version=(
                            "cera.deepseek_scene_composer_prompt.v10"
                        ),
                        expected_composer_dto_version=(
                            "cera.deepseek_composition_draft.v5"
                        ),
                        expected_composer_model="deepseek-v4-pro",
                        expected_verifier_model="gpt-5.6-sol",
                    )


if __name__ == "__main__":
    unittest.main()
