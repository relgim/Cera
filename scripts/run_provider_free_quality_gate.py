"""Run the provider-free CERA quality gate against this exact checkout.

This runner intentionally removes provider credentials and prepends the
checkout's ``src`` directory before importing CERA.  It exists because linked
worktrees may share a virtual environment whose editable-install pointer names
a different checkout.  Compilation is limited to Git-tracked Python sources;
the reported commit, tree, and source manifest bind the bytes that were checked.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = (ROOT / "src").resolve()
_GATE_ISOLATION_DIRECTORY = tempfile.TemporaryDirectory(prefix="cera-provider-free-quality-gate-")
_GATE_ISOLATION_ROOT = Path(_GATE_ISOLATION_DIRECTORY.name).resolve()
_UNAVAILABLE_SILLYTAVERN_ROOT = _GATE_ISOLATION_ROOT / "installed-sillytavern-unavailable"
_PROVIDER_ENVIRONMENT = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "CERA_PI_SCENE_TOKEN",
    "CERA_REQUEST_EVIDENCE_TOKEN",
)
_REMOVED_ISOLATION_ENVIRONMENT = ("CERA_V3_READINESS_EVIDENCE_DIRECTORY",)


def _qualified_test_ids(
    module: str,
    class_name: str,
    methods: tuple[str, ...],
) -> frozenset[str]:
    return frozenset(f"{module}.{class_name}.{method}" for method in methods)


_PINNED_QUALITY_TOOLS = {
    "mypy": "2.3.0",
    "ruff": "0.16.2",
}
_GENERATED_CONTRACT_CHECK = "scripts/generate_provider_stage_retry_contracts.py"
_ORDINARY_REVIEW_GENERATED_CONTRACT_CHECK = "scripts/generate_ordinary_review_contracts.py"
_ORDINARY_REVIEW_V3_GENERATED_CONTRACT_CHECK = "scripts/generate_ordinary_review_contracts_v3.py"
_GENERATED_CONTRACT_CHECKS = (
    ("provider-stage", _GENERATED_CONTRACT_CHECK),
    ("ordinary-review-v2", _ORDINARY_REVIEW_GENERATED_CONTRACT_CHECK),
    ("ordinary-review-v3", _ORDINARY_REVIEW_V3_GENERATED_CONTRACT_CHECK),
)
_EXPECTED_PROVIDER_STAGES = (
    "planner",
    "semantic_validator",
    "reader",
    "writer",
    "recorder",
    "adult_scene",
    "adult_filter",
)
_PROVIDER_STAGE_RETRY_SCHEMA_TARGETS = (
    "schemas/provider_stage_retry/v1/action.schema.json",
    "schemas/provider_stage_retry/v1/blocked_ambiguous.schema.json",
    "schemas/provider_stage_retry/v1/common.schema.json",
    "schemas/provider_stage_retry/v1/compatibility_adapter.schema.json",
    "schemas/provider_stage_retry/v1/exhausted.schema.json",
    "schemas/provider_stage_retry/v1/status.schema.json",
    "schemas/provider_stage_retry/v1/status_envelope.schema.json",
)
_ORDINARY_REVIEW_SCHEMA_TARGETS = (
    "schemas/pi_scene/ordinary_review/v2/common.schema.json",
    "schemas/pi_scene/ordinary_review/v2/review.schema.json",
    "schemas/pi_scene/ordinary_review/v2/review_checks.schema.json",
    "schemas/pi_scene/ordinary_review/v2/review_decision.schema.json",
    "schemas/pi_scene/ordinary_review/v2/review_lifecycle.schema.json",
    "schemas/pi_scene/ordinary_review/v3/common.schema.json",
    "schemas/pi_scene/ordinary_review/v3/review.schema.json",
    "schemas/pi_scene/ordinary_review/v3/review_checks.schema.json",
    "schemas/pi_scene/ordinary_review/v3/review_decision.schema.json",
    "schemas/pi_scene/ordinary_review/v3/review_lifecycle.schema.json",
)
_ORDINARY_REVIEW_GENERATED_TARGETS = (
    "docs/generated/ORDINARY_REVIEW_CONTRACTS_V2.md",
    "integrations/sillytavern/generated/ordinary-review-contracts-v2.mjs",
    "integrations/sillytavern/cera-review-proxy-plugin/generated/ordinary-review-contracts-v2.mjs",
    "integrations/sillytavern/creator-review-extension/generated/ordinary-review-contracts-v2.mjs",
    "src/cera/generated/ordinary_review_contracts_v2.py",
    "tests/fixtures/generated/ordinary_review_v2_negative.json",
    "tests/fixtures/generated/ordinary_review_v2_positive.json",
    "docs/generated/ORDINARY_REVIEW_CONTRACTS_V3.md",
    "integrations/sillytavern/generated/ordinary-review-contracts-v3.mjs",
    "integrations/sillytavern/cera-review-proxy-plugin/generated/ordinary-review-contracts-v3.mjs",
    "integrations/sillytavern/creator-review-extension/generated/ordinary-review-contracts-v3.mjs",
    "src/cera/generated/ordinary_review_contracts_v3.py",
    "tests/fixtures/generated/ordinary_review_v3_negative.json",
    "tests/fixtures/generated/ordinary_review_v3_positive.json",
)
_READER_SOURCE_TARGETS = (
    "src/cera/reader_validation/__init__.py",
    "src/cera/reader_validation/bridge.py",
    "src/cera/reader_validation/contracts.py",
    "src/cera/reader_validation/prompting.py",
    "src/cera/reader_validation/provider.py",
    "src/cera/reader_validation/schema.py",
    "src/cera/reader_validation/session.py",
)
_ORDINARY_REVIEW_LIFECYCLE_SOURCE_TARGETS = (
    "src/cera/pi_scene/http.py",
    "src/cera/pi_scene/http_contracts.py",
    "src/cera/pi_scene/ordinary_http.py",
    "src/cera/pi_scene/ordinary_rejection_policy.py",
    "src/cera/pi_scene/provider_stage_retry_executor.py",
    "src/cera/pi_scene/provider_stage_retry_http.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_custody.py",
    "src/cera/pi_scene/provider_stage_retry_runtime.py",
    "src/cera/pi_scene/request_binding.py",
    "src/cera/pi_scene/review_lifecycle.py",
    "src/cera/pi_scene/review_store.py",
    "src/cera/pi_scene/runtime.py",
    "src/cera/pi_scene/store.py",
)
# The large pre-existing filesystem store remains lint, compile, and behavior
# bound. Whole-file strict mypy would pull unrelated legacy typing debt into
# this lifecycle tranche; all new and tractable lifecycle modules stay strict.
_ORDINARY_REVIEW_LEGACY_TYPE_TARGETS = ("src/cera/pi_scene/store.py",)
_ORDINARY_REVIEW_TEST_MODULES = (
    "tests.test_ordinary_review_schema_generation",
    "tests.test_ordinary_review_schema_generation_v3",
    "tests.test_pi_scene_accepted_regenerate_runtime",
    "tests.test_pi_scene_provisional_review_lifecycle",
    "tests.test_pi_scene_reader_validation",
    "tests.test_pi_scene_semantic_runtime",
    "tests.test_provider_stage_retry_assembly",
    "tests.test_provider_stage_retry_http",
    "tests.test_provider_stage_retry_ordinary",
    "tests.test_provider_stage_retry_runtime",
)
_ORDINARY_REVIEW_LEGACY_TYPE_BEHAVIOR_BINDINGS = {
    "src/cera/pi_scene/store.py": (
        "tests.test_pi_scene_accepted_regenerate_runtime",
        "tests.test_pi_scene_provisional_review_lifecycle",
        "tests.test_pi_scene_semantic_runtime",
    )
}
_SILLYTAVERN_ORDINARY_REVIEW_ASSET_TARGETS = (
    "integrations/sillytavern/CERA_FULL_MODEL_COMPLETION_METADATA_BRIDGE.md",
    "integrations/sillytavern/README.md",
    "integrations/sillytavern/pi_scene_lean_v1_profile.json",
    "integrations/sillytavern/cera-review-proxy-plugin/index.js",
    "integrations/sillytavern/cera-review-proxy-plugin/test.mjs",
    "integrations/sillytavern/creator-review-extension/completion-metadata.js",
    "integrations/sillytavern/creator-review-extension/index.js",
    "integrations/sillytavern/creator-review-extension/manifest.json",
    "integrations/sillytavern/creator-review-extension/metadata-panel.test.mjs",
    "integrations/sillytavern/creator-review-extension/style.css",
)
_PROVIDER_STAGE_RETRY_FORMAT_TARGETS = (
    "scripts/run_pi_scene_full_model_qualification.py",
    "scripts/run_pi_scene_lean_server.py",
    "src/cera/adult_pipeline/pi_roles.py",
    "src/cera/adult_pipeline/pipeline.py",
    "src/cera/generated/__init__.py",
    "src/cera/generated/provider_stage_retry_contracts_v1.py",
    "src/cera/pi_scene/adult_stage_retry_integration.py",
    "src/cera/pi_scene/http.py",
    "src/cera/pi_scene/operation_ledger.py",
    "src/cera/pi_scene/provider_stage_retry.py",
    "src/cera/pi_scene/provider_stage_retry_adapters.py",
    "src/cera/pi_scene/provider_stage_retry_adult_actions.py",
    "src/cera/pi_scene/provider_stage_retry_assembly.py",
    "src/cera/pi_scene/provider_stage_retry_blob.py",
    "src/cera/pi_scene/provider_stage_retry_controller.py",
    "src/cera/pi_scene/provider_stage_retry_port.py",
    "src/cera/pi_scene/provider_stage_retry_runtime.py",
    "src/cera/pi_scene/provider_stage_retry_store.py",
    "src/cera/pi_scene/provider_stage_retry_scope.py",
    "src/cera/pi_scene/provider_stage_retry_packets.py",
    "src/cera/pi_scene/provider_stage_retry_executor.py",
    "src/cera/pi_scene/provider_stage_retry_http.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_custody.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_retrieval.py",
    "src/cera/pi_scene/qualification_isolation.py",
    "src/cera/pi_scene/runtime.py",
    "src/cera/providers/__init__.py",
    "src/cera/storage/migrations.py",
    "src/cera/storage/provider_stage_retry_store.py",
    "tests/test_provider_stage_retry_schema_generation.py",
    "tests/test_provider_stage_retry.py",
    "tests/test_provider_stage_retry_adapters.py",
    "tests/test_provider_stage_retry_sqlite.py",
    "tests/test_provider_stage_retry_scope.py",
    "tests/test_provider_stage_retry_packets.py",
    "tests/test_provider_stage_retry_runtime.py",
    "tests/test_provider_stage_retry_executor.py",
    "tests/test_provider_retryable_failure_metadata.py",
    "tests/test_adult_pipeline_pi_integration.py",
    "tests/test_pi_scene_adult_stage_retry_integration.py",
    "tests/test_pi_scene_full_model_qualification.py",
    "tests/test_provider_stage_retry_assembly.py",
    "tests/test_provider_stage_retry_http.py",
    "tests/test_provider_stage_retry_ordinary.py",
    "tests/test_provider_stage_retry_ordinary_custody.py",
    "tests/test_provider_stage_retry_ordinary_retrieval.py",
    "tests/test_sqlite_store.py",
)
# These pre-existing provider modules are intentionally not whole-file formatted
# as part of this narrow correction.  They remain exact lint, type, compile, and
# test dependencies; adding them to Ruff format would require a large unrelated
# legacy rewrite.
_PROVIDER_STAGE_RETRY_LEGACY_SOURCE_TARGETS = (
    "src/cera/pi_scene/pi_adapter.py",
    "src/cera/providers/codex.py",
    "src/cera/providers/codex_exec.py",
    "src/cera/providers/deepseek.py",
    "src/cera/providers/models.py",
)
_PROVIDER_STAGE_RETRY_TYPE_TARGETS = (
    _GENERATED_CONTRACT_CHECK,
    "scripts/run_pi_scene_full_model_qualification.py",
    "scripts/run_pi_scene_lean_server.py",
    "src/cera/adult_pipeline/pi_roles.py",
    "src/cera/adult_pipeline/pipeline.py",
    "src/cera/generated/__init__.py",
    "src/cera/generated/provider_stage_retry_contracts_v1.py",
    "src/cera/pi_scene/adult_stage_retry_integration.py",
    "src/cera/pi_scene/http.py",
    "src/cera/pi_scene/operation_ledger.py",
    "src/cera/pi_scene/provider_stage_retry.py",
    "src/cera/pi_scene/provider_stage_retry_adapters.py",
    "src/cera/pi_scene/provider_stage_retry_adult_actions.py",
    "src/cera/pi_scene/provider_stage_retry_assembly.py",
    "src/cera/pi_scene/provider_stage_retry_blob.py",
    "src/cera/pi_scene/provider_stage_retry_controller.py",
    "src/cera/pi_scene/provider_stage_retry_port.py",
    "src/cera/pi_scene/provider_stage_retry_runtime.py",
    "src/cera/pi_scene/provider_stage_retry_store.py",
    "src/cera/pi_scene/provider_stage_retry_scope.py",
    "src/cera/pi_scene/provider_stage_retry_packets.py",
    "src/cera/pi_scene/provider_stage_retry_executor.py",
    "src/cera/pi_scene/provider_stage_retry_http.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_custody.py",
    "src/cera/pi_scene/provider_stage_retry_ordinary_retrieval.py",
    "src/cera/pi_scene/qualification.py",
    "src/cera/pi_scene/qualification_isolation.py",
    "src/cera/pi_scene/runtime.py",
    "src/cera/providers/__init__.py",
    "src/cera/storage/migrations.py",
    "src/cera/storage/provider_stage_retry_store.py",
)
_PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS = _PROVIDER_STAGE_RETRY_LEGACY_SOURCE_TARGETS
_PROVIDER_STAGE_RETRY_TEST_MODULES = (
    "tests.test_adult_pipeline_pi_integration",
    "tests.test_pi_scene_adult_stage_retry_integration",
    "tests.test_pi_scene_full_model_launcher",
    "tests.test_pi_scene_http_session_review",
    "tests.test_pi_scene_lean_v1",
    "tests.test_provider_stage_retry_schema_generation",
    "tests.test_provider_stage_retry",
    "tests.test_provider_stage_retry_adapters",
    "tests.test_provider_stage_retry_assembly",
    "tests.test_provider_stage_retry_sqlite",
    "tests.test_provider_stage_retry_scope",
    "tests.test_provider_stage_retry_packets",
    "tests.test_provider_stage_retry_runtime",
    "tests.test_provider_stage_retry_executor",
    "tests.test_provider_stage_retry_http",
    "tests.test_provider_stage_retry_ordinary",
    "tests.test_provider_stage_retry_ordinary_custody",
    "tests.test_provider_stage_retry_ordinary_retrieval",
    "tests.test_provider_retryable_failure_metadata",
    "tests.test_pi_scene_full_model_qualification",
    "tests.test_sqlite_store",
)
_PROVIDER_STAGE_RETRY_COMPILE_TARGETS = (
    *_PROVIDER_STAGE_RETRY_TYPE_TARGETS,
    *_PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS,
    *(f"{module.replace('.', '/')}.py" for module in _PROVIDER_STAGE_RETRY_TEST_MODULES),
)
_ORDINARY_REVIEW_FORMAT_TARGETS = (
    _ORDINARY_REVIEW_GENERATED_CONTRACT_CHECK,
    _ORDINARY_REVIEW_V3_GENERATED_CONTRACT_CHECK,
    "src/cera/adult_pipeline/contracts.py",
    "src/cera/generated/ordinary_review_contracts_v2.py",
    "src/cera/generated/ordinary_review_contracts_v3.py",
    *_READER_SOURCE_TARGETS,
    "src/cera/pi_scene/review_lifecycle.py",
    "tests/test_ordinary_review_schema_generation.py",
    "tests/test_ordinary_review_schema_generation_v3.py",
    "tests/test_pi_scene_provisional_review_lifecycle.py",
    "tests/test_pi_scene_reader_validation.py",
)
_ORDINARY_REVIEW_LINT_TARGETS = (
    *_ORDINARY_REVIEW_FORMAT_TARGETS,
    *_ORDINARY_REVIEW_LIFECYCLE_SOURCE_TARGETS,
    "tests/test_pi_scene_accepted_regenerate_runtime.py",
    "tests/test_pi_scene_semantic_runtime.py",
    "tests/test_provider_stage_retry_assembly.py",
    "tests/test_provider_stage_retry_http.py",
    "tests/test_provider_stage_retry_ordinary.py",
    "tests/test_provider_stage_retry_runtime.py",
)
_ORDINARY_REVIEW_TYPE_TARGETS = (
    _ORDINARY_REVIEW_GENERATED_CONTRACT_CHECK,
    _ORDINARY_REVIEW_V3_GENERATED_CONTRACT_CHECK,
    "src/cera/adult_pipeline/contracts.py",
    "src/cera/generated/ordinary_review_contracts_v2.py",
    "src/cera/generated/ordinary_review_contracts_v3.py",
    *_READER_SOURCE_TARGETS,
    *(
        target
        for target in _ORDINARY_REVIEW_LIFECYCLE_SOURCE_TARGETS
        if target not in _ORDINARY_REVIEW_LEGACY_TYPE_TARGETS
    ),
)
_ORDINARY_REVIEW_COMPILE_TARGETS = (
    *_ORDINARY_REVIEW_TYPE_TARGETS,
    *_ORDINARY_REVIEW_LEGACY_TYPE_TARGETS,
    *(f"{module.replace('.', '/')}.py" for module in _ORDINARY_REVIEW_TEST_MODULES),
)
_SILLYTAVERN_RETRY_NODE_CHECK_TARGETS = (
    "integrations/sillytavern/generated/provider-stage-retry-contracts-v1.mjs",
    "integrations/sillytavern/cera-review-proxy-plugin/generated/provider-stage-retry-contracts-v1.mjs",
    "integrations/sillytavern/cera-review-proxy-plugin/index.js",
    "integrations/sillytavern/cera-review-proxy-plugin/test.mjs",
    "integrations/sillytavern/creator-review-extension/generated/provider-stage-retry-contracts-v1.mjs",
    "integrations/sillytavern/creator-review-extension/index.js",
    "integrations/sillytavern/creator-review-extension/review-actions.js",
    "integrations/sillytavern/creator-review-extension/metadata-panel.test.mjs",
)
_SILLYTAVERN_ORDINARY_REVIEW_NODE_CHECK_TARGETS = (
    "integrations/sillytavern/generated/ordinary-review-contracts-v2.mjs",
    "integrations/sillytavern/generated/ordinary-review-contracts-v3.mjs",
    "integrations/sillytavern/cera-review-proxy-plugin/generated/ordinary-review-contracts-v2.mjs",
    "integrations/sillytavern/cera-review-proxy-plugin/generated/ordinary-review-contracts-v3.mjs",
    "integrations/sillytavern/creator-review-extension/generated/ordinary-review-contracts-v2.mjs",
    "integrations/sillytavern/creator-review-extension/generated/ordinary-review-contracts-v3.mjs",
    "integrations/sillytavern/creator-review-extension/completion-metadata.js",
)
_SILLYTAVERN_NODE_CHECK_TARGETS = tuple(
    dict.fromkeys(
        (
            *_SILLYTAVERN_RETRY_NODE_CHECK_TARGETS,
            *_SILLYTAVERN_ORDINARY_REVIEW_NODE_CHECK_TARGETS,
        )
    )
)
_SILLYTAVERN_RETRY_NODE_TEST_TARGETS = (
    "integrations/sillytavern/cera-review-proxy-plugin/test.mjs",
    "integrations/sillytavern/creator-review-extension/metadata-panel.test.mjs",
)
# These exact tests cross the prequalification boundary through direct reads,
# class setup, helper call chains, real databases, fixed-port services, or
# frozen external evidence. Repository/staged source behavior remains active.
_DIRECT_INSTALLED_ROOT_TESTS = _qualified_test_ids(
    "tests.test_sillytavern_installation_contract",
    "SillyTavernInstallationContractTests",
    (
        "test_installed_creator_review_extension_matches_repository_source",
        "test_installed_loopback_relay_matches_repository_source",
        "test_sillytavern_enables_local_plugins_without_auto_update",
        "test_openai_bridge_routes_all_cera_metadata_through_closed_projection",
        "test_cera_controls_cross_the_server_bridge",
        "test_cera_controls_cross_the_client_bridge",
        "test_provisional_messages_are_excluded_from_exports",
    ),
)
_CONTINUOUS_MANUAL_LIFECYCLE_TESTS = _qualified_test_ids(
    "tests.test_sillytavern_continuous_manual",
    "ContinuousManualLifecycleTests",
    (
        "test_real_process_start_health_restart_and_isolation",
        "test_unresolved_review_restart_requires_exact_recovered_decline",
        "test_cli_review_lookup_rejects_a_different_stopped_root",
        "test_restart_reconciles_exact_accept_after_manual_terminalization_cut",
        "test_unproven_decision_intent_disables_actions_and_restart_fails_closed",
        "test_restart_finishes_terminal_review_record_after_state_write_cut",
        "test_restart_reconciles_exact_decline_after_manual_terminalization_cut",
        "test_dead_process_and_stop_request_recovery_is_identity_bound",
        "test_stop_terminalization_failure_is_frozen_without_raw_error_text",
        "test_execution_manifest_and_process_tampering_fail_closed",
        "test_overlong_windows_root_fails_before_state_creation",
    ),
)
_CONTINUOUS_MANUAL_READINESS_TESTS = _qualified_test_ids(
    "tests.test_sillytavern_continuous_manual",
    "ContinuousManualReadinessTests",
    (
        "test_manifest_preserves_run001_debit_and_fresh_v2_identities",
        "test_production_shaped_two_run_route_passes_locally_and_resets",
    ),
)
_CONTINUOUS_V2_EXTERNAL_TESTS = _qualified_test_ids(
    "tests.test_continuous_v2_execution_authority",
    "ContinuousV2ExecutionAuthorityTests",
    ("test_provider_manual_fake_construction_has_zero_external_calls",),
)
_CONTINUOUS_V3_EXTERNAL_TESTS = _qualified_test_ids(
    "tests.test_continuous_v3_executable_readiness",
    "ContinuousV3ExecutableReadinessTests",
    (
        "test_actual_v2_parent_child_failure_recovery_and_long_root",
        "test_provider_backed_manual_actual_process_fake_ports",
        "test_provider_backed_pending_decision_recovery_fails_closed",
    ),
)
_FROZEN_EXTERNAL_EVIDENCE_TESTS = (
    _qualified_test_ids(
        "tests.test_queue0056_frozen_validator_regression",
        "Queue0056FrozenValidatorRegressionTests",
        (
            "test_candidate_one_reciprocal_gaze_remains_a_hard_ted_assertion",
            "test_candidate_two_relational_color_is_soft_and_noncanonical",
            "test_candidate_three_silence_is_source_grounded_public_state",
        ),
    )
    | _qualified_test_ids(
        "tests.test_validator_simplification_source_grounded_state",
        "ValidatorSimplificationSourceGroundedStateTests",
        (
            "test_optional_incidental_prop_offer_is_soft_and_nonpersistent",
            "test_exact_frozen_candidate_exercises_both_corrected_classes",
        ),
    )
    | _qualified_test_ids(
        "tests.test_cera_runtime_model_v3",
        "RuntimeModelV3ReaderAndDocumentationTests",
        ("test_frozen_v7_evidence_and_deferred_fallback_invariants",),
    )
    | _qualified_test_ids(
        "tests.test_runtime_model_v3_stage4_harness_identity_contract",
        "RuntimeModelV3Stage4HarnessIdentityContractTests",
        ("test_historical_runner_remains_immutable_evidence",),
    )
    | _qualified_test_ids(
        "tests.test_runtime_model_v3_reader_fixtures",
        "RuntimeModelV3ReaderFixtureAuthorityTests",
        ("test_historical_failed_fixture_source_is_not_rewritten",),
    )
    | _qualified_test_ids(
        "tests.test_runtime_model_v3_stage4_harness_contract",
        "RuntimeModelV3Stage4HarnessContractTests",
        ("test_historical_runner_is_preserved_and_identifies_drift",),
    )
)
_DEFERRED_PREQUALIFICATION_TESTS: frozenset[str] = frozenset().union(
    _DIRECT_INSTALLED_ROOT_TESTS,
    _CONTINUOUS_MANUAL_LIFECYCLE_TESTS,
    _CONTINUOUS_MANUAL_READINESS_TESTS,
    _CONTINUOUS_V2_EXTERNAL_TESTS,
    _CONTINUOUS_V3_EXTERNAL_TESTS,
    _FROZEN_EXTERNAL_EVIDENCE_TESTS,
)
_AUDITED_WHOLE_CLASS_TESTS = (
    (
        "tests/test_sillytavern_continuous_manual.py",
        "tests.test_sillytavern_continuous_manual",
        "ContinuousManualLifecycleTests",
        _CONTINUOUS_MANUAL_LIFECYCLE_TESTS,
    ),
    (
        "tests/test_sillytavern_continuous_manual.py",
        "tests.test_sillytavern_continuous_manual",
        "ContinuousManualReadinessTests",
        _CONTINUOUS_MANUAL_READINESS_TESTS,
    ),
    (
        "tests/test_continuous_v3_executable_readiness.py",
        "tests.test_continuous_v3_executable_readiness",
        "ContinuousV3ExecutableReadinessTests",
        _CONTINUOUS_V3_EXTERNAL_TESTS,
    ),
)
_AUDITED_EXTERNAL_CUSTODY_SOURCE_SHA256 = {
    "scripts/run_cera_sillytavern_continuous_manual.py": (
        "476f84ce5dd4a57ea7a93df0e4d89eb4bda51898559412761a7e0bd9af376208"
    ),
    "scripts/run_cera_sillytavern_continuous_manual_readiness.py": (
        "e61b593047d68a38640d386921be498331ead62387e2d122302bd3fe6a9aca77"
    ),
    "tests/test_sillytavern_installation_contract.py": (
        "d309bc8491666274c745db2f4a60b23835b06b0b9a59f37bc7d4136427316a93"
    ),
    "tests/test_sillytavern_continuous_manual.py": (
        "f3ef281b018bb2cd0b4e596ba4eb5ee99049325c13e1032ba5636b8720a1d5e1"
    ),
    "tests/test_continuous_v2_execution_authority.py": (
        "f1e64f2146810bafac83979f06bcd3441e695e0672fb2f50059464554619ff8d"
    ),
    "tests/test_continuous_v3_executable_readiness.py": (
        "497ae959bf92092c4f817cb8b5db09e96954a44250d179682c4de598c1072531"
    ),
    "tests/test_queue0056_frozen_validator_regression.py": (
        "29e1cda795f3e39389c9c99f6feb44aec22561d9095d624816401d4b1a2e029d"
    ),
    "tests/test_validator_simplification_source_grounded_state.py": (
        "ea186653a37db83cea9a0a2fc7af08bfe5e9ddf885aed85277bdc7f8159575bc"
    ),
    "tests/test_cera_runtime_model_v3.py": (
        "7366180b8a300531697f27c0bde39010fb86ae4435817d96adc68137fc3b405c"
    ),
    "tests/test_runtime_model_v3_stage4_harness_identity_contract.py": (
        "cb0c2dab11d37a1b4fda6c8683ea383d6c060bb5f1424890ad2707cb8a9bb82d"
    ),
    "tests/test_runtime_model_v3_reader_fixtures.py": (
        "3e85217f96858f7964b237308a598e8b4d4bd36b8d45480d429dc061e1feaf9a"
    ),
    "tests/test_runtime_model_v3_stage4_harness_contract.py": (
        "4c718f11b0083f8368fad73be5d85d16520197d1f18d7b94c69d55330cb59d0a"
    ),
}
_AUDITED_HAZARD_CALL_NAMES = frozenset(
    {
        "_authority_bindings",
        "_export_readiness_evidence",
        "isolation_inventory",
        "readiness_manifest",
        "reset_manual_root",
        "run_readiness",
        "start_manual_root",
        "stop_manual_root",
        "verify_isolation",
    }
)
_AUDITED_TEST_LIFECYCLE_METHODS = frozenset(
    {
        "asyncSetUp",
        "asyncTearDown",
        "setUp",
        "setUpClass",
        "tearDown",
        "tearDownClass",
    }
)
_FORMAT_TARGETS = tuple(
    dict.fromkeys(
        (
            _GENERATED_CONTRACT_CHECK,
            "scripts/run_provider_free_quality_gate.py",
            "src/cera/provider_dispatch_guard.py",
            "src/cera/cognition",
            "src/cera/pi_scene/qualification.py",
            "src/cera/semantic_validation",
            "tests/test_cognition_contracts.py",
            "tests/test_cognition_provider.py",
            "tests/test_pi_scene_cognition_authority.py",
            "tests/test_provider_dispatch_guard.py",
            "tests/test_provider_free_quality_gate.py",
            "tests/test_semantic_validation_contracts.py",
            "tests/test_semantic_validation_session.py",
            "tests/test_phase1_provider_boundaries.py",
            *_PROVIDER_STAGE_RETRY_FORMAT_TARGETS,
            *_ORDINARY_REVIEW_FORMAT_TARGETS,
        )
    )
)
_LINT_TARGETS = tuple(
    dict.fromkeys(
        (
            *_FORMAT_TARGETS,
            *_PROVIDER_STAGE_RETRY_LEGACY_SOURCE_TARGETS,
            *_ORDINARY_REVIEW_LINT_TARGETS,
        )
    )
)
_TYPE_TARGETS = tuple(
    dict.fromkeys(
        (
            "scripts/run_provider_free_quality_gate.py",
            "src/cera/provider_dispatch_guard.py",
            "src/cera/cognition",
            "src/cera/semantic_validation",
            *_PROVIDER_STAGE_RETRY_TYPE_TARGETS,
            *_ORDINARY_REVIEW_TYPE_TARGETS,
        )
    )
)


def _assert_isolation_sentinel_absent() -> str:
    if not _GATE_ISOLATION_ROOT.is_dir():
        raise RuntimeError("provider-free gate isolation directory is unavailable")
    if _UNAVAILABLE_SILLYTAVERN_ROOT.exists() or _UNAVAILABLE_SILLYTAVERN_ROOT.is_symlink():
        raise RuntimeError("provider-free SillyTavern sentinel unexpectedly exists")
    return str(_UNAVAILABLE_SILLYTAVERN_ROOT)


def _test_class_node(relative_path: str, class_name: str) -> ast.ClassDef:
    path = (ROOT / relative_path).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise RuntimeError(f"audited test source escaped checkout: {relative_path}") from exc
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    ]
    if len(matches) != 1:
        raise RuntimeError(f"audited test class changed identity: {relative_path}:{class_name}")
    return matches[0]


def _class_test_ids(
    relative_path: str,
    module: str,
    class_name: str,
) -> frozenset[str]:
    class_node = _test_class_node(relative_path, class_name)
    methods = tuple(
        node.name
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    )
    return _qualified_test_ids(module, class_name, methods)


def _calls_audited_hazard(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        function = child.func
        if isinstance(function, ast.Name) and function.id in _AUDITED_HAZARD_CALL_NAMES:
            return True
        if isinstance(function, ast.Attribute) and function.attr in _AUDITED_HAZARD_CALL_NAMES:
            return True
    return False


def _direct_hazard_test_ids() -> frozenset[str]:
    hazardous_ids: set[str] = set()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = f"tests.{path.stem}"
        for class_node in (node for node in tree.body if isinstance(node, ast.ClassDef)):
            methods = tuple(
                node
                for node in class_node.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            test_methods = tuple(method for method in methods if method.name.startswith("test_"))
            lifecycle_is_hazardous = any(
                method.name in _AUDITED_TEST_LIFECYCLE_METHODS and _calls_audited_hazard(method)
                for method in methods
            )
            if lifecycle_is_hazardous:
                hazardous_ids.update(
                    f"{module}.{class_node.name}.{method.name}" for method in test_methods
                )
                continue
            hazardous_ids.update(
                f"{module}.{class_node.name}.{method.name}"
                for method in test_methods
                if _calls_audited_hazard(method)
            )
    return frozenset(hazardous_ids)


def _assert_audited_hazard_manifest() -> None:
    groups = (
        _DIRECT_INSTALLED_ROOT_TESTS,
        _CONTINUOUS_MANUAL_LIFECYCLE_TESTS,
        _CONTINUOUS_MANUAL_READINESS_TESTS,
        _CONTINUOUS_V2_EXTERNAL_TESTS,
        _CONTINUOUS_V3_EXTERNAL_TESTS,
        _FROZEN_EXTERNAL_EVIDENCE_TESTS,
    )
    if tuple(map(len, groups)) != (7, 11, 2, 1, 3, 9):
        raise RuntimeError("audited prequalification hazard group changed shape")
    if sum(map(len, groups)) != len(_DEFERRED_PREQUALIFICATION_TESTS):
        raise RuntimeError("audited prequalification hazard groups overlap")
    if len(_DEFERRED_PREQUALIFICATION_TESTS) != 33:
        raise RuntimeError("audited prequalification hazard manifest changed size")
    for relative_path, module, class_name, expected in _AUDITED_WHOLE_CLASS_TESTS:
        actual = _class_test_ids(relative_path, module, class_name)
        if actual != expected:
            raise RuntimeError(
                f"audited external-custody test class changed: {module}.{class_name}"
            )
    installation_class = _test_class_node(
        "tests/test_sillytavern_installation_contract.py",
        "SillyTavernInstallationContractTests",
    )
    direct_installed_readers = _qualified_test_ids(
        "tests.test_sillytavern_installation_contract",
        "SillyTavernInstallationContractTests",
        tuple(
            node.name
            for node in installation_class.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
            and any(
                isinstance(child, ast.Name) and child.id == "SILLYTAVERN_ROOT"
                for child in ast.walk(node)
            )
        ),
    )
    if direct_installed_readers != _DIRECT_INSTALLED_ROOT_TESTS:
        raise RuntimeError("direct installed-SillyTavern reader manifest changed")
    direct_hazard_ids = _direct_hazard_test_ids()
    unexpected_hazard_ids = direct_hazard_ids - _DEFERRED_PREQUALIFICATION_TESTS
    if unexpected_hazard_ids:
        raise RuntimeError(
            "prequalification test directly reaches external custody: "
            + ", ".join(sorted(unexpected_hazard_ids))
        )
    for relative_path, expected_sha256 in sorted(_AUDITED_EXTERNAL_CUSTODY_SOURCE_SHA256.items()):
        path = ROOT / relative_path
        if not path.is_file():
            raise RuntimeError(f"audited external-custody source is unavailable: {relative_path}")
        actual_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_sha256 != expected_sha256:
            raise RuntimeError(
                f"audited external-custody source changed before qualification: {relative_path}"
            )


def _configure_checkout() -> Path:
    sys.dont_write_bytecode = True
    for name in _PROVIDER_ENVIRONMENT:
        os.environ.pop(name, None)
    for name in _REMOVED_ISOLATION_ENVIRONMENT:
        os.environ.pop(name, None)
    os.environ["CERA_SILLYTAVERN_ROOT"] = _assert_isolation_sentinel_absent()
    _assert_audited_hazard_manifest()
    # Set the kill switch before importing any CERA module.  A package import
    # must never get an opportunity to dispatch while the gate is bootstrapping.
    os.environ["CERA_PROVIDER_DISPATCH_DISABLED"] = "1"
    source_text = str(SOURCE_ROOT)
    if not sys.path or Path(sys.path[0]).resolve() != SOURCE_ROOT:
        sys.path.insert(0, source_text)
    root_text = str(ROOT)
    if not any(Path(value or os.getcwd()).resolve() == ROOT for value in sys.path):
        sys.path.insert(1, root_text)
    import cera
    from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV

    if PROVIDER_DISPATCH_DISABLED_ENV != "CERA_PROVIDER_DISPATCH_DISABLED":
        raise RuntimeError("provider dispatch guard environment identity changed")

    package_root = Path(cera.__file__).resolve().parent
    expected = SOURCE_ROOT / "cera"
    if package_root != expected:
        raise RuntimeError(
            "quality gate imported CERA from the wrong checkout: "
            f"expected={expected}, actual={package_root}"
        )
    return package_root


def _assert_provider_stage_closure() -> None:
    from cera.pi_scene.provider_stage_retry import ProviderStage

    runtime_stages = tuple(stage.value for stage in ProviderStage)
    if runtime_stages != _EXPECTED_PROVIDER_STAGES:
        raise RuntimeError(
            "provider-stage runtime closure changed: "
            f"expected={_EXPECTED_PROVIDER_STAGES}, actual={runtime_stages}"
        )
    common_schema = json.loads(
        (ROOT / "schemas/provider_stage_retry/v1/common.schema.json").read_text(encoding="utf-8")
    )
    schema_stages = tuple(common_schema["$defs"]["stage"]["enum"])
    if schema_stages != _EXPECTED_PROVIDER_STAGES:
        raise RuntimeError(
            "provider-stage schema closure changed: "
            f"expected={_EXPECTED_PROVIDER_STAGES}, actual={schema_stages}"
        )


def _assert_reader_medium_profile() -> None:
    from cera.reader_validation import SOL_READER_PROFILE, sol_reader_route

    route = sol_reader_route()
    actual = (
        SOL_READER_PROFILE,
        route.model_name,
        route.reasoning_effort,
        route.automatic_retry_count,
        route.fallback_enabled,
        route.production_enabled,
    )
    expected = (
        "cera.reader_validation.sol_medium.v1",
        "gpt-5.6-sol",
        "medium",
        0,
        False,
        False,
    )
    if actual != expected:
        raise RuntimeError(f"Reader provider profile changed: expected={expected}, actual={actual}")


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        diagnostic = " ".join(completed.stderr.split())[:400] or "unknown git error"
        raise RuntimeError(f"quality gate could not inspect Git checkout: {diagnostic}")
    return completed.stdout.strip()


def _checkout_identity(
    *,
    expected_commit: str | None = None,
    expected_tree: str | None = None,
    require_clean: bool = False,
) -> tuple[str, str, bool]:
    if (expected_commit is None) != (expected_tree is None):
        raise RuntimeError("quality gate commit and tree pins must be supplied together")
    for value, label in (
        (expected_commit, "commit"),
        (expected_tree, "tree"),
    ):
        if value is not None and re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise RuntimeError(f"quality gate {label} pin must be a full lowercase Git hash")
    checkout_root = Path(_git("rev-parse", "--show-toplevel")).resolve()
    if checkout_root != ROOT:
        raise RuntimeError(
            "quality gate resolved a different Git checkout: "
            f"expected={ROOT}, actual={checkout_root}"
        )
    commit = _git("rev-parse", "HEAD")
    tree = _git("rev-parse", "HEAD^{tree}")
    if expected_commit is not None and commit != expected_commit:
        raise RuntimeError(
            f"quality gate commit pin mismatch: expected={expected_commit}, actual={commit}"
        )
    if expected_tree is not None and tree != expected_tree:
        raise RuntimeError(
            f"quality gate tree pin mismatch: expected={expected_tree}, actual={tree}"
        )
    clean = not _git("status", "--porcelain=v1", "--untracked-files=all")
    if require_clean and not clean:
        raise RuntimeError("quality gate requires a clean checkout")
    return commit, tree, clean


def _tracked_python_paths() -> tuple[Path, ...]:
    completed = subprocess.run(
        ("git", "-C", str(ROOT), "ls-files", "-z", "--", "*.py"),
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        diagnostic = completed.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(
            "quality gate could not enumerate tracked Python sources: "
            f"{' '.join(diagnostic.split())[:400]}"
        )
    paths: list[Path] = []
    for raw_relative in completed.stdout.split(b"\0"):
        if not raw_relative:
            continue
        relative = Path(raw_relative.decode("utf-8"))
        path = (ROOT / relative).resolve()
        try:
            path.relative_to(ROOT)
        except ValueError as exc:
            raise RuntimeError(f"tracked Python source escaped checkout: {relative}") from exc
        if not path.is_file():
            raise RuntimeError(f"tracked Python source is unavailable: {relative}")
        paths.append(path)
    if not paths:
        raise RuntimeError("quality gate found no tracked Python sources")
    return tuple(sorted(paths))


def _tracked_python_sources() -> tuple[tuple[Path, bytes], ...]:
    return tuple((path, path.read_bytes()) for path in _tracked_python_paths())


def _assert_required_retry_targets(
    sources: tuple[tuple[Path, bytes], ...],
) -> None:
    missing_files = sorted(
        target
        for target in (
            *_PROVIDER_STAGE_RETRY_SCHEMA_TARGETS,
            *_ORDINARY_REVIEW_SCHEMA_TARGETS,
            *_ORDINARY_REVIEW_GENERATED_TARGETS,
            *_SILLYTAVERN_NODE_CHECK_TARGETS,
            *_SILLYTAVERN_ORDINARY_REVIEW_ASSET_TARGETS,
        )
        if not (ROOT / target).is_file()
    )
    if missing_files:
        raise RuntimeError(
            "provider-free quality gate targets are unavailable: " + ", ".join(missing_files)
        )
    compiled_targets = {path.relative_to(ROOT).as_posix() for path, _ in sources}
    required_python = {
        *_PROVIDER_STAGE_RETRY_COMPILE_TARGETS,
        *_ORDINARY_REVIEW_COMPILE_TARGETS,
    }
    missing_python = sorted(required_python - compiled_targets)
    if missing_python:
        raise RuntimeError(
            "provider-free quality gate Python targets are not Git-tracked for compilation: "
            + ", ".join(missing_python)
        )


def _compile_python(
    sources: tuple[tuple[Path, bytes], ...] | None = None,
) -> int:
    snapshot = _tracked_python_sources() if sources is None else sources
    for path, content in snapshot:
        source = content.decode("utf-8-sig")
        compile(source, str(path), "exec", dont_inherit=True)
    return len(snapshot)


def _tracked_python_manifest_sha256(
    sources: tuple[tuple[Path, bytes], ...] | None = None,
) -> str:
    digest = hashlib.sha256()
    snapshot = _tracked_python_sources() if sources is None else sources
    for path, content in snapshot:
        relative = path.relative_to(ROOT).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_final_checkout_identity(
    *,
    commit: str,
    tree: str,
    clean: bool,
    manifest_sha256: str,
    pyproject_sha256: str,
    require_clean: bool,
) -> None:
    _assert_isolation_sentinel_absent()
    final_commit, final_tree, final_clean = _checkout_identity(
        expected_commit=commit,
        expected_tree=tree,
        require_clean=require_clean,
    )
    final_sources = _tracked_python_sources()
    final_manifest_sha256 = _tracked_python_manifest_sha256(final_sources)
    final_pyproject_sha256 = _file_sha256(ROOT / "pyproject.toml")
    if (final_commit, final_tree, final_clean) != (commit, tree, clean):
        raise RuntimeError("quality gate checkout identity changed while checks were running")
    if final_manifest_sha256 != manifest_sha256:
        raise RuntimeError("tracked Python bytes changed while quality checks were running")
    if final_pyproject_sha256 != pyproject_sha256:
        raise RuntimeError("quality-tool configuration changed while checks were running")
    _assert_isolation_sentinel_absent()


def _assert_quality_tool_versions() -> None:
    for distribution, expected in sorted(_PINNED_QUALITY_TOOLS.items()):
        try:
            actual = version(distribution)
        except PackageNotFoundError as exc:
            raise RuntimeError(
                f"missing pinned quality tool {distribution}=={expected}; "
                "install the project's dev extra"
            ) from exc
        if actual != expected:
            raise RuntimeError(
                f"quality tool version mismatch for {distribution}: "
                f"expected={expected}, actual={actual}"
            )


def _run_checked(command: tuple[str, ...], *, label: str) -> None:
    environment = dict(os.environ)
    for name in _PROVIDER_ENVIRONMENT:
        environment.pop(name, None)
    for name in _REMOVED_ISOLATION_ENVIRONMENT:
        environment.pop(name, None)
    environment["CERA_SILLYTAVERN_ROOT"] = _assert_isolation_sentinel_absent()
    environment["CERA_PROVIDER_DISPATCH_DISABLED"] = "1"
    existing_pythonpath = environment.get("PYTHONPATH")
    python_paths = (str(SOURCE_ROOT), str(ROOT))
    environment["PYTHONPATH"] = os.pathsep.join(
        (*python_paths, *((existing_pythonpath,) if existing_pythonpath else ()))
    )
    completed = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        env=environment,
    )
    _assert_isolation_sentinel_absent()
    if completed.returncode:
        raise RuntimeError(f"{label} failed with exit code {completed.returncode}")


def _run_quality_tools() -> None:
    _assert_quality_tool_versions()
    for family, target in _GENERATED_CONTRACT_CHECKS:
        _run_checked(
            (
                sys.executable,
                str(ROOT / target),
                "--check",
            ),
            label=f"{family} generated contract drift check",
        )
    _run_checked(
        (
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "--config",
            str(ROOT / "pyproject.toml"),
            *(_FORMAT_TARGETS),
        ),
        label="Ruff format check",
    )
    _run_checked(
        (
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(ROOT / "pyproject.toml"),
            *(_LINT_TARGETS),
        ),
        label="Ruff lint check",
    )
    _run_checked(
        (
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(ROOT / "pyproject.toml"),
            *(_TYPE_TARGETS),
        ),
        label="mypy type check",
    )
    _run_checked(
        (
            sys.executable,
            "-m",
            "mypy",
            "--config-file",
            str(ROOT / "pyproject.toml"),
            "--allow-redefinition",
            *_PROVIDER_STAGE_RETRY_LEGACY_TYPE_TARGETS,
        ),
        label="mypy legacy provider Retry dependency type check",
    )
    for target in _SILLYTAVERN_NODE_CHECK_TARGETS:
        _run_checked(
            ("node", "--check", target),
            label=f"SillyTavern review syntax check ({target})",
        )
    _run_checked(
        (
            "node",
            "--test",
            *_SILLYTAVERN_RETRY_NODE_TEST_TARGETS,
        ),
        label="SillyTavern review tests",
    )


def _test_cases(suite: unittest.TestSuite) -> tuple[unittest.TestCase, ...]:
    cases: list[unittest.TestCase] = []
    for value in suite:
        if isinstance(value, unittest.TestSuite):
            cases.extend(_test_cases(value))
        elif isinstance(value, unittest.TestCase):
            cases.append(value)
        else:
            raise RuntimeError("quality gate discovered an unknown unittest value")
    return tuple(cases)


def _suite(names: tuple[str, ...]) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    if names:
        unsafe_selectors = {
            name
            for name in names
            if any(
                deferred == name or deferred.startswith(f"{name}.")
                for deferred in _DEFERRED_PREQUALIFICATION_TESTS
            )
        }
        if unsafe_selectors:
            raise RuntimeError(
                "prequalification gate cannot load external-custody selectors: "
                + ", ".join(sorted(unsafe_selectors))
            )
        selected = loader.loadTestsFromNames(names)
        deferred = {
            value.id()
            for value in _test_cases(selected)
            if value.id() in _DEFERRED_PREQUALIFICATION_TESTS
        }
        if deferred:
            raise RuntimeError(
                "prequalification gate cannot select external-custody tests: "
                + ", ".join(sorted(deferred))
            )
        return selected
    discovered = _test_cases(loader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT)))
    discovered_ids = {value.id() for value in discovered}
    missing = _DEFERRED_PREQUALIFICATION_TESTS - discovered_ids
    if missing:
        raise RuntimeError(
            "deferred installed-SillyTavern test identity changed: " + ", ".join(sorted(missing))
        )
    return unittest.TestSuite(
        value for value in discovered if value.id() not in _DEFERRED_PREQUALIFICATION_TESTS
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "tests",
        nargs="*",
        help="Optional dotted unittest names; omit for complete discovery.",
    )
    parser.add_argument(
        "--compile-only",
        action="store_true",
        help="Compile tracked checkout Python without running tests.",
    )
    parser.add_argument(
        "--expected-commit",
        help="Fail unless HEAD is this exact full commit hash.",
    )
    parser.add_argument(
        "--expected-tree",
        help="Fail unless HEAD has this exact full tree hash.",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="Development-only: allow changes outside the pinned HEAD tree.",
    )
    arguments = parser.parse_args(argv)
    package_root = _configure_checkout()
    _assert_provider_stage_closure()
    _assert_reader_medium_profile()
    commit, tree, clean = _checkout_identity(
        expected_commit=arguments.expected_commit,
        expected_tree=arguments.expected_tree,
        require_clean=not arguments.allow_dirty,
    )
    sources = _tracked_python_sources()
    _assert_required_retry_targets(sources)
    compiled = _compile_python(sources)
    manifest_sha256 = _tracked_python_manifest_sha256(sources)
    pyproject_sha256 = _file_sha256(ROOT / "pyproject.toml")
    from cera.active_runtime import ACTIVE_RUNTIME_PROFILE

    print(f"checkout={ROOT}")
    print(f"checkout_head_commit={commit}")
    print(f"checkout_head_tree={tree}")
    print(f"checkout_clean={str(clean).lower()}")
    print(f"python_runtime={sys.version.split()[0]}")
    print(f"cera_import={package_root}")
    print(f"active_runtime_profile_id={ACTIVE_RUNTIME_PROFILE.profile_id}")
    print(f"active_runtime_profile_sha256={ACTIVE_RUNTIME_PROFILE.profile_sha256}")
    print(f"compiled_python_files={compiled}")
    print(f"tracked_python_manifest_sha256={manifest_sha256}")
    print(f"pyproject_sha256={pyproject_sha256}")
    print("provider_credentials=removed")
    print("provider_dispatch_guard=enabled")
    print("installed_sillytavern_root=nonexistent_gate_sentinel")
    print("v3_readiness_evidence_export=disabled")
    print("provider_stage_closure=seven")
    print("reader_profile=sol_medium")
    if arguments.compile_only:
        _assert_final_checkout_identity(
            commit=commit,
            tree=tree,
            clean=clean,
            manifest_sha256=manifest_sha256,
            pyproject_sha256=pyproject_sha256,
            require_clean=not arguments.allow_dirty,
        )
        return 0
    _run_quality_tools()
    print("generated_contract_checks=passed")
    print("ruff_format=passed")
    print("ruff_lint=passed")
    print("mypy=passed")
    print("sillytavern_retry_syntax=passed")
    print("sillytavern_retry_tests=passed")
    if not arguments.tests:
        print("external_custody_and_service_checks=deferred_until_disposable_qualification")
    result = unittest.TextTestRunner(verbosity=2).run(_suite(tuple(arguments.tests)))
    if not result.wasSuccessful():
        return 1
    _assert_final_checkout_identity(
        commit=commit,
        tree=tree,
        clean=clean,
        manifest_sha256=manifest_sha256,
        pyproject_sha256=pyproject_sha256,
        require_clean=not arguments.allow_dirty,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
