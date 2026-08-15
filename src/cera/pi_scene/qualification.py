"""Frozen, fail-closed qualification contracts for the full-model CERA route.

The qualification runner owns no story logic and never automatically crosses a
provider boundary. It submits two ordered retained-session campaigns, verifies
each committed HTTP projection, and reconciles it with the append-only Codex
and DeepSeek ledgers. A generated provider-stage status envelope from any of
the seven live stages may expose an exact backend-issued manual ``provider_retry``,
``resume_prepared``, or ``repair_recording`` action. The latter two have
separate control budgets; Recorder repair creates one fresh successor chain and
can never recurse. Automatic behavior is GET-only. After one exact external
per-action authorization, the runner revalidates by GET, consumes the approval,
POSTs once, and treats authenticated GET as the only later authority. Semantic
Regenerate and Replan remain separate actions and counters. Exact ordinary or
adult prose is never copied into qualification evidence.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil
from pathlib import Path
from statistics import median
from types import MappingProxyType
from typing import Any, Protocol, cast

from cera.errors import ContractValidationError, StateConflictError
from cera.generated.ordinary_review_contracts_v3 import (
    OrdinaryReviewDecisionV3,
    OrdinaryReviewLifecycleV2,
    OrdinaryReviewV3,
    validate_ordinary_review_decision_v3,
    validate_ordinary_review_lifecycle_v2,
    validate_ordinary_review_v3,
)
from cera.generated.provider_stage_retry_contracts_v1 import (
    ProviderStageRetryStatusEnvelopeV1,
    validate_provider_stage_retry_action_v1,
    validate_provider_stage_retry_status_envelope_v1,
)
from cera.provider_dispatch_guard import provider_dispatch_disabled
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
)

from .http_contracts import PI_SCENE_AUTO_MODEL, PI_SCENE_PROFILE
from .ordinary_rejection_policy import (
    build_ordinary_policy_acceptance_audit_from_bindings,
    ordinary_policy_acceptance_projection,
    ordinary_standing_creator_policy,
)

QUALIFICATION_FIXTURE_SCHEMA_V1 = "cera.pi_scene.full_model_qualification_fixtures.v1"
QUALIFICATION_FIXTURE_SCHEMA_V2 = "cera.pi_scene.full_model_qualification_fixtures.v2"
QUALIFICATION_FIXTURE_SCHEMA_V3 = "cera.pi_scene.full_model_qualification_fixtures.v3"
QUALIFICATION_FIXTURE_SCHEMA_V4 = "cera.pi_scene.full_model_qualification_fixtures.v4"
QUALIFICATION_FIXTURE_SCHEMA_V5 = "cera.pi_scene.full_model_qualification_fixtures.v5"
QUALIFICATION_FIXTURE_SCHEMA_V6 = "cera.pi_scene.full_model_qualification_fixtures.v6"
QUALIFICATION_FIXTURE_SCHEMA_V7 = "cera.pi_scene.full_model_qualification_fixtures.v7"
QUALIFICATION_FIXTURE_SCHEMA_V8 = "cera.pi_scene.full_model_qualification_fixtures.v8"
QUALIFICATION_FIXTURE_SCHEMA_V9 = "cera.pi_scene.full_model_qualification_fixtures.v9"
QUALIFICATION_FIXTURE_SCHEMA_V10 = "cera.pi_scene.full_model_qualification_fixtures.v10"
QUALIFICATION_FIXTURE_SCHEMA_V11 = "cera.pi_scene.full_model_qualification_fixtures.v11"
QUALIFICATION_FIXTURE_SCHEMA_V12 = "cera.pi_scene.full_model_qualification_fixtures.v12"
QUALIFICATION_FIXTURE_SCHEMA_V13 = "cera.pi_scene.full_model_qualification_fixtures.v13"
QUALIFICATION_FIXTURE_SCHEMA_V14 = "cera.pi_scene.full_model_qualification_fixtures.v14"
QUALIFICATION_FIXTURE_SCHEMA_V15 = "cera.pi_scene.full_model_qualification_fixtures.v15"
QUALIFICATION_FIXTURE_SCHEMA_V16 = "cera.pi_scene.full_model_qualification_fixtures.v16"
QUALIFICATION_FIXTURE_SCHEMA_V17 = "cera.pi_scene.full_model_qualification_fixtures.v17"
QUALIFICATION_FIXTURE_SCHEMA_V18 = "cera.pi_scene.full_model_qualification_fixtures.v18"
QUALIFICATION_FIXTURE_SCHEMA_V19 = "cera.pi_scene.full_model_qualification_fixtures.v19"
QUALIFICATION_FIXTURE_SCHEMA_V20 = "cera.pi_scene.full_model_qualification_fixtures.v20"
QUALIFICATION_FIXTURE_SCHEMA_V21 = "cera.pi_scene.full_model_qualification_fixtures.v21"
QUALIFICATION_FIXTURE_SCHEMA_V22 = "cera.pi_scene.full_model_qualification_fixtures.v22"
QUALIFICATION_FIXTURE_SCHEMA_V23 = "cera.pi_scene.full_model_qualification_fixtures.v23"
QUALIFICATION_FIXTURE_SCHEMA_V24 = "cera.pi_scene.full_model_qualification_fixtures.v24"
QUALIFICATION_FIXTURE_SCHEMA_V25 = "cera.pi_scene.full_model_qualification_fixtures.v25"
QUALIFICATION_FIXTURE_SCHEMA_V26 = "cera.pi_scene.full_model_qualification_fixtures.v26"
QUALIFICATION_FIXTURE_SCHEMA_V27 = "cera.pi_scene.full_model_qualification_fixtures.v27"
QUALIFICATION_FIXTURE_SCHEMA_V28 = "cera.pi_scene.full_model_qualification_fixtures.v28"
QUALIFICATION_FIXTURE_SCHEMA_V29 = "cera.pi_scene.full_model_qualification_fixtures.v29"
QUALIFICATION_FIXTURE_SCHEMA_V30 = "cera.pi_scene.full_model_qualification_fixtures.v30"
QUALIFICATION_FIXTURE_SCHEMA_V31 = "cera.pi_scene.full_model_qualification_fixtures.v31"
QUALIFICATION_FIXTURE_SCHEMA_V32 = "cera.pi_scene.full_model_qualification_fixtures.v32"
QUALIFICATION_FIXTURE_SCHEMA_V33 = "cera.pi_scene.full_model_qualification_fixtures.v33"
QUALIFICATION_FIXTURE_SCHEMA_V34 = "cera.pi_scene.full_model_qualification_fixtures.v34"
QUALIFICATION_FIXTURE_SCHEMA_V35 = "cera.pi_scene.full_model_qualification_fixtures.v35"
QUALIFICATION_FIXTURE_SCHEMA_V36 = "cera.pi_scene.full_model_qualification_fixtures.v36"
QUALIFICATION_FIXTURE_SCHEMA_V37 = "cera.pi_scene.full_model_qualification_fixtures.v37"
QUALIFICATION_FIXTURE_SCHEMA_V38 = "cera.pi_scene.full_model_qualification_fixtures.v38"
QUALIFICATION_FIXTURE_SCHEMA = "cera.pi_scene.full_model_qualification_fixtures.v39"
QUALIFICATION_FIXTURE_PATH_V1 = "pi_scene_full_model_qualification_v1.json"
QUALIFICATION_FIXTURE_SHA256_V1 = "0df9fc6ca8f621ab6ea44ed2ed5c7751138f9442ea3a16af9c97a57679163f35"
QUALIFICATION_FIXTURE_PATH_V2 = "pi_scene_full_model_qualification_v2.json"
QUALIFICATION_FIXTURE_SHA256_V2 = "32d92771e78b39c90722e05cc85738ed3b9f23dd6b5e15fd7d11ef7818d7ba88"
QUALIFICATION_FIXTURE_PATH_V3 = "pi_scene_full_model_qualification_v3.json"
QUALIFICATION_FIXTURE_SHA256_V3 = "259029f7485a352b8029c65a4193929d22f19694f67aa0ab3d8eabf3a3803f29"
QUALIFICATION_FIXTURE_PATH_V4 = "pi_scene_full_model_qualification_v4.json"
QUALIFICATION_FIXTURE_SHA256_V4 = "cc9e92c6dbfdf27b496b2dfc8be8a5893c70fc6176cbee72d64bca164a54c443"
QUALIFICATION_FIXTURE_PATH_V5 = "pi_scene_full_model_qualification_v5.json"
QUALIFICATION_FIXTURE_SHA256_V5 = "3c158401574e61b13e65fccfc7be96a5c772b30e95238a975c7ee0699c7a2733"
QUALIFICATION_FIXTURE_PATH_V6 = "pi_scene_full_model_qualification_v6.json"
QUALIFICATION_FIXTURE_SHA256_V6 = "772a98366e669d18c395407ea2e6f90b1aa990f99fb23aec2f1f788797facfbc"
QUALIFICATION_FIXTURE_PATH_V7 = "pi_scene_full_model_qualification_v7.json"
QUALIFICATION_FIXTURE_SHA256_V7 = "2fa469ecf457e07eb1c55ae00d2a6b9258ed9cc3ef22dfd85044a638684caf28"
QUALIFICATION_FIXTURE_PATH_V8 = "pi_scene_full_model_qualification_v8.json"
QUALIFICATION_FIXTURE_SHA256_V8 = "48bc606cd46be333a7227e2352b2a3083f248983da50fdb9cfb382abd713dae0"
QUALIFICATION_FIXTURE_PATH_V9 = "pi_scene_full_model_qualification_v9.json"
QUALIFICATION_FIXTURE_SHA256_V9 = "e4d45c6c95b2681988417e4406fb4b9ee27e3bc2fd7a437b48a4a6dfc48195d8"
QUALIFICATION_FIXTURE_PATH_V10 = "pi_scene_full_model_qualification_v10.json"
QUALIFICATION_FIXTURE_SHA256_V10 = (
    "6486c588ba6295c09d08f34b040c5b8b8ad5c2ab5faae9130766c8eb548a38ed"
)
QUALIFICATION_FIXTURE_PATH_V11 = "pi_scene_full_model_qualification_v11.json"
QUALIFICATION_FIXTURE_SHA256_V11 = (
    "5a4b63c8fac7725b97d36585d13eb29f7f91271bb04ddc4ec559a64da50237e3"
)
QUALIFICATION_FIXTURE_PATH_V12 = "pi_scene_full_model_qualification_v12.json"
QUALIFICATION_FIXTURE_SHA256_V12 = (
    "f752ee5af0bb3b31ab023739e872c724f31caf999d6039f8fb299d2c2e828f9f"
)
QUALIFICATION_FIXTURE_PATH_V13 = "pi_scene_full_model_qualification_v13.json"
QUALIFICATION_FIXTURE_SHA256_V13 = (
    "a40507bf965973a0706f999c9f112045354fa75aa78d950904f02e292350618c"
)
QUALIFICATION_FIXTURE_PATH_V14 = "pi_scene_full_model_qualification_v14.json"
QUALIFICATION_FIXTURE_SHA256_V14 = (
    "7fa51625d3c12d217055c94ce6521f46ecb90fb12895ae7f668ed9cd2dc05e4e"
)
QUALIFICATION_FIXTURE_PATH_V15 = "pi_scene_full_model_qualification_v15.json"
QUALIFICATION_FIXTURE_SHA256_V15 = (
    "681433dcf9c0d6002c3f551bd121891327ccc64969e6b35f6203d53a2aa50e57"
)
QUALIFICATION_FIXTURE_PATH_V16 = "pi_scene_full_model_qualification_v16.json"
QUALIFICATION_FIXTURE_SHA256_V16 = (
    "6aaa241abef93cc202b0d60cd7727a08863fafb25c2bfb9f701591a949e3c918"
)
QUALIFICATION_FIXTURE_PATH_V17 = "pi_scene_full_model_qualification_v17.json"
QUALIFICATION_FIXTURE_SHA256_V17 = (
    "47db525139e6e24fb69701824ad27d593ec89a2cba587d249b080c58675c1c29"
)
QUALIFICATION_FIXTURE_PATH_V18 = "pi_scene_full_model_qualification_v18.json"
QUALIFICATION_FIXTURE_SHA256_V18 = (
    "8a1e05607be423a47872e1a9530e8b101b5c87c405e487c96d5e74e17d919a77"
)
QUALIFICATION_FIXTURE_PATH_V19 = "pi_scene_full_model_qualification_v19.json"
QUALIFICATION_FIXTURE_SHA256_V19 = (
    "dab0d4a9921a68869af8b3e688fdd1f9d354eb812786525a7424b52623a6e407"
)
QUALIFICATION_FIXTURE_PATH_V20 = "pi_scene_full_model_qualification_v20.json"
QUALIFICATION_FIXTURE_SHA256_V20 = (
    "aaa752c2381bae86350bd11da56b40b3acd7b8918eff27769af4b2129e9ccad9"
)
QUALIFICATION_FIXTURE_PATH_V21 = "pi_scene_full_model_qualification_v21.json"
QUALIFICATION_FIXTURE_SHA256_V21 = (
    "e34d046fc9774d34a597f3ef46d3a5ebf70e71f84586425390ca0619004a8ead"
)
QUALIFICATION_FIXTURE_PATH_V22 = "pi_scene_full_model_qualification_v22.json"
QUALIFICATION_FIXTURE_SHA256_V22 = (
    "5631e9e1def0b3badb7d5720306ddf22b149cdb41514c037b7dfaf961ea56dea"
)
QUALIFICATION_FIXTURE_PATH_V23 = "pi_scene_full_model_qualification_v23.json"
QUALIFICATION_FIXTURE_SHA256_V23 = (
    "ac46c8743abb4b2f680c06542d5e78699f44ccc9044be4fdda1a69591480c0c6"
)
QUALIFICATION_FIXTURE_PATH_V24 = "pi_scene_full_model_qualification_v24.json"
QUALIFICATION_FIXTURE_SHA256_V24 = (
    "81f1985d9056360b4314cba6a43e014e274ba559acb3c8f3ef39e27a201bb54a"
)
QUALIFICATION_FIXTURE_PATH_V25 = "pi_scene_full_model_qualification_v25.json"
QUALIFICATION_FIXTURE_SHA256_V25 = (
    "9a8557f6ec56fac0ce16d21670d880852e467d0f161e5edea49b6d80b90605c1"
)
QUALIFICATION_FIXTURE_PATH_V26 = "pi_scene_full_model_qualification_v26.json"
QUALIFICATION_FIXTURE_SHA256_V26 = (
    "3725b529fbfe5686a3aa7e005b9850ac8eca43f8be1a0b201195027d4e4dbdc2"
)
QUALIFICATION_FIXTURE_PATH_V27 = "pi_scene_full_model_qualification_v27.json"
QUALIFICATION_FIXTURE_SHA256_V27 = (
    "6e35c724ab00f58ef3ebeaa2a5b96f665ea9ed38c16c72031e832edbc4e37a02"
)
QUALIFICATION_FIXTURE_PATH_V28 = "pi_scene_full_model_qualification_v28.json"
QUALIFICATION_FIXTURE_SHA256_V28 = (
    "05d50222534d094bd6505d2afd1b25caf918e3607c655f78b8f3d442b4aa8945"
)
QUALIFICATION_FIXTURE_PATH_V29 = "pi_scene_full_model_qualification_v29.json"
QUALIFICATION_FIXTURE_SHA256_V29 = (
    "8eb9f5f4cfe43dd875d28ec8bf7345d8d4b6624447e1ca5255512788ebed9145"
)
QUALIFICATION_FIXTURE_PATH_V30 = "pi_scene_full_model_qualification_v30.json"
QUALIFICATION_FIXTURE_SHA256_V30 = (
    "35bbe74091d9de87c18a55104d9566cb771f3652643360ea03493731a421d697"
)
QUALIFICATION_FIXTURE_PATH_V31 = "pi_scene_full_model_qualification_v31.json"
QUALIFICATION_FIXTURE_SHA256_V31 = (
    "3dc9ca897f8b1719c5551a083b4debfb90035f4d873b87cb1450365fd8d0146d"
)
QUALIFICATION_FIXTURE_PATH_V32 = "pi_scene_full_model_qualification_v32.json"
QUALIFICATION_FIXTURE_SHA256_V32 = (
    "5e6e1d7ce3f15cc9e7d25fcf063c369de0646be1d17e1a3b9542c36c10d6dd32"
)
QUALIFICATION_FIXTURE_PATH_V33 = "pi_scene_full_model_qualification_v33.json"
QUALIFICATION_FIXTURE_SHA256_V33 = (
    "d354adf9f1e933397b567eef792779e71e6676e77274850dd757e02aa7ff8917"
)
QUALIFICATION_FIXTURE_PATH_V34 = "pi_scene_full_model_qualification_v34.json"
QUALIFICATION_FIXTURE_SHA256_V34 = (
    "ab123164415ddc670a57c91c3d5032f29b348917c18e77fb49621e2188ddac9b"
)
QUALIFICATION_FIXTURE_PATH_V35 = "pi_scene_full_model_qualification_v35.json"
QUALIFICATION_FIXTURE_SHA256_V35 = (
    "6c54b0be88d20c7972b726ac793bea75d4213185b885940f791b2caccf11b33f"
)
QUALIFICATION_FIXTURE_PATH_V36 = "pi_scene_full_model_qualification_v36.json"
QUALIFICATION_FIXTURE_SHA256_V36 = (
    "5d5fb2a20a6d35efd5ff7b1b9b5a47a214b695d84190ed2392defd710c41b089"
)
QUALIFICATION_FIXTURE_PATH_V37 = "pi_scene_full_model_qualification_v37.json"
QUALIFICATION_FIXTURE_SHA256_V37 = (
    "e9865eda770db68f893cdf08c5b6669dfdcc259b9c22ab0343c7dd87b96ce279"
)
QUALIFICATION_FIXTURE_PATH_V38 = "pi_scene_full_model_qualification_v38.json"
QUALIFICATION_FIXTURE_SHA256_V38 = (
    "f062d5e31fac073f09325848b5345761be449381fb5fd5f44c86947ad354b4b4"
)
QUALIFICATION_BASELINE_FIXTURE_PATH = QUALIFICATION_FIXTURE_PATH_V38
QUALIFICATION_BASELINE_FIXTURE_SHA256 = QUALIFICATION_FIXTURE_SHA256_V38
QUALIFICATION_MANIFEST_SCHEMA_V5 = "cera.pi_scene.full_model_qualification_manifest.v5"
QUALIFICATION_MANIFEST_SCHEMA_V6 = "cera.pi_scene.full_model_qualification_manifest.v6"
QUALIFICATION_MANIFEST_SCHEMA_V7 = "cera.pi_scene.full_model_qualification_manifest.v7"
QUALIFICATION_MANIFEST_SCHEMA_V8 = "cera.pi_scene.full_model_qualification_manifest.v8"
QUALIFICATION_MANIFEST_SCHEMA_V9 = "cera.pi_scene.full_model_qualification_manifest.v9"
QUALIFICATION_MANIFEST_SCHEMA_V10 = "cera.pi_scene.full_model_qualification_manifest.v10"
QUALIFICATION_MANIFEST_SCHEMA_V11 = "cera.pi_scene.full_model_qualification_manifest.v11"
QUALIFICATION_MANIFEST_SCHEMA_V12 = "cera.pi_scene.full_model_qualification_manifest.v12"
QUALIFICATION_MANIFEST_SCHEMA_V13 = "cera.pi_scene.full_model_qualification_manifest.v13"
QUALIFICATION_MANIFEST_SCHEMA_V14 = "cera.pi_scene.full_model_qualification_manifest.v14"
QUALIFICATION_MANIFEST_SCHEMA_V15 = "cera.pi_scene.full_model_qualification_manifest.v15"
QUALIFICATION_MANIFEST_SCHEMA_V16 = "cera.pi_scene.full_model_qualification_manifest.v16"
QUALIFICATION_MANIFEST_SCHEMA_V17 = "cera.pi_scene.full_model_qualification_manifest.v17"
QUALIFICATION_MANIFEST_SCHEMA_V18 = "cera.pi_scene.full_model_qualification_manifest.v18"
QUALIFICATION_MANIFEST_SCHEMA_V19 = "cera.pi_scene.full_model_qualification_manifest.v19"
QUALIFICATION_MANIFEST_SCHEMA_V20 = "cera.pi_scene.full_model_qualification_manifest.v20"
QUALIFICATION_MANIFEST_SCHEMA_V21 = "cera.pi_scene.full_model_qualification_manifest.v21"
QUALIFICATION_MANIFEST_SCHEMA_V22 = "cera.pi_scene.full_model_qualification_manifest.v22"
QUALIFICATION_MANIFEST_SCHEMA_V23 = "cera.pi_scene.full_model_qualification_manifest.v23"
QUALIFICATION_MANIFEST_SCHEMA_V24 = "cera.pi_scene.full_model_qualification_manifest.v24"
QUALIFICATION_MANIFEST_SCHEMA_V25 = "cera.pi_scene.full_model_qualification_manifest.v25"
QUALIFICATION_MANIFEST_SCHEMA_V26 = "cera.pi_scene.full_model_qualification_manifest.v26"
QUALIFICATION_MANIFEST_SCHEMA_V27 = "cera.pi_scene.full_model_qualification_manifest.v27"
QUALIFICATION_MANIFEST_SCHEMA_V28 = "cera.pi_scene.full_model_qualification_manifest.v28"
QUALIFICATION_MANIFEST_SCHEMA_V29 = "cera.pi_scene.full_model_qualification_manifest.v29"
QUALIFICATION_MANIFEST_SCHEMA_V30 = "cera.pi_scene.full_model_qualification_manifest.v30"
QUALIFICATION_MANIFEST_SCHEMA_V31 = "cera.pi_scene.full_model_qualification_manifest.v31"
QUALIFICATION_MANIFEST_SCHEMA_V32 = "cera.pi_scene.full_model_qualification_manifest.v32"
QUALIFICATION_MANIFEST_SCHEMA_V33 = "cera.pi_scene.full_model_qualification_manifest.v33"
QUALIFICATION_MANIFEST_SCHEMA_V34 = "cera.pi_scene.full_model_qualification_manifest.v34"
QUALIFICATION_MANIFEST_SCHEMA_V35 = "cera.pi_scene.full_model_qualification_manifest.v35"
QUALIFICATION_MANIFEST_SCHEMA_V36 = "cera.pi_scene.full_model_qualification_manifest.v36"
QUALIFICATION_MANIFEST_SCHEMA_V37 = "cera.pi_scene.full_model_qualification_manifest.v37"
QUALIFICATION_MANIFEST_SCHEMA_V38 = "cera.pi_scene.full_model_qualification_manifest.v38"
QUALIFICATION_MANIFEST_SCHEMA_V39 = "cera.pi_scene.full_model_qualification_manifest.v39"
QUALIFICATION_MANIFEST_SCHEMA_V40 = "cera.pi_scene.full_model_qualification_manifest.v40"
QUALIFICATION_MANIFEST_SCHEMA_V41 = "cera.pi_scene.full_model_qualification_manifest.v41"
QUALIFICATION_MANIFEST_SCHEMA_V42 = "cera.pi_scene.full_model_qualification_manifest.v42"
QUALIFICATION_MANIFEST_SCHEMA = "cera.pi_scene.full_model_qualification_manifest.v43"
QUALIFICATION_RESULT_SCHEMA = "cera.pi_scene.full_model_qualification_result.v6"

_QUALIFICATION_ADVERSARIAL_STRESS_TAGS = frozenset(
    {
        "authority_spoof",
        "coercive_pressure",
        "consent_ambiguity",
        "external_interruption",
        "false_continuity",
        "identity_conflict",
        "instruction_injection",
        "nonverbal_consent",
        "offscreen_cast",
        "roleplay_canon",
        "synthetic_media",
        "temporal_conflict",
        "untrusted_metadata",
    }
)
_QUALIFICATION_BOUNDARY_STRESS_TAGS = frozenset(
    {
        "character_autonomy",
        "consent_withdrawal",
        "privacy_boundary",
        "protected_user_custody",
        "recording_custody",
        "route_transition",
    }
)
_QUALIFICATION_STRESS_TAGS = (
    _QUALIFICATION_ADVERSARIAL_STRESS_TAGS | _QUALIFICATION_BOUNDARY_STRESS_TAGS
)
_QUALIFICATION_UNSUPPORTED_STRESS_CAST = re.compile(r"\b(?:Hana|Mia|Tomi|Enne|Aoi|Yuuni)\b")

SOL_FAMILY_CEILING = 60
DEEPSEEK_HTTP_OPERATION_CEILING = 480
DEEPSEEK_PER_INVOCATION_CEILING = 6
TERRA_CEILING = 0
USER_AUTHORIZED_CODEX_OPERATION_CEILING = 500
USER_AUTHORIZED_DEEPSEEK_OPERATION_CEILING = 500

_QUALIFICATION_FIXTURE_LOAD_CACHE: ContextVar[
    dict[tuple[Path, str], tuple[QualificationFixtureV1, ...]] | None
] = ContextVar("qualification_fixture_load_cache", default=None)

# The first Planner operation on each physical thread hydrates world/context
# state and is reported separately, including a fresh thread after transport
# recovery. Every provider attempt has its own three-minute hard boundary. An
# explicitly authorized retry is a separate attempt with a fresh boundary; its
# duration is never added to the failed attempt when enforcing this limit.
RETAINED_PLANNER_LATENCY_CONCERN_MS = 180_000
QUALIFICATION_PLANNER_REASONING_EFFORT = "medium"
QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS = 180
# One complete ordinary generation may use one Planner plus two Writer, Luna,
# Reader, and Recorder occurrences.  Each occurrence retains its own 3-attempt
# authority; Recorder's second occurrence is the sole explicit repair successor.
QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES = 9
QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS = (
    QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES + 1
) * QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS
PROVIDER_STAGE_RETRY_STATUS_POLL_SECONDS = 0.25
# If the one POST disconnects immediately, the replacement can still be
# completing Planner, Writer, Luna, repair, and Recorder work. Read-only GET
# reconciliation therefore outlives the same complete outer HTTP boundary.
PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS = QUALIFICATION_HTTP_HARD_TIMEOUT_SECONDS + 15
MANUAL_PROVIDER_STAGE_RETRY_POLICY: Mapping[str, Any] = {
    "authorized": True,
    "identity_scope": "branch_generation_stage_unique_occurrence",
    "stages": [
        "planner",
        "semantic_validator",
        "reader",
        "writer",
        "recorder",
        "adult_scene",
        "adult_filter",
    ],
    "maximum_actions_per_chain": 2,
    "maximum_attempts_per_chain": 3,
    "maximum_resume_prepared_actions_per_chain": 1,
    "maximum_recording_repair_actions_per_request": 1,
    "recording_repair_successor_maximum_attempts": 3,
    "recording_repair_successor_maximum_retry_actions": 2,
    "recursive_recording_repair": False,
    "automatic": False,
    "external_authorization_required": True,
    "fallback": False,
    "model_substitution": False,
    "whole_request_replay": False,
    "action_body": "exact_backend_issued_action_v1",
    "pre_dispatch_authority": "authenticated_get",
    "post_dispatch_authority": "authenticated_get_only",
    "check_status_provider_dispatch": False,
    "status_envelope_schema": "cera.provider_stage_retry_status_envelope.v1",
    "terminal_states": [
        "attempts_exhausted",
        "recording_repair_required",
        "recovery_required",
    ],
    "blocked_ambiguous_timeout_state": "blocked_ambiguous",
}
FINAL_PROVIDER_FAILURE_CLASSES = frozenset(
    {
        "transport_timeout",
        "provider_unavailable",
        "provider_process_failed",
        "provider_stream_incomplete",
        "provider_completion_incomplete",
        "provider_output_invalid",
    }
)

QUALIFICATION_ACTION_BUDGETS: Mapping[str, Any] = {
    "technical_provider_retry": {
        "authority_scope": "branch_generation_stage_occurrence",
        "maximum_stage_attempts": 3,
        "maximum_manual_retry_actions": 2,
        "automatic_provider_redispatch": False,
    },
    "prepared_dispatch_resume": {
        "authority_scope": "exact_prepared_stage_attempt",
        "maximum_actions_per_stage_occurrence": 1,
        "consumes_retry_action": False,
        "automatic": False,
    },
    "semantic_regenerate": {
        "authority_scope": "review_candidate",
        "maximum_explicit_actions_per_rejected_first_pass": 1,
        "automatic": False,
        "external_authorization_required": True,
    },
    "replan": {
        "authority_scope": "accepted_generation",
        "maximum_actions_per_qualification_fixture": 0,
    },
    "recorder_repair": {
        "authority_scope": "accepted_turn_recording",
        "maximum_actions_per_qualification_fixture": 1,
        "maximum_successor_stage_attempts": 3,
        "maximum_successor_retry_actions": 2,
        "recursive_repair": False,
        "automatic": False,
        "external_authorization_required": True,
        "separate_from_provider_retry": True,
    },
}

QUALIFICATION_COMPLETE_GENERATION_CEILINGS: Mapping[str, Any] = {
    "ordinary": {
        "maximum_stage_occurrences": {
            "planner": 1,
            "writer": 2,
            "semantic_validator": 2,
            "reader": 2,
            "recorder": 2,
        },
        "maximum_codex_operations": 15,
        "maximum_deepseek_http_operations": 72,
    },
    "adult": {
        "maximum_stage_occurrences": {
            "planner_transition": 1,
            "adult_scene": 1,
            "adult_filter": 1,
        },
        "maximum_codex_operations": 3,
        "maximum_deepseek_http_operations": 36,
    },
    "deepseek_http_operations_per_stage_attempt": DEEPSEEK_PER_INVOCATION_CEILING,
}

QUALIFICATION_EXECUTION_POLICY: Mapping[str, Any] = {
    "one_sequential_session_per_phase": True,
    "backend_route_order": ["ordinary"] * 5 + ["adult"] * 5 + ["ordinary"] * 5 + ["adult"] * 5,
    "sillytavern_route_order": ["ordinary"] * 3 + ["adult"] * 3 + ["ordinary"] * 2 + ["adult"] * 2,
    "first_pass_outcome_preserved": True,
    "maximum_explicit_regenerates_per_prompt": 1,
    "action_budgets": deepcopy(QUALIFICATION_ACTION_BUDGETS),
    "complete_generation_ceilings": deepcopy(QUALIFICATION_COMPLETE_GENERATION_CEILINGS),
    "automatic_retry": False,
    "automatic_behavior": "authenticated_get_only",
    "provider_bearing_manual_actions_require_exact_external_authorization": True,
    "external_manual_action_receipt_custody": "hash_only",
    "provider_free_simulation_requires_dispatch_disabled": True,
    "manual_provider_stage_retry": dict(MANUAL_PROVIDER_STAGE_RETRY_POLICY),
    "fallback": False,
    "model_substitution": False,
    "planner_reasoning_effort": QUALIFICATION_PLANNER_REASONING_EFFORT,
    "semantic_validator_reasoning_effort": "xhigh",
    "reader_reasoning_effort": "medium",
    "ordinary_review_mode": "automatic",
    "ordinary_luna_reader_python_pass_auto_accept_required": True,
    "ordinary_standing_creator_policy": {
        "authority_kind": ordinary_standing_creator_policy().authority_kind,
        "policy_id": ordinary_standing_creator_policy().policy_id,
        "policy_version": ordinary_standing_creator_policy().policy_version,
        "policy_sha256": ordinary_standing_creator_policy().policy_sha256,
        "policy_text_sha256": ordinary_standing_creator_policy().policy_text_sha256,
        "soft_semantic_conflict_classes": list(
            ordinary_standing_creator_policy().soft_semantic_conflict_classes
        ),
        "soft_reader_feedback_scopes": list(
            ordinary_standing_creator_policy().soft_reader_feedback_scopes
        ),
        "hard_signal_wins": True,
        "first_writer_candidate_retained": True,
        "second_writer_call": False,
        "manual_action_required": False,
        "adult_routes_excluded": True,
    },
    "adult_filter_pass_atomic_accept_required": True,
    "exact_adult_prose_in_qualification_evidence": False,
    "dynamic_loopback_only_cera_port": True,
    "installed_cera_port_5101_untouched": True,
    "frozen_isolated_sillytavern_tree_required": True,
    "phase_order": ["backend", "sillytavern"],
}
QUALIFICATION_EXECUTION_POLICY_V31: Mapping[str, Any] = deepcopy(QUALIFICATION_EXECUTION_POLICY)
QUALIFICATION_EXECUTION_POLICY = {
    **deepcopy(QUALIFICATION_EXECUTION_POLICY_V31),
    "ordinary_standing_creator_policy": {
        **deepcopy(
            cast(
                Mapping[str, Any],
                QUALIFICATION_EXECUTION_POLICY_V31["ordinary_standing_creator_policy"],
            )
        ),
        "hard_regenerate_soft_successor_tolerated": True,
        "additional_writer_call_after_soft_successor": False,
    },
}
QUALIFICATION_EXECUTION_POLICY_V42: Mapping[str, Any] = deepcopy(
    QUALIFICATION_EXECUTION_POLICY
)
QUALIFICATION_EXECUTION_POLICY = {
    **deepcopy(QUALIFICATION_EXECUTION_POLICY_V42),
    "provider_stage_attempt_timeout_seconds": (
        QUALIFICATION_PROVIDER_STAGE_HARD_TIMEOUT_SECONDS
    ),
    "provider_stage_retry_timeout_accounting": "separate_per_attempt",
}
QUALIFICATION_EXECUTION_POLICY_V22: Mapping[str, Any] = {
    key: deepcopy(value)
    for key, value in QUALIFICATION_EXECUTION_POLICY_V31.items()
    if key != "ordinary_standing_creator_policy"
}

EXPECTED_PHASE_COUNTS: Mapping[str, Mapping[str, int]] = {
    "backend": {"ordinary": 10, "adult": 10},
    "sillytavern": {"ordinary": 5, "adult": 5},
}


class QualificationPhase(StrEnum):
    BACKEND = "backend"
    SILLYTAVERN = "sillytavern"


class QualificationRoute(StrEnum):
    ORDINARY = "ordinary"
    ADULT = "adult"


class _CriticalProviderStageError(StateConflictError):
    """Qualification reached a stable provider-stage stop condition."""

    def __init__(self, terminal_state: str) -> None:
        self.terminal_state = terminal_state
        super().__init__(f"qualification stopped at provider-stage state {terminal_state}")


class ManualActionRequiredError(StateConflictError):
    """Live qualification paused before one provider-bearing manual action."""

    def __init__(self, checkpoint_sha256: str) -> None:
        self.checkpoint_sha256 = checkpoint_sha256
        super().__init__("qualification requires one externally authorized manual action")


@dataclass(frozen=True, slots=True)
class QualificationFixtureV1:
    fixture_id: str
    phase: QualificationPhase
    initial_route: QualificationRoute
    expected_route: QualificationRoute
    expected_next_route: QualificationRoute
    adult_craft_mode: str
    user_source: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{3,63}", self.fixture_id):
            raise ContractValidationError("qualification fixture identity is invalid")
        if self.adult_craft_mode not in {"off", "on", "ex"}:
            raise ContractValidationError("qualification adult-craft mode is invalid")
        if self.expected_route is QualificationRoute.ORDINARY:
            if (
                self.initial_route is not QualificationRoute.ORDINARY
                or self.expected_next_route is not QualificationRoute.ORDINARY
            ):
                raise ContractValidationError("ordinary qualification changed route ownership")
            if self.adult_craft_mode != "off":
                raise ContractValidationError("ordinary qualification enabled adult craft")
        elif self.adult_craft_mode == "off":
            raise ContractValidationError("adult qualification omitted adult craft retrieval")
        if len(self.user_source.strip()) < 40:
            raise ContractValidationError("qualification fixture source is too short")


@dataclass(frozen=True, slots=True)
class QualificationFixtureV2(QualificationFixtureV1):
    """One novel stress case bound to the retired generic fixture baseline."""

    novelty_id: str
    stress_tags: tuple[str, ...]

    def __post_init__(self) -> None:
        QualificationFixtureV1.__post_init__(self)
        if re.fullmatch(r"stress:[a-z][a-z0-9_]{2,95}", self.novelty_id) is None:
            raise ContractValidationError("qualification novelty identity is invalid")
        if not 2 <= len(self.stress_tags) <= 4:
            raise ContractValidationError("qualification stress fixture requires two to four tags")
        if self.stress_tags != tuple(sorted(self.stress_tags)):
            raise ContractValidationError("qualification stress tags are not sorted")
        if len(set(self.stress_tags)) != len(self.stress_tags):
            raise ContractValidationError("qualification stress tags are duplicated")
        unknown = set(self.stress_tags) - _QUALIFICATION_STRESS_TAGS
        if unknown:
            raise ContractValidationError("qualification stress tag is unknown")
        if not set(self.stress_tags).intersection(_QUALIFICATION_ADVERSARIAL_STRESS_TAGS):
            raise ContractValidationError("qualification stress fixture is not adversarial")
        if not set(self.stress_tags).intersection(_QUALIFICATION_BOUNDARY_STRESS_TAGS):
            raise ContractValidationError("qualification stress fixture lacks a boundary")
        if re.search(r"\bSakura\b", self.user_source) is None:
            raise ContractValidationError("qualification stress fixture lacks its bound actor")
        if _QUALIFICATION_UNSUPPORTED_STRESS_CAST.search(self.user_source) is not None:
            raise ContractValidationError("qualification stress fixture exceeds its bound cast")


@dataclass(frozen=True, slots=True)
class QualificationFixtureV3(QualificationFixtureV2):
    """One novel stress case bound to the cumulative V1/V2 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV4(QualificationFixtureV3):
    """One novel stress case bound to the cumulative V1/V2/V3 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV5(QualificationFixtureV4):
    """One novel stress case bound to cumulative V1/V2/V3/V4 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV6(QualificationFixtureV5):
    """One novel stress case bound to cumulative V1/V2/V3/V4/V5 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV7(QualificationFixtureV6):
    """One novel stress case bound to cumulative V1/V2/V3/V4/V5/V6 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV8(QualificationFixtureV7):
    """One novel stress case bound to cumulative V1-V7 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV9(QualificationFixtureV8):
    """One novel stress case bound to cumulative V1-V8 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV10(QualificationFixtureV9):
    """One novel stress case bound to cumulative V1-V9 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV11(QualificationFixtureV10):
    """One novel stress case bound to cumulative V1-V10 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV12(QualificationFixtureV11):
    """One novel stress case bound to cumulative V1-V11 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV13(QualificationFixtureV12):
    """One novel stress case bound to cumulative V1-V12 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV14(QualificationFixtureV13):
    """One novel stress case bound to cumulative V1-V13 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV15(QualificationFixtureV14):
    """One novel stress case bound to cumulative V1-V14 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV16(QualificationFixtureV15):
    """One novel stress case bound to cumulative V1-V15 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV17(QualificationFixtureV16):
    """One novel stress case bound to cumulative V1-V16 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV18(QualificationFixtureV17):
    """One novel stress case bound to cumulative V1-V17 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV19(QualificationFixtureV18):
    """One novel stress case bound to cumulative V1-V18 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV20(QualificationFixtureV19):
    """One novel stress case bound to cumulative V1-V19 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV21(QualificationFixtureV20):
    """One novel stress case bound to cumulative V1-V20 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV22(QualificationFixtureV21):
    """One novel stress case bound to cumulative V1-V21 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV23(QualificationFixtureV22):
    """One novel stress case bound to cumulative V1-V22 spent ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV24(QualificationFixtureV23):
    """One novel practical-boundary case bound to cumulative V1-V23 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV25(QualificationFixtureV24):
    """One novel practical-boundary case bound to cumulative V1-V24 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV26(QualificationFixtureV25):
    """One novel practical-boundary case bound to cumulative V1-V25 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV27(QualificationFixtureV26):
    """One novel practical-boundary case bound to cumulative V1-V26 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV28(QualificationFixtureV27):
    """One novel practical-boundary case bound to cumulative V1-V27 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV29(QualificationFixtureV28):
    """One novel practical-boundary case bound to cumulative V1-V28 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV30(QualificationFixtureV29):
    """One novel practical-boundary case bound to cumulative V1-V29 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV31(QualificationFixtureV30):
    """One novel practical-boundary case bound to cumulative V1-V30 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV32(QualificationFixtureV31):
    """One novel practical-boundary case bound to cumulative V1-V31 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV33(QualificationFixtureV32):
    """One novel practical-boundary case bound to cumulative V1-V32 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV34(QualificationFixtureV33):
    """One novel practical-boundary case bound to cumulative V1-V33 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV35(QualificationFixtureV34):
    """One novel practical-boundary case bound to cumulative V1-V34 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV36(QualificationFixtureV35):
    """One novel practical-boundary case bound to cumulative V1-V35 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV37(QualificationFixtureV36):
    """One novel practical-boundary case bound to cumulative V1-V36 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV38(QualificationFixtureV37):
    """One novel practical-boundary case bound to cumulative V1-V37 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationFixtureV39(QualificationFixtureV38):
    """One novel story-capability case bound to cumulative V1-V38 ancestry."""


@dataclass(frozen=True, slots=True)
class QualificationManualActionRequestV1:
    """In-memory exact authority plus its hash-only persistent projection."""

    qualification_id: str
    phase: QualificationPhase
    turn_index: int
    fixture_id: str
    action_family: str
    action_kind: str
    action_id: str
    exact_action: Mapping[str, Any]
    authority_sha256: str
    chain_id: str | None = None
    review_id: str | None = None
    candidate_sha256: str | None = None
    stage: str | None = None
    provider: str | None = None
    model_family: str | None = None
    retry_action_ordinal: int | None = None

    SCHEMA_VERSION = "cera.pi_scene.qualification_manual_action_required.v1"

    def __post_init__(self) -> None:
        family_kinds = {
            "provider_stage_control": {
                "provider_retry",
                "resume_prepared",
                "repair_recording",
            },
            "semantic_regenerate": {"regenerate"},
            "recording_repair": {"repair_recording"},
        }
        if self.action_family not in family_kinds:
            raise ContractValidationError("qualification manual action family changed")
        if self.action_kind not in family_kinds[self.action_family]:
            raise ContractValidationError("qualification manual action kind changed")
        if type(self.turn_index) is not int or self.turn_index < 1:
            raise ContractValidationError("qualification manual action turn changed")
        if not self.qualification_id or not self.fixture_id or not self.action_id:
            raise ContractValidationError("qualification manual action identity is incomplete")
        if not isinstance(self.exact_action, Mapping) or not self.exact_action:
            raise ContractValidationError("qualification manual action body is incomplete")
        normalized_action = dict(self.exact_action)
        if any(isinstance(value, (Mapping, list, tuple)) for value in normalized_action.values()):
            raise ContractValidationError("qualification manual action body is not closed")
        object.__setattr__(self, "exact_action", MappingProxyType(normalized_action))
        if not re_is_sha256(self.authority_sha256):
            raise ContractValidationError("qualification manual authority hash is invalid")
        if self.candidate_sha256 is not None and not re_is_sha256(self.candidate_sha256):
            raise ContractValidationError("qualification manual candidate hash is invalid")
        if self.action_family == "provider_stage_control":
            if self.chain_id is None or self.review_id is not None:
                raise ContractValidationError("provider-stage manual action lost its chain")
            try:
                generated_action = validate_provider_stage_retry_action_v1(normalized_action)
            except ContractValidationError as exc:
                raise ContractValidationError(
                    "provider-stage manual action failed generated validation"
                ) from exc
            if (
                generated_action["action_family"] != self.action_family
                or generated_action["action_kind"] != self.action_kind
                or generated_action["action_id"] != self.action_id
                or generated_action["chain_id"] != self.chain_id
                or generated_action["retry_action_ordinal"] != self.retry_action_ordinal
                or not all(
                    isinstance(value, str) and value.strip()
                    for value in (self.stage, self.provider, self.model_family)
                )
            ):
                raise ContractValidationError(
                    "provider-stage manual action body changed its bound identity"
                )
        elif self.chain_id is not None or self.review_id is None or self.candidate_sha256 is None:
            raise ContractValidationError("review manual action lost its exclusive identity")
        elif normalized_action != {"action": self.action_kind}:
            raise ContractValidationError("review manual action body changed")
        elif self.action_id != "review-action-" + text_sha256(
            f"{self.review_id}:{self.candidate_sha256}:{self.action_kind}"
        ) or any(
            value is not None
            for value in (
                self.stage,
                self.provider,
                self.model_family,
                self.retry_action_ordinal,
            )
        ):
            raise ContractValidationError("review manual action identity changed")

    @property
    def exact_action_sha256(self) -> str:
        return canonical_sha256(self.exact_action)

    def binding_projection(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "qualification_id_sha256": text_sha256(self.qualification_id),
            "phase": self.phase.value,
            "turn_index": self.turn_index,
            "fixture_id_sha256": text_sha256(self.fixture_id),
            "action_family": self.action_family,
            "action_kind": self.action_kind,
            "action_id_sha256": text_sha256(self.action_id),
            "exact_action_sha256": self.exact_action_sha256,
            "chain_id_sha256": (None if self.chain_id is None else text_sha256(self.chain_id)),
            "review_id_sha256": (None if self.review_id is None else text_sha256(self.review_id)),
            "candidate_sha256": self.candidate_sha256,
            "authority_sha256": self.authority_sha256,
            "stage": self.stage,
            "provider": self.provider,
            "model_family": self.model_family,
            "retry_action_ordinal": self.retry_action_ordinal,
        }

    @property
    def checkpoint_sha256(self) -> str:
        return canonical_sha256(self.binding_projection())

    def pending_projection(self) -> dict[str, Any]:
        return {
            **self.binding_projection(),
            "status": "manual_action_required",
            "checkpoint_sha256": self.checkpoint_sha256,
        }


@dataclass(frozen=True, slots=True)
class QualificationManualActionAuthorizationV1:
    """One external per-action approval retained only for exact comparison."""

    checkpoint_sha256: str
    phase: QualificationPhase
    turn_index: int
    action_family: str
    action_kind: str
    action_id: str
    exact_action_sha256: str
    authority_sha256: str
    chain_id: str | None
    review_id: str | None
    candidate_sha256: str | None
    stage: str | None
    provider: str | None
    model_family: str | None
    retry_action_ordinal: int | None
    authorization_source: str
    authorization_id: str

    def __post_init__(self) -> None:
        if self.authorization_source not in {
            "external_manual_receipt",
            "provider_free_simulation",
        }:
            raise ContractValidationError("qualification authorization source changed")
        if not self.authorization_id.strip():
            raise ContractValidationError("qualification authorization identity is empty")

    @classmethod
    def approve(
        cls,
        request: QualificationManualActionRequestV1,
        *,
        authorization_source: str,
        authorization_id: str,
    ) -> QualificationManualActionAuthorizationV1:
        return cls(
            checkpoint_sha256=request.checkpoint_sha256,
            phase=request.phase,
            turn_index=request.turn_index,
            action_family=request.action_family,
            action_kind=request.action_kind,
            action_id=request.action_id,
            exact_action_sha256=request.exact_action_sha256,
            authority_sha256=request.authority_sha256,
            chain_id=request.chain_id,
            review_id=request.review_id,
            candidate_sha256=request.candidate_sha256,
            stage=request.stage,
            provider=request.provider,
            model_family=request.model_family,
            retry_action_ordinal=request.retry_action_ordinal,
            authorization_source=authorization_source,
            authorization_id=authorization_id,
        )


class QualificationManualActionAuthorizer(Protocol):
    def authorize(
        self,
        request: QualificationManualActionRequestV1,
    ) -> QualificationManualActionAuthorizationV1 | None: ...

    def mark_consumed(
        self,
        request: QualificationManualActionRequestV1,
        authorization: QualificationManualActionAuthorizationV1,
    ) -> None: ...


def _validate_manual_action_authorization(
    request: QualificationManualActionRequestV1,
    authorization: QualificationManualActionAuthorizationV1,
) -> None:
    expected = (
        request.checkpoint_sha256,
        request.phase,
        request.turn_index,
        request.action_family,
        request.action_kind,
        request.action_id,
        request.exact_action_sha256,
        request.authority_sha256,
        request.chain_id,
        request.review_id,
        request.candidate_sha256,
        request.stage,
        request.provider,
        request.model_family,
        request.retry_action_ordinal,
    )
    observed = (
        authorization.checkpoint_sha256,
        authorization.phase,
        authorization.turn_index,
        authorization.action_family,
        authorization.action_kind,
        authorization.action_id,
        authorization.exact_action_sha256,
        authorization.authority_sha256,
        authorization.chain_id,
        authorization.review_id,
        authorization.candidate_sha256,
        authorization.stage,
        authorization.provider,
        authorization.model_family,
        authorization.retry_action_ordinal,
    )
    if observed != expected:
        raise StateConflictError("qualification manual authorization binding changed")
    if not authorization.authorization_source.strip() or not authorization.authorization_id.strip():
        raise StateConflictError("qualification manual authorization identity is incomplete")


@dataclass(frozen=True, slots=True)
class ClientResponseV1:
    """One observed HTTP operation without persisting response prose."""

    transport: str
    path: str
    status_code: int
    duration_ms: int
    body: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not self.transport.strip() or not self.path.startswith("/"):
            raise ContractValidationError("qualification HTTP identity is invalid")
        if type(self.status_code) is not int or not 100 <= self.status_code <= 599:
            raise ContractValidationError("qualification HTTP status is invalid")
        if type(self.duration_ms) is not int or self.duration_ms < 0:
            raise ContractValidationError("qualification HTTP duration is invalid")


@dataclass(frozen=True, slots=True)
class RejectionReviewV1:
    """One noncritical first-pass rejection eligible for a user Regenerate."""

    review_id: str
    candidate_id: str
    candidate_sha256: str | None
    authority_sha256: str
    conflict_sha256: str
    provider_operations: Mapping[str, int]
    projection: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class OrdinaryProvisionalCompletionV1:
    """Exact visible Writer candidate bound to one provisional review."""

    response: ClientResponseV1
    review_id: str
    review_url: str
    candidate_sha256: str
    story_text: str
    lifecycle: OrdinaryReviewLifecycleV2


@dataclass(frozen=True, slots=True)
class OrdinaryReviewResolutionV1:
    """Provider-free review reconciliation result for one candidate."""

    result: Mapping[str, Any] | RejectionReviewV1
    review: OrdinaryReviewV3
    retry_resolutions: tuple[ProviderStageRetryResolutionV1, ...]
    http_duration_ms: int
    terminal_decision_sha256: str | None


@dataclass(frozen=True, slots=True)
class OrdinaryReviewStopV1:
    """One stable technical stop reached while joining a provisional review."""

    retry_resolutions: tuple[ProviderStageRetryResolutionV1, ...]


@dataclass(frozen=True, slots=True)
class ProviderStageRetryResolutionV1:
    """One authenticated same-request sequence of stage-occurrence chains."""

    request_sha256: str
    chain_ids: tuple[str, ...]
    envelopes: tuple[ProviderStageRetryStatusEnvelopeV1, ...]
    completion_response: ClientResponseV1 | None
    actions: tuple[Mapping[str, Any], ...]
    critical_failure: Mapping[str, Any] | None
    retry_action_count: int
    resume_prepared_action_count: int
    repair_recording_action_count: int
    duration_ms: int

    @property
    def terminal_status(self) -> Mapping[str, Any]:
        if not self.envelopes:
            raise StateConflictError("qualification provider-stage observations are empty")
        return cast(Mapping[str, Any], self.envelopes[-1]["status"])


class QualificationClient(Protocol):
    def complete(
        self,
        *,
        fixture: QualificationFixtureV1,
        session_id: str,
        payload: Mapping[str, Any],
    ) -> ClientResponseV1: ...

    def regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
    ) -> ClientResponseV1: ...

    def review(self, *, review_id: str) -> ClientResponseV1: ...

    def review_action(
        self,
        *,
        fixture: QualificationFixtureV1,
        review_id: str,
        action: Mapping[str, Any],
    ) -> ClientResponseV1: ...

    def terminal_review_decision(self, *, review_id: str) -> ClientResponseV1: ...

    def provider_stage_retry_status(self, *, chain_id: str) -> ClientResponseV1: ...

    def provider_stage_retry_action(
        self,
        *,
        chain_id: str,
        action: Mapping[str, Any],
    ) -> ClientResponseV1: ...


@dataclass(frozen=True, slots=True)
class ProviderLedgerSnapshotV1:
    sol_events: tuple[Mapping[str, Any], ...]
    deepseek_events: tuple[Mapping[str, Any], ...]

    @classmethod
    def load(cls, runtime_root: Path) -> ProviderLedgerSnapshotV1:
        root = runtime_root.resolve()
        return cls(
            sol_events=_load_sol_events(root / "SOL_PROVIDER_CALLS.jsonl"),
            deepseek_events=_load_deepseek_events(root / "DEEPSEEK_PROVIDER_OPERATIONS.jsonl"),
        )

    @property
    def sol_transport_operations(self) -> int:
        return sum(value.get("state") == "transport_invoked" for value in self.sol_events)

    @property
    def sol_charged_operations(self) -> int:
        return _sol_charged_operation_count(self.sol_events)

    @property
    def deepseek_started_operations(self) -> int:
        return sum(
            value.get("event") == "provider_operation_started" for value in self.deepseek_events
        )

    def delta_from(self, prior: ProviderLedgerSnapshotV1) -> ProviderLedgerDeltaV1:
        if self.sol_events[: len(prior.sol_events)] != prior.sol_events:
            raise StateConflictError("Sol provider ledger changed its existing prefix")
        if self.deepseek_events[: len(prior.deepseek_events)] != prior.deepseek_events:
            raise StateConflictError("DeepSeek provider ledger changed its existing prefix")
        return ProviderLedgerDeltaV1(
            sol_events=self.sol_events[len(prior.sol_events) :],
            deepseek_events=self.deepseek_events[len(prior.deepseek_events) :],
        )


@dataclass(frozen=True, slots=True)
class ProviderLedgerDeltaV1:
    sol_events: tuple[Mapping[str, Any], ...]
    deepseek_events: tuple[Mapping[str, Any], ...]

    @property
    def sol_transport_operations(self) -> int:
        return sum(value.get("state") == "transport_invoked" for value in self.sol_events)

    @property
    def sol_charged_operations(self) -> int:
        return _sol_charged_operation_count(self.sol_events)

    @property
    def deepseek_started_operations(self) -> int:
        return sum(
            value.get("event") == "provider_operation_started" for value in self.deepseek_events
        )

    @property
    def deepseek_completed_operations(self) -> int:
        return sum(
            value.get("event") == "provider_operation_completed" for value in self.deepseek_events
        )

    @property
    def deepseek_cached_input_tokens(self) -> int:
        return sum(
            _nonnegative_int(value.get("cached_input_tokens"), default=0)
            for value in self.deepseek_events
            if value.get("event") == "provider_operation_completed"
        )

    @property
    def deepseek_input_tokens(self) -> int:
        return sum(
            _nonnegative_int(value.get("input_tokens"), default=0)
            for value in self.deepseek_events
            if value.get("event") == "provider_operation_completed"
        )


class QualificationEvidenceStore:
    """Append concise hash-only evidence before returning each fixture result."""

    def __init__(self, root: Path) -> None:
        if not root.is_absolute():
            raise ContractValidationError("qualification evidence root must be absolute")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "QUALIFICATION_EVENTS.jsonl"

    def append(self, payload: Mapping[str, Any]) -> None:
        value = {
            **dict(payload),
            "recorded_at_utc": datetime.now(UTC).isoformat(timespec="microseconds"),
        }
        with self.events_path.open("ab") as stream:
            stream.write(canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def publish_result(self, phase: QualificationPhase, payload: Mapping[str, Any]) -> Path:
        path = self.root / f"{phase.value.upper()}_RESULT.json"
        if path.exists():
            raise StateConflictError("qualification phase result is already published")
        _atomic_write_json(path, payload)
        return path


class FullModelQualificationRunner:
    """Run one fixed phase through a supplied direct or SillyTavern client."""

    def __init__(
        self,
        *,
        manifest: Mapping[str, Any],
        runtime_root: Path,
        evidence_root: Path,
        manual_action_authorizer: QualificationManualActionAuthorizer | None = None,
    ) -> None:
        validate_qualification_manifest(manifest)
        self.manifest = dict(manifest)
        self.runtime_root = runtime_root.resolve()
        self.evidence = QualificationEvidenceStore(evidence_root.resolve())
        self.manual_action_authorizer = manual_action_authorizer

    def authorize_manual_action(
        self,
        request: QualificationManualActionRequestV1,
    ) -> QualificationManualActionAuthorizationV1:
        pending = request.pending_projection()
        self.evidence.append(
            {
                **pending,
                "event": "manual_action_required",
            }
        )
        authorizer = self.manual_action_authorizer
        if authorizer is None:
            raise ManualActionRequiredError(request.checkpoint_sha256)
        authorization = authorizer.authorize(request)
        if authorization is None:
            raise ManualActionRequiredError(request.checkpoint_sha256)
        _validate_manual_action_authorization(request, authorization)
        if (
            authorization.authorization_source == "provider_free_simulation"
            and not provider_dispatch_disabled()
        ):
            raise StateConflictError(
                "simulated manual authorization requires provider dispatch to be disabled"
            )
        self.evidence.append(
            {
                **pending,
                "event": "externally_authorized_manual_action",
                "status": "externally_authorized",
                "authorization_source": authorization.authorization_source,
                "authorization_id_sha256": text_sha256(authorization.authorization_id),
            }
        )
        return authorization

    def consume_manual_action(
        self,
        request: QualificationManualActionRequestV1,
        authorization: QualificationManualActionAuthorizationV1,
    ) -> None:
        _validate_manual_action_authorization(request, authorization)
        if (
            authorization.authorization_source == "provider_free_simulation"
            and not provider_dispatch_disabled()
        ):
            raise StateConflictError(
                "simulated manual authorization lost the provider-dispatch guard"
            )
        authorizer = self.manual_action_authorizer
        if authorizer is None:
            raise ManualActionRequiredError(request.checkpoint_sha256)
        marker = getattr(authorizer, "mark_consumed", None)
        if callable(marker):
            marker(request, authorization)
        elif authorization.authorization_source != "provider_free_simulation":
            raise StateConflictError(
                "external manual authorizer cannot consume an exact authorization"
            )
        self.evidence.append(
            {
                **request.pending_projection(),
                "event": "externally_authorized_manual_action_revalidated",
                "status": "consumed_for_one_dispatch",
                "authorization_source": authorization.authorization_source,
                "authorization_id_sha256": text_sha256(authorization.authorization_id),
            }
        )

    def run_phase(
        self,
        phase: QualificationPhase,
        fixtures: Sequence[QualificationFixtureV1],
        client: QualificationClient,
    ) -> dict[str, Any]:
        campaign = self.start_phase(phase, fixtures)
        campaign.run_segment(
            client=client,
            runtime_root=self.runtime_root,
            turn_count=len(campaign.fixtures),
        )
        return campaign.finish()

    def start_phase(
        self,
        phase: QualificationPhase,
        fixtures: Sequence[QualificationFixtureV1],
    ) -> QualificationCampaignRun:
        return QualificationCampaignRun(parent=self, phase=phase, fixtures=fixtures)


class QualificationCampaignRun:
    """One sequential accepted branch that may cross a deliberate restart."""

    def __init__(
        self,
        *,
        parent: FullModelQualificationRunner,
        phase: QualificationPhase,
        fixtures: Sequence[QualificationFixtureV1],
    ) -> None:
        selected = _ordered_phase_fixtures(phase, fixtures)
        _validate_phase_fixture_counts(phase, selected)
        _validate_phase_route_lineage(selected)
        self.parent = parent
        self.phase = phase
        self.fixtures = selected
        self.session_id = _session_id(parent.manifest, phase)
        self.next_index = 0
        self.history: list[dict[str, str]] = []
        results: list[dict[str, Any]] = []
        self.results = results
        self.failure: BaseException | None = None
        self.sol_operations = 0
        self.deepseek_operations = 0
        self.deepseek_cached_input_tokens = 0
        self.deepseek_input_tokens = 0
        self.planner_operation_count = 0
        self.planner_thread_operation_counts: dict[str, int] = {}
        self.provider_operation_records: list[dict[str, Any]] = []
        self.provider_stage_retry_actions = 0
        self.provider_stage_resume_prepared_actions = 0
        self.provider_stage_repair_recording_actions = 0
        self.provider_stage_retry_chains = 0
        self.provider_stage_retry_terminal_critical_failures = 0
        self.externally_authorized_manual_actions = 0
        self.semantic_regenerate_actions = 0
        self.review_recording_repair_actions = 0
        self.restart_count = 0

    def run_segment(
        self,
        *,
        client: QualificationClient,
        runtime_root: Path,
        turn_count: int,
        restarted: bool = False,
    ) -> None:
        if self.failure is not None:
            raise StateConflictError("failed qualification campaign cannot continue")
        if type(turn_count) is not int or turn_count < 1:
            raise ContractValidationError("qualification segment turn count is invalid")
        stop = self.next_index + turn_count
        if stop > len(self.fixtures):
            raise ContractValidationError("qualification segment exceeds its fixture set")
        if restarted:
            self.restart_count += 1
        segment_root = runtime_root.resolve()
        for offset in range(self.next_index, stop):
            fixture = self.fixtures[offset]
            turn_index = offset + 1
            externally_authorized_before = self.externally_authorized_manual_actions
            semantic_regenerate_before = self.semantic_regenerate_actions
            review_recording_repair_before = self.review_recording_repair_actions
            provider_stage_control_before = (
                self.provider_stage_retry_actions
                + self.provider_stage_resume_prepared_actions
                + self.provider_stage_repair_recording_actions
            )
            before = ProviderLedgerSnapshotV1.load(segment_root)
            request_messages = [*self.history, {"role": "user", "content": fixture.user_source}]
            payload = qualification_request_payload(
                fixture,
                session_id=self.session_id,
                messages=request_messages,
            )
            planner_latency: list[dict[str, Any]] | None = None
            retry_resolutions: list[ProviderStageRetryResolutionV1] = []
            standing_policy_provisional_acceptances = 0
            first_pass_policy_provisional = False
            try:
                initial_response = client.complete(
                    fixture=fixture,
                    session_id=self.session_id,
                    payload=payload,
                )
                self.parent.evidence.append(
                    {
                        "schema_version": "cera.pi_scene.qualification_http_event.v1",
                        "event": "client_http_first_pass_completed",
                        "fixture_id": fixture.fixture_id,
                        "phase": self.phase.value,
                        "turn_index": turn_index,
                        "transport": initial_response.transport,
                        "path": initial_response.path,
                        "status_code": initial_response.status_code,
                        "duration_ms": initial_response.duration_ms,
                        "response_sha256": canonical_sha256(initial_response.body),
                    }
                )
                retry_resolution = self._resolve_manual_provider_stage_retry(
                    fixture=fixture,
                    turn_index=turn_index,
                    client=client,
                    runtime_root=segment_root,
                    before=before,
                    response=initial_response,
                )
                if retry_resolution is not None and retry_resolution.completion_response is None:
                    self._record_critical_provider_stage_retry_failure(
                        fixture=fixture,
                        turn_index=turn_index,
                        runtime_root=segment_root,
                        before=before,
                        initial_response=initial_response,
                        externally_authorized_before=externally_authorized_before,
                        semantic_regenerate_before=semantic_regenerate_before,
                        review_recording_repair_before=review_recording_repair_before,
                        provider_stage_control_before=provider_stage_control_before,
                        resolutions=[*retry_resolutions, retry_resolution],
                    )
                    break
                response = initial_response
                if retry_resolution is not None:
                    assert retry_resolution.completion_response is not None
                    retry_resolutions.append(retry_resolution)
                    response = retry_resolution.completion_response
                regeneration: ClientResponseV1 | None = None
                regeneration_http_duration_ms = 0
                review_http_duration_ms = 0
                terminal_decision_sha256: str | None = None
                final_review_sha256: str | None = None
                projections: list[Mapping[str, Any]] = []
                projection: Mapping[str, Any]
                accepted_response: ClientResponseV1
                if fixture.expected_route is QualificationRoute.ORDINARY:
                    initial_provisional = _validate_ordinary_provisional_completion(
                        fixture,
                        response,
                    )
                    first_join = self._resolve_ordinary_review(
                        fixture=fixture,
                        turn_index=turn_index,
                        client=client,
                        runtime_root=segment_root,
                        before=before,
                        provisional=initial_provisional,
                    )
                    if isinstance(first_join, OrdinaryReviewStopV1):
                        self._record_critical_provider_stage_retry_failure(
                            fixture=fixture,
                            turn_index=turn_index,
                            runtime_root=segment_root,
                            before=before,
                            initial_response=initial_response,
                            externally_authorized_before=externally_authorized_before,
                            semantic_regenerate_before=semantic_regenerate_before,
                            review_recording_repair_before=review_recording_repair_before,
                            provider_stage_control_before=provider_stage_control_before,
                            resolutions=[
                                *retry_resolutions,
                                *first_join.retry_resolutions,
                            ],
                        )
                        break
                    retry_resolutions.extend(first_join.retry_resolutions)
                    review_http_duration_ms += first_join.http_duration_ms
                    first = first_join.result
                    first_pass = isinstance(first, Mapping) and first["first_pass_accepted"] is True
                    if isinstance(first, RejectionReviewV1):
                        self.parent.evidence.append(
                            {
                                "schema_version": (
                                    "cera.pi_scene.qualification_first_pass_rejection.v1"
                                ),
                                "event": "first_pass_rejected",
                                "fixture_id": fixture.fixture_id,
                                "phase": self.phase.value,
                                "turn_index": turn_index,
                                "review_id_sha256": text_sha256(first.review_id),
                                "conflict_sha256": first.conflict_sha256,
                                "provider_operations": first.provider_operations,
                            }
                        )
                        review_http_duration_ms += self._authorize_ordinary_review_action(
                            fixture=fixture,
                            turn_index=turn_index,
                            client=client,
                            provisional=initial_provisional,
                            review=first_join.review,
                            action_kind="regenerate",
                            action_family="semantic_regenerate",
                        )
                        self.semantic_regenerate_actions += 1
                        regenerate_post_failure: Exception | None = None
                        try:
                            regeneration = client.regenerate(
                                fixture=fixture,
                                review_id=first.review_id,
                            )
                            regeneration_http_duration_ms += regeneration.duration_ms
                        except Exception as exc:
                            # The POST may have reached the backend. Never replay it;
                            # ordinary v2 exposes the exact terminal transition by GET.
                            regenerate_post_failure = exc
                            regeneration, reconciliation_duration_ms = (
                                self._reconcile_lost_ordinary_regenerate(
                                    fixture=fixture,
                                    client=client,
                                    review=first_join.review,
                                )
                            )
                            regeneration_http_duration_ms += reconciliation_duration_ms
                        self.parent.evidence.append(
                            {
                                "schema_version": "cera.pi_scene.qualification_http_event.v1",
                                "event": "externally_authorized_regenerate_observed",
                                "fixture_id": fixture.fixture_id,
                                "phase": self.phase.value,
                                "turn_index": turn_index,
                                "transport": regeneration.transport,
                                "path": regeneration.path,
                                "status_code": regeneration.status_code,
                                "duration_ms": regeneration.duration_ms,
                                "response_sha256": canonical_sha256(regeneration.body),
                                "post_response_lost": regenerate_post_failure is not None,
                            }
                        )
                        decision_response = regeneration
                        regeneration_retry = self._resolve_manual_provider_stage_retry(
                            fixture=fixture,
                            turn_index=turn_index,
                            client=client,
                            runtime_root=segment_root,
                            before=before,
                            response=regeneration,
                        )
                        if (
                            regeneration_retry is not None
                            and regeneration_retry.completion_response is None
                        ):
                            self._record_critical_provider_stage_retry_failure(
                                fixture=fixture,
                                turn_index=turn_index,
                                runtime_root=segment_root,
                                before=before,
                                initial_response=regeneration,
                                externally_authorized_before=externally_authorized_before,
                                semantic_regenerate_before=semantic_regenerate_before,
                                review_recording_repair_before=review_recording_repair_before,
                                provider_stage_control_before=provider_stage_control_before,
                                resolutions=[
                                    *retry_resolutions,
                                    regeneration_retry,
                                ],
                            )
                            break
                        if regeneration_retry is not None:
                            assert regeneration_retry.completion_response is not None
                            retry_resolutions.append(regeneration_retry)
                            decision_response = regeneration_retry.completion_response
                        terminal_regeneration = client.terminal_review_decision(
                            review_id=first.review_id
                        )
                        regeneration_http_duration_ms += terminal_regeneration.duration_ms
                        if terminal_regeneration.status_code != 200 or canonical_sha256(
                            terminal_regeneration.body
                        ) != canonical_sha256(decision_response.body):
                            raise StateConflictError(
                                "ordinary Regenerate terminal decision changed after dispatch"
                            )
                        decision_response = terminal_regeneration
                        successor_provisional = _validate_ordinary_regenerate_response(
                            fixture,
                            decision_response,
                            predecessor=first_join.review,
                        )
                        successor_join = self._resolve_ordinary_review(
                            fixture=fixture,
                            turn_index=turn_index,
                            client=client,
                            runtime_root=segment_root,
                            before=before,
                            provisional=successor_provisional,
                            standing_policy_after_regenerate=True,
                        )
                        if isinstance(successor_join, OrdinaryReviewStopV1):
                            self._record_critical_provider_stage_retry_failure(
                                fixture=fixture,
                                turn_index=turn_index,
                                runtime_root=segment_root,
                                before=before,
                                initial_response=regeneration,
                                externally_authorized_before=externally_authorized_before,
                                semantic_regenerate_before=semantic_regenerate_before,
                                review_recording_repair_before=review_recording_repair_before,
                                provider_stage_control_before=provider_stage_control_before,
                                resolutions=[
                                    *retry_resolutions,
                                    *successor_join.retry_resolutions,
                                ],
                            )
                            break
                        retry_resolutions.extend(successor_join.retry_resolutions)
                        review_http_duration_ms += successor_join.http_duration_ms
                        if isinstance(successor_join.result, RejectionReviewV1):
                            raise StateConflictError(
                                "ordinary qualification exhausted its one Regenerate"
                            )
                        projection = successor_join.result
                        accepted_response = successor_provisional.response
                        final_review_sha256 = canonical_sha256(successor_join.review)
                        terminal_decision_sha256 = successor_join.terminal_decision_sha256
                    else:
                        projection = first
                        accepted_response = initial_provisional.response
                        final_review_sha256 = canonical_sha256(first_join.review)
                        terminal_decision_sha256 = first_join.terminal_decision_sha256
                    # review.v2 provider accounting is request-total across an
                    # explicit Regenerate, so only the terminal projection is
                    # reconciled with the append-only ledgers.
                    projections.append(projection)
                    policy_audit = projection.get("standing_policy_audit")
                    if isinstance(policy_audit, Mapping):
                        standing_policy_provisional_acceptances = 1
                        first_pass_policy_provisional = bool(
                            projection.get("first_pass_policy_provisional", False)
                        )
                        self.parent.evidence.append(
                            {
                                "schema_version": (
                                    "cera.pi_scene.qualification_standing_policy.v1"
                                ),
                                "event": (
                                    "first_pass_policy_provisional"
                                    if projection.get("first_pass_policy_provisional") is True
                                    else "regenerate_successor_policy_provisional"
                                ),
                                "fixture_id": fixture.fixture_id,
                                "phase": self.phase.value,
                                "turn_index": turn_index,
                                "candidate_sha256": policy_audit["candidate_sha256"],
                                "policy_sha256": policy_audit["policy_sha256"],
                                "audit_sha256": policy_audit["audit_sha256"],
                                "semantic_validation_sha256": (
                                    policy_audit["semantic_validation_sha256"]
                                ),
                                "reader_validation_sha256": (
                                    policy_audit["reader_validation_sha256"]
                                ),
                                "python_qualification_sha256": (
                                    policy_audit["python_qualification_sha256"]
                                ),
                                "tolerated_reason_codes": list(
                                    policy_audit["tolerated_reason_codes"]
                                ),
                            }
                        )
                else:
                    first = _classify_completion_response(fixture, response)
                    first_pass = isinstance(first, Mapping) and first["first_pass_accepted"] is True
                    if isinstance(first, RejectionReviewV1):
                        self.parent.evidence.append(
                            {
                                "schema_version": (
                                    "cera.pi_scene.qualification_first_pass_rejection.v1"
                                ),
                                "event": "first_pass_rejected",
                                "fixture_id": fixture.fixture_id,
                                "phase": self.phase.value,
                                "turn_index": turn_index,
                                "review_id_sha256": text_sha256(first.review_id),
                                "conflict_sha256": first.conflict_sha256,
                                "provider_operations": first.provider_operations,
                            }
                        )
                        review_http_duration_ms += self._authorize_adult_regenerate(
                            fixture=fixture,
                            turn_index=turn_index,
                            client=client,
                            rejection=first,
                        )
                        self.semantic_regenerate_actions += 1
                        try:
                            regeneration = client.regenerate(
                                fixture=fixture,
                                review_id=first.review_id,
                            )
                            regeneration_http_duration_ms += regeneration.duration_ms
                        except Exception as exc:
                            # Adult review.v1 has no terminal successor-decision GET.
                            # Re-read only to classify the ambiguity, never to replay.
                            try:
                                ambiguous_review = client.review(review_id=first.review_id)
                                regeneration_http_duration_ms += ambiguous_review.duration_ms
                                _validate_adult_rejection_review_authority(
                                    ambiguous_review,
                                    rejection=first,
                                )
                            except Exception as reconciliation_exc:
                                raise StateConflictError(
                                    "adult Regenerate response was lost after an authority "
                                    "transition; action was not replayed"
                                ) from reconciliation_exc
                            raise StateConflictError(
                                "adult Regenerate response was lost with unchanged authority; "
                                "action was not replayed"
                            ) from exc
                        self.parent.evidence.append(
                            {
                                "schema_version": ("cera.pi_scene.qualification_http_event.v1"),
                                "event": "externally_authorized_regenerate_observed",
                                "fixture_id": fixture.fixture_id,
                                "phase": self.phase.value,
                                "turn_index": turn_index,
                                "transport": regeneration.transport,
                                "path": regeneration.path,
                                "status_code": regeneration.status_code,
                                "duration_ms": regeneration.duration_ms,
                                "response_sha256": canonical_sha256(regeneration.body),
                            }
                        )
                        regeneration_retry = self._resolve_manual_provider_stage_retry(
                            fixture=fixture,
                            turn_index=turn_index,
                            client=client,
                            runtime_root=segment_root,
                            before=before,
                            response=regeneration,
                        )
                        if (
                            regeneration_retry is not None
                            and regeneration_retry.completion_response is None
                        ):
                            self._record_critical_provider_stage_retry_failure(
                                fixture=fixture,
                                turn_index=turn_index,
                                runtime_root=segment_root,
                                before=before,
                                initial_response=regeneration,
                                externally_authorized_before=externally_authorized_before,
                                semantic_regenerate_before=semantic_regenerate_before,
                                review_recording_repair_before=review_recording_repair_before,
                                provider_stage_control_before=provider_stage_control_before,
                                resolutions=[*retry_resolutions, regeneration_retry],
                            )
                            break
                        if regeneration_retry is not None:
                            assert regeneration_retry.completion_response is not None
                            retry_resolutions.append(regeneration_retry)
                            regeneration = regeneration_retry.completion_response
                        successor = _successor_completion(regeneration)
                        projection = _validate_completion_response(
                            fixture,
                            successor,
                            regenerated=True,
                        )
                        projections.extend((first.projection, projection))
                    else:
                        projection = first
                        projections.append(projection)
                    accepted_response = (
                        response if regeneration is None else _successor_completion(regeneration)
                    )
                accepted_prose = _visible_prose(accepted_response.body)
                self.history.extend(
                    (
                        {"role": "user", "content": fixture.user_source},
                        {"role": "assistant", "content": accepted_prose},
                    )
                )
                after = ProviderLedgerSnapshotV1.load(segment_root)
                delta = after.delta_from(before)
                operation_records = _provider_operation_records(delta)
                self.provider_operation_records.extend(operation_records)
                retry_chains: list[dict[str, Any]] = []
                for observed_resolution in retry_resolutions:
                    retry_chain = _finalize_provider_stage_retry_chain(observed_resolution)
                    retry_chains.append(retry_chain)
                    self.provider_stage_retry_chains += len(observed_resolution.chain_ids)
                    self.parent.evidence.append(
                        {
                            "schema_version": (
                                "cera.pi_scene.qualification_provider_stage_retry_chain.v1"
                            ),
                            "event": "manual_provider_stage_retry_succeeded",
                            "fixture_id": fixture.fixture_id,
                            "phase": self.phase.value,
                            "turn_index": turn_index,
                            **retry_chain,
                        }
                    )
                telemetry = _validate_provider_delta(
                    fixture,
                    projections,
                    delta,
                    provider_stage_retry_chains=retry_chains,
                )
                planner_latency = self._planner_latency_observations(operation_records)
                total_http_latency_ms = (
                    initial_response.duration_ms
                    + sum(value.duration_ms for value in retry_resolutions)
                    + regeneration_http_duration_ms
                    + review_http_duration_ms
                )
                provider_transport_duration_ms = sum(
                    cast(int, operation["duration_ms"])
                    for operation in operation_records
                    if _provider_operation_has_transport(operation)
                    and type(operation.get("duration_ms")) is int
                )
                for operation in operation_records:
                    self.parent.evidence.append(
                        {
                            "schema_version": ("cera.pi_scene.qualification_provider_operation.v1"),
                            "event": "provider_http_operation",
                            "fixture_id": fixture.fixture_id,
                            "phase": self.phase.value,
                            "turn_index": turn_index,
                            **operation,
                        }
                    )
                provider_stage_control_actions = (
                    self.provider_stage_retry_actions
                    + self.provider_stage_resume_prepared_actions
                    + self.provider_stage_repair_recording_actions
                    - provider_stage_control_before
                )
                explicit_regenerate_actions = (
                    self.semantic_regenerate_actions - semantic_regenerate_before
                )
                review_recording_repair_actions = (
                    self.review_recording_repair_actions - review_recording_repair_before
                )
                externally_authorized_manual_actions = (
                    self.externally_authorized_manual_actions - externally_authorized_before
                )
                if externally_authorized_manual_actions != (
                    provider_stage_control_actions
                    + explicit_regenerate_actions
                    + review_recording_repair_actions
                ):
                    raise StateConflictError(
                        "qualification external manual-action accounting diverged"
                    )
                result = {
                    "fixture_id": fixture.fixture_id,
                    **_fixture_novelty_evidence(fixture),
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    "initial_route": fixture.initial_route.value,
                    "expected_route": fixture.expected_route.value,
                    "expected_next_route": fixture.expected_next_route.value,
                    "observed_route": projection["observed_route"],
                    "observed_next_route": projection["observed_next_route"],
                    "session_id_sha256": text_sha256(self.session_id),
                    "source_sha256": text_sha256(fixture.user_source),
                    "status": "passed",
                    "first_pass_accepted": first_pass,
                    "first_pass_policy_provisional": first_pass_policy_provisional,
                    "standing_policy_provisional_acceptances": int(
                        standing_policy_provisional_acceptances
                    ),
                    "explicit_regenerate_actions": explicit_regenerate_actions,
                    "review_recording_repair_actions": review_recording_repair_actions,
                    "externally_authorized_manual_actions": (externally_authorized_manual_actions),
                    "provider_stage_retry_actions": sum(
                        value.retry_action_count for value in retry_resolutions
                    ),
                    "provider_stage_resume_prepared_actions": sum(
                        value.resume_prepared_action_count for value in retry_resolutions
                    ),
                    "provider_stage_repair_recording_actions": sum(
                        value.repair_recording_action_count for value in retry_resolutions
                    ),
                    "provider_stage_control_actions": provider_stage_control_actions,
                    "automatic_provider_stage_control_actions": 0,
                    "automatic_manual_actions": 0,
                    "provider_stage_retry_chains": retry_chains,
                    "automatic_repair_actions": projection["automatic_repair_actions"],
                    "initial_http_response_sha256": canonical_sha256(initial_response.body),
                    "first_pass_response_sha256": canonical_sha256(response.body),
                    "response_sha256": canonical_sha256(accepted_response.body),
                    "final_review_sha256": final_review_sha256,
                    "terminal_review_decision_sha256": terminal_decision_sha256,
                    "visible_prose_sha256": projection["visible_prose_sha256"],
                    "accepted_turn_id": projection["accepted_turn_id"],
                    "accepted_receipt_sha256": projection["accepted_receipt_sha256"],
                    "provider_operations": projection["provider_operations"],
                    "latency_ms": total_http_latency_ms,
                    "provider_transport_duration_ms": provider_transport_duration_ms,
                    "non_provider_http_duration_ms": max(
                        0,
                        total_http_latency_ms - provider_transport_duration_ms,
                    ),
                    "planner_latency": planner_latency,
                    **telemetry,
                }
                self.results.append(result)
                self.sol_operations += delta.sol_charged_operations
                self.deepseek_operations += delta.deepseek_started_operations
                self.deepseek_cached_input_tokens += delta.deepseek_cached_input_tokens
                self.deepseek_input_tokens += delta.deepseek_input_tokens
                self.parent.evidence.append(
                    {
                        "schema_version": "cera.pi_scene.qualification_fixture_result.v2",
                        "event": "fixture_passed",
                        **result,
                    }
                )
            except BaseException as exc:
                self.failure = exc
                after = ProviderLedgerSnapshotV1.load(segment_root)
                delta = after.delta_from(before)
                failed_operation_records = _provider_operation_records(delta)
                self.provider_operation_records.extend(failed_operation_records)
                if planner_latency is None:
                    planner_latency = self._planner_latency_observations(failed_operation_records)
                failed_provider_stage_control_actions = (
                    self.provider_stage_retry_actions
                    + self.provider_stage_resume_prepared_actions
                    + self.provider_stage_repair_recording_actions
                    - provider_stage_control_before
                )
                failed_explicit_regenerate_actions = (
                    self.semantic_regenerate_actions - semantic_regenerate_before
                )
                failed_review_recording_repair_actions = (
                    self.review_recording_repair_actions - review_recording_repair_before
                )
                failed_external_actions = (
                    self.externally_authorized_manual_actions - externally_authorized_before
                )
                failed = {
                    "fixture_id": fixture.fixture_id,
                    **_fixture_novelty_evidence(fixture),
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    "initial_route": fixture.initial_route.value,
                    "expected_route": fixture.expected_route.value,
                    "expected_next_route": fixture.expected_next_route.value,
                    "session_id_sha256": text_sha256(self.session_id),
                    "source_sha256": text_sha256(fixture.user_source),
                    "status": "failed",
                    **_closed_failure_projection(exc),
                    "first_pass_policy_provisional": first_pass_policy_provisional,
                    "standing_policy_provisional_acceptances": (
                        standing_policy_provisional_acceptances
                    ),
                    "explicit_regenerate_actions": failed_explicit_regenerate_actions,
                    "review_recording_repair_actions": (failed_review_recording_repair_actions),
                    "provider_stage_control_actions": (failed_provider_stage_control_actions),
                    "externally_authorized_manual_actions": failed_external_actions,
                    "automatic_provider_stage_control_actions": 0,
                    "automatic_manual_actions": 0,
                    "sol_operations_observed": delta.sol_transport_operations,
                    "sol_charged_operations_observed": delta.sol_charged_operations,
                    "deepseek_operations_observed": delta.deepseek_started_operations,
                    "planner_latency": planner_latency,
                }
                self.results.append(failed)
                self.sol_operations += delta.sol_charged_operations
                self.deepseek_operations += delta.deepseek_started_operations
                self.parent.evidence.append(
                    {
                        "schema_version": "cera.pi_scene.qualification_fixture_result.v2",
                        "event": "fixture_failed",
                        **failed,
                    }
                )
                break
            self.next_index = turn_index
        if self.sol_operations > int(
            cast(Mapping[str, int], self.parent.manifest["provider_ceilings"])["sol"]
        ):
            raise StateConflictError("qualification exceeded its Sol ceiling")
        if self.deepseek_operations > int(
            cast(Mapping[str, int], self.parent.manifest["provider_ceilings"])[
                "deepseek_http_operations"
            ]
        ):
            raise StateConflictError("qualification exceeded its DeepSeek ceiling")

    def _resolve_manual_provider_stage_retry(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        client: QualificationClient,
        runtime_root: Path,
        before: ProviderLedgerSnapshotV1,
        response: ClientResponseV1,
        candidate_sha256: str | None = None,
    ) -> ProviderStageRetryResolutionV1 | None:
        initial = _provider_stage_retry_envelope(response)
        if initial is None:
            return None
        policy = self.parent.manifest.get("execution_policy")
        retry_policy = (
            policy.get("manual_provider_stage_retry") if isinstance(policy, Mapping) else None
        )
        if retry_policy != MANUAL_PROVIDER_STAGE_RETRY_POLICY:
            raise StateConflictError("qualification provider-stage Retry is not authorized")
        status_reader = getattr(client, "provider_stage_retry_status", None)
        action_writer = getattr(client, "provider_stage_retry_action", None)
        if not callable(status_reader) or not callable(action_writer):
            raise StateConflictError("qualification client lacks provider-stage Retry support")

        request_sha256 = _provider_stage_request_sha256(initial)
        envelopes: list[ProviderStageRetryStatusEnvelopeV1] = []
        chain_ids: list[str] = []
        chain_bindings: dict[str, tuple[object, ...]] = {}
        actions: list[Mapping[str, Any]] = []
        posted_action_ids: set[str] = set()
        posted_retry_actions_by_chain: dict[str, int] = {}
        posted_resume_actions_by_chain: dict[str, int] = {}
        retry_action_count = 0
        resume_prepared_action_count = 0
        repair_recording_action_count = 0
        total_duration_ms = 0
        current = initial
        blocked_started: float | None = None
        polling_started = time.monotonic()
        maximum_chains = (
            QUALIFICATION_MAX_SEQUENTIAL_PROVIDER_STAGES
            if fixture.expected_route is QualificationRoute.ORDINARY
            else 3
        )

        def resolution(
            *,
            completion: ClientResponseV1 | None,
            critical: Mapping[str, Any] | None,
        ) -> ProviderStageRetryResolutionV1:
            return ProviderStageRetryResolutionV1(
                request_sha256=request_sha256,
                chain_ids=tuple(chain_ids),
                envelopes=tuple(envelopes),
                completion_response=completion,
                actions=tuple(actions),
                critical_failure=critical,
                retry_action_count=retry_action_count,
                resume_prepared_action_count=resume_prepared_action_count,
                repair_recording_action_count=repair_recording_action_count,
                duration_ms=total_duration_ms,
            )

        while True:
            status = _remember_provider_stage_envelope(
                current,
                request_sha256=request_sha256,
                expected_route=fixture.expected_route,
                observations=envelopes,
                chain_ids=chain_ids,
                chain_bindings=chain_bindings,
                maximum_chains=maximum_chains,
            )
            state = cast(str, status["state"])
            chain_id = cast(str, status["chain_id"])
            exposed_actions = current["actions"]
            if state in {"attempts_exhausted", "recovery_required"} or (
                state == "recording_repair_required" and not exposed_actions
            ):
                critical = _critical_provider_stage_projection(current)
                return resolution(completion=None, critical=critical)

            expected_action_kind = _manual_provider_stage_action_kind(current)
            if expected_action_kind is not None:
                # Refresh authority immediately before the sole provider-bearing
                # action. Any state change restarts the loop without dispatch.
                pre_response = status_reader(chain_id=chain_id)
                total_duration_ms += pre_response.duration_ms
                pre_envelope = _provider_stage_retry_envelope(pre_response)
                if pre_envelope is None:
                    completion = _authenticated_provider_stage_completion(pre_response)
                    return resolution(completion=completion, critical=None)
                pre_status = _remember_provider_stage_envelope(
                    pre_envelope,
                    request_sha256=request_sha256,
                    expected_route=fixture.expected_route,
                    observations=envelopes,
                    chain_ids=chain_ids,
                    chain_bindings=chain_bindings,
                    maximum_chains=maximum_chains,
                )
                if _manual_provider_stage_action_kind(pre_envelope) != expected_action_kind:
                    current = pre_envelope
                    continue
                backend_actions = pre_envelope["actions"]
                if len(backend_actions) != 1:
                    raise StateConflictError(
                        "qualification manual stage status did not expose one backend action"
                    )
                action = validate_provider_stage_retry_action_v1(backend_actions[0])
                if (
                    action["action_kind"] != expected_action_kind
                    or action["automatic"] is not False
                    or action["provider_dispatch_authorized"] is not True
                    or action["chain_id"] != chain_id
                ):
                    raise StateConflictError(
                        "qualification backend action is not an exact manual stage control"
                    )
                ordinal = action["retry_action_ordinal"]
                if expected_action_kind == "provider_retry":
                    accepted = cast(int, pre_status["retry_actions_accepted"])
                    if (
                        action["consumes_retry_action"] is not True
                        or ordinal != accepted + 1
                        or ordinal not in {1, 2}
                    ):
                        raise StateConflictError("qualification Provider Retry ordinal changed")
                    chain_action_count = posted_retry_actions_by_chain.get(chain_id, 0)
                    if chain_action_count >= 2:
                        raise StateConflictError(
                            "qualification backend exposed a fourth provider-stage attempt"
                        )
                elif action["consumes_retry_action"] is not False or ordinal is not None:
                    raise StateConflictError(
                        "qualification manual control consumed Provider Retry authority"
                    )
                elif expected_action_kind == "resume_prepared":
                    chain_action_count = posted_resume_actions_by_chain.get(chain_id, 0)
                    if chain_action_count >= 1:
                        raise StateConflictError(
                            "qualification prepared-resume control budget was exceeded"
                        )
                elif expected_action_kind == "repair_recording":
                    if (
                        pre_status["stage"] != "recorder"
                        or pre_status["story_state_committed"] is not True
                        or repair_recording_action_count >= 1
                    ):
                        raise StateConflictError(
                            "qualification exposed recursive or invalid Recorder repair"
                        )
                    chain_action_count = 0
                else:  # pragma: no cover - closed by helper and generated schema
                    raise StateConflictError("qualification manual stage action changed")
                if action["action_id"] in posted_action_ids:
                    raise StateConflictError(
                        "qualification backend re-exposed an already POSTed action"
                    )

                manual_request = QualificationManualActionRequestV1(
                    qualification_id=str(self.parent.manifest["qualification_id"]),
                    phase=self.phase,
                    turn_index=turn_index,
                    fixture_id=fixture.fixture_id,
                    action_family="provider_stage_control",
                    action_kind=expected_action_kind,
                    action_id=action["action_id"],
                    exact_action=dict(action),
                    authority_sha256=canonical_sha256(pre_envelope),
                    chain_id=chain_id,
                    candidate_sha256=candidate_sha256,
                    stage=cast(str, pre_status["stage"]),
                    provider=cast(str, pre_status["provider"]),
                    model_family=cast(str, pre_status["model_family"]),
                    retry_action_ordinal=ordinal,
                )
                authorization = self.parent.authorize_manual_action(manual_request)

                # External approval is per exact action, not standing Retry
                # authority. Re-read immediately before dispatch and reject a
                # stale approval instead of adapting it or posting a successor.
                authorized_response = status_reader(chain_id=chain_id)
                total_duration_ms += authorized_response.duration_ms
                authorized_envelope = _provider_stage_retry_envelope(authorized_response)
                if authorized_envelope is None:
                    raise StateConflictError(
                        "qualification manual provider-stage authority disappeared"
                    )
                authorized_status = _remember_provider_stage_envelope(
                    authorized_envelope,
                    request_sha256=request_sha256,
                    expected_route=fixture.expected_route,
                    observations=envelopes,
                    chain_ids=chain_ids,
                    chain_bindings=chain_bindings,
                    maximum_chains=maximum_chains,
                )
                authorized_actions = authorized_envelope["actions"]
                if (
                    canonical_sha256(authorized_envelope) != canonical_sha256(pre_envelope)
                    or authorized_status["chain_id"] != chain_id
                    or len(authorized_actions) != 1
                    or dict(authorized_actions[0]) != dict(action)
                ):
                    raise StateConflictError(
                        "qualification manual provider-stage authority changed before dispatch"
                    )
                self.parent.consume_manual_action(manual_request, authorization)
                self.externally_authorized_manual_actions += 1

                # Advance the local one-shot boundary before POST. If delivery
                # is ambiguous, this exact action identity is never sent again.
                posted_action_ids.add(action["action_id"])
                if expected_action_kind == "provider_retry":
                    posted_retry_actions_by_chain[chain_id] = chain_action_count + 1
                    retry_action_count += 1
                    self.provider_stage_retry_actions += 1
                elif expected_action_kind == "resume_prepared":
                    posted_resume_actions_by_chain[chain_id] = chain_action_count + 1
                    resume_prepared_action_count += 1
                    self.provider_stage_resume_prepared_actions += 1
                else:
                    repair_recording_action_count += 1
                    self.provider_stage_repair_recording_actions += 1
                action_ledger_before = ProviderLedgerSnapshotV1.load(runtime_root)
                technical = cast(Mapping[str, Any], pre_status["technical_details"])
                dispatch_event = {
                    "schema_version": (
                        "cera.pi_scene.qualification_provider_stage_control_action.v1"
                    ),
                    "event": "manual_provider_stage_control_dispatched",
                    "fixture_id": fixture.fixture_id,
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    "stage": pre_status["stage"],
                    "provider": pre_status["provider"],
                    "model_family": pre_status["model_family"],
                    "chain_id": chain_id,
                    "chain_id_sha256": text_sha256(chain_id),
                    "action_id": action["action_id"],
                    "action_id_sha256": text_sha256(action["action_id"]),
                    "action_kind": expected_action_kind,
                    "consumes_retry_action": action["consumes_retry_action"],
                    "retry_action_ordinal": ordinal,
                    "request_sha256": request_sha256,
                    "stage_input_sha256": technical["stage_input_sha256"],
                    "exact_action_sha256": canonical_sha256(action),
                    "pre_status_sha256": canonical_sha256(pre_envelope),
                }
                self.parent.evidence.append(dispatch_event)

                post_response: ClientResponseV1 | None = None
                post_failure: dict[str, str] | None = None
                post_started_ns = time.perf_counter_ns()
                try:
                    post_response = action_writer(
                        chain_id=chain_id,
                        action=dict(action),
                    )
                    post_envelope = _provider_stage_retry_envelope(post_response)
                    if post_envelope is not None:
                        _validate_provider_stage_successor_request(
                            post_envelope,
                            request_sha256=request_sha256,
                        )
                except Exception as exc:
                    post_failure = _closed_failure_projection(exc)
                post_duration_ms = max(
                    0,
                    (time.perf_counter_ns() - post_started_ns) // 1_000_000,
                )
                total_duration_ms += post_duration_ms

                # POST is not authority. Reconcile the old chain through GET;
                # the backend authenticates any same-request later-stage chain.
                get_response = status_reader(chain_id=chain_id)
                total_duration_ms += get_response.duration_ms
                get_envelope = _provider_stage_retry_envelope(get_response)
                action_ledger_after = ProviderLedgerSnapshotV1.load(runtime_root)
                action_delta = action_ledger_after.delta_from(action_ledger_before)
                receipt = {
                    **dispatch_event,
                    "event": "manual_provider_stage_control_reconciled",
                    "post_status_code": (
                        None if post_response is None else post_response.status_code
                    ),
                    "post_response_sha256": (
                        None if post_response is None else canonical_sha256(post_response.body)
                    ),
                    "post_failure": post_failure,
                    "post_duration_ms": post_duration_ms,
                    "get_status_code": get_response.status_code,
                    "get_response_sha256": canonical_sha256(get_response.body),
                    "get_duration_ms": get_response.duration_ms,
                    "provider_operation_delta": _safe_provider_operation_delta(action_delta),
                }
                actions.append(receipt)
                self.parent.evidence.append(receipt)
                if get_envelope is None:
                    completion = _authenticated_provider_stage_completion(get_response)
                    return resolution(completion=completion, critical=None)
                current = get_envelope
                blocked_started = None
                continue

            if state == "blocked_ambiguous":
                if blocked_started is None:
                    blocked_started = time.monotonic()
                if (
                    time.monotonic() - blocked_started
                    >= PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS
                ):
                    critical = _critical_provider_stage_projection(
                        current,
                        stop_reason="blocked_ambiguous_timeout",
                    )
                    return resolution(completion=None, critical=critical)
            else:
                blocked_started = None
            if time.monotonic() - polling_started >= PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS:
                raise StateConflictError(
                    "qualification provider-stage GET reconciliation timed out"
                )
            poll_response = status_reader(chain_id=chain_id)
            total_duration_ms += poll_response.duration_ms
            poll_envelope = _provider_stage_retry_envelope(poll_response)
            if poll_envelope is None:
                completion = _authenticated_provider_stage_completion(poll_response)
                return resolution(completion=completion, critical=None)
            current = poll_envelope
            if state in {"in_progress", "succeeded", "blocked_ambiguous"}:
                time.sleep(PROVIDER_STAGE_RETRY_STATUS_POLL_SECONDS)

    def _resolve_ordinary_review(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        client: QualificationClient,
        runtime_root: Path,
        before: ProviderLedgerSnapshotV1,
        provisional: OrdinaryProvisionalCompletionV1,
        standing_policy_after_regenerate: bool = False,
    ) -> OrdinaryReviewResolutionV1 | OrdinaryReviewStopV1:
        """Join one provisional candidate using read-only review reconciliation.

        The initial POST starts the backend-owned validation join.  From this
        point qualification only polls, executes an exact backend-issued stage
        action, or performs the separately authorized Recorder repair action.
        It never treats a browser projection as acceptance authority.
        """

        review_reader = getattr(client, "review", None)
        review_action = getattr(client, "review_action", None)
        terminal_reader = getattr(client, "terminal_review_decision", None)
        if not callable(review_reader):
            raise StateConflictError("qualification client lacks ordinary review GET")
        if not callable(review_action):
            raise StateConflictError("qualification client lacks ordinary review action")
        if not callable(terminal_reader):
            raise StateConflictError("qualification client lacks terminal review-decision GET")

        retry_resolutions: list[ProviderStageRetryResolutionV1] = []
        total_duration_ms = 0
        started = time.monotonic()
        recorder_repair_posted = False
        current_response: ClientResponseV1 | None = None
        while True:
            if current_response is None:
                current_response = review_reader(review_id=provisional.review_id)
                total_duration_ms += current_response.duration_ms
            recorder_retry = self._resolve_manual_provider_stage_retry(
                fixture=fixture,
                turn_index=turn_index,
                client=client,
                runtime_root=runtime_root,
                before=before,
                response=current_response,
                candidate_sha256=provisional.candidate_sha256,
            )
            if recorder_retry is not None:
                retry_resolutions.append(recorder_retry)
                total_duration_ms += recorder_retry.duration_ms
                if recorder_retry.completion_response is None:
                    return OrdinaryReviewStopV1(tuple(retry_resolutions))
                # Recorder continuation may return the protected original
                # completion or review-action decision.  The joined review GET
                # is the sole canonical projection after recording is repaired.
                current_response = None
                continue
            review = _validate_ordinary_review_response(
                fixture,
                current_response,
                provisional=provisional,
            )

            lane_envelopes = _ordinary_review_lane_retry_envelopes(review)
            if lane_envelopes:
                # Luna and Reader are independent.  Re-read the joined review
                # after each exact lane action so concurrent authority cannot
                # be overwritten or inferred from the peer lane.
                lane_envelope = lane_envelopes[0]
                lane_response = ClientResponseV1(
                    transport="ordinary_review_provider_stage_status",
                    path=(
                        "/v1/cera/provider-stage-retries/"
                        + cast(
                            str,
                            cast(Mapping[str, Any], lane_envelope["status"])["chain_id"],
                        )
                    ),
                    status_code=409,
                    duration_ms=0,
                    body=lane_envelope,
                )
                retry = self._resolve_manual_provider_stage_retry(
                    fixture=fixture,
                    turn_index=turn_index,
                    client=client,
                    runtime_root=runtime_root,
                    before=before,
                    response=lane_response,
                    candidate_sha256=provisional.candidate_sha256,
                )
                if retry is None:
                    raise StateConflictError(
                        "ordinary review lost its generated provider-stage status"
                    )
                retry_resolutions.append(retry)
                if retry.completion_response is None:
                    return OrdinaryReviewStopV1(tuple(retry_resolutions))
                current_response = retry.completion_response
                continue

            state = review["state"]
            gate_status = review["gate_status"]
            if state == "review_ready" and gate_status == "reject":
                rejection = _ordinary_rejection_review(fixture, review)
                return OrdinaryReviewResolutionV1(
                    result=rejection,
                    review=review,
                    retry_resolutions=tuple(retry_resolutions),
                    http_duration_ms=total_duration_ms,
                    terminal_decision_sha256=None,
                )
            if state == "accepted":
                recording_status = review["recording_status"]
                actions = cast(Mapping[str, Any], review["actions"])
                if recording_status == "complete":
                    terminal_response = terminal_reader(review_id=provisional.review_id)
                    total_duration_ms += terminal_response.duration_ms
                    terminal_projection_failure: StateConflictError | None = None
                    try:
                        decision = _validate_ordinary_terminal_decision_response(
                            fixture,
                            terminal_response,
                            review=review,
                        )
                        projection = _validate_ordinary_accepted_review(
                            fixture,
                            review,
                            standing_policy_after_regenerate=(
                                standing_policy_after_regenerate
                            ),
                        )
                    except StateConflictError as exc:
                        terminal_projection_failure = exc
                    if terminal_projection_failure is not None:
                        # Review and terminal-decision GETs are separate
                        # read-only projections. A background-owned terminal
                        # transition can make any cross-projection invariant
                        # temporarily stale, so require a fresh exact pair.
                        # The same strict validators still have to pass before
                        # this fixture progresses; no action or provider call
                        # is authorized by this reconciliation.
                        if (
                            time.monotonic() - started
                            >= PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS
                        ):
                            raise terminal_projection_failure
                        time.sleep(PROVIDER_STAGE_RETRY_STATUS_POLL_SECONDS)
                        current_response = None
                        continue
                    return OrdinaryReviewResolutionV1(
                        result=projection,
                        review=review,
                        retry_resolutions=tuple(retry_resolutions),
                        http_duration_ms=total_duration_ms,
                        terminal_decision_sha256=canonical_sha256(decision),
                    )
                if actions.get("repair_recording_enabled") is True:
                    if recorder_repair_posted:
                        raise StateConflictError("qualification Recorder repair authority recurred")
                    total_duration_ms += self._authorize_ordinary_review_action(
                        fixture=fixture,
                        turn_index=turn_index,
                        client=client,
                        provisional=provisional,
                        review=review,
                        action_kind="repair_recording",
                        action_family="recording_repair",
                    )
                    recorder_repair_posted = True
                    self.review_recording_repair_actions += 1
                    try:
                        repair_response = review_action(
                            fixture=fixture,
                            review_id=provisional.review_id,
                            action={"action": "repair_recording"},
                        )
                    except Exception:
                        # Delivery is ambiguous. The action identity is consumed
                        # locally and the joined review GET is the only recovery.
                        current_response = None
                        continue
                    total_duration_ms += repair_response.duration_ms
                    retry = self._resolve_manual_provider_stage_retry(
                        fixture=fixture,
                        turn_index=turn_index,
                        client=client,
                        runtime_root=runtime_root,
                        before=before,
                        response=repair_response,
                        candidate_sha256=provisional.candidate_sha256,
                    )
                    if retry is not None:
                        retry_resolutions.append(retry)
                        if retry.completion_response is None:
                            return OrdinaryReviewStopV1(tuple(retry_resolutions))
                        repair_response = retry.completion_response
                    _validate_optional_ordinary_repair_response(
                        fixture,
                        repair_response,
                        review_id=provisional.review_id,
                    )
                    current_response = None
                    continue
                if recording_status not in {"projection_pending", "pending_repair"}:
                    raise StateConflictError(
                        "qualification accepted review changed recording state"
                    )
            elif state == "checks_pending":
                if gate_status == "blocked":
                    raise StateConflictError(
                        "ordinary review has a non-retryable validation failure"
                    )
                if gate_status not in {"pending", "reject"}:
                    raise StateConflictError("ordinary pending review changed its gate state")
            elif state == "review_ready" and gate_status == "pass":
                # Automatic qualification never supplies creator acceptance.
                # A pass must be autoaccepted by backend authority.
                pass
            else:
                raise StateConflictError("ordinary review reached an unauthorized terminal state")

            if time.monotonic() - started >= PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS:
                raise StateConflictError("ordinary review GET reconciliation timed out")
            time.sleep(PROVIDER_STAGE_RETRY_STATUS_POLL_SECONDS)
            current_response = None

    def _reconcile_lost_ordinary_regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        client: QualificationClient,
        review: OrdinaryReviewV3,
    ) -> tuple[ClientResponseV1, int]:
        started = time.monotonic()
        total_duration_ms = 0
        last_failure: Exception | None = None
        while True:
            try:
                response = client.terminal_review_decision(review_id=review["review_id"])
            except Exception as exc:
                last_failure = exc
            else:
                total_duration_ms += response.duration_ms
                if response.status_code == 200:
                    _validate_ordinary_regenerate_response(
                        fixture,
                        response,
                        predecessor=review,
                    )
                    return response, total_duration_ms
                if response.status_code not in {404, 409}:
                    raise StateConflictError(
                        "ordinary Regenerate terminal reconciliation returned "
                        f"HTTP {response.status_code}; action was not replayed"
                    )
                last_failure = StateConflictError(
                    "ordinary Regenerate terminal decision is not durable yet"
                )
            if time.monotonic() - started >= PROVIDER_STAGE_RETRY_STATUS_TIMEOUT_SECONDS:
                raise StateConflictError(
                    "ordinary Regenerate response was lost and its terminal decision "
                    "did not become durable; action was not replayed"
                ) from last_failure
            time.sleep(PROVIDER_STAGE_RETRY_STATUS_POLL_SECONDS)

    def _authorize_ordinary_review_action(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        client: QualificationClient,
        provisional: OrdinaryProvisionalCompletionV1,
        review: OrdinaryReviewV3,
        action_kind: str,
        action_family: str,
    ) -> int:
        actions = cast(Mapping[str, Any], review["actions"])
        enabled_field = (
            "regenerate_enabled" if action_kind == "regenerate" else "repair_recording_enabled"
        )
        if actions.get(enabled_field) is not True:
            raise StateConflictError("qualification review action is not currently authorized")
        exact_action = {"action": action_kind}
        action_id = "review-action-" + text_sha256(
            f"{review['review_id']}:{review['candidate_sha256']}:{action_kind}"
        )
        request = QualificationManualActionRequestV1(
            qualification_id=str(self.parent.manifest["qualification_id"]),
            phase=self.phase,
            turn_index=turn_index,
            fixture_id=fixture.fixture_id,
            action_family=action_family,
            action_kind=action_kind,
            action_id=action_id,
            exact_action=exact_action,
            authority_sha256=canonical_sha256(review),
            review_id=review["review_id"],
            candidate_sha256=review["candidate_sha256"],
        )
        authorization = self.parent.authorize_manual_action(request)
        refreshed_response = client.review(review_id=review["review_id"])
        refreshed = _validate_ordinary_review_response(
            fixture,
            refreshed_response,
            provisional=provisional,
        )
        refreshed_actions = cast(Mapping[str, Any], refreshed["actions"])
        if (
            canonical_sha256(refreshed) != request.authority_sha256
            or refreshed_actions.get(enabled_field) is not True
        ):
            raise StateConflictError(
                "qualification manual review authority changed before dispatch"
            )
        self.parent.consume_manual_action(request, authorization)
        self.externally_authorized_manual_actions += 1
        return refreshed_response.duration_ms

    def _authorize_adult_regenerate(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        client: QualificationClient,
        rejection: RejectionReviewV1,
    ) -> int:
        initial_response = client.review(review_id=rejection.review_id)
        initial = _validate_adult_rejection_review_authority(
            initial_response,
            rejection=rejection,
        )
        candidate_sha256 = cast(str, initial["candidate_sha256"])
        action = {"action": "regenerate"}
        request = QualificationManualActionRequestV1(
            qualification_id=str(self.parent.manifest["qualification_id"]),
            phase=self.phase,
            turn_index=turn_index,
            fixture_id=fixture.fixture_id,
            action_family="semantic_regenerate",
            action_kind="regenerate",
            action_id=(
                "review-action-"
                + text_sha256(f"{rejection.review_id}:{candidate_sha256}:regenerate")
            ),
            exact_action=action,
            authority_sha256=canonical_sha256(initial),
            review_id=rejection.review_id,
            candidate_sha256=candidate_sha256,
        )
        authorization = self.parent.authorize_manual_action(request)
        refreshed_response = client.review(review_id=rejection.review_id)
        refreshed = _validate_adult_rejection_review_authority(
            refreshed_response,
            rejection=rejection,
        )
        if canonical_sha256(refreshed) != request.authority_sha256:
            raise StateConflictError(
                "qualification adult manual review authority changed before dispatch"
            )
        self.parent.consume_manual_action(request, authorization)
        self.externally_authorized_manual_actions += 1
        return initial_response.duration_ms + refreshed_response.duration_ms

    def _record_critical_provider_stage_retry_failure(
        self,
        *,
        fixture: QualificationFixtureV1,
        turn_index: int,
        runtime_root: Path,
        before: ProviderLedgerSnapshotV1,
        initial_response: ClientResponseV1,
        externally_authorized_before: int,
        semantic_regenerate_before: int,
        review_recording_repair_before: int,
        provider_stage_control_before: int,
        resolutions: Sequence[ProviderStageRetryResolutionV1],
    ) -> None:
        if not resolutions:
            raise StateConflictError("qualification critical Retry resolution is missing")
        resolution = resolutions[-1]
        critical = resolution.critical_failure
        if critical is None or resolution.completion_response is not None:
            raise StateConflictError("qualification critical Retry resolution changed")
        after = ProviderLedgerSnapshotV1.load(runtime_root)
        delta = after.delta_from(before)
        operation_records = _provider_operation_records(delta)
        chains = [_finalize_provider_stage_retry_chain(value) for value in resolutions]
        _validate_terminal_provider_stage_accounting(
            resolution,
            operation_records=operation_records,
        )
        planner_latency = self._planner_latency_observations(operation_records)
        self.provider_operation_records.extend(operation_records)
        for operation in operation_records:
            self.parent.evidence.append(
                {
                    "schema_version": "cera.pi_scene.qualification_provider_operation.v1",
                    "event": "provider_http_operation",
                    "fixture_id": fixture.fixture_id,
                    "phase": self.phase.value,
                    "turn_index": turn_index,
                    **operation,
                }
            )
        self.provider_stage_retry_chains += sum(len(value.chain_ids) for value in resolutions)
        self.provider_stage_retry_terminal_critical_failures += 1
        terminal_state = cast(str, resolution.terminal_status["state"])
        failure_identity = {
            "failure_type": "CriticalProviderStageError",
            "failure_category": f"provider_stage_{terminal_state}",
        }
        provider_stage_control_actions = (
            self.provider_stage_retry_actions
            + self.provider_stage_resume_prepared_actions
            + self.provider_stage_repair_recording_actions
            - provider_stage_control_before
        )
        explicit_regenerate_actions = self.semantic_regenerate_actions - semantic_regenerate_before
        review_recording_repair_actions = (
            self.review_recording_repair_actions - review_recording_repair_before
        )
        externally_authorized_manual_actions = (
            self.externally_authorized_manual_actions - externally_authorized_before
        )
        failed = {
            "fixture_id": fixture.fixture_id,
            **_fixture_novelty_evidence(fixture),
            "phase": self.phase.value,
            "turn_index": turn_index,
            "initial_route": fixture.initial_route.value,
            "expected_route": fixture.expected_route.value,
            "expected_next_route": fixture.expected_next_route.value,
            "session_id_sha256": text_sha256(self.session_id),
            "source_sha256": text_sha256(fixture.user_source),
            "status": "failed",
            **failure_identity,
            "explicit_regenerate_actions": explicit_regenerate_actions,
            "review_recording_repair_actions": review_recording_repair_actions,
            "externally_authorized_manual_actions": externally_authorized_manual_actions,
            "failure_sha256": canonical_sha256(failure_identity),
            "critical_provider_stage_failure": dict(critical),
            "provider_stage_retry_actions": sum(value.retry_action_count for value in resolutions),
            "provider_stage_resume_prepared_actions": sum(
                value.resume_prepared_action_count for value in resolutions
            ),
            "provider_stage_repair_recording_actions": sum(
                value.repair_recording_action_count for value in resolutions
            ),
            "provider_stage_control_actions": provider_stage_control_actions,
            "provider_stage_retry_chains": chains,
            "automatic_provider_stage_retry_actions": 0,
            "automatic_provider_stage_control_actions": 0,
            "automatic_manual_actions": 0,
            "fallback_used": False,
            "initial_http_response_sha256": canonical_sha256(initial_response.body),
            "latency_ms": initial_response.duration_ms
            + sum(value.duration_ms for value in resolutions),
            "planner_latency": planner_latency,
            "sol_operations_observed": delta.sol_transport_operations,
            "sol_charged_operations_observed": delta.sol_charged_operations,
            "deepseek_operations_observed": delta.deepseek_started_operations,
            "provider_operation_evidence_sha256": canonical_sha256(operation_records),
        }
        self.results.append(failed)
        self.sol_operations += delta.sol_charged_operations
        self.deepseek_operations += delta.deepseek_started_operations
        self.deepseek_cached_input_tokens += delta.deepseek_cached_input_tokens
        self.deepseek_input_tokens += delta.deepseek_input_tokens
        self.parent.evidence.append(
            {
                "schema_version": "cera.pi_scene.qualification_fixture_result.v4",
                "event": "fixture_failed_critical_provider_stage",
                **failed,
            }
        )
        self.failure = _CriticalProviderStageError(terminal_state)

    def _planner_latency_observations(
        self,
        operation_records: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        planner_operations = [
            operation
            for operation in operation_records
            if operation.get("provider_family") == "sol" and operation.get("owner") == "planner"
        ]
        for operation in planner_operations:
            session_identity_sha256 = operation.get("session_identity_sha256")
            duration_ms = operation.get("duration_ms")
            if not isinstance(session_identity_sha256, str) or not re_is_sha256(
                session_identity_sha256
            ):
                raise StateConflictError("qualification Planner session identity is invalid")
            if duration_ms is not None and (type(duration_ms) is not int or duration_ms < 0):
                raise StateConflictError("qualification Planner duration is invalid")
            if type(operation.get("submitted")) is not bool:
                raise StateConflictError("qualification Planner submission state is invalid")

        observations: list[dict[str, Any]] = []
        for operation in planner_operations:
            session_identity_sha256 = cast(str, operation["session_identity_sha256"])
            if operation["submitted"] is False:
                # A proven local pretransport failure is not a model call and
                # consumes no grant. Retain the physical thread identity so a
                # later fresh replacement is still classified as rehydration.
                self.planner_thread_operation_counts.setdefault(session_identity_sha256, 0)
                continue
            self.planner_operation_count += 1
            planner_thread_call_index = (
                self.planner_thread_operation_counts.get(session_identity_sha256, 0) + 1
            )
            rehydrated_after_thread_rotation = planner_thread_call_index == 1 and bool(
                self.planner_thread_operation_counts
            )
            self.planner_thread_operation_counts[session_identity_sha256] = (
                planner_thread_call_index
            )
            duration_ms = operation.get("duration_ms")
            cold_start = planner_thread_call_index == 1
            retained_concern = (
                not cold_start
                and type(duration_ms) is int
                and duration_ms >= RETAINED_PLANNER_LATENCY_CONCERN_MS
            )
            observations.append(
                {
                    "planner_call_index": self.planner_operation_count,
                    "planner_thread_call_index": planner_thread_call_index,
                    "operation_id": operation["operation_id"],
                    "session_identity_sha256": session_identity_sha256,
                    "duration_ms": duration_ms,
                    "latency_class": (
                        ("cold_rehydration" if rehydrated_after_thread_rotation else "cold_start")
                        if cold_start
                        else (
                            "retained_latency_concern"
                            if retained_concern
                            else "retained_within_target"
                        )
                    ),
                    "cold_start": cold_start,
                    "rehydrated_after_thread_rotation": (rehydrated_after_thread_rotation),
                    "retained_latency_concern": retained_concern,
                    "retained_concern_threshold_ms": (RETAINED_PLANNER_LATENCY_CONCERN_MS),
                }
            )
        return observations

    def finish(self) -> dict[str, Any]:
        required = len(self.fixtures)
        planner_latency = [
            observation
            for result in self.results
            for observation in cast(Sequence[Mapping[str, Any]], result["planner_latency"])
        ]
        cold_start_latency = next(
            (
                observation["duration_ms"]
                for observation in planner_latency
                if observation["cold_start"] is True
            ),
            None,
        )
        retained_latencies = [
            observation["duration_ms"]
            for observation in planner_latency
            if observation["cold_start"] is False and type(observation["duration_ms"]) is int
        ]
        rehydration_latencies = [
            observation["duration_ms"]
            for observation in planner_latency
            if observation["rehydrated_after_thread_rotation"] is True
            and type(observation["duration_ms"]) is int
        ]
        passed = (
            self.failure is None and self.next_index == required and len(self.results) == required
        )
        provider_stage_control_actions = (
            self.provider_stage_retry_actions
            + self.provider_stage_resume_prepared_actions
            + self.provider_stage_repair_recording_actions
        )
        if self.externally_authorized_manual_actions != (
            provider_stage_control_actions
            + self.semantic_regenerate_actions
            + self.review_recording_repair_actions
        ):
            raise StateConflictError(
                "qualification phase external manual-action accounting diverged"
            )
        result_payload = {
            "schema_version": QUALIFICATION_RESULT_SCHEMA,
            "qualification_id": self.parent.manifest["qualification_id"],
            "manifest_sha256": self.parent.manifest["manifest_sha256"],
            "phase": self.phase.value,
            "status": "passed" if passed else "failed",
            "required_fixtures": required,
            "passed_fixtures": sum(value["status"] == "passed" for value in self.results),
            "first_pass_accepted": sum(
                value.get("first_pass_accepted") is True for value in self.results
            ),
            "first_pass_policy_provisional": sum(
                value.get("first_pass_policy_provisional") is True for value in self.results
            ),
            "standing_policy_provisional_acceptances": sum(
                int(value.get("standing_policy_provisional_acceptances", 0))
                for value in self.results
            ),
            "explicit_regenerate_actions": sum(
                int(value.get("explicit_regenerate_actions", 0)) for value in self.results
            ),
            "review_recording_repair_actions": self.review_recording_repair_actions,
            "externally_authorized_manual_actions": (self.externally_authorized_manual_actions),
            "provider_stage_retry_actions": self.provider_stage_retry_actions,
            "provider_stage_resume_prepared_actions": (self.provider_stage_resume_prepared_actions),
            "provider_stage_repair_recording_actions": (
                self.provider_stage_repair_recording_actions
            ),
            "provider_stage_control_actions": provider_stage_control_actions,
            "provider_stage_retry_chains": self.provider_stage_retry_chains,
            "provider_stage_retry_chain_actions": sum(
                int(chain.get("retry_action_count", 0))
                for result in self.results
                for chain in cast(
                    Sequence[Mapping[str, Any]],
                    result.get("provider_stage_retry_chains", ()),
                )
            ),
            "provider_stage_control_chain_actions": sum(
                int(chain.get("control_action_count", 0))
                for result in self.results
                for chain in cast(
                    Sequence[Mapping[str, Any]],
                    result.get("provider_stage_retry_chains", ()),
                )
            ),
            "provider_stage_retry_terminal_critical_failures": (
                self.provider_stage_retry_terminal_critical_failures
            ),
            "automatic_provider_stage_retry_actions": 0,
            "automatic_provider_stage_control_actions": 0,
            "automatic_manual_actions": 0,
            "automatic_repair_actions": sum(
                int(value.get("automatic_repair_actions", 0)) for value in self.results
            ),
            "sequential_session_id_sha256": text_sha256(self.session_id),
            "retained_conversation_messages": len(self.history),
            "restart_count": self.restart_count,
            "sol_operations": self.sol_operations,
            "sol_submitted_operations": sum(
                int(value.get("sol_http_operations", value.get("sol_operations_observed", 0)))
                for value in self.results
            ),
            "sol_charged_operations": self.sol_operations,
            "deepseek_http_operations": self.deepseek_operations,
            "deepseek_cached_input_tokens": self.deepseek_cached_input_tokens,
            "deepseek_input_tokens": self.deepseek_input_tokens,
            "planner_latency_summary": {
                "cold_start_latency_ms": cold_start_latency,
                "retained_concern_threshold_ms": (RETAINED_PLANNER_LATENCY_CONCERN_MS),
                "cold_rehydration_calls": sum(
                    observation["rehydrated_after_thread_rotation"] is True
                    for observation in planner_latency
                ),
                "maximum_cold_rehydration_latency_ms": (
                    max(rehydration_latencies) if rehydration_latencies else None
                ),
                "retained_planner_calls": sum(
                    observation["cold_start"] is False for observation in planner_latency
                ),
                "retained_latency_concern_count": sum(
                    observation["retained_latency_concern"] is True
                    for observation in planner_latency
                ),
                "maximum_retained_latency_ms": (
                    max(retained_latencies) if retained_latencies else None
                ),
                "average_retained_latency_ms": (
                    round(sum(retained_latencies) / len(retained_latencies))
                    if retained_latencies
                    else None
                ),
            },
            "planner_session_latency_summary": _planner_session_latency_summary(planner_latency),
            "provider_stage_latency_summary": _provider_stage_latency_summary(
                self.provider_operation_records,
                planner_latency,
            ),
            "provider_transport_latency_evidence": _provider_transport_latency_evidence(
                self.provider_operation_records,
                planner_latency,
            ),
            "http_latency_summary": _latency_summary(
                [
                    cast(int, value["latency_ms"])
                    for value in self.results
                    if type(value.get("latency_ms")) is int
                ],
                failures=sum(value["status"] == "failed" for value in self.results),
            ),
            "non_provider_http_latency_summary": _latency_summary(
                [
                    cast(int, value["non_provider_http_duration_ms"])
                    for value in self.results
                    if type(value.get("non_provider_http_duration_ms")) is int
                ],
                failures=sum(value["status"] == "failed" for value in self.results),
            ),
            "phase_timing_evidence": _phase_timing_evidence(
                phase=self.phase,
                results=self.results,
                provider_records=self.provider_operation_records,
            ),
            "results": self.results,
        }
        result_payload["result_sha256"] = canonical_sha256(result_payload)
        self.parent.evidence.publish_result(self.phase, result_payload)
        if self.failure is not None:
            failure = _closed_failure_projection(self.failure)
            checkpoint = failure.get("manual_action_checkpoint_sha256")
            checkpoint_suffix = "" if checkpoint is None else f" ({checkpoint})"
            raise StateConflictError(
                "qualification failed at "
                f"{self.results[-1]['fixture_id']}: {failure['failure_category']}"
                f"{checkpoint_suffix}"
            ) from None
        if not passed:
            raise StateConflictError("qualification campaign ended before all accepted turns")
        return result_payload


def load_qualification_fixtures(path: Path) -> tuple[QualificationFixtureV1, ...]:
    cache = _QUALIFICATION_FIXTURE_LOAD_CACHE.get()
    token = None
    if cache is None:
        cache = {}
        token = _QUALIFICATION_FIXTURE_LOAD_CACHE.set(cache)
    try:
        fixture_bytes = path.read_bytes()
        cache_key = (path.resolve(), bytes_sha256(fixture_bytes))
        if cache_key in cache:
            return cache[cache_key]
        raw = json.loads(fixture_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        if token is not None:
            _QUALIFICATION_FIXTURE_LOAD_CACHE.reset(token)
        raise ContractValidationError("qualification fixture file is unreadable") from exc
    try:
        if not isinstance(raw, Mapping):
            raise ContractValidationError("qualification fixture envelope changed")
        schema_version = raw.get("schema_version")
        if schema_version == QUALIFICATION_FIXTURE_SCHEMA_V1:
            if set(raw) != {"schema_version", "fixtures"}:
                raise ContractValidationError("qualification fixture envelope changed")
            fixtures = _decode_qualification_fixture_rows(
                raw.get("fixtures"),
                schema_version=QUALIFICATION_FIXTURE_SCHEMA_V1,
            )
        elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V2:
            if set(raw) != {
                "schema_version",
                "baseline_fixture_path",
                "baseline_fixture_sha256",
                "fixtures",
            }:
                raise ContractValidationError("qualification fixture envelope changed")
            fixtures = _decode_qualification_fixture_rows(
                raw.get("fixtures"),
                schema_version=QUALIFICATION_FIXTURE_SCHEMA_V2,
            )
            _validate_v2_qualification_novelty_baseline(
                fixture_path=path,
                fixtures=fixtures,
                baseline_fixture_path=raw.get("baseline_fixture_path"),
                baseline_fixture_sha256=raw.get("baseline_fixture_sha256"),
            )
        elif schema_version in {
            QUALIFICATION_FIXTURE_SCHEMA_V3,
            QUALIFICATION_FIXTURE_SCHEMA_V4,
            QUALIFICATION_FIXTURE_SCHEMA_V5,
            QUALIFICATION_FIXTURE_SCHEMA_V6,
            QUALIFICATION_FIXTURE_SCHEMA_V7,
            QUALIFICATION_FIXTURE_SCHEMA_V8,
            QUALIFICATION_FIXTURE_SCHEMA_V9,
            QUALIFICATION_FIXTURE_SCHEMA_V10,
            QUALIFICATION_FIXTURE_SCHEMA_V11,
            QUALIFICATION_FIXTURE_SCHEMA_V12,
            QUALIFICATION_FIXTURE_SCHEMA_V13,
            QUALIFICATION_FIXTURE_SCHEMA_V14,
            QUALIFICATION_FIXTURE_SCHEMA_V15,
            QUALIFICATION_FIXTURE_SCHEMA_V16,
            QUALIFICATION_FIXTURE_SCHEMA_V17,
            QUALIFICATION_FIXTURE_SCHEMA_V18,
            QUALIFICATION_FIXTURE_SCHEMA_V19,
            QUALIFICATION_FIXTURE_SCHEMA_V20,
            QUALIFICATION_FIXTURE_SCHEMA_V21,
            QUALIFICATION_FIXTURE_SCHEMA_V22,
            QUALIFICATION_FIXTURE_SCHEMA_V23,
            QUALIFICATION_FIXTURE_SCHEMA_V24,
            QUALIFICATION_FIXTURE_SCHEMA_V25,
            QUALIFICATION_FIXTURE_SCHEMA_V26,
            QUALIFICATION_FIXTURE_SCHEMA_V27,
            QUALIFICATION_FIXTURE_SCHEMA_V28,
            QUALIFICATION_FIXTURE_SCHEMA_V29,
            QUALIFICATION_FIXTURE_SCHEMA_V30,
            QUALIFICATION_FIXTURE_SCHEMA_V31,
            QUALIFICATION_FIXTURE_SCHEMA_V32,
            QUALIFICATION_FIXTURE_SCHEMA_V33,
            QUALIFICATION_FIXTURE_SCHEMA_V34,
            QUALIFICATION_FIXTURE_SCHEMA_V35,
            QUALIFICATION_FIXTURE_SCHEMA_V36,
            QUALIFICATION_FIXTURE_SCHEMA_V37,
            QUALIFICATION_FIXTURE_SCHEMA_V38,
            QUALIFICATION_FIXTURE_SCHEMA,
        }:
            if set(raw) != {
                "schema_version",
                "baseline_fixture_path",
                "baseline_fixture_sha256",
                "baseline_ancestry",
                "fixtures",
            }:
                raise ContractValidationError("qualification fixture envelope changed")
            fixtures = _decode_qualification_fixture_rows(
                raw.get("fixtures"),
                schema_version=str(schema_version),
            )
            validator = {
                QUALIFICATION_FIXTURE_SCHEMA_V3: _validate_v3_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V4: _validate_v4_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V5: _validate_v5_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V6: _validate_v6_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V7: _validate_v7_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V8: _validate_v8_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V9: _validate_v9_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V10: _validate_v10_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V11: _validate_v11_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V12: _validate_v12_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V13: _validate_v13_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V14: _validate_v14_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V15: _validate_v15_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V16: _validate_v16_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V17: _validate_v17_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V18: _validate_v18_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V19: _validate_v19_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V20: _validate_v20_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V21: _validate_v21_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V22: _validate_v22_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V23: _validate_v23_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V24: _validate_v24_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V25: _validate_v25_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V26: _validate_v26_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V27: _validate_v27_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V28: _validate_v28_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V29: _validate_v29_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V30: _validate_v30_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V31: _validate_v31_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V32: _validate_v32_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V33: _validate_v33_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V34: _validate_v34_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V35: _validate_v35_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V36: _validate_v36_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V37: _validate_v37_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA_V38: _validate_v38_qualification_novelty_ancestry,
                QUALIFICATION_FIXTURE_SCHEMA: _validate_v39_qualification_novelty_ancestry,
            }[str(schema_version)]
            validator(
                fixture_path=path,
                fixtures=fixtures,
                baseline_fixture_path=raw.get("baseline_fixture_path"),
                baseline_fixture_sha256=raw.get("baseline_fixture_sha256"),
                baseline_ancestry=raw.get("baseline_ancestry"),
            )
        else:
            raise ContractValidationError("qualification fixture schema changed")
        _validate_qualification_fixture_set(fixtures)
        cache[cache_key] = fixtures
        return fixtures
    finally:
        if token is not None:
            _QUALIFICATION_FIXTURE_LOAD_CACHE.reset(token)


def _decode_qualification_fixture_rows(
    rows: object,
    *,
    schema_version: str,
) -> tuple[QualificationFixtureV1, ...]:
    if schema_version not in {
        QUALIFICATION_FIXTURE_SCHEMA_V1,
        QUALIFICATION_FIXTURE_SCHEMA_V2,
        QUALIFICATION_FIXTURE_SCHEMA_V3,
        QUALIFICATION_FIXTURE_SCHEMA_V4,
        QUALIFICATION_FIXTURE_SCHEMA_V5,
        QUALIFICATION_FIXTURE_SCHEMA_V6,
        QUALIFICATION_FIXTURE_SCHEMA_V7,
        QUALIFICATION_FIXTURE_SCHEMA_V8,
        QUALIFICATION_FIXTURE_SCHEMA_V9,
        QUALIFICATION_FIXTURE_SCHEMA_V10,
        QUALIFICATION_FIXTURE_SCHEMA_V11,
        QUALIFICATION_FIXTURE_SCHEMA_V12,
        QUALIFICATION_FIXTURE_SCHEMA_V13,
        QUALIFICATION_FIXTURE_SCHEMA_V14,
        QUALIFICATION_FIXTURE_SCHEMA_V15,
        QUALIFICATION_FIXTURE_SCHEMA_V16,
        QUALIFICATION_FIXTURE_SCHEMA_V17,
        QUALIFICATION_FIXTURE_SCHEMA_V18,
        QUALIFICATION_FIXTURE_SCHEMA_V19,
        QUALIFICATION_FIXTURE_SCHEMA_V20,
        QUALIFICATION_FIXTURE_SCHEMA_V21,
        QUALIFICATION_FIXTURE_SCHEMA_V22,
        QUALIFICATION_FIXTURE_SCHEMA_V23,
        QUALIFICATION_FIXTURE_SCHEMA_V24,
        QUALIFICATION_FIXTURE_SCHEMA_V25,
        QUALIFICATION_FIXTURE_SCHEMA_V26,
        QUALIFICATION_FIXTURE_SCHEMA_V27,
        QUALIFICATION_FIXTURE_SCHEMA_V28,
        QUALIFICATION_FIXTURE_SCHEMA_V29,
        QUALIFICATION_FIXTURE_SCHEMA_V30,
        QUALIFICATION_FIXTURE_SCHEMA_V31,
        QUALIFICATION_FIXTURE_SCHEMA_V32,
        QUALIFICATION_FIXTURE_SCHEMA_V33,
        QUALIFICATION_FIXTURE_SCHEMA_V34,
        QUALIFICATION_FIXTURE_SCHEMA_V35,
        QUALIFICATION_FIXTURE_SCHEMA_V36,
        QUALIFICATION_FIXTURE_SCHEMA_V37,
        QUALIFICATION_FIXTURE_SCHEMA_V38,
        QUALIFICATION_FIXTURE_SCHEMA,
    }:
        raise ContractValidationError("qualification fixture schema changed")
    if not isinstance(rows, list):
        raise ContractValidationError("qualification fixtures are not a list")
    fixtures: list[QualificationFixtureV1] = []
    common_keys = {
        "fixture_id",
        "phase",
        "initial_route",
        "expected_route",
        "expected_next_route",
        "adult_craft_mode",
        "user_source",
    }
    stress = schema_version != QUALIFICATION_FIXTURE_SCHEMA_V1
    required_keys = common_keys | ({"novelty_id", "stress_tags"} if stress else set())
    for value in rows:
        if not isinstance(value, Mapping) or set(value) != required_keys:
            raise ContractValidationError("qualification fixture shape changed")
        try:
            common: dict[str, Any] = {
                "fixture_id": str(value["fixture_id"]),
                "phase": QualificationPhase(str(value["phase"])),
                "initial_route": QualificationRoute(str(value["initial_route"])),
                "expected_route": QualificationRoute(str(value["expected_route"])),
                "expected_next_route": QualificationRoute(str(value["expected_next_route"])),
                "adult_craft_mode": str(value["adult_craft_mode"]),
                "user_source": str(value["user_source"]),
            }
            fixture: QualificationFixtureV1
            if stress:
                tags = value["stress_tags"]
                if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                    raise ContractValidationError("qualification stress tags are invalid")
                fixture_type = {
                    QUALIFICATION_FIXTURE_SCHEMA_V2: QualificationFixtureV2,
                    QUALIFICATION_FIXTURE_SCHEMA_V3: QualificationFixtureV3,
                    QUALIFICATION_FIXTURE_SCHEMA_V4: QualificationFixtureV4,
                    QUALIFICATION_FIXTURE_SCHEMA_V5: QualificationFixtureV5,
                    QUALIFICATION_FIXTURE_SCHEMA_V6: QualificationFixtureV6,
                    QUALIFICATION_FIXTURE_SCHEMA_V7: QualificationFixtureV7,
                    QUALIFICATION_FIXTURE_SCHEMA_V8: QualificationFixtureV8,
                    QUALIFICATION_FIXTURE_SCHEMA_V9: QualificationFixtureV9,
                    QUALIFICATION_FIXTURE_SCHEMA_V10: QualificationFixtureV10,
                    QUALIFICATION_FIXTURE_SCHEMA_V11: QualificationFixtureV11,
                    QUALIFICATION_FIXTURE_SCHEMA_V12: QualificationFixtureV12,
                    QUALIFICATION_FIXTURE_SCHEMA_V13: QualificationFixtureV13,
                    QUALIFICATION_FIXTURE_SCHEMA_V14: QualificationFixtureV14,
                    QUALIFICATION_FIXTURE_SCHEMA_V15: QualificationFixtureV15,
                    QUALIFICATION_FIXTURE_SCHEMA_V16: QualificationFixtureV16,
                    QUALIFICATION_FIXTURE_SCHEMA_V17: QualificationFixtureV17,
                    QUALIFICATION_FIXTURE_SCHEMA_V18: QualificationFixtureV18,
                    QUALIFICATION_FIXTURE_SCHEMA_V19: QualificationFixtureV19,
                    QUALIFICATION_FIXTURE_SCHEMA_V20: QualificationFixtureV20,
                    QUALIFICATION_FIXTURE_SCHEMA_V21: QualificationFixtureV21,
                    QUALIFICATION_FIXTURE_SCHEMA_V22: QualificationFixtureV22,
                    QUALIFICATION_FIXTURE_SCHEMA_V23: QualificationFixtureV23,
                    QUALIFICATION_FIXTURE_SCHEMA_V24: QualificationFixtureV24,
                    QUALIFICATION_FIXTURE_SCHEMA_V25: QualificationFixtureV25,
                    QUALIFICATION_FIXTURE_SCHEMA_V26: QualificationFixtureV26,
                    QUALIFICATION_FIXTURE_SCHEMA_V27: QualificationFixtureV27,
                    QUALIFICATION_FIXTURE_SCHEMA_V28: QualificationFixtureV28,
                    QUALIFICATION_FIXTURE_SCHEMA_V29: QualificationFixtureV29,
                    QUALIFICATION_FIXTURE_SCHEMA_V30: QualificationFixtureV30,
                    QUALIFICATION_FIXTURE_SCHEMA_V31: QualificationFixtureV31,
                    QUALIFICATION_FIXTURE_SCHEMA_V32: QualificationFixtureV32,
                    QUALIFICATION_FIXTURE_SCHEMA_V33: QualificationFixtureV33,
                    QUALIFICATION_FIXTURE_SCHEMA_V34: QualificationFixtureV34,
                    QUALIFICATION_FIXTURE_SCHEMA_V35: QualificationFixtureV35,
                    QUALIFICATION_FIXTURE_SCHEMA_V36: QualificationFixtureV36,
                    QUALIFICATION_FIXTURE_SCHEMA_V37: QualificationFixtureV37,
                    QUALIFICATION_FIXTURE_SCHEMA_V38: QualificationFixtureV38,
                    QUALIFICATION_FIXTURE_SCHEMA: QualificationFixtureV39,
                }[schema_version]
                fixture = fixture_type(
                    **common, novelty_id=str(value["novelty_id"]), stress_tags=tuple(tags)
                )
            else:
                fixture = QualificationFixtureV1(**common)
        except ValueError as exc:
            raise ContractValidationError("qualification fixture enum changed") from exc
        fixtures.append(fixture)
    return tuple(fixtures)


def _validate_qualification_fixture_set(
    fixtures: tuple[QualificationFixtureV1, ...],
) -> None:
    ids = [value.fixture_id for value in fixtures]
    if len(set(ids)) != len(ids):
        raise ContractValidationError("qualification fixture identity is duplicated")
    source_hashes = [text_sha256(value.user_source) for value in fixtures]
    if len(set(source_hashes)) != len(source_hashes):
        raise ContractValidationError("qualification fixture source is duplicated")
    stress_fixtures = [value for value in fixtures if isinstance(value, QualificationFixtureV2)]
    if stress_fixtures and len(stress_fixtures) != len(fixtures):
        raise ContractValidationError("qualification fixture versions are mixed")
    novelty_ids = [value.novelty_id for value in stress_fixtures]
    if len(set(novelty_ids)) != len(novelty_ids):
        raise ContractValidationError("qualification novelty identity is duplicated")
    for phase in QualificationPhase:
        selected = _ordered_phase_fixtures(phase, fixtures)
        _validate_phase_fixture_counts(phase, selected)
        _validate_phase_route_lineage(selected)


def _validate_v2_qualification_novelty_baseline(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
) -> None:
    if baseline_fixture_path != QUALIFICATION_FIXTURE_PATH_V1:
        raise ContractValidationError("qualification novelty baseline path is invalid")
    if baseline_fixture_sha256 != QUALIFICATION_FIXTURE_SHA256_V1:
        raise ContractValidationError("qualification novelty baseline hash is invalid")
    _, baseline_raw = _read_bound_qualification_fixture(
        fixture_path=fixture_path,
        bound_path=QUALIFICATION_FIXTURE_PATH_V1,
        bound_sha256=QUALIFICATION_FIXTURE_SHA256_V1,
        expected_schema=QUALIFICATION_FIXTURE_SCHEMA_V1,
    )
    if set(baseline_raw) != {"schema_version", "fixtures"}:
        raise ContractValidationError("qualification novelty baseline schema changed")
    baseline = _decode_qualification_fixture_rows(
        baseline_raw.get("fixtures"),
        schema_version=QUALIFICATION_FIXTURE_SCHEMA_V1,
    )
    _validate_qualification_fixture_set(baseline)
    prior_sources = {text_sha256(value.user_source) for value in baseline}
    if any(text_sha256(value.user_source) in prior_sources for value in fixtures):
        raise ContractValidationError("qualification stress fixture reuses a baseline source")


def _qualification_fixture_ancestry() -> list[dict[str, str]]:
    return [
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V1,
            "path": QUALIFICATION_FIXTURE_PATH_V1,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V1,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V2,
            "path": QUALIFICATION_FIXTURE_PATH_V2,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V2,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V3,
            "path": QUALIFICATION_FIXTURE_PATH_V3,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V3,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V4,
            "path": QUALIFICATION_FIXTURE_PATH_V4,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V4,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V5,
            "path": QUALIFICATION_FIXTURE_PATH_V5,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V5,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V6,
            "path": QUALIFICATION_FIXTURE_PATH_V6,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V6,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V7,
            "path": QUALIFICATION_FIXTURE_PATH_V7,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V7,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V8,
            "path": QUALIFICATION_FIXTURE_PATH_V8,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V8,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V9,
            "path": QUALIFICATION_FIXTURE_PATH_V9,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V9,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V10,
            "path": QUALIFICATION_FIXTURE_PATH_V10,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V10,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V11,
            "path": QUALIFICATION_FIXTURE_PATH_V11,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V11,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V12,
            "path": QUALIFICATION_FIXTURE_PATH_V12,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V12,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V13,
            "path": QUALIFICATION_FIXTURE_PATH_V13,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V13,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V14,
            "path": QUALIFICATION_FIXTURE_PATH_V14,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V14,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V15,
            "path": QUALIFICATION_FIXTURE_PATH_V15,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V15,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V16,
            "path": QUALIFICATION_FIXTURE_PATH_V16,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V16,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V17,
            "path": QUALIFICATION_FIXTURE_PATH_V17,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V17,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V18,
            "path": QUALIFICATION_FIXTURE_PATH_V18,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V18,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V19,
            "path": QUALIFICATION_FIXTURE_PATH_V19,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V19,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V20,
            "path": QUALIFICATION_FIXTURE_PATH_V20,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V20,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V21,
            "path": QUALIFICATION_FIXTURE_PATH_V21,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V21,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V22,
            "path": QUALIFICATION_FIXTURE_PATH_V22,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V22,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V23,
            "path": QUALIFICATION_FIXTURE_PATH_V23,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V23,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V24,
            "path": QUALIFICATION_FIXTURE_PATH_V24,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V24,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V25,
            "path": QUALIFICATION_FIXTURE_PATH_V25,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V25,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V26,
            "path": QUALIFICATION_FIXTURE_PATH_V26,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V26,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V27,
            "path": QUALIFICATION_FIXTURE_PATH_V27,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V27,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V28,
            "path": QUALIFICATION_FIXTURE_PATH_V28,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V28,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V29,
            "path": QUALIFICATION_FIXTURE_PATH_V29,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V29,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V30,
            "path": QUALIFICATION_FIXTURE_PATH_V30,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V30,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V31,
            "path": QUALIFICATION_FIXTURE_PATH_V31,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V31,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V32,
            "path": QUALIFICATION_FIXTURE_PATH_V32,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V32,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V33,
            "path": QUALIFICATION_FIXTURE_PATH_V33,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V33,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V34,
            "path": QUALIFICATION_FIXTURE_PATH_V34,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V34,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V35,
            "path": QUALIFICATION_FIXTURE_PATH_V35,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V35,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V36,
            "path": QUALIFICATION_FIXTURE_PATH_V36,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V36,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V37,
            "path": QUALIFICATION_FIXTURE_PATH_V37,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V37,
        },
        {
            "schema_version": QUALIFICATION_FIXTURE_SCHEMA_V38,
            "path": QUALIFICATION_FIXTURE_PATH_V38,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V38,
        },
    ]


def _read_bound_qualification_fixture(
    *,
    fixture_path: Path,
    bound_path: str,
    bound_sha256: str,
    expected_schema: str,
) -> tuple[Path, Mapping[str, Any]]:
    baseline_path = fixture_path.resolve().parent / bound_path
    if baseline_path.is_symlink() or not baseline_path.is_file():
        raise ContractValidationError("qualification novelty baseline is unreadable")
    try:
        baseline_bytes = baseline_path.read_bytes()
        baseline_raw = json.loads(baseline_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("qualification novelty baseline is unreadable") from exc
    if bytes_sha256(baseline_bytes) != bound_sha256:
        raise StateConflictError("qualification novelty baseline binding changed")
    if (
        not isinstance(baseline_raw, Mapping)
        or baseline_raw.get("schema_version") != expected_schema
    ):
        raise ContractValidationError("qualification novelty baseline schema changed")
    return baseline_path, baseline_raw


def _validate_v3_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V2,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V2,
        expected_ancestry=_qualification_fixture_ancestry()[:2],
    )


def _validate_v4_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V3,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V3,
        expected_ancestry=_qualification_fixture_ancestry()[:3],
    )


def _validate_v5_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V4,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V4,
        expected_ancestry=_qualification_fixture_ancestry()[:4],
    )


def _validate_v6_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V5,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V5,
        expected_ancestry=_qualification_fixture_ancestry()[:5],
    )


def _validate_v7_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V6,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V6,
        expected_ancestry=_qualification_fixture_ancestry()[:6],
    )


def _validate_v8_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V7,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V7,
        expected_ancestry=_qualification_fixture_ancestry()[:7],
    )


def _validate_v9_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V8,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V8,
        expected_ancestry=_qualification_fixture_ancestry()[:8],
    )


def _validate_v10_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V9,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V9,
        expected_ancestry=_qualification_fixture_ancestry()[:9],
    )


def _validate_v11_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V10,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V10,
        expected_ancestry=_qualification_fixture_ancestry()[:10],
    )


def _validate_v12_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V11,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V11,
        expected_ancestry=_qualification_fixture_ancestry()[:11],
    )


def _validate_v13_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V12,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V12,
        expected_ancestry=_qualification_fixture_ancestry()[:12],
    )


def _validate_v14_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V13,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V13,
        expected_ancestry=_qualification_fixture_ancestry()[:13],
    )


def _validate_v15_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V14,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V14,
        expected_ancestry=_qualification_fixture_ancestry()[:14],
    )


def _validate_v16_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V15,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V15,
        expected_ancestry=_qualification_fixture_ancestry()[:15],
    )


def _validate_v17_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V16,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V16,
        expected_ancestry=_qualification_fixture_ancestry()[:16],
    )


def _validate_v18_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V17,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V17,
        expected_ancestry=_qualification_fixture_ancestry()[:17],
    )


def _validate_v19_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V18,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V18,
        expected_ancestry=_qualification_fixture_ancestry()[:18],
    )


def _validate_v20_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V19,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V19,
        expected_ancestry=_qualification_fixture_ancestry()[:19],
    )


def _validate_v21_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V20,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V20,
        expected_ancestry=_qualification_fixture_ancestry()[:20],
    )


def _validate_v22_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V21,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V21,
        expected_ancestry=_qualification_fixture_ancestry()[:21],
    )


def _validate_v23_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V22,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V22,
        expected_ancestry=_qualification_fixture_ancestry()[:22],
    )


def _validate_v24_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V23,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V23,
        expected_ancestry=_qualification_fixture_ancestry()[:23],
    )


def _validate_v25_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V24,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V24,
        expected_ancestry=_qualification_fixture_ancestry()[:24],
    )


def _validate_v26_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V25,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V25,
        expected_ancestry=_qualification_fixture_ancestry()[:25],
    )


def _validate_v27_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V26,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V26,
        expected_ancestry=_qualification_fixture_ancestry()[:26],
    )


def _validate_v28_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V27,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V27,
        expected_ancestry=_qualification_fixture_ancestry()[:27],
    )


def _validate_v29_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V28,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V28,
        expected_ancestry=_qualification_fixture_ancestry()[:28],
    )


def _validate_v30_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V29,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V29,
        expected_ancestry=_qualification_fixture_ancestry()[:29],
    )


def _validate_v31_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V30,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V30,
        expected_ancestry=_qualification_fixture_ancestry()[:30],
    )


def _validate_v32_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V31,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V31,
        expected_ancestry=_qualification_fixture_ancestry()[:31],
    )


def _validate_v33_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V32,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V32,
        expected_ancestry=_qualification_fixture_ancestry()[:32],
    )


def _validate_v34_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V33,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V33,
        expected_ancestry=_qualification_fixture_ancestry()[:33],
    )


def _validate_v35_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V34,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V34,
        expected_ancestry=_qualification_fixture_ancestry()[:34],
    )


def _validate_v36_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V35,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V35,
        expected_ancestry=_qualification_fixture_ancestry()[:35],
    )


def _validate_v37_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V36,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V36,
        expected_ancestry=_qualification_fixture_ancestry()[:36],
    )


def _validate_v38_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_FIXTURE_PATH_V37,
        expected_baseline_sha256=QUALIFICATION_FIXTURE_SHA256_V37,
        expected_ancestry=_qualification_fixture_ancestry()[:37],
    )


def _validate_v39_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
) -> None:
    _validate_qualification_novelty_ancestry(
        fixture_path=fixture_path,
        fixtures=fixtures,
        baseline_fixture_path=baseline_fixture_path,
        baseline_fixture_sha256=baseline_fixture_sha256,
        baseline_ancestry=baseline_ancestry,
        expected_baseline_path=QUALIFICATION_BASELINE_FIXTURE_PATH,
        expected_baseline_sha256=QUALIFICATION_BASELINE_FIXTURE_SHA256,
        expected_ancestry=_qualification_fixture_ancestry(),
    )


def _validate_qualification_novelty_ancestry(
    *,
    fixture_path: Path,
    fixtures: tuple[QualificationFixtureV1, ...],
    baseline_fixture_path: object,
    baseline_fixture_sha256: object,
    baseline_ancestry: object,
    expected_baseline_path: str,
    expected_baseline_sha256: str,
    expected_ancestry: list[dict[str, str]],
) -> None:
    if baseline_fixture_path != expected_baseline_path:
        raise ContractValidationError("qualification novelty baseline path is invalid")
    if baseline_fixture_sha256 != expected_baseline_sha256:
        raise ContractValidationError("qualification novelty baseline hash is invalid")
    if not isinstance(baseline_ancestry, list) or any(
        not isinstance(value, Mapping) or set(value) != {"schema_version", "path", "sha256"}
        for value in baseline_ancestry
    ):
        raise ContractValidationError("qualification novelty ancestry changed")
    if baseline_ancestry != expected_ancestry:
        raise StateConflictError("qualification novelty ancestry binding changed")

    ancestors: list[tuple[QualificationFixtureV1, ...]] = []
    for binding in expected_ancestry:
        ancestor_path, _ = _read_bound_qualification_fixture(
            fixture_path=fixture_path,
            bound_path=binding["path"],
            bound_sha256=binding["sha256"],
            expected_schema=binding["schema_version"],
        )
        ancestors.append(load_qualification_fixtures(ancestor_path))

    prior_sources = {text_sha256(value.user_source) for ancestor in ancestors for value in ancestor}
    if any(text_sha256(value.user_source) in prior_sources for value in fixtures):
        raise ContractValidationError("qualification stress fixture reuses an ancestor source")
    prior_novelty = {
        value.novelty_id
        for ancestor in ancestors
        for value in ancestor
        if isinstance(value, QualificationFixtureV2)
    }
    current_novelty = {
        value.novelty_id for value in fixtures if isinstance(value, QualificationFixtureV2)
    }
    if current_novelty.intersection(prior_novelty):
        raise ContractValidationError("qualification stress fixture reuses ancestor novelty")


def _fixture_novelty_evidence(
    fixture: QualificationFixtureV1,
) -> dict[str, Any]:
    if isinstance(fixture, QualificationFixtureV39):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV38):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V38,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV37):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V37,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV36):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V36,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV35):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V35,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV34):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V34,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV33):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V33,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV32):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V32,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV31):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V31,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV30):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V30,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV29):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V29,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV28):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V28,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV27):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V27,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV26):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V26,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV25):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V25,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV24):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V24,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV23):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V23,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV22):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V22,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV21):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V21,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV20):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V20,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV19):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V19,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV18):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V18,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV17):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V17,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV16):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V16,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV15):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V15,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV14):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V14,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV13):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V13,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV12):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V12,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV11):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V11,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV10):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V10,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV9):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V9,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV8):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V8,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV7):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V7,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV6):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V6,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV5):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V5,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV4):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V4,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV3):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V3,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    if isinstance(fixture, QualificationFixtureV2):
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V2,
            "novelty_id": fixture.novelty_id,
            "stress_tags": list(fixture.stress_tags),
        }
    return {
        "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V1,
        "novelty_id": None,
        "stress_tags": [],
    }


def qualification_request_payload(
    fixture: QualificationFixtureV1,
    *,
    session_id: str,
    messages: Sequence[Mapping[str, str]] | None = None,
) -> dict[str, Any]:
    visible_messages = (
        [{"role": "user", "content": fixture.user_source}]
        if messages is None
        else [dict(value) for value in messages]
    )
    if not visible_messages or visible_messages[-1] != {
        "role": "user",
        "content": fixture.user_source,
    }:
        raise ContractValidationError("qualification conversation floor changed")
    return {
        "model": PI_SCENE_AUTO_MODEL,
        "messages": visible_messages,
        "stream": False,
        "cera_session_id": session_id,
        "cera_profile_id": PI_SCENE_PROFILE,
        "cera_character_autonomy": "both",
        "cera_adult_craft_mode": fixture.adult_craft_mode,
        "cera_prompt_handling": "adjustment",
        "cera_reasoning_effort": QUALIFICATION_PLANNER_REASONING_EFFORT,
        "cera_scene_depth": "auto",
        "cera_review_mode": "automatic",
    }


def qualification_fixture_manifest_metadata(
    fixtures: Sequence[QualificationFixtureV1],
) -> dict[str, Any]:
    fixture_types = {type(value) for value in fixtures}
    if not fixtures or fixture_types == {QualificationFixtureV1}:
        return {
            "fixture_schema_version": QUALIFICATION_FIXTURE_SCHEMA_V1,
            "fixture_baseline": None,
            "fixture_ancestry": [],
            "fixture_novelty": [],
            "novelty_set_sha256": None,
            "stress_coverage": {},
        }
    if fixture_types == {QualificationFixtureV2}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V2
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V1,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V1,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:1]
    elif fixture_types == {QualificationFixtureV3}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V3
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V2,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V2,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:2]
    elif fixture_types == {QualificationFixtureV4}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V4
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V3,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V3,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:3]
    elif fixture_types == {QualificationFixtureV5}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V5
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V4,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V4,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:4]
    elif fixture_types == {QualificationFixtureV6}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V6
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V5,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V5,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:5]
    elif fixture_types == {QualificationFixtureV7}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V7
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V6,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V6,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:6]
    elif fixture_types == {QualificationFixtureV8}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V8
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V7,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V7,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:7]
    elif fixture_types == {QualificationFixtureV9}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V9
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V8,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V8,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:8]
    elif fixture_types == {QualificationFixtureV10}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V10
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V9,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V9,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:9]
    elif fixture_types == {QualificationFixtureV11}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V11
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V10,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V10,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:10]
    elif fixture_types == {QualificationFixtureV12}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V12
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V11,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V11,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:11]
    elif fixture_types == {QualificationFixtureV13}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V13
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V12,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V12,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:12]
    elif fixture_types == {QualificationFixtureV14}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V14
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V13,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V13,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:13]
    elif fixture_types == {QualificationFixtureV15}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V15
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V14,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V14,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:14]
    elif fixture_types == {QualificationFixtureV16}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V16
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V15,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V15,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:15]
    elif fixture_types == {QualificationFixtureV17}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V17
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V16,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V16,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:16]
    elif fixture_types == {QualificationFixtureV18}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V18
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V17,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V17,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:17]
    elif fixture_types == {QualificationFixtureV19}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V19
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V18,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V18,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:18]
    elif fixture_types == {QualificationFixtureV20}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V20
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V19,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V19,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:19]
    elif fixture_types == {QualificationFixtureV21}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V21
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V20,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V20,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:20]
    elif fixture_types == {QualificationFixtureV22}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V22
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V21,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V21,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:21]
    elif fixture_types == {QualificationFixtureV23}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V23
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V22,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V22,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:22]
    elif fixture_types == {QualificationFixtureV24}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V24
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V23,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V23,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:23]
    elif fixture_types == {QualificationFixtureV25}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V25
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V24,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V24,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:24]
    elif fixture_types == {QualificationFixtureV26}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V26
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V25,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V25,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:25]
    elif fixture_types == {QualificationFixtureV27}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V27
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V26,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V26,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:26]
    elif fixture_types == {QualificationFixtureV28}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V28
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V27,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V27,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:27]
    elif fixture_types == {QualificationFixtureV29}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V29
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V28,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V28,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:28]
    elif fixture_types == {QualificationFixtureV30}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V30
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V29,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V29,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:29]
    elif fixture_types == {QualificationFixtureV31}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V31
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V30,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V30,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:30]
    elif fixture_types == {QualificationFixtureV32}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V32
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V31,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V31,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:31]
    elif fixture_types == {QualificationFixtureV33}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V33
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V32,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V32,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:32]
    elif fixture_types == {QualificationFixtureV34}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V34
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V33,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V33,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:33]
    elif fixture_types == {QualificationFixtureV35}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V35
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V34,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V34,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:34]
    elif fixture_types == {QualificationFixtureV36}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V36
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V35,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V35,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:35]
    elif fixture_types == {QualificationFixtureV37}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V37
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V36,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V36,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:36]
    elif fixture_types == {QualificationFixtureV38}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA_V38
        fixture_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V37,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V37,
        }
        fixture_ancestry = _qualification_fixture_ancestry()[:37]
    elif fixture_types == {QualificationFixtureV39}:
        fixture_schema_version = QUALIFICATION_FIXTURE_SCHEMA
        fixture_baseline = {
            "path": QUALIFICATION_BASELINE_FIXTURE_PATH,
            "sha256": QUALIFICATION_BASELINE_FIXTURE_SHA256,
        }
        fixture_ancestry = _qualification_fixture_ancestry()
    else:
        raise ContractValidationError("qualification fixture versions are mixed")
    stress_fixtures = cast(Sequence[QualificationFixtureV2], fixtures)
    novelty = [
        {
            "fixture_id": value.fixture_id,
            "novelty_id": value.novelty_id,
            "stress_tags": list(value.stress_tags),
            "source_sha256": text_sha256(value.user_source),
        }
        for value in sorted(stress_fixtures, key=lambda item: item.fixture_id)
    ]
    coverage = {
        tag: sum(tag in value.stress_tags for value in stress_fixtures)
        for tag in sorted({tag for value in stress_fixtures for tag in value.stress_tags})
    }
    return {
        "fixture_schema_version": fixture_schema_version,
        "fixture_baseline": fixture_baseline,
        "fixture_ancestry": fixture_ancestry,
        "fixture_novelty": novelty,
        "novelty_set_sha256": canonical_sha256(novelty),
        "stress_coverage": coverage,
    }


def _validate_spent_fixture_authority(
    *,
    fixture_set_sha256: str,
    fixture_novelty: object,
    spent_manifests: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    if not isinstance(fixture_novelty, list):
        raise ContractValidationError("qualification novelty authority changed")
    current_novelty = {
        str(value["novelty_id"]) for value in fixture_novelty if isinstance(value, Mapping)
    }
    current_sources = {
        str(value["source_sha256"]) for value in fixture_novelty if isinstance(value, Mapping)
    }
    spent_hashes: set[str] = set()
    spent_fixture_sets: set[str] = set()
    spent_novelty: set[str] = set()
    spent_sources: set[str] = set()
    for manifest in spent_manifests:
        validate_qualification_manifest(manifest)
        spent_hashes.add(str(manifest["manifest_sha256"]))
        spent_hashes.update(str(value) for value in manifest["spent_manifest_sha256s"])
        spent_fixture_sets.add(str(manifest["fixture_set_sha256"]))
        spent_fixture_sets.update(str(value) for value in manifest["spent_fixture_set_sha256s"])
        prior_novelty = manifest["fixture_novelty"]
        if not isinstance(prior_novelty, list):
            raise StateConflictError("spent qualification novelty authority changed")
        spent_novelty.update(
            str(value["novelty_id"]) for value in prior_novelty if isinstance(value, Mapping)
        )
        spent_sources.update(
            str(value["source_sha256"]) for value in prior_novelty if isinstance(value, Mapping)
        )
        spent_novelty.update(str(value) for value in manifest["spent_novelty_ids"])
        spent_sources.update(str(value) for value in manifest["spent_source_sha256s"])
    if fixture_set_sha256 in spent_fixture_sets:
        raise StateConflictError("qualification fixture set was already frozen")
    if current_novelty.intersection(spent_novelty):
        raise StateConflictError("qualification novelty identity was already frozen")
    if current_sources.intersection(spent_sources):
        raise StateConflictError("qualification fixture source was already frozen")
    return {
        "spent_manifest_sha256s": sorted(spent_hashes),
        "spent_fixture_set_sha256s": sorted(spent_fixture_sets),
        "spent_novelty_ids": sorted(spent_novelty),
        "spent_source_sha256s": sorted(spent_sources),
    }


def build_qualification_manifest(
    *,
    repository_root: Path,
    qualification_id: str,
    source_commit: str,
    source_tree: str,
    fixture_path: Path,
    repository_artifacts: Mapping[str, Sequence[Path]],
    external_artifacts: Mapping[str, Sequence[Path]],
    spent_manifests: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{7,95}", qualification_id):
        raise ContractValidationError("qualification identity is invalid")
    if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or not re.fullmatch(
        r"[0-9a-f]{40}", source_tree
    ):
        raise ContractValidationError("qualification Git identity is invalid")
    fixtures = load_qualification_fixtures(fixture_path)
    root = repository_root.resolve()
    categories: dict[str, list[dict[str, Any]]] = {}
    for category, paths in repository_artifacts.items():
        categories[category] = _artifact_entries(root, paths, external=False)
    for category, paths in external_artifacts.items():
        if category in categories:
            raise ContractValidationError("qualification artifact category is duplicated")
        categories[category] = _artifact_entries(root, paths, external=True)
    if any(not values for values in categories.values()):
        raise ContractValidationError("qualification artifact category is empty")
    fixture_counts = {
        phase.value: {
            route.value: sum(
                value.phase is phase and value.expected_route is route for value in fixtures
            )
            for route in QualificationRoute
        }
        for phase in QualificationPhase
    }
    fixture_metadata = qualification_fixture_manifest_metadata(fixtures)
    spent_authority = _validate_spent_fixture_authority(
        fixture_set_sha256=bytes_sha256(fixture_path.read_bytes()),
        fixture_novelty=fixture_metadata["fixture_novelty"],
        spent_manifests=spent_manifests,
    )
    body: dict[str, Any] = {
        "schema_version": QUALIFICATION_MANIFEST_SCHEMA,
        "qualification_id": qualification_id,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "route_model": PI_SCENE_AUTO_MODEL,
        "profile_id": PI_SCENE_PROFILE,
        "fixture_set_sha256": bytes_sha256(fixture_path.read_bytes()),
        **fixture_metadata,
        **spent_authority,
        "fixture_counts": fixture_counts,
        "provider_ceilings": {
            "sol": SOL_FAMILY_CEILING,
            "deepseek_http_operations": DEEPSEEK_HTTP_OPERATION_CEILING,
            "deepseek_per_invocation": DEEPSEEK_PER_INVOCATION_CEILING,
            "terra": TERRA_CEILING,
            "user_authorized_codex_operations": (USER_AUTHORIZED_CODEX_OPERATION_CEILING),
            "user_authorized_deepseek_operations": (USER_AUTHORIZED_DEEPSEEK_OPERATION_CEILING),
        },
        "execution_policy": deepcopy(QUALIFICATION_EXECUTION_POLICY),
        "artifact_categories": categories,
    }
    return {**body, "manifest_sha256": canonical_sha256(body)}


def validate_qualification_manifest(manifest: Mapping[str, Any]) -> None:
    schema_version = manifest.get("schema_version")
    required = {
        "schema_version",
        "qualification_id",
        "source_commit",
        "source_tree",
        "route_model",
        "profile_id",
        "fixture_set_sha256",
        "fixture_schema_version",
        "fixture_baseline",
        "fixture_novelty",
        "novelty_set_sha256",
        "stress_coverage",
        "spent_manifest_sha256s",
        "spent_fixture_set_sha256s",
        "spent_novelty_ids",
        "spent_source_sha256s",
        "fixture_counts",
        "provider_ceilings",
        "execution_policy",
        "artifact_categories",
        "manifest_sha256",
    }
    if schema_version in {
        QUALIFICATION_MANIFEST_SCHEMA_V6,
        QUALIFICATION_MANIFEST_SCHEMA_V7,
        QUALIFICATION_MANIFEST_SCHEMA_V8,
        QUALIFICATION_MANIFEST_SCHEMA_V9,
        QUALIFICATION_MANIFEST_SCHEMA_V10,
        QUALIFICATION_MANIFEST_SCHEMA_V11,
        QUALIFICATION_MANIFEST_SCHEMA_V12,
        QUALIFICATION_MANIFEST_SCHEMA_V13,
        QUALIFICATION_MANIFEST_SCHEMA_V14,
        QUALIFICATION_MANIFEST_SCHEMA_V15,
        QUALIFICATION_MANIFEST_SCHEMA_V16,
        QUALIFICATION_MANIFEST_SCHEMA_V17,
        QUALIFICATION_MANIFEST_SCHEMA_V18,
        QUALIFICATION_MANIFEST_SCHEMA_V19,
        QUALIFICATION_MANIFEST_SCHEMA_V20,
        QUALIFICATION_MANIFEST_SCHEMA_V21,
        QUALIFICATION_MANIFEST_SCHEMA_V22,
        QUALIFICATION_MANIFEST_SCHEMA_V23,
        QUALIFICATION_MANIFEST_SCHEMA_V24,
        QUALIFICATION_MANIFEST_SCHEMA_V25,
        QUALIFICATION_MANIFEST_SCHEMA_V26,
        QUALIFICATION_MANIFEST_SCHEMA_V27,
        QUALIFICATION_MANIFEST_SCHEMA_V28,
        QUALIFICATION_MANIFEST_SCHEMA_V29,
        QUALIFICATION_MANIFEST_SCHEMA_V30,
        QUALIFICATION_MANIFEST_SCHEMA_V31,
        QUALIFICATION_MANIFEST_SCHEMA_V32,
        QUALIFICATION_MANIFEST_SCHEMA_V33,
        QUALIFICATION_MANIFEST_SCHEMA_V34,
        QUALIFICATION_MANIFEST_SCHEMA_V35,
        QUALIFICATION_MANIFEST_SCHEMA_V36,
        QUALIFICATION_MANIFEST_SCHEMA_V37,
        QUALIFICATION_MANIFEST_SCHEMA_V38,
        QUALIFICATION_MANIFEST_SCHEMA_V39,
        QUALIFICATION_MANIFEST_SCHEMA_V40,
        QUALIFICATION_MANIFEST_SCHEMA_V41,
        QUALIFICATION_MANIFEST_SCHEMA_V42,
        QUALIFICATION_MANIFEST_SCHEMA,
    }:
        required.add("fixture_ancestry")
    elif schema_version != QUALIFICATION_MANIFEST_SCHEMA_V5:
        raise ContractValidationError("qualification manifest schema changed")
    if set(manifest) != required:
        raise ContractValidationError("qualification manifest shape changed")
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if manifest["manifest_sha256"] != canonical_sha256(unsigned):
        raise StateConflictError("qualification manifest binding changed")
    if manifest["route_model"] != PI_SCENE_AUTO_MODEL:
        raise StateConflictError("qualification route model changed")
    if manifest["profile_id"] != PI_SCENE_PROFILE:
        raise StateConflictError("qualification profile changed")
    _validate_manifest_fixture_metadata(manifest, manifest_schema=str(schema_version))
    expected_counts = {phase: dict(routes) for phase, routes in EXPECTED_PHASE_COUNTS.items()}
    if manifest["fixture_counts"] != expected_counts:
        raise StateConflictError("qualification fixture counts changed")
    if manifest["provider_ceilings"] != {
        "sol": SOL_FAMILY_CEILING,
        "deepseek_http_operations": DEEPSEEK_HTTP_OPERATION_CEILING,
        "deepseek_per_invocation": DEEPSEEK_PER_INVOCATION_CEILING,
        "terra": TERRA_CEILING,
        "user_authorized_codex_operations": USER_AUTHORIZED_CODEX_OPERATION_CEILING,
        "user_authorized_deepseek_operations": USER_AUTHORIZED_DEEPSEEK_OPERATION_CEILING,
    }:
        raise StateConflictError("qualification provider ceilings changed")
    if schema_version == QUALIFICATION_MANIFEST_SCHEMA:
        expected_execution_policy = QUALIFICATION_EXECUTION_POLICY
    elif schema_version in {
        QUALIFICATION_MANIFEST_SCHEMA_V32,
        QUALIFICATION_MANIFEST_SCHEMA_V33,
        QUALIFICATION_MANIFEST_SCHEMA_V34,
        QUALIFICATION_MANIFEST_SCHEMA_V35,
        QUALIFICATION_MANIFEST_SCHEMA_V36,
        QUALIFICATION_MANIFEST_SCHEMA_V37,
        QUALIFICATION_MANIFEST_SCHEMA_V38,
        QUALIFICATION_MANIFEST_SCHEMA_V39,
        QUALIFICATION_MANIFEST_SCHEMA_V40,
        QUALIFICATION_MANIFEST_SCHEMA_V41,
        QUALIFICATION_MANIFEST_SCHEMA_V42,
    }:
        expected_execution_policy = QUALIFICATION_EXECUTION_POLICY_V42
    elif schema_version in {
        QUALIFICATION_MANIFEST_SCHEMA_V23,
        QUALIFICATION_MANIFEST_SCHEMA_V24,
        QUALIFICATION_MANIFEST_SCHEMA_V25,
        QUALIFICATION_MANIFEST_SCHEMA_V26,
        QUALIFICATION_MANIFEST_SCHEMA_V27,
        QUALIFICATION_MANIFEST_SCHEMA_V28,
        QUALIFICATION_MANIFEST_SCHEMA_V29,
        QUALIFICATION_MANIFEST_SCHEMA_V30,
        QUALIFICATION_MANIFEST_SCHEMA_V31,
    }:
        expected_execution_policy = QUALIFICATION_EXECUTION_POLICY_V31
    else:
        expected_execution_policy = QUALIFICATION_EXECUTION_POLICY_V22
    if manifest["execution_policy"] != expected_execution_policy:
        raise StateConflictError("qualification execution policy changed")


def _validate_manifest_fixture_metadata(
    manifest: Mapping[str, Any],
    *,
    manifest_schema: str,
) -> None:
    schema_version = manifest["fixture_schema_version"]
    baseline = manifest["fixture_baseline"]
    novelty = manifest["fixture_novelty"]
    novelty_sha256 = manifest["novelty_set_sha256"]
    coverage = manifest["stress_coverage"]
    spent = manifest["spent_manifest_sha256s"]
    spent_fixture_sets = manifest["spent_fixture_set_sha256s"]
    spent_novelty = manifest["spent_novelty_ids"]
    spent_sources = manifest["spent_source_sha256s"]
    manifest_has_ancestry = manifest_schema in {
        QUALIFICATION_MANIFEST_SCHEMA_V6,
        QUALIFICATION_MANIFEST_SCHEMA_V7,
        QUALIFICATION_MANIFEST_SCHEMA_V8,
        QUALIFICATION_MANIFEST_SCHEMA_V9,
        QUALIFICATION_MANIFEST_SCHEMA_V10,
        QUALIFICATION_MANIFEST_SCHEMA_V11,
        QUALIFICATION_MANIFEST_SCHEMA_V12,
        QUALIFICATION_MANIFEST_SCHEMA_V13,
        QUALIFICATION_MANIFEST_SCHEMA_V14,
        QUALIFICATION_MANIFEST_SCHEMA_V15,
        QUALIFICATION_MANIFEST_SCHEMA_V16,
        QUALIFICATION_MANIFEST_SCHEMA_V17,
        QUALIFICATION_MANIFEST_SCHEMA_V18,
        QUALIFICATION_MANIFEST_SCHEMA_V19,
        QUALIFICATION_MANIFEST_SCHEMA_V20,
        QUALIFICATION_MANIFEST_SCHEMA_V21,
        QUALIFICATION_MANIFEST_SCHEMA_V22,
        QUALIFICATION_MANIFEST_SCHEMA_V23,
        QUALIFICATION_MANIFEST_SCHEMA_V24,
        QUALIFICATION_MANIFEST_SCHEMA_V25,
        QUALIFICATION_MANIFEST_SCHEMA_V26,
        QUALIFICATION_MANIFEST_SCHEMA_V27,
        QUALIFICATION_MANIFEST_SCHEMA_V28,
        QUALIFICATION_MANIFEST_SCHEMA_V29,
        QUALIFICATION_MANIFEST_SCHEMA_V30,
        QUALIFICATION_MANIFEST_SCHEMA_V31,
        QUALIFICATION_MANIFEST_SCHEMA_V32,
        QUALIFICATION_MANIFEST_SCHEMA_V33,
        QUALIFICATION_MANIFEST_SCHEMA_V34,
        QUALIFICATION_MANIFEST_SCHEMA_V35,
        QUALIFICATION_MANIFEST_SCHEMA_V36,
        QUALIFICATION_MANIFEST_SCHEMA_V37,
        QUALIFICATION_MANIFEST_SCHEMA_V38,
        QUALIFICATION_MANIFEST_SCHEMA_V39,
        QUALIFICATION_MANIFEST_SCHEMA_V40,
        QUALIFICATION_MANIFEST_SCHEMA_V41,
        QUALIFICATION_MANIFEST_SCHEMA_V42,
        QUALIFICATION_MANIFEST_SCHEMA,
    }
    if (
        not isinstance(spent, list)
        or any(not isinstance(value, str) or not re_is_sha256(value) for value in spent)
        or spent != sorted(set(spent))
    ):
        raise ContractValidationError("qualification spent manifest authority changed")
    if (
        not isinstance(spent_fixture_sets, list)
        or any(
            not isinstance(value, str) or not re_is_sha256(value) for value in spent_fixture_sets
        )
        or spent_fixture_sets != sorted(set(spent_fixture_sets))
    ):
        raise ContractValidationError("qualification spent fixture-set authority changed")
    if (
        not isinstance(spent_novelty, list)
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"stress:[a-z][a-z0-9_]{2,95}", value) is None
            for value in spent_novelty
        )
        or spent_novelty != sorted(set(spent_novelty))
    ):
        raise ContractValidationError("qualification spent novelty authority changed")
    if (
        not isinstance(spent_sources, list)
        or any(not isinstance(value, str) or not re_is_sha256(value) for value in spent_sources)
        or spent_sources != sorted(set(spent_sources))
    ):
        raise ContractValidationError("qualification spent source authority changed")
    if manifest["fixture_set_sha256"] in spent_fixture_sets:
        raise StateConflictError("qualification current fixture set is marked spent")
    if schema_version == QUALIFICATION_FIXTURE_SCHEMA_V1:
        if baseline is not None or novelty != [] or novelty_sha256 is not None or coverage != {}:
            raise StateConflictError("legacy qualification fixture metadata changed")
        if manifest_has_ancestry and manifest["fixture_ancestry"] != []:
            raise StateConflictError("legacy qualification fixture ancestry changed")
        return
    if schema_version == QUALIFICATION_FIXTURE_SCHEMA_V2:
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V1,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V1,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:1]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V3 and manifest_schema in {
        QUALIFICATION_MANIFEST_SCHEMA_V6,
        QUALIFICATION_MANIFEST_SCHEMA_V7,
        QUALIFICATION_MANIFEST_SCHEMA_V8,
        QUALIFICATION_MANIFEST_SCHEMA_V9,
        QUALIFICATION_MANIFEST_SCHEMA_V10,
        QUALIFICATION_MANIFEST_SCHEMA_V11,
        QUALIFICATION_MANIFEST_SCHEMA_V12,
        QUALIFICATION_MANIFEST_SCHEMA_V13,
        QUALIFICATION_MANIFEST_SCHEMA_V14,
        QUALIFICATION_MANIFEST_SCHEMA_V15,
        QUALIFICATION_MANIFEST_SCHEMA_V16,
        QUALIFICATION_MANIFEST_SCHEMA_V17,
        QUALIFICATION_MANIFEST_SCHEMA_V18,
        QUALIFICATION_MANIFEST_SCHEMA_V19,
        QUALIFICATION_MANIFEST_SCHEMA_V20,
        QUALIFICATION_MANIFEST_SCHEMA_V21,
        QUALIFICATION_MANIFEST_SCHEMA_V22,
        QUALIFICATION_MANIFEST_SCHEMA_V23,
        QUALIFICATION_MANIFEST_SCHEMA_V24,
        QUALIFICATION_MANIFEST_SCHEMA_V25,
        QUALIFICATION_MANIFEST_SCHEMA_V26,
        QUALIFICATION_MANIFEST_SCHEMA_V27,
        QUALIFICATION_MANIFEST_SCHEMA_V28,
        QUALIFICATION_MANIFEST_SCHEMA_V29,
        QUALIFICATION_MANIFEST_SCHEMA_V30,
        QUALIFICATION_MANIFEST_SCHEMA_V31,
        QUALIFICATION_MANIFEST_SCHEMA_V32,
        QUALIFICATION_MANIFEST_SCHEMA_V33,
        QUALIFICATION_MANIFEST_SCHEMA_V34,
        QUALIFICATION_MANIFEST_SCHEMA_V35,
        QUALIFICATION_MANIFEST_SCHEMA_V36,
        QUALIFICATION_MANIFEST_SCHEMA_V37,
        QUALIFICATION_MANIFEST_SCHEMA_V38,
        QUALIFICATION_MANIFEST_SCHEMA_V39,
        QUALIFICATION_MANIFEST_SCHEMA_V40,
        QUALIFICATION_MANIFEST_SCHEMA_V41,
        QUALIFICATION_MANIFEST_SCHEMA_V42,
        QUALIFICATION_MANIFEST_SCHEMA,
    }:
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V2,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V2,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:2]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V4 and manifest_schema in {
        QUALIFICATION_MANIFEST_SCHEMA_V7,
        QUALIFICATION_MANIFEST_SCHEMA_V8,
        QUALIFICATION_MANIFEST_SCHEMA_V9,
        QUALIFICATION_MANIFEST_SCHEMA_V10,
        QUALIFICATION_MANIFEST_SCHEMA_V11,
        QUALIFICATION_MANIFEST_SCHEMA_V12,
        QUALIFICATION_MANIFEST_SCHEMA_V13,
        QUALIFICATION_MANIFEST_SCHEMA_V14,
        QUALIFICATION_MANIFEST_SCHEMA_V15,
        QUALIFICATION_MANIFEST_SCHEMA_V16,
        QUALIFICATION_MANIFEST_SCHEMA_V17,
        QUALIFICATION_MANIFEST_SCHEMA_V18,
        QUALIFICATION_MANIFEST_SCHEMA_V19,
        QUALIFICATION_MANIFEST_SCHEMA_V20,
        QUALIFICATION_MANIFEST_SCHEMA_V21,
        QUALIFICATION_MANIFEST_SCHEMA_V22,
        QUALIFICATION_MANIFEST_SCHEMA_V23,
        QUALIFICATION_MANIFEST_SCHEMA_V24,
        QUALIFICATION_MANIFEST_SCHEMA_V25,
        QUALIFICATION_MANIFEST_SCHEMA_V26,
        QUALIFICATION_MANIFEST_SCHEMA_V27,
        QUALIFICATION_MANIFEST_SCHEMA_V28,
        QUALIFICATION_MANIFEST_SCHEMA_V29,
        QUALIFICATION_MANIFEST_SCHEMA_V30,
        QUALIFICATION_MANIFEST_SCHEMA_V31,
        QUALIFICATION_MANIFEST_SCHEMA_V32,
        QUALIFICATION_MANIFEST_SCHEMA_V33,
        QUALIFICATION_MANIFEST_SCHEMA_V34,
        QUALIFICATION_MANIFEST_SCHEMA_V35,
        QUALIFICATION_MANIFEST_SCHEMA_V36,
        QUALIFICATION_MANIFEST_SCHEMA_V37,
        QUALIFICATION_MANIFEST_SCHEMA_V38,
        QUALIFICATION_MANIFEST_SCHEMA_V39,
        QUALIFICATION_MANIFEST_SCHEMA_V40,
        QUALIFICATION_MANIFEST_SCHEMA_V41,
        QUALIFICATION_MANIFEST_SCHEMA_V42,
        QUALIFICATION_MANIFEST_SCHEMA,
    }:
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V3,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V3,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:3]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V5 and manifest_schema in {
        QUALIFICATION_MANIFEST_SCHEMA_V8,
        QUALIFICATION_MANIFEST_SCHEMA_V9,
        QUALIFICATION_MANIFEST_SCHEMA_V10,
        QUALIFICATION_MANIFEST_SCHEMA_V11,
        QUALIFICATION_MANIFEST_SCHEMA_V12,
        QUALIFICATION_MANIFEST_SCHEMA_V13,
        QUALIFICATION_MANIFEST_SCHEMA_V14,
        QUALIFICATION_MANIFEST_SCHEMA_V15,
        QUALIFICATION_MANIFEST_SCHEMA_V16,
        QUALIFICATION_MANIFEST_SCHEMA_V17,
        QUALIFICATION_MANIFEST_SCHEMA_V18,
        QUALIFICATION_MANIFEST_SCHEMA_V19,
        QUALIFICATION_MANIFEST_SCHEMA_V20,
        QUALIFICATION_MANIFEST_SCHEMA_V21,
        QUALIFICATION_MANIFEST_SCHEMA_V22,
        QUALIFICATION_MANIFEST_SCHEMA_V23,
        QUALIFICATION_MANIFEST_SCHEMA_V24,
        QUALIFICATION_MANIFEST_SCHEMA_V25,
        QUALIFICATION_MANIFEST_SCHEMA_V26,
        QUALIFICATION_MANIFEST_SCHEMA_V27,
        QUALIFICATION_MANIFEST_SCHEMA_V28,
        QUALIFICATION_MANIFEST_SCHEMA_V29,
        QUALIFICATION_MANIFEST_SCHEMA_V30,
        QUALIFICATION_MANIFEST_SCHEMA_V31,
        QUALIFICATION_MANIFEST_SCHEMA_V32,
        QUALIFICATION_MANIFEST_SCHEMA_V33,
        QUALIFICATION_MANIFEST_SCHEMA_V34,
        QUALIFICATION_MANIFEST_SCHEMA_V35,
        QUALIFICATION_MANIFEST_SCHEMA_V36,
        QUALIFICATION_MANIFEST_SCHEMA_V37,
        QUALIFICATION_MANIFEST_SCHEMA_V38,
        QUALIFICATION_MANIFEST_SCHEMA_V39,
        QUALIFICATION_MANIFEST_SCHEMA_V40,
        QUALIFICATION_MANIFEST_SCHEMA_V41,
        QUALIFICATION_MANIFEST_SCHEMA_V42,
        QUALIFICATION_MANIFEST_SCHEMA,
    }:
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V4,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V4,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:4]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V6 and manifest_schema in {
        QUALIFICATION_MANIFEST_SCHEMA_V9,
        QUALIFICATION_MANIFEST_SCHEMA_V10,
        QUALIFICATION_MANIFEST_SCHEMA_V11,
        QUALIFICATION_MANIFEST_SCHEMA_V12,
        QUALIFICATION_MANIFEST_SCHEMA_V13,
        QUALIFICATION_MANIFEST_SCHEMA_V14,
        QUALIFICATION_MANIFEST_SCHEMA_V15,
        QUALIFICATION_MANIFEST_SCHEMA_V16,
        QUALIFICATION_MANIFEST_SCHEMA_V17,
        QUALIFICATION_MANIFEST_SCHEMA_V18,
        QUALIFICATION_MANIFEST_SCHEMA_V19,
        QUALIFICATION_MANIFEST_SCHEMA_V20,
        QUALIFICATION_MANIFEST_SCHEMA_V21,
        QUALIFICATION_MANIFEST_SCHEMA_V22,
        QUALIFICATION_MANIFEST_SCHEMA_V23,
        QUALIFICATION_MANIFEST_SCHEMA_V24,
        QUALIFICATION_MANIFEST_SCHEMA_V25,
        QUALIFICATION_MANIFEST_SCHEMA_V26,
        QUALIFICATION_MANIFEST_SCHEMA_V27,
        QUALIFICATION_MANIFEST_SCHEMA_V28,
        QUALIFICATION_MANIFEST_SCHEMA_V29,
        QUALIFICATION_MANIFEST_SCHEMA_V30,
        QUALIFICATION_MANIFEST_SCHEMA_V31,
        QUALIFICATION_MANIFEST_SCHEMA_V32,
        QUALIFICATION_MANIFEST_SCHEMA_V33,
        QUALIFICATION_MANIFEST_SCHEMA_V34,
        QUALIFICATION_MANIFEST_SCHEMA_V35,
        QUALIFICATION_MANIFEST_SCHEMA_V36,
        QUALIFICATION_MANIFEST_SCHEMA_V37,
        QUALIFICATION_MANIFEST_SCHEMA_V38,
        QUALIFICATION_MANIFEST_SCHEMA_V39,
        QUALIFICATION_MANIFEST_SCHEMA_V40,
        QUALIFICATION_MANIFEST_SCHEMA_V41,
        QUALIFICATION_MANIFEST_SCHEMA_V42,
        QUALIFICATION_MANIFEST_SCHEMA,
    }:
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V5,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V5,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:5]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V7 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V10
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V6,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V6,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:6]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V8 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V11
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V7,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V7,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:7]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V9 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V12
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V8,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V8,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:8]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V10 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V13
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V9,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V9,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:9]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V11 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V14
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V10,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V10,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:10]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V12 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V15
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V11,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V11,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:11]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V13 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V16
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V12,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V12,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:12]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V14 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V17
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V13,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V13,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:13]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V15 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V18
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V14,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V14,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:14]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V16 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V19
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V15,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V15,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:15]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V17 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V20
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V16,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V16,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:16]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V18 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V21
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V17,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V17,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:17]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V19 and manifest_schema in {
        QUALIFICATION_MANIFEST_SCHEMA_V22,
        QUALIFICATION_MANIFEST_SCHEMA_V23,
    }:
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V18,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V18,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:18]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V20 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V24
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V19,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V19,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:19]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V21 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V25
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V20,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V20,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:20]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V22 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V26
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V21,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V21,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:21]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V23 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V27
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V22,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V22,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:22]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V24 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V28
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V23,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V23,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:23]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V25 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V29
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V24,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V24,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:24]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V26 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V30
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V25,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V25,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:25]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V27 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V31
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V26,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V26,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:26]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V28 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V32
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V27,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V27,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:27]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V29 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V33
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V28,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V28,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:28]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V30 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V34
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V29,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V29,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:29]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V31 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V35
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V30,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V30,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:30]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V32 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V36
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V31,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V31,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:31]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V33 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V37
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V32,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V32,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:32]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V34 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V38
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V33,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V33,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:33]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V35 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V39
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V34,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V34,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:34]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V36 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V40
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V35,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V35,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:35]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V37 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V41
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V36,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V36,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:36]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA_V38 and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA_V42
    ):
        expected_baseline = {
            "path": QUALIFICATION_FIXTURE_PATH_V37,
            "sha256": QUALIFICATION_FIXTURE_SHA256_V37,
        }
        expected_ancestry = _qualification_fixture_ancestry()[:37]
    elif schema_version == QUALIFICATION_FIXTURE_SCHEMA and manifest_schema == (
        QUALIFICATION_MANIFEST_SCHEMA
    ):
        expected_baseline = {
            "path": QUALIFICATION_BASELINE_FIXTURE_PATH,
            "sha256": QUALIFICATION_BASELINE_FIXTURE_SHA256,
        }
        expected_ancestry = _qualification_fixture_ancestry()
    else:
        raise ContractValidationError("qualification fixture manifest schema changed")
    if baseline != expected_baseline:
        raise StateConflictError("qualification fixture baseline changed")
    if manifest_has_ancestry and manifest["fixture_ancestry"] != expected_ancestry:
        raise StateConflictError("qualification fixture ancestry changed")
    if not isinstance(novelty, list) or len(novelty) != 30:
        raise ContractValidationError("qualification fixture novelty set changed")
    fixture_ids: list[str] = []
    novelty_ids: list[str] = []
    source_hashes: list[str] = []
    expected_coverage: dict[str, int] = {}
    for value in novelty:
        if not isinstance(value, Mapping) or set(value) != {
            "fixture_id",
            "novelty_id",
            "stress_tags",
            "source_sha256",
        }:
            raise ContractValidationError("qualification novelty record changed")
        fixture_id = value["fixture_id"]
        novelty_id = value["novelty_id"]
        tags = value["stress_tags"]
        source_sha256 = value["source_sha256"]
        if (
            not isinstance(fixture_id, str)
            or re.fullmatch(r"[a-z][a-z0-9-]{3,63}", fixture_id) is None
        ):
            raise ContractValidationError("qualification novelty slot changed")
        if (
            not isinstance(novelty_id, str)
            or re.fullmatch(r"stress:[a-z][a-z0-9_]{2,95}", novelty_id) is None
        ):
            raise ContractValidationError("qualification novelty identity changed")
        if (
            not isinstance(tags, list)
            or any(not isinstance(tag, str) for tag in tags)
            or not 2 <= len(tags) <= 4
            or tags != sorted(set(tags))
            or set(tags) - _QUALIFICATION_STRESS_TAGS
            or not set(tags).intersection(_QUALIFICATION_ADVERSARIAL_STRESS_TAGS)
            or not set(tags).intersection(_QUALIFICATION_BOUNDARY_STRESS_TAGS)
        ):
            raise ContractValidationError("qualification novelty stress tags changed")
        if not isinstance(source_sha256, str) or not re_is_sha256(source_sha256):
            raise ContractValidationError("qualification novelty source binding changed")
        fixture_ids.append(fixture_id)
        novelty_ids.append(novelty_id)
        source_hashes.append(source_sha256)
        for tag in tags:
            expected_coverage[tag] = expected_coverage.get(tag, 0) + 1
    if novelty != sorted(novelty, key=lambda value: str(value["fixture_id"])):
        raise ContractValidationError("qualification novelty records are not sorted")
    if len(set(fixture_ids)) != 30 or len(set(novelty_ids)) != 30:
        raise ContractValidationError("qualification novelty identity is duplicated")
    if len(set(source_hashes)) != 30:
        raise ContractValidationError("qualification novelty source is duplicated")
    if set(novelty_ids).intersection(spent_novelty):
        raise StateConflictError("qualification current novelty identity is marked spent")
    if set(source_hashes).intersection(spent_sources):
        raise StateConflictError("qualification current fixture source is marked spent")
    if novelty_sha256 != canonical_sha256(novelty):
        raise StateConflictError("qualification novelty set binding changed")
    if coverage != dict(sorted(expected_coverage.items())):
        raise StateConflictError("qualification stress coverage changed")


def verify_qualification_artifacts(
    manifest: Mapping[str, Any],
    *,
    repository_root: Path,
) -> None:
    validate_qualification_manifest(manifest)
    root = repository_root.resolve()
    categories = manifest["artifact_categories"]
    if not isinstance(categories, Mapping):
        raise StateConflictError("qualification artifact categories are invalid")
    for raw_entries in categories.values():
        if not isinstance(raw_entries, list) or not raw_entries:
            raise StateConflictError("qualification artifact category is invalid")
        for entry in raw_entries:
            if not isinstance(entry, Mapping) or set(entry) != {
                "path",
                "location",
                "bytes",
                "sha256",
            }:
                raise StateConflictError("qualification artifact entry changed")
            location = entry["location"]
            if location == "repository":
                path = (root / str(entry["path"])).resolve()
                if not path.is_relative_to(root):
                    raise StateConflictError("qualification artifact escaped repository")
            elif location == "external":
                path = Path(str(entry["path"])).resolve()
            else:
                raise StateConflictError("qualification artifact location changed")
            if path.is_symlink() or not path.is_file():
                raise StateConflictError("qualification artifact is unavailable")
            data = path.read_bytes()
            if len(data) != entry["bytes"] or bytes_sha256(data) != entry["sha256"]:
                raise StateConflictError(f"qualification artifact changed: {entry['path']}")


def load_qualification_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractValidationError("qualification manifest is unreadable") from exc
    if not isinstance(value, dict):
        raise ContractValidationError("qualification manifest is not an object")
    validate_qualification_manifest(value)
    return value


def write_qualification_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    validate_qualification_manifest(manifest)
    if path.exists():
        raise StateConflictError("qualification manifest already exists")
    _atomic_write_json(path, manifest)


def _validate_accepted_route_projection(
    fixture: QualificationFixtureV1,
    cera: Mapping[str, Any],
) -> None:
    creator_trace = cera.get("creator_trace")
    if not isinstance(creator_trace, Mapping):
        raise StateConflictError("qualification completion omitted its creator trace")
    transition = cera.get("route_transition")
    trace_transition = creator_trace.get("route_transition")
    if fixture.expected_route is QualificationRoute.ORDINARY:
        if (
            creator_trace.get("logic_owner") != "codex_cognition"
            or transition is not None
            or trace_transition is not None
        ):
            raise StateConflictError("ordinary qualification route projection changed")
        current_logic_route = cera.get("current_logic_route")
        if current_logic_route not in (None, QualificationRoute.ORDINARY.value):
            raise StateConflictError("ordinary qualification next route changed")
        return

    if not isinstance(transition, Mapping) or not isinstance(trace_transition, Mapping):
        raise StateConflictError("adult qualification route transition is missing")
    expected_next = fixture.expected_next_route.value
    if (
        transition.get("to_route") != expected_next
        or trace_transition.get("to_route") != expected_next
        or cera.get("current_logic_route") != expected_next
    ):
        raise StateConflictError("adult qualification next route changed")
    expected_return = fixture.expected_next_route is QualificationRoute.ORDINARY
    if (
        cera.get("return_to_codex") is not expected_return
        or trace_transition.get("return_to_codex") is not expected_return
    ):
        raise StateConflictError("adult qualification return-route binding changed")


def _validate_rejected_route_projection(
    fixture: QualificationFixtureV1,
    cera: Mapping[str, Any],
) -> None:
    creator_trace = cera.get("creator_trace")
    if not isinstance(creator_trace, Mapping):
        raise StateConflictError("rejected qualification omitted its creator trace")
    transition = cera.get("route_transition")
    trace_transition = creator_trace.get("route_transition")
    if fixture.expected_route is QualificationRoute.ORDINARY:
        if transition is not None or trace_transition is not None:
            raise StateConflictError("ordinary rejection changed route ownership")
        current_logic_route = cera.get("current_logic_route")
        if current_logic_route not in (None, fixture.initial_route.value):
            raise StateConflictError("ordinary rejection changed accepted route")
        return

    if not isinstance(transition, Mapping) or not isinstance(trace_transition, Mapping):
        raise StateConflictError("adult rejection route transition is missing")
    if (
        transition.get("to_route") != fixture.expected_next_route.value
        or trace_transition.get("to_route") != fixture.expected_next_route.value
        or cera.get("current_logic_route") != fixture.initial_route.value
    ):
        raise StateConflictError("adult rejection route custody changed")


def _adult_repair_count(cera: Mapping[str, Any]) -> int:
    attempts = cera.get("repair_attempts")
    if not isinstance(attempts, list) or len(attempts) > 1:
        raise StateConflictError("adult qualification repair trace changed")
    expected_fields = {
        "public_review_id",
        "conflict_class",
        "operation_sha256",
        "outcome_sha256",
        "planner_provider_operations",
        "adult_scene_provider_operations",
        "adult_filter_provider_operations",
    }
    critical_classes = {
        "logic_contradiction",
        "current_data_conflict",
        "knowledge_or_privacy_conflict",
        "unsupported_durable_effect",
        "route_transition_conflict",
    }
    for attempt in attempts:
        if not isinstance(attempt, Mapping) or set(attempt) != expected_fields:
            raise StateConflictError("adult qualification repair trace shape changed")
        if (
            re.fullmatch(r"review-[a-f0-9]{28}", str(attempt["public_review_id"])) is None
            or attempt["conflict_class"] not in critical_classes
            or not re_is_sha256(str(attempt["operation_sha256"]))
            or not re_is_sha256(str(attempt["outcome_sha256"]))
        ):
            raise StateConflictError("adult qualification repair trace is invalid")
        for field_name in (
            "planner_provider_operations",
            "adult_scene_provider_operations",
            "adult_filter_provider_operations",
        ):
            value = attempt[field_name]
            if type(value) is not int or value < 0:
                raise StateConflictError("adult qualification repair operation count is invalid")
    return len(attempts)


def _validate_ordinary_provisional_completion(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
) -> OrdinaryProvisionalCompletionV1:
    if fixture.expected_route is not QualificationRoute.ORDINARY:
        raise StateConflictError("adult qualification used an ordinary review lifecycle")
    if response.status_code != 200:
        raise StateConflictError(f"qualification API returned HTTP {response.status_code}")
    body = response.body
    if body.get("error") is not None or body.get("status") == "error":
        raise StateConflictError("qualification API returned a typed error")
    prose = _visible_prose(body)
    if len(prose.strip()) < 40:
        raise StateConflictError("qualification completion prose is unusable")
    lowered = prose.casefold()
    if any(
        marker in lowered
        for marker in (
            "i can't help with that",
            "i cannot help with that",
            "unable to comply",
        )
    ):
        raise StateConflictError("qualification completion is a refusal")
    choices = body.get("choices")
    choice = choices[0] if isinstance(choices, list) and len(choices) == 1 else None
    if not isinstance(choice, Mapping) or choice.get("finish_reason") != "stop":
        raise StateConflictError("qualification provisional completion did not stop normally")
    cera = body.get("cera")
    if not isinstance(cera, Mapping):
        raise StateConflictError("qualification completion omitted CERA metadata")
    if (
        cera.get("profile_id") != PI_SCENE_PROFILE
        or cera.get("route_mode") != QualificationRoute.ORDINARY.value
        or cera.get("status") != "review_ready"
        or cera.get("provisional") is not True
        or cera.get("story_state_committed") is not False
        or cera.get("canon_status") is not None
        or cera.get("operational_warnings") not in (None, [])
    ):
        raise StateConflictError("ordinary qualification initial completion is not provisional")
    _validate_accepted_route_projection(fixture, cera)
    try:
        lifecycle = validate_ordinary_review_lifecycle_v2(cera.get("review_lifecycle"))
    except ContractValidationError as exc:
        raise StateConflictError(
            "ordinary qualification lifecycle failed generated validation"
        ) from exc
    review_id = lifecycle["review_id"]
    review_url = lifecycle["review_url"]
    candidate_sha256 = cera.get("candidate_sha256")
    if (
        lifecycle["review_mode"] != "automatic"
        or review_url != f"/v1/cera/reviews/{review_id}"
        or cera.get("review_url") != review_url
        or cera.get("provisional_review_id") != review_id
        or not isinstance(candidate_sha256, str)
        or not re_is_sha256(candidate_sha256)
    ):
        raise StateConflictError("ordinary provisional review binding changed")
    controls = cera.get("request_controls")
    if not isinstance(controls, Mapping) or (
        controls.get("schema_version") != "cera.pi_scene.request_controls.v3"
        or controls.get("review_mode") != "automatic"
        or controls.get("reasoning_effort") != QUALIFICATION_PLANNER_REASONING_EFFORT
        or controls.get("adult_craft_mode") != "off"
    ):
        raise StateConflictError("ordinary provisional request controls changed")
    return OrdinaryProvisionalCompletionV1(
        response=response,
        review_id=review_id,
        review_url=review_url,
        candidate_sha256=candidate_sha256,
        story_text=prose,
        lifecycle=lifecycle,
    )


def _validate_adult_rejection_review_authority(
    response: ClientResponseV1,
    *,
    rejection: RejectionReviewV1,
) -> Mapping[str, Any]:
    if response.status_code != 200:
        raise StateConflictError(f"adult review GET returned HTTP {response.status_code}")
    review = response.body
    candidate_sha256 = review.get("candidate_sha256")
    semantic = review.get("semantic_validation")
    if (
        review.get("schema_version") != "cera.pi_scene.review.v1"
        or review.get("review_id") != rejection.review_id
        or review.get("state") != "review_ready"
        or review.get("provisional") is not True
        or review.get("route") != "adult"
        or review.get("story_text") is not None
        or review.get("story_state_committed") is not False
        or review.get("candidate_id") != rejection.candidate_id
        or not isinstance(candidate_sha256, str)
        or not re_is_sha256(candidate_sha256)
        or review.get("primary_authority_sha256") != rejection.authority_sha256
        or review.get("regenerate_enabled") is not True
        or review.get("accept_enabled") is not False
        or review.get("provisional_accept_enabled") is not False
        or review.get("replan_enabled") is not False
        or review.get("repair_recording_enabled") is not False
        or review.get("operation_state") != "executed-rejected"
        or not isinstance(semantic, Mapping)
        or semantic.get("verdict") != "reject"
    ):
        raise StateConflictError("adult Regenerate review authority changed")
    return review


def _validate_ordinary_review_response(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
    *,
    provisional: OrdinaryProvisionalCompletionV1,
) -> OrdinaryReviewV3:
    if response.status_code != 200:
        raise StateConflictError(f"ordinary review GET returned HTTP {response.status_code}")
    try:
        review = validate_ordinary_review_v3(response.body)
    except ContractValidationError as exc:
        raise StateConflictError("ordinary review failed generated validation") from exc
    if (
        fixture.expected_route is not QualificationRoute.ORDINARY
        or review["review_id"] != provisional.review_id
        or review["review_mode"] != "automatic"
        or review["route"] != "ordinary"
        or review["story_text"] != provisional.story_text
        or review["candidate_sha256"] != provisional.candidate_sha256
    ):
        raise StateConflictError("ordinary review changed its provisional candidate")
    controls = cast(Mapping[str, Any], review["request_controls"])
    if (
        controls.get("schema_version") != "cera.pi_scene.request_controls.v3"
        or controls.get("review_mode") != "automatic"
        or controls.get("reasoning_effort") != QUALIFICATION_PLANNER_REASONING_EFFORT
        or controls.get("adult_craft_mode") != "off"
    ):
        raise StateConflictError("ordinary review changed qualification controls")
    return review


def _ordinary_review_lane_retry_envelopes(
    review: OrdinaryReviewV3,
) -> tuple[ProviderStageRetryStatusEnvelopeV1, ...]:
    checks = cast(Mapping[str, Any], review["checks"])
    envelopes: list[ProviderStageRetryStatusEnvelopeV1] = []
    for lane_name, expected_stage, expected_model in (
        ("luna", "semantic_validator", "luna"),
        ("reader", "reader", "sol"),
    ):
        lane = checks.get(lane_name)
        if not isinstance(lane, Mapping):
            raise StateConflictError("ordinary review check lane changed shape")
        raw = lane.get("provider_stage_retry_status")
        if raw is None:
            continue
        try:
            envelope = validate_provider_stage_retry_status_envelope_v1(raw)
        except ContractValidationError as exc:
            raise StateConflictError(
                "ordinary review lane Retry status failed generated validation"
            ) from exc
        status = cast(Mapping[str, Any], envelope["status"])
        if (
            status["stage"] != expected_stage
            or status["provider"] != "codex"
            or status["model_family"] != expected_model
            or status["story_state_committed"] is not False
        ):
            raise StateConflictError("ordinary review lane Retry identity changed")
        envelopes.append(envelope)
    return tuple(envelopes)


def _ordinary_attempt_trace_v2(
    review: OrdinaryReviewV3,
    *,
    accepted: bool,
    standing_policy: bool = False,
) -> None:
    attempts = review["provider_attempts"]
    operations = cast(Mapping[str, Any], review["provider_operations"])
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= 2:
        raise StateConflictError("ordinary review attempt count changed")
    totals = {key: 0 for key in ("planner", "writer", "validator", "reader")}
    candidate_ids: list[str] = []
    for ordinal, raw_attempt in enumerate(attempts, start=1):
        if not isinstance(raw_attempt, Mapping):
            raise StateConflictError("ordinary review attempt changed shape")
        attempt_operations = raw_attempt.get("provider_operations")
        candidate_id = raw_attempt.get("candidate_id")
        disposition = raw_attempt.get("disposition")
        if (
            raw_attempt.get("attempt_number") != ordinal
            or not isinstance(candidate_id, str)
            or not isinstance(attempt_operations, Mapping)
            or set(attempt_operations) != set(totals)
            or any(
                type(attempt_operations[key]) is not int or cast(int, attempt_operations[key]) < 0
                for key in totals
            )
        ):
            raise StateConflictError("ordinary review attempt binding changed")
        expected_planner = 1 if ordinal == 1 else 0
        if (
            attempt_operations["planner"] != expected_planner
            or cast(int, attempt_operations["writer"]) < 1
            or cast(int, attempt_operations["validator"]) < 1
            or cast(int, attempt_operations["reader"]) < 1
        ):
            raise StateConflictError("ordinary review attempt accounting changed")
        terminal_attempt = ordinal == len(attempts)
        if accepted and terminal_attempt:
            if standing_policy:
                if disposition not in {
                    "luna_rejected",
                    "reader_rejected",
                    "luna_reader_rejected",
                }:
                    raise StateConflictError(
                        "standing-policy acceptance hid its rejected disposition"
                    )
            elif disposition != "checks_passed":
                raise StateConflictError("accepted ordinary attempt did not pass all checks")
        elif disposition not in {
            "luna_rejected",
            "reader_rejected",
            "luna_reader_rejected",
        }:
            raise StateConflictError("ordinary rejected attempt disposition changed")
        candidate_ids.append(candidate_id)
        for key in totals:
            totals[key] += cast(int, attempt_operations[key])
    if len(candidate_ids) != len(set(candidate_ids)):
        raise StateConflictError("ordinary review reused a candidate identity")
    if any(operations.get(key) != total for key, total in totals.items()):
        raise StateConflictError("ordinary review request-total accounting changed")
    recorder = operations.get("recorder")
    if type(recorder) is not int or (accepted and recorder < 1) or (not accepted and recorder != 0):
        raise StateConflictError("ordinary review Recorder accounting changed")
    if totals["planner"] + totals["validator"] + totals["reader"] > cast(
        int,
        cast(Mapping[str, Any], QUALIFICATION_COMPLETE_GENERATION_CEILINGS["ordinary"])[
            "maximum_codex_operations"
        ],
    ):
        raise StateConflictError("ordinary review exceeded its Codex generation ceiling")
    if totals["writer"] + recorder > cast(
        int,
        cast(Mapping[str, Any], QUALIFICATION_COMPLETE_GENERATION_CEILINGS["ordinary"])[
            "maximum_deepseek_http_operations"
        ],
    ):
        raise StateConflictError("ordinary review exceeded its DeepSeek generation ceiling")


def _ordinary_rejection_review(
    fixture: QualificationFixtureV1,
    review: OrdinaryReviewV3,
) -> RejectionReviewV1:
    if review["state"] != "review_ready" or review["gate_status"] != "reject":
        raise StateConflictError("ordinary review is not an eligible joined rejection")
    actions = cast(Mapping[str, Any], review["actions"])
    if dict(actions) != {
        "accept_enabled": False,
        "regenerate_enabled": True,
        "decline_enabled": True,
        "replan_enabled": False,
        "auditable_override_enabled": True,
        "auditable_override_action": "accept_provisional",
        "repair_recording_enabled": False,
    }:
        raise StateConflictError("ordinary rejected review action authority changed")
    checks = cast(Mapping[str, Any], review["checks"])
    python_lane = cast(Mapping[str, Any], checks["python"])
    if python_lane["status"] != "pass":
        raise StateConflictError("ordinary rejected review did not pass Python custody")
    _ordinary_attempt_trace_v2(review, accepted=False)
    failures = {
        lane: cast(Mapping[str, Any], checks[lane])["failures"] for lane in ("luna", "reader")
    }
    operations = cast(Mapping[str, int], review["provider_operations"])
    projection = {
        "provider_operations": dict(operations),
        "accepted_turn_id": None,
        "accepted_receipt_sha256": None,
        "visible_prose_sha256": text_sha256(review["story_text"]),
        "observed_route": fixture.expected_route.value,
        "observed_next_route": fixture.expected_next_route.value,
        "first_pass_accepted": False,
        "first_pass_policy_provisional": False,
        "standing_policy_provisional_acceptances": 0,
        "standing_policy_audit": None,
        "automatic_repair_actions": 0,
    }
    return RejectionReviewV1(
        review_id=review["review_id"],
        candidate_id=review["candidate_id"],
        candidate_sha256=review["candidate_sha256"],
        authority_sha256=canonical_sha256(review),
        conflict_sha256=canonical_sha256(failures),
        provider_operations=operations,
        projection=projection,
    )


def _ordinary_standing_policy_reasons(review: OrdinaryReviewV3) -> tuple[str, ...]:
    """Independently derive exact soft reasons from the public V3 projection."""

    checks = cast(Mapping[str, Any], review["checks"])
    python_lane = cast(Mapping[str, Any], checks["python"])
    if (
        python_lane.get("status") != "pass"
        or python_lane.get("failures") != []
        or python_lane.get("provider_stage_retry_status") is not None
    ):
        raise StateConflictError("standing policy bypassed Python qualification")
    reasons: set[str] = set()
    luna = cast(Mapping[str, Any], checks["luna"])
    if luna.get("provider_stage_retry_status") is not None:
        raise StateConflictError("standing policy retained Luna Retry authority")
    luna_status = luna.get("status")
    luna_failures = luna.get("failures")
    if not isinstance(luna_failures, list):
        raise StateConflictError("standing-policy Luna failures changed shape")
    conflicts = [
        value
        for value in luna_failures
        if isinstance(value, Mapping) and value.get("source_kind") == "verdict_conflict"
    ]
    if any(
        not isinstance(value, Mapping)
        or value.get("source_kind") not in {"verdict_conflict", "review_flag"}
        for value in luna_failures
    ):
        raise StateConflictError("standing policy encountered a hard Luna signal")
    if luna_status == "pass":
        if conflicts:
            raise StateConflictError("passing Luna lane exposed a conflict")
    elif luna_status == "reject":
        if len(conflicts) != 1:
            raise StateConflictError("standing policy requires one typed Luna conflict")
        code = conflicts[0].get("code")
        if code not in set(ordinary_standing_creator_policy().soft_semantic_conflict_classes):
            raise StateConflictError("standing policy encountered a hard Luna conflict")
        reasons.add(f"luna:{code}")
    else:
        raise StateConflictError("standing policy encountered inconclusive Luna evidence")
    reader = cast(Mapping[str, Any], checks["reader"])
    if reader.get("provider_stage_retry_status") is not None:
        raise StateConflictError("standing policy retained Reader Retry authority")
    reader_status = reader.get("status")
    reader_failures = reader.get("failures")
    if not isinstance(reader_failures, list):
        raise StateConflictError("standing-policy Reader failures changed shape")
    if reader_status == "pass":
        if reader_failures:
            raise StateConflictError("passing Reader lane exposed a failure")
    elif reader_status == "reject":
        if not reader_failures or any(
            not isinstance(value, Mapping)
            or value.get("source_kind") != "reader_issue"
            or value.get("feedback_scope")
            not in set(ordinary_standing_creator_policy().soft_reader_feedback_scopes)
            for value in reader_failures
        ):
            raise StateConflictError("standing policy encountered a hard Reader issue")
        reasons.update(
            f"reader:{cast(Mapping[str, Any], value)['feedback_scope']}"
            for value in reader_failures
        )
    else:
        raise StateConflictError("standing policy encountered inconclusive Reader evidence")
    if not reasons:
        raise StateConflictError("standing policy accepted without a soft rejection")
    return tuple(sorted(reasons))


def _validate_ordinary_accepted_review(
    fixture: QualificationFixtureV1,
    review: OrdinaryReviewV3,
    *,
    standing_policy_after_regenerate: bool = False,
) -> dict[str, Any]:
    if (
        review["state"] != "accepted"
        or review["review_mode"] != "automatic"
        or review["recording_status"] != "complete"
    ):
        raise StateConflictError("ordinary qualification review was not fully accepted")
    checks = cast(Mapping[str, Any], review["checks"])
    reader_lane = cast(Mapping[str, Any], checks["reader"])
    if reader_lane["provider_stage_retry_status"] is not None:
        raise StateConflictError("ordinary acceptance retained Reader Retry authority")
    acceptance = review["acceptance"]
    if not isinstance(acceptance, Mapping) or (
        not isinstance(acceptance.get("accepted_turn_id"), str)
        or not re_is_sha256(str(acceptance.get("accepted_receipt_sha256")))
    ):
        raise StateConflictError("ordinary acceptance identity changed")
    standing_policy = acceptance.get("mode") == "standing_policy"
    if standing_policy:
        if review["gate_status"] != "reject" or acceptance.get("canon_status") != "provisional":
            raise StateConflictError("ordinary standing-policy acceptance changed disposition")
        reasons = _ordinary_standing_policy_reasons(review)
        expected_audit = build_ordinary_policy_acceptance_audit_from_bindings(
            candidate_sha256=review["candidate_sha256"],
            semantic_validation_sha256=cast(str, checks["luna"]["verdict_sha256"]),
            reader_validation_sha256=cast(str, checks["reader"]["verdict_sha256"]),
            python_qualification_sha256=cast(str, checks["python"]["verdict_sha256"]),
            tolerated_reason_codes=reasons,
        )
        if acceptance.get("standing_policy") != ordinary_policy_acceptance_projection(
            expected_audit
        ):
            raise StateConflictError("ordinary standing-policy audit did not recompute")
        expected_attempts = 2 if standing_policy_after_regenerate else 1
        if len(review["provider_attempts"]) != expected_attempts:
            raise StateConflictError(
                "ordinary standing-policy Writer count changed for its governed path"
            )
    else:
        if (
            acceptance.get("mode") != "automatic"
            or acceptance.get("canon_status") != "accepted"
            or acceptance.get("standing_policy") is not None
            or review["gate_status"] != "pass"
            or any(
                cast(Mapping[str, Any], checks[lane])["status"] != "pass"
                for lane in ("luna", "reader", "python")
            )
        ):
            raise StateConflictError("ordinary automatic acceptance authority changed")
    actions = cast(Mapping[str, Any], review["actions"])
    if (
        any(
            actions.get(key) is not False
            for key in (
                "accept_enabled",
                "regenerate_enabled",
                "decline_enabled",
                "replan_enabled",
                "auditable_override_enabled",
                "repair_recording_enabled",
            )
        )
        or actions.get("auditable_override_action") is not None
    ):
        raise StateConflictError("ordinary accepted review exposed a creator action")
    _ordinary_attempt_trace_v2(
        review,
        accepted=True,
        standing_policy=standing_policy,
    )
    return {
        "visible_prose_sha256": text_sha256(review["story_text"]),
        "accepted_turn_id": acceptance["accepted_turn_id"],
        "accepted_receipt_sha256": acceptance["accepted_receipt_sha256"],
        "observed_route": fixture.expected_route.value,
        "observed_next_route": fixture.expected_next_route.value,
        "first_pass_accepted": not standing_policy and len(review["provider_attempts"]) == 1,
        "first_pass_policy_provisional": (standing_policy and not standing_policy_after_regenerate),
        "standing_policy_provisional_acceptances": int(standing_policy),
        "standing_policy_audit": (acceptance.get("standing_policy") if standing_policy else None),
        "automatic_repair_actions": 0,
        "provider_operations": dict(cast(Mapping[str, int], review["provider_operations"])),
    }


def _validate_ordinary_terminal_decision_response(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
    *,
    review: OrdinaryReviewV3,
) -> OrdinaryReviewDecisionV3:
    if response.status_code != 200:
        raise StateConflictError(
            f"ordinary terminal decision GET returned HTTP {response.status_code}"
        )
    try:
        decision = validate_ordinary_review_decision_v3(response.body)
    except ContractValidationError as exc:
        raise StateConflictError("ordinary terminal decision failed generated validation") from exc
    decision_review = decision.get("review")
    if decision_review != review or decision.get("successor") is not None:
        raise StateConflictError("ordinary terminal decision changed its final review")
    creator_action = decision.get("creator_action")
    acceptance = cast(Mapping[str, Any], review["acceptance"])
    expected_actions = (
        {"standing_policy_accept_provisional", "repair_recording"}
        if acceptance.get("mode") == "standing_policy"
        else {"automatic_accept", "repair_recording"}
    )
    if creator_action not in expected_actions:
        raise StateConflictError("ordinary terminal action changed acceptance authority")
    if (
        decision.get("story_state_committed") is not True
        or decision.get("status") != "story_committed"
        or decision.get("accepted_turn_id") != acceptance["accepted_turn_id"]
        or decision.get("accepted_receipt_sha256") != acceptance["accepted_receipt_sha256"]
    ):
        raise StateConflictError("ordinary terminal decision changed acceptance identity")
    return decision


def _validate_optional_ordinary_repair_response(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
    *,
    review_id: str,
) -> None:
    if response.status_code != 200:
        raise StateConflictError(f"ordinary Recorder repair returned HTTP {response.status_code}")
    if response.body.get("schema_version") == "cera.pi_scene.review.v3":
        review = validate_ordinary_review_v3(response.body)
        if review["review_id"] != review_id:
            raise StateConflictError("ordinary Recorder repair changed review identity")
        return
    try:
        decision = validate_ordinary_review_decision_v3(response.body)
    except ContractValidationError as exc:
        raise StateConflictError("ordinary Recorder repair returned an invalid decision") from exc
    projected_review = decision.get("review")
    if (
        decision.get("creator_action") != "repair_recording"
        or not isinstance(projected_review, Mapping)
        or projected_review.get("review_id") != review_id
        or decision.get("successor") is not None
    ):
        raise StateConflictError("ordinary Recorder repair changed decision identity")


def _validate_ordinary_regenerate_response(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
    *,
    predecessor: OrdinaryReviewV3,
) -> OrdinaryProvisionalCompletionV1:
    if response.status_code != 200:
        raise StateConflictError(f"ordinary Regenerate returned HTTP {response.status_code}")
    captured: list[OrdinaryProvisionalCompletionV1] = []

    def validate_successor(value: object) -> object:
        if not isinstance(value, Mapping):
            raise ContractValidationError("ordinary Regenerate successor is not an object")
        provisional = _validate_ordinary_provisional_completion(
            fixture,
            ClientResponseV1(
                transport=response.transport,
                path=response.path,
                status_code=200,
                duration_ms=0,
                body=cast(Mapping[str, Any], value),
            ),
        )
        captured.append(provisional)
        return dict(value)

    try:
        decision = validate_ordinary_review_decision_v3(
            response.body,
            successor_validator=validate_successor,
        )
    except ContractValidationError as exc:
        raise StateConflictError(
            "ordinary Regenerate failed generated decision validation"
        ) from exc
    projected_predecessor = decision.get("review")
    if (
        decision.get("creator_action") != "regenerate"
        or decision.get("status") != "review_transitioned"
        or decision.get("story_state_committed") is not False
        or not isinstance(projected_predecessor, Mapping)
        or projected_predecessor.get("review_id") != predecessor["review_id"]
        or projected_predecessor.get("candidate_sha256") != predecessor["candidate_sha256"]
        or projected_predecessor.get("state") != "regenerated"
        or len(captured) != 1
    ):
        raise StateConflictError("ordinary Regenerate changed decision identity")
    successor = captured[0]
    if successor.review_id == predecessor["review_id"]:
        raise StateConflictError("ordinary Regenerate reused its predecessor review")
    return successor


def _ordinary_attempt_trace(
    cera: Mapping[str, Any],
    *,
    operations: Mapping[str, int],
    regenerated: bool,
    accepted: bool,
) -> tuple[bool, int]:
    attempts = cera.get("provider_attempts")
    if not isinstance(attempts, list) or not 1 <= len(attempts) <= 2:
        raise StateConflictError("ordinary qualification attempt trace changed")
    if accepted:
        expected_dispositions = (
            ("semantic_pass",)
            if len(attempts) == 1
            else (
                "semantic_rejected",
                "semantic_pass",
            )
        )
    else:
        expected_dispositions = ("semantic_rejected",)
    if len(attempts) != len(expected_dispositions):
        raise StateConflictError("ordinary qualification attempt disposition changed")
    candidate_ids: list[str] = []
    totals = {"planner": 0, "writer": 0, "validator": 0}
    for index, (attempt, disposition) in enumerate(
        zip(attempts, expected_dispositions, strict=True),
        start=1,
    ):
        if not isinstance(attempt, Mapping) or set(attempt) != {
            "attempt_number",
            "candidate_id",
            "disposition",
            "provider_operations",
        }:
            raise StateConflictError("ordinary qualification attempt shape changed")
        candidate_id = attempt["candidate_id"]
        attempt_operations = attempt["provider_operations"]
        if (
            attempt["attempt_number"] != index
            or attempt["disposition"] != disposition
            or not isinstance(candidate_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,239}", candidate_id) is None
            or not isinstance(attempt_operations, Mapping)
            or set(attempt_operations) != {"planner", "writer", "validator"}
        ):
            raise StateConflictError("ordinary qualification attempt binding changed")
        expected_planner = 0 if regenerated or index > 1 else 1
        if (
            attempt_operations["planner"] != expected_planner
            or type(attempt_operations["writer"]) is not int
            or attempt_operations["writer"] < 1
            or attempt_operations["validator"] != 1
        ):
            raise StateConflictError("ordinary qualification attempt accounting changed")
        candidate_ids.append(candidate_id)
        for key in totals:
            totals[key] += int(attempt_operations[key])
    if len(candidate_ids) != len(set(candidate_ids)):
        raise StateConflictError("ordinary qualification repair reused its candidate")
    if any(operations[key] != value for key, value in totals.items()):
        raise StateConflictError("ordinary qualification attempt totals changed")
    return expected_dispositions[0] == "semantic_pass", len(attempts) - 1


def _validate_completion_response(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
    *,
    regenerated: bool = False,
) -> dict[str, Any]:
    if response.status_code != 200:
        raise StateConflictError(f"qualification API returned HTTP {response.status_code}")
    body = response.body
    if body.get("error") is not None or body.get("status") == "error":
        raise StateConflictError("qualification API returned a typed error")
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise StateConflictError("qualification completion choices changed")
    choice = choices[0]
    if not isinstance(choice, Mapping) or choice.get("finish_reason") != "stop":
        raise StateConflictError("qualification completion did not stop normally")
    message = choice.get("message")
    if not isinstance(message, Mapping) or message.get("role") != "assistant":
        raise StateConflictError("qualification completion message changed")
    prose = message.get("content")
    if not isinstance(prose, str) or len(prose.strip()) < 40:
        raise StateConflictError("qualification completion prose is unusable")
    lowered = prose.casefold()
    if any(
        marker in lowered
        for marker in (
            "i can't help with that",
            "i cannot help with that",
            "unable to comply",
        )
    ):
        raise StateConflictError("qualification completion is a refusal")
    cera = body.get("cera")
    if not isinstance(cera, Mapping):
        raise StateConflictError("qualification completion omitted CERA metadata")
    if cera.get("profile_id") != PI_SCENE_PROFILE:
        raise StateConflictError("qualification completion profile changed")
    if cera.get("route_mode") != fixture.expected_route.value:
        raise StateConflictError("qualification completion used the wrong logic route")
    _validate_accepted_route_projection(fixture, cera)
    if (
        cera.get("status") != "accepted"
        or cera.get("provisional") is not False
        or cera.get("story_state_committed") is not True
        or cera.get("canon_status") != "accepted"
    ):
        raise StateConflictError("qualification completion was not atomically accepted")
    if cera.get("operational_warnings") not in (None, []):
        raise StateConflictError("qualification completion has an operational warning")
    accepted_turn_id = cera.get("accepted_turn_id")
    accepted_receipt_sha256 = cera.get("accepted_receipt_sha256")
    if not isinstance(accepted_turn_id, str) or not accepted_turn_id.strip():
        raise StateConflictError("qualification accepted turn identity is missing")
    if not isinstance(accepted_receipt_sha256, str) or not re_is_sha256(accepted_receipt_sha256):
        raise StateConflictError("qualification accepted receipt binding is missing")
    raw_operations = cera.get("provider_operations")
    if not isinstance(raw_operations, Mapping):
        raise StateConflictError("qualification provider operation projection is missing")
    operations = {str(key): value for key, value in raw_operations.items()}
    if any(type(value) is not int or value < 0 for value in operations.values()):
        raise StateConflictError("qualification provider operation count is invalid")
    if fixture.expected_route is QualificationRoute.ORDINARY:
        semantic = cera.get("semantic_validation")
        if not isinstance(semantic, Mapping) or semantic.get("verdict") != "pass":
            raise StateConflictError("ordinary qualification did not pass Luna validation")
        if cera.get("recording_status") != "complete":
            raise StateConflictError("ordinary qualification did not record on first invocation")
        required = {"planner", "writer", "validator", "recorder"}
        if (
            set(operations) != required
            or operations["writer"] < 1
            or operations["validator"] < 1
            or operations["recorder"] < 1
        ):
            raise StateConflictError("ordinary qualification operation projection changed")
        first_pass_accepted, automatic_repair_actions = _ordinary_attempt_trace(
            cera,
            operations=operations,
            regenerated=regenerated,
            accepted=True,
        )
    else:
        adult_filter = cera.get("adult_filter")
        if not isinstance(adult_filter, Mapping) or adult_filter.get("verdict") != "pass":
            raise StateConflictError("adult qualification did not pass its Filter")
        if (
            cera.get("recorder_required") is not False
            or cera.get("recording_status") != "complete_preaccept_filter"
        ):
            raise StateConflictError("adult qualification retained a Recorder dependency")
        required = {"planner", "adult_scene", "adult_filter", "recorder"}
        if set(operations) != required:
            raise StateConflictError("adult qualification operation projection changed")
        expected_planner = (
            0 if regenerated or fixture.initial_route is QualificationRoute.ADULT else 1
        )
        if (
            operations["planner"] != expected_planner
            or operations["adult_scene"] < 1
            or operations["adult_filter"] < 1
            or operations["recorder"] != 0
        ):
            raise StateConflictError("adult qualification operation count changed")
        for field_name in ("protected_full_record_sha256", "codex_projection_sha256"):
            value = cera.get(field_name)
            if not isinstance(value, str) or not re_is_sha256(value):
                raise StateConflictError("adult qualification custody binding is missing")
        operation_sha256 = cera.get("operation_sha256")
        candidate_id = cera.get("candidate_id")
        if not isinstance(operation_sha256, str) or not re_is_sha256(operation_sha256):
            raise StateConflictError("adult qualification operation binding is missing")
        if not isinstance(candidate_id, str) or not re.fullmatch(
            r"candidate:adult:(?:[a-f0-9]{32})|candidate:adult-regenerate:[a-f0-9]{24,32}",
            candidate_id,
        ):
            raise StateConflictError("adult qualification candidate binding is missing")
        creator_trace = cera.get("creator_trace")
        recording = creator_trace.get("recording") if isinstance(creator_trace, Mapping) else None
        if not isinstance(recording, Mapping) or dict(recording) != {
            "status": "complete_preaccept_filter",
            "recorder_required": False,
            "projection_status": "complete",
            "protected_record_status": "complete",
        }:
            raise StateConflictError("adult qualification promotion custody is incomplete")
        automatic_repair_actions = _adult_repair_count(cera)
        first_pass_accepted = automatic_repair_actions == 0
    return {
        "visible_prose_sha256": text_sha256(prose),
        "accepted_turn_id": accepted_turn_id,
        "accepted_receipt_sha256": accepted_receipt_sha256,
        "observed_route": fixture.expected_route.value,
        "observed_next_route": fixture.expected_next_route.value,
        "first_pass_accepted": first_pass_accepted,
        "automatic_repair_actions": automatic_repair_actions,
        "provider_operations": operations,
    }


def _classify_completion_response(
    fixture: QualificationFixtureV1,
    response: ClientResponseV1,
) -> Mapping[str, Any] | RejectionReviewV1:
    try:
        return _validate_completion_response(fixture, response)
    except StateConflictError as exc:
        body = response.body
        cera = body.get("cera") if isinstance(body, Mapping) else None
        if not isinstance(cera, Mapping) or cera.get("status") != "validation_rejected":
            raise
        if cera.get("route_mode") != fixture.expected_route.value:
            raise
        _validate_rejected_route_projection(fixture, cera)
        if cera.get("story_state_committed") is not False:
            raise StateConflictError("rejected qualification changed accepted state") from exc
        if cera.get("regenerate_enabled") is not True:
            raise StateConflictError("rejected qualification cannot be regenerated") from exc
        ordinary_review_id = cera.get("provisional_review_id")
        adult_review_id = cera.get("review_id")
        if fixture.expected_route is QualificationRoute.ORDINARY:
            if adult_review_id is not None:
                raise StateConflictError(
                    "ordinary rejection exposed an adult review identity"
                ) from exc
            review_id = ordinary_review_id
        else:
            if ordinary_review_id != adult_review_id:
                raise StateConflictError("adult rejection review aliases disagree") from exc
            review_id = adult_review_id
        if not isinstance(review_id, str) or not re.fullmatch(
            r"(?:[a-z][a-z0-9_]{0,31}:[A-Za-z0-9._-]{1,160}|review-[a-f0-9]{28})",
            review_id,
        ):
            raise StateConflictError("rejected qualification review identity is invalid") from exc
        conflict = _rejection_conflict(cera, fixture.expected_route)
        conflict_class = conflict.get("conflict_class")
        allowed_classes = (
            {"omitted_decision", "severe_incompleteness"}
            if fixture.expected_route is QualificationRoute.ORDINARY
            else {"logic_not_realized", "severe_incompleteness"}
        )
        if conflict_class not in allowed_classes:
            raise StateConflictError(
                "qualification rejection is not an isolated quality miss"
            ) from exc
        encoded = json.dumps(conflict, sort_keys=True, separators=(",", ":")).casefold()
        forbidden = (
            "critical",
            "custody",
            "schema",
            "route",
            "structural",
            "identity",
            "transaction",
            "api_error",
        )
        if any(token in encoded for token in forbidden):
            raise StateConflictError(
                "critical or structural qualification rejection cannot regenerate"
            ) from exc
        raw_operations = cera.get("provider_operations")
        if not isinstance(raw_operations, Mapping):
            raise StateConflictError("rejected qualification omitted provider accounting") from exc
        operations = {str(key): value for key, value in raw_operations.items()}
        if any(type(value) is not int or value < 0 for value in operations.values()):
            raise StateConflictError(
                "rejected qualification provider accounting is invalid"
            ) from exc
        if fixture.expected_route is QualificationRoute.ORDINARY:
            if (
                set(operations) != {"planner", "writer", "validator", "recorder"}
                or operations["writer"] < 1
                or operations["validator"] != 1
                or operations["recorder"] != 0
            ):
                raise StateConflictError(
                    "ordinary rejection is not one isolated first-pass miss"
                ) from exc
            _ordinary_attempt_trace(
                cera,
                operations=operations,
                regenerated=False,
                accepted=False,
            )
            raw_attempts = cera.get("provider_attempts")
            terminal_attempt = (
                raw_attempts[-1] if isinstance(raw_attempts, list) and raw_attempts else None
            )
            rejection_candidate_id = (
                terminal_attempt.get("candidate_id")
                if isinstance(terminal_attempt, Mapping)
                else None
            )
            if not isinstance(rejection_candidate_id, str):
                raise StateConflictError(
                    "ordinary rejection candidate identity is unavailable"
                ) from exc
            rejection_candidate_sha256 = text_sha256(rejection_candidate_id)
            rejection_authority_sha256 = canonical_sha256(cera)
        else:
            expected_planner = 1 if fixture.initial_route is QualificationRoute.ORDINARY else 0
            candidate_id = cera.get("candidate_id")
            authority_sha256 = cera.get("operation_sha256")
            if (
                set(operations) != {"planner", "adult_scene", "adult_filter", "recorder"}
                or operations["planner"] != expected_planner
                or operations["adult_scene"] < 1
                or operations["adult_filter"] < 1
                or operations["recorder"] != 0
                or not isinstance(candidate_id, str)
                or not re.fullmatch(r"candidate:adult:[a-f0-9]{32}", candidate_id)
                or not isinstance(authority_sha256, str)
                or not re_is_sha256(authority_sha256)
            ):
                raise StateConflictError(
                    "adult rejection is not one isolated first-pass miss"
                ) from exc
            if cera.get("repair_attempts") != []:
                raise StateConflictError(
                    "adult rejection already consumed its automatic repair"
                ) from exc
            rejection_candidate_id = candidate_id
            rejection_candidate_sha256 = None
            rejection_authority_sha256 = authority_sha256
        projection = {
            "provider_operations": operations,
            "accepted_turn_id": None,
            "accepted_receipt_sha256": None,
            "visible_prose_sha256": text_sha256(_visible_prose(body)),
            "observed_route": fixture.expected_route.value,
            "observed_next_route": fixture.expected_next_route.value,
            "first_pass_accepted": False,
            "automatic_repair_actions": 0,
        }
        return RejectionReviewV1(
            review_id=review_id,
            candidate_id=rejection_candidate_id,
            candidate_sha256=rejection_candidate_sha256,
            authority_sha256=rejection_authority_sha256,
            conflict_sha256=canonical_sha256(conflict),
            provider_operations=cast(Mapping[str, int], operations),
            projection=projection,
        )


def _rejection_conflict(
    cera: Mapping[str, Any],
    route: QualificationRoute,
) -> Mapping[str, Any]:
    if route is QualificationRoute.ORDINARY:
        validation = cera.get("semantic_validation")
        conflict = validation.get("conflict") if isinstance(validation, Mapping) else None
    else:
        adult_filter = cera.get("adult_filter")
        conflict = adult_filter.get("conflict") if isinstance(adult_filter, Mapping) else None
    if not isinstance(conflict, Mapping) or not conflict:
        raise StateConflictError("qualification rejection omitted typed conflict evidence")
    return conflict


def _successor_completion(response: ClientResponseV1) -> ClientResponseV1:
    if "choices" in response.body:
        return response
    successor = response.body.get("successor")
    if not isinstance(successor, Mapping):
        raise StateConflictError("Regenerate did not return one successor completion")
    return ClientResponseV1(
        transport=response.transport,
        path=response.path,
        status_code=response.status_code,
        duration_ms=response.duration_ms,
        body=successor,
    )


def _visible_prose(body: Mapping[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise StateConflictError("qualification response omitted its visible prose")
    choice = choices[0]
    message = choice.get("message") if isinstance(choice, Mapping) else None
    prose = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(prose, str) or not prose.strip():
        raise StateConflictError("qualification response visible prose is invalid")
    return prose


def _provider_stage_retry_envelope(
    response: ClientResponseV1,
) -> ProviderStageRetryStatusEnvelopeV1 | None:
    """Return one exact generated envelope; legacy/narrative errors are inert."""

    schema_version = response.body.get("schema_version")
    if schema_version != "cera.provider_stage_retry_status_envelope.v1":
        return None
    if response.status_code not in {200, 409}:
        raise StateConflictError(
            "qualification provider-stage envelope used an invalid HTTP status"
        )
    try:
        return validate_provider_stage_retry_status_envelope_v1(response.body)
    except ContractValidationError as exc:
        raise StateConflictError(
            "qualification provider-stage envelope failed generated validation"
        ) from exc


def _manual_provider_stage_action_kind(
    envelope: ProviderStageRetryStatusEnvelopeV1,
) -> str | None:
    status = cast(Mapping[str, Any], envelope["status"])
    raw_state = status.get("state")
    state = raw_state if isinstance(raw_state, str) else None
    actions = envelope["actions"]
    expected_by_state = {
        "eligible": "provider_retry",
        "in_progress": "resume_prepared",
        "recording_repair_required": "repair_recording",
    }
    expected = expected_by_state.get(state) if state is not None else None
    if not actions:
        return None
    if len(actions) != 1:
        raise StateConflictError("qualification provider-stage action cardinality changed")
    action = validate_provider_stage_retry_action_v1(actions[0])
    if state == "blocked_ambiguous" and action["action_kind"] == "check_status":
        # Check Status is authenticated GET and is never POSTed by qualification.
        return None
    if expected is None or action["action_kind"] != expected:
        raise StateConflictError("qualification provider-stage action/state changed")
    return expected


def _provider_stage_request_sha256(
    envelope: ProviderStageRetryStatusEnvelopeV1,
) -> str:
    status = cast(Mapping[str, Any], envelope["status"])
    technical = cast(Mapping[str, Any], status["technical_details"])
    request_sha256 = technical.get("request_sha256")
    if not isinstance(request_sha256, str) or not re_is_sha256(request_sha256):
        raise StateConflictError("qualification provider-stage request binding is invalid")
    return request_sha256


def _validate_provider_stage_successor_request(
    envelope: ProviderStageRetryStatusEnvelopeV1,
    *,
    request_sha256: str,
) -> None:
    if _provider_stage_request_sha256(envelope) != request_sha256:
        raise StateConflictError("qualification provider-stage successor changed request")


def _remember_provider_stage_envelope(
    envelope: ProviderStageRetryStatusEnvelopeV1,
    *,
    request_sha256: str,
    expected_route: QualificationRoute,
    observations: list[ProviderStageRetryStatusEnvelopeV1],
    chain_ids: list[str],
    chain_bindings: dict[str, tuple[object, ...]],
    maximum_chains: int,
) -> Mapping[str, Any]:
    canonical = validate_provider_stage_retry_status_envelope_v1(envelope)
    _validate_provider_stage_successor_request(
        canonical,
        request_sha256=request_sha256,
    )
    status = cast(Mapping[str, Any], canonical["status"])
    technical = cast(Mapping[str, Any], status["technical_details"])
    stage = cast(str, status["stage"])
    allowed_stages = (
        {"planner", "semantic_validator", "reader", "writer", "recorder"}
        if expected_route is QualificationRoute.ORDINARY
        else {"planner", "adult_scene", "adult_filter"}
    )
    if stage not in allowed_stages:
        raise StateConflictError("qualification provider-stage Retry changed route ownership")
    chain_id = cast(str, status["chain_id"])
    binding = (
        status["provider"],
        status["model_family"],
        stage,
        technical["request_occurrence_sha256"],
        technical["request_sha256"],
        technical["stage_input_sha256"],
        technical["accepted_state_sha256"],
    )
    prior_binding = chain_bindings.get(chain_id)
    if prior_binding is not None and prior_binding != binding:
        raise StateConflictError("qualification provider-stage chain changed frozen identity")
    if prior_binding is None:
        if len(chain_ids) >= maximum_chains:
            raise StateConflictError(
                "qualification provider-stage successor exceeded stage occurrences"
            )
        chain_bindings[chain_id] = binding
        chain_ids.append(chain_id)
    prior_status = next(
        (
            cast(Mapping[str, Any], value["status"])
            for value in reversed(observations)
            if cast(Mapping[str, Any], value["status"])["chain_id"] == chain_id
        ),
        None,
    )
    if prior_status is not None:
        monotonic_fields = (
            "stage_attempts_total",
            "retry_actions_accepted",
            "provider_operations_observed_total",
            "provider_operations_conservative_total",
        )
        if any(
            cast(int, status[field]) < cast(int, prior_status[field]) for field in monotonic_fields
        ):
            raise StateConflictError("qualification provider-stage counters moved backward")
    observations.append(canonical)
    return status


def _authenticated_provider_stage_completion(
    response: ClientResponseV1,
) -> ClientResponseV1:
    if response.status_code != 200:
        raise StateConflictError(
            "qualification provider-stage GET did not return status or completion"
        )
    return ClientResponseV1(
        transport="authenticated_provider_stage_retry_get",
        path=response.path,
        status_code=200,
        duration_ms=0,
        body=dict(response.body),
    )


def _safe_provider_operation_delta(delta: ProviderLedgerDeltaV1) -> dict[str, Any]:
    records = _provider_operation_records(delta)
    return {
        "sol_transport_operations": delta.sol_transport_operations,
        "sol_charged_operations": delta.sol_charged_operations,
        "deepseek_http_operations": delta.deepseek_started_operations,
        "operation_evidence_sha256": canonical_sha256(records),
        "stage_counts": {
            stage: sum(value.get("stage") == stage for value in records)
            for stage in (
                "planner",
                "semantic_validator",
                "reader",
                "writer",
                "recorder",
                "adult_scene",
                "adult_filter",
            )
        },
    }


def _critical_provider_stage_projection(
    envelope: ProviderStageRetryStatusEnvelopeV1,
    *,
    stop_reason: str | None = None,
) -> dict[str, Any]:
    status = cast(Mapping[str, Any], envelope["status"])
    technical = cast(Mapping[str, Any], status["technical_details"])
    state = cast(str, status["state"])
    if state not in {
        "attempts_exhausted",
        "recording_repair_required",
        "recovery_required",
        "blocked_ambiguous",
    }:
        raise StateConflictError("qualification critical provider-stage state changed")
    body: dict[str, Any] = {
        "schema_version": "cera.pi_scene.qualification_critical_provider_stage_failure.v2",
        "severity": "critical",
        "state": state,
        "provider": status["provider"],
        "model_family": status["model_family"],
        "stage": status["stage"],
        "maximum_attempts": status["maximum_attempts"],
        "stage_attempts_total": status["stage_attempts_total"],
        "retry_actions_accepted": status["retry_actions_accepted"],
        "provider_operations_observed_total": status["provider_operations_observed_total"],
        "provider_operations_conservative_total": status["provider_operations_conservative_total"],
        "story_state_committed": status["story_state_committed"],
        "failure_category": status["failure_category"],
        "chain_id": status["chain_id"],
        "request_occurrence_sha256": technical["request_occurrence_sha256"],
        "request_sha256": technical["request_sha256"],
        "stage_input_sha256": technical["stage_input_sha256"],
        "accepted_state_sha256": technical["accepted_state_sha256"],
        "chain_sha256": technical["chain_sha256"],
        "status_sha256": canonical_sha256(envelope),
    }
    if stop_reason is not None:
        body["stop_reason"] = stop_reason
    return {**body, "critical_failure_sha256": canonical_sha256(body)}


def _finalize_provider_stage_retry_chain(
    resolution: ProviderStageRetryResolutionV1,
) -> dict[str, Any]:
    if not resolution.chain_ids or not resolution.envelopes:
        raise StateConflictError("qualification provider-stage chain evidence is empty")
    body = {
        "schema_version": "cera.pi_scene.qualification_provider_stage_retry_chain.v1",
        "request_sha256": resolution.request_sha256,
        "chain_ids": list(resolution.chain_ids),
        "chain_id_hashes": [text_sha256(value) for value in resolution.chain_ids],
        "retry_action_count": resolution.retry_action_count,
        "resume_prepared_action_count": resolution.resume_prepared_action_count,
        "repair_recording_action_count": resolution.repair_recording_action_count,
        "control_action_count": len(resolution.actions),
        "maximum_retry_actions_per_chain": 2,
        "maximum_attempts_per_chain": 3,
        "maximum_resume_prepared_actions_per_chain": 1,
        "maximum_recording_repair_actions_per_request": 1,
        "recursive_recording_repair": False,
        "actions": [dict(value) for value in resolution.actions],
        "status_observations": [dict(value) for value in resolution.envelopes],
        "terminal_state": resolution.terminal_status["state"],
        "completion_sha256": (
            None
            if resolution.completion_response is None
            else canonical_sha256(resolution.completion_response.body)
        ),
        "critical_failure": (
            None if resolution.critical_failure is None else dict(resolution.critical_failure)
        ),
        "duration_ms": resolution.duration_ms,
    }
    return {**body, "retry_chain_sha256": canonical_sha256(body)}


def _validate_terminal_provider_stage_accounting(
    resolution: ProviderStageRetryResolutionV1,
    *,
    operation_records: Sequence[Mapping[str, Any]],
) -> None:
    status = resolution.terminal_status
    stage = status["stage"]
    observed = cast(int, status["provider_operations_observed_total"])
    stage_records = [value for value in operation_records if value.get("stage") == stage]
    if len(stage_records) < observed:
        raise StateConflictError(
            "qualification provider-stage status exceeds append-only ledger accounting"
        )
    if status["provider"] == "deepseek" and any(
        value.get("provider_family") != "deepseek" for value in stage_records
    ):
        raise StateConflictError("qualification DeepSeek stage changed ledger family")
    if status["provider"] == "codex" and any(
        value.get("provider_family") not in {"sol", "luna"} for value in stage_records
    ):
        raise StateConflictError("qualification Codex stage changed ledger family")
    expected_codex_family = {
        "planner": "sol",
        "semantic_validator": "luna",
        "reader": "sol",
    }.get(cast(str, stage))
    if expected_codex_family is not None and (
        status["provider"] != "codex"
        or status["model_family"] != expected_codex_family
        or any(value.get("provider_family") != expected_codex_family for value in stage_records)
    ):
        raise StateConflictError("qualification Codex stage/model ownership changed")


def _sol_charged_operation_count(events: Sequence[Mapping[str, Any]]) -> int:
    by_call: dict[str, list[Mapping[str, Any]]] = {}
    for event in events:
        call_id = event.get("call_id")
        if isinstance(call_id, str):
            by_call.setdefault(call_id, []).append(event)
    charged = 0
    for values in by_call.values():
        states = {value.get("state") for value in values}
        if "transport_invoked" in states or values[-1].get("state") in {
            "prepared_not_invoked",
            "worker_started_not_invoked",
            "worker_preflight_not_invoked",
        }:
            charged += 1
    return charged


def _validate_provider_delta(
    fixture: QualificationFixtureV1,
    projections: Sequence[Mapping[str, Any]],
    delta: ProviderLedgerDeltaV1,
    *,
    provider_stage_retry_chains: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    if not projections or len(projections) > 2:
        raise StateConflictError("qualification attempt accounting is invalid")
    operation_sets = [
        cast(Mapping[str, int], projection["provider_operations"]) for projection in projections
    ]
    if fixture.expected_route is QualificationRoute.ORDINARY:
        expected_sol = sum(
            value["planner"] + value["validator"] + value["reader"] for value in operation_sets
        )
        expected_deepseek = sum(value["writer"] + value["recorder"] for value in operation_sets)
    else:
        expected_sol = sum(value["planner"] for value in operation_sets)
        expected_deepseek = sum(
            value["adult_scene"] + value["adult_filter"] for value in operation_sets
        )
    retry_extra_observed = {"codex": 0, "deepseek": 0}
    retry_extra_conservative = {"codex": 0, "deepseek": 0}
    retried_stages: set[str] = set()
    for chain in provider_stage_retry_chains:
        raw_observations = chain.get("status_observations")
        if not isinstance(raw_observations, list) or not raw_observations:
            raise StateConflictError("qualification provider-stage observations changed")
        latest_by_chain: dict[str, Mapping[str, Any]] = {}
        for raw_envelope in raw_observations:
            canonical = validate_provider_stage_retry_status_envelope_v1(raw_envelope)
            status = cast(Mapping[str, Any], canonical["status"])
            latest_by_chain[cast(str, status["chain_id"])] = status
        for status in latest_by_chain.values():
            provider = cast(str, status["provider"])
            retried_stages.add(cast(str, status["stage"]))
            observed = cast(int, status["provider_operations_observed_total"])
            conservative = cast(int, status["provider_operations_conservative_total"])
            if provider == "deepseek":
                # A Pi attempt may contain 1..6 provider operations.  Never
                # guess that the accepted attempt contributed exactly one;
                # exact nonaccepted invocation operations are reconciled from
                # the append-only invocation ledger below.
                continue
            if fixture.expected_route is QualificationRoute.ORDINARY and status["stage"] in {
                "semantic_validator",
                "reader",
            }:
                # review.v2 receives exact generic-chain ledger totals for both
                # independent validation lanes.  Those failed+accepted calls
                # are already included in its request-total projection.
                continue
            # A succeeded status includes the accepted operation. Eligible and
            # in-progress observations precede the successful Retry dispatch,
            # so all operations they report are additional to the completion.
            accepted_operation = 1 if status["state"] == "succeeded" else 0
            retry_extra_observed[provider] += max(0, observed - accepted_operation)
            retry_extra_conservative[provider] += max(
                0,
                conservative - accepted_operation,
            )
    expected_submitted_sol = expected_sol + retry_extra_observed["codex"]
    expected_charged_sol = expected_sol + retry_extra_conservative["codex"]
    if delta.sol_transport_operations != expected_submitted_sol:
        raise StateConflictError("qualification Sol ledger differs from HTTP projection")
    if delta.sol_charged_operations != expected_charged_sol:
        raise StateConflictError("qualification charged Sol ledger differs from HTTP projection")
    deepseek_nonaccepted_operations = _deepseek_nonaccepted_retry_operations(
        delta,
        retried_stages=retried_stages,
    )
    if delta.deepseek_started_operations != (expected_deepseek + deepseek_nonaccepted_operations):
        raise StateConflictError("qualification DeepSeek ledger differs from HTTP projection")
    for value in delta.sol_events:
        state = value.get("state")
        if state in {
            "provider_completed_post_validation_failed",
            "pretransport_failed",
            "provider_failed",
        }:
            sol_stage_by_owner = {
                "planner": "planner",
                "validator": "semantic_validator",
                "reader": "reader",
            }
            owner = value.get("owner")
            if owner not in sol_stage_by_owner:
                raise StateConflictError("qualification Sol failure owner changed")
            owner_stage = sol_stage_by_owner[cast(str, owner)]
            if state not in {"pretransport_failed", "provider_failed"} or owner_stage not in (
                retried_stages
            ):
                raise StateConflictError("qualification Sol ledger contains an unrelated failure")
    prepared_stages = {
        cast(str, value["invocation_id"]): _deepseek_stage(value.get("purpose"))
        for value in delta.deepseek_events
        if value.get("event") == "invocation_prepared"
        and isinstance(value.get("invocation_id"), str)
    }
    if any(
        value.get("event") == "forbidden_automatic_operation_observed"
        or (
            value.get("event") == "invocation_failed"
            and prepared_stages.get(cast(str, value.get("invocation_id"))) not in retried_stages
        )
        for value in delta.deepseek_events
    ):
        raise StateConflictError("qualification DeepSeek ledger contains a failure")
    input_tokens = delta.deepseek_input_tokens
    cached_tokens = delta.deepseek_cached_input_tokens
    return {
        "sol_http_operations": delta.sol_transport_operations,
        "sol_charged_operations": delta.sol_charged_operations,
        "deepseek_http_operations": delta.deepseek_started_operations,
        "deepseek_input_tokens": input_tokens,
        "deepseek_cached_input_tokens": cached_tokens,
        "deepseek_cache_ratio": (
            0.0 if input_tokens == 0 else round(cached_tokens / input_tokens, 6)
        ),
        "provider_operation_evidence_sha256": canonical_sha256(_provider_operation_records(delta)),
    }


def _deepseek_nonaccepted_retry_operations(
    delta: ProviderLedgerDeltaV1,
    *,
    retried_stages: set[str],
) -> int:
    """Count exact started operations from nonaccepted Pi invocations.

    Writer/Recorder/Adult invocations may perform multiple provider HTTP
    operations.  The terminal invocation event, not a guessed subtraction,
    identifies whether those operations are already represented by the safe
    accepted-result projection.
    """

    prepared: dict[str, str] = {}
    started: dict[str, int] = {}
    terminal: dict[str, str] = {}
    for event in delta.deepseek_events:
        invocation_id = event.get("invocation_id")
        if not isinstance(invocation_id, str):
            continue
        event_name = event.get("event")
        if event_name == "invocation_prepared":
            prepared_stage = _deepseek_stage(event.get("purpose"))
            prepared[invocation_id] = prepared_stage
        elif event_name == "provider_operation_started":
            started[invocation_id] = started.get(invocation_id, 0) + 1
        elif event_name in {"invocation_completed", "invocation_failed"}:
            if invocation_id in terminal:
                raise StateConflictError("qualification DeepSeek invocation terminalized twice")
            terminal[invocation_id] = cast(str, event_name)
    extra = 0
    for invocation_id, operation_count in started.items():
        bound_stage = prepared.get(invocation_id)
        if bound_stage is None:
            raise StateConflictError(
                "qualification DeepSeek operation lost its prepared invocation"
            )
        terminal_event = terminal.get(invocation_id)
        if terminal_event == "invocation_completed":
            continue
        if bound_stage not in retried_stages:
            raise StateConflictError(
                "qualification DeepSeek ledger contains an unauthorized incomplete attempt"
            )
        extra += operation_count
    return extra


def _provider_stage_latency_summary(
    records: Sequence[Mapping[str, Any]],
    planner_observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Summarize measured provider transport time by the seven live stages."""

    planner_classes: dict[str, str] = {}
    for value in planner_observations:
        operation_id = value.get("operation_id")
        latency_class = value.get("latency_class")
        if isinstance(operation_id, str) and isinstance(latency_class, str):
            planner_classes[operation_id] = latency_class
    stage_names = (
        "planner",
        "semantic_validator",
        "reader",
        "writer",
        "recorder",
        "adult_scene",
        "adult_filter",
    )
    result: dict[str, Any] = {}
    for stage in stage_names:
        selected = [value for value in records if value.get("stage") == stage]
        durations = [
            cast(int, value["duration_ms"])
            for value in selected
            if _provider_operation_has_transport(value) and type(value.get("duration_ms")) is int
        ]
        prepared_durations = [
            cast(int, value["prepared_to_terminal_duration_ms"])
            for value in selected
            if type(value.get("prepared_to_terminal_duration_ms")) is int
        ]
        failures = sum(_provider_operation_failed(value) for value in selected)
        classes: dict[str, list[int]] = {}
        for value in selected:
            duration_ms = value.get("duration_ms")
            if not _provider_operation_has_transport(value) or type(duration_ms) is not int:
                continue
            if stage == "planner":
                operation_id = value.get("operation_id")
                session_class = (
                    planner_classes.get(operation_id, "unclassified")
                    if isinstance(operation_id, str)
                    else "unclassified"
                )
            elif stage == "writer":
                session_class = "fresh_rehydration"
            else:
                session_class = "fresh_single_use"
            classes.setdefault(session_class, []).append(duration_ms)
        retained_violations: list[dict[str, Any]] = []
        if stage == "planner":
            for value in selected:
                operation_id = value.get("operation_id")
                duration_ms = value.get("duration_ms")
                if (
                    isinstance(operation_id, str)
                    and type(duration_ms) is int
                    and planner_classes.get(operation_id) == "retained_latency_concern"
                ):
                    retained_violations.append(
                        {
                            "operation_id_sha256": text_sha256(operation_id),
                            "duration_ms": duration_ms,
                        }
                    )
        result[stage] = {
            "operations_total": len(selected),
            "provider_transport_operations_total": sum(
                _provider_operation_has_transport(value) for value in selected
            ),
            "provider_transport_durations_unavailable": sum(
                _provider_operation_has_transport(value)
                and type(value.get("duration_ms")) is not int
                for value in selected
            ),
            **_latency_summary(durations, failures=failures),
            "prepared_to_terminal": _latency_summary(
                prepared_durations,
                failures=failures,
            ),
            "session_classes": {
                name: _latency_summary(values, failures=0)
                for name, values in sorted(classes.items())
            },
            "retained_latency_violations": retained_violations,
        }
    return result


def _planner_session_latency_summary(
    planner_observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {
        "cold": [],
        "rehydrated": [],
        "retained": [],
    }
    for observation in planner_observations:
        latency_class = observation.get("latency_class")
        if latency_class == "cold_start":
            grouped["cold"].append(observation)
        elif latency_class == "cold_rehydration":
            grouped["rehydrated"].append(observation)
        elif latency_class in {"retained_within_target", "retained_latency_concern"}:
            grouped["retained"].append(observation)
        else:
            raise StateConflictError("qualification Planner latency class changed")

    categories: dict[str, Any] = {}
    for name, observations in grouped.items():
        durations = [
            cast(int, value["duration_ms"])
            for value in observations
            if type(value.get("duration_ms")) is int
        ]
        categories[name] = {
            "operations_total": len(observations),
            "durations_unavailable": len(observations) - len(durations),
            **_latency_summary(durations, failures=0),
        }
    retained = grouped["retained"]
    return {
        "schema_version": "cera.pi_scene.planner_session_latency_summary.v1",
        "metric": "planner_provider_transport_duration_ms",
        "categories": categories,
        "retained_transport_concern": {
            "scope": "retained_planner_provider_transport_duration_ms_only",
            "threshold_ms": RETAINED_PLANNER_LATENCY_CONCERN_MS,
            "inclusive": True,
            "cold_excluded": True,
            "rehydrated_excluded": True,
            "violations": sum(value.get("retained_latency_concern") is True for value in retained),
        },
    }


def _provider_transport_latency_evidence(
    records: Sequence[Mapping[str, Any]],
    planner_observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    planner_classes = {
        cast(str, value["operation_id"]): _normalized_planner_session_class(value)
        for value in planner_observations
        if isinstance(value.get("operation_id"), str)
    }
    groups: dict[
        tuple[str, str, str, int | None, str, str | None],
        list[Mapping[str, Any]],
    ] = {}
    unavailable_attempts = 0
    for record in records:
        provider = record.get("provider")
        model = record.get("model")
        stage = record.get("stage")
        outcome = record.get("outcome")
        if not all(isinstance(value, str) and value for value in (provider, model, stage)):
            raise StateConflictError("qualification provider latency identity is incomplete")
        if outcome not in {"success", "failure", "unavailable"}:
            raise StateConflictError("qualification provider latency outcome changed")
        raw_attempt = record.get("attempt_number")
        attempt_number = raw_attempt if type(raw_attempt) is int else None
        if attempt_number is None:
            unavailable_attempts += 1
        operation_id = record.get("operation_id")
        planner_class = (
            planner_classes.get(operation_id, "unavailable")
            if stage == "planner" and isinstance(operation_id, str)
            else None
        )
        key = (
            cast(str, provider),
            cast(str, model),
            cast(str, stage),
            attempt_number,
            cast(str, outcome),
            planner_class,
        )
        groups.setdefault(key, []).append(record)

    rows: list[dict[str, Any]] = []
    for key, selected in sorted(
        groups.items(),
        key=lambda item: tuple("" if value is None else str(value) for value in item[0]),
    ):
        provider, model, stage, attempt_number, outcome, planner_class = key
        durations = [
            cast(int, value["duration_ms"])
            for value in selected
            if _provider_operation_has_transport(value) and type(value.get("duration_ms")) is int
        ]
        rows.append(
            {
                "provider": provider,
                "model": model,
                "stage": stage,
                "attempt_number": attempt_number,
                "attempt_number_availability": (
                    "measured" if attempt_number is not None else "unavailable"
                ),
                "outcome": outcome,
                "planner_session_class": planner_class,
                "operations_total": len(selected),
                "provider_transport_operations_total": sum(
                    _provider_operation_has_transport(value) for value in selected
                ),
                "provider_transport_durations_unavailable": sum(
                    _provider_operation_has_transport(value)
                    and type(value.get("duration_ms")) is not int
                    for value in selected
                ),
                **_latency_summary(
                    durations,
                    failures=sum(value.get("outcome") == "failure" for value in selected),
                ),
            }
        )
    return {
        "schema_version": "cera.pi_scene.provider_transport_latency_evidence.v1",
        "metric": "provider_transport_duration_ms",
        "aggregation_dimensions": [
            "provider",
            "model",
            "stage",
            "attempt_number",
            "outcome",
            "planner_session_class",
        ],
        "attempt_number_dimension": {
            "availability": (
                "measured"
                if records and unavailable_attempts == 0
                else ("partial" if unavailable_attempts < len(records) else "unavailable")
            ),
            "unavailable_operations": unavailable_attempts,
            "reason": (
                None
                if unavailable_attempts == 0
                else "provider_ledgers_do_not_bind_stage_occurrence_attempt_ordinals"
            ),
            "status_counters_preserved_in": ("provider_stage_retry_chains.status_observations"),
        },
        "groups": rows,
        "planner_session_classes": _planner_session_latency_summary(planner_observations),
    }


def _normalized_planner_session_class(observation: Mapping[str, Any]) -> str:
    latency_class = observation.get("latency_class")
    if latency_class == "cold_start":
        return "cold"
    if latency_class == "cold_rehydration":
        return "rehydrated"
    if latency_class in {"retained_within_target", "retained_latency_concern"}:
        return "retained"
    raise StateConflictError("qualification Planner latency class changed")


def _phase_timing_evidence(
    *,
    phase: QualificationPhase,
    results: Sequence[Mapping[str, Any]],
    provider_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    failures = sum(value.get("status") == "failed" for value in results)
    http_durations = [
        cast(int, value["latency_ms"]) for value in results if type(value.get("latency_ms")) is int
    ]
    provider_durations = [
        cast(int, value["duration_ms"])
        for value in provider_records
        if _provider_operation_has_transport(value) and type(value.get("duration_ms")) is int
    ]
    non_provider_durations = [
        cast(int, value["non_provider_http_duration_ms"])
        for value in results
        if type(value.get("non_provider_http_duration_ms")) is int
    ]
    sillytavern_overhead: dict[str, Any]
    if phase is QualificationPhase.SILLYTAVERN:
        sillytavern_overhead = {
            "availability": "unavailable",
            "total_duration_ms": None,
            "reason": ("end_to_end_timer_does_not_isolate_sillytavern_from_backend_processing"),
            "estimated": False,
        }
    else:
        sillytavern_overhead = {
            "availability": "not_applicable",
            "total_duration_ms": None,
            "reason": "direct_backend_phase",
            "estimated": False,
        }
    return {
        "schema_version": "cera.pi_scene.qualification_phase_timing.v1",
        "phase": phase.value,
        "http_total": {
            "fixtures_total": len(results),
            "fixtures_with_measured_duration": len(http_durations),
            **_latency_summary(http_durations, failures=failures),
        },
        "provider_transport_total": {
            "operations_total": sum(
                _provider_operation_has_transport(value) for value in provider_records
            ),
            **_latency_summary(provider_durations, failures=failures),
        },
        "non_provider_http_total": {
            "fixtures_total": len(results),
            "fixtures_with_measured_duration": len(non_provider_durations),
            **_latency_summary(non_provider_durations, failures=failures),
        },
        "sillytavern_overhead": sillytavern_overhead,
    }


def _latency_summary(values: Sequence[int], *, failures: int) -> dict[str, Any]:
    if any(type(value) is not int or value < 0 for value in values):
        raise StateConflictError("qualification latency sample is invalid")
    if type(failures) is not int or failures < 0:
        raise StateConflictError("qualification latency failure count is invalid")
    if not values:
        return {
            "availability": "unavailable",
            "n": 0,
            "measured_samples": 0,
            "total_duration_ms": None,
            "mean_duration_ms": None,
            "average_duration_ms": None,
            "median_duration_ms": None,
            "p95_duration_ms": None,
            "p95_method": None,
            "minimum_duration_ms": None,
            "maximum_duration_ms": None,
            "failures": failures,
        }
    ordered = sorted(values)
    mean_duration_ms = round(sum(values) / len(values))
    return {
        "availability": "measured",
        "n": len(values),
        "measured_samples": len(values),
        "total_duration_ms": sum(values),
        "mean_duration_ms": mean_duration_ms,
        "average_duration_ms": mean_duration_ms,
        "median_duration_ms": round(median(values)),
        "p95_duration_ms": ordered[ceil(0.95 * len(ordered)) - 1],
        "p95_method": "nearest_rank_observed",
        "minimum_duration_ms": min(values),
        "maximum_duration_ms": max(values),
        "failures": failures,
    }


def _provider_operation_failed(value: Mapping[str, Any]) -> bool:
    return (
        value.get("outcome") == "failure"
        or _sol_provider_operation_outcome(value.get("terminal_state")) == "failure"
    )


def _sol_provider_operation_outcome(terminal_state: object) -> str:
    if terminal_state == "typed_accepted":
        return "success"
    if terminal_state in {
        "prepared_not_invoked",
        "worker_started_not_invoked",
        "worker_preflight_not_invoked",
        "pretransport_failed",
        "provider_failed",
        "provider_completed_post_validation_failed",
    }:
        return "failure"
    return "unavailable"


def _provider_operation_has_transport(value: Mapping[str, Any]) -> bool:
    if value.get("provider") == "deepseek":
        return isinstance(value.get("started_at_utc"), str)
    return value.get("provider") == "codex" and value.get("submitted") is True


def _provider_operation_records(delta: ProviderLedgerDeltaV1) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    sol_by_call: dict[str, list[Mapping[str, Any]]] = {}
    for event in delta.sol_events:
        call_id = event.get("call_id")
        if isinstance(call_id, str):
            sol_by_call.setdefault(call_id, []).append(event)
    for call_id, events in sol_by_call.items():
        identity = events[0]
        owner = identity.get("owner")
        model = identity.get("model")
        stage_by_owner = {
            "planner": "planner",
            "validator": "semantic_validator",
            "reader": "reader",
        }
        if owner not in stage_by_owner:
            raise StateConflictError("qualification Sol ledger owner changed")
        stage = stage_by_owner[cast(str, owner)]
        if (
            not isinstance(model, str)
            or (stage == "semantic_validator" and "luna" not in model.casefold())
            or (stage in {"planner", "reader"} and "sol" not in model.casefold())
        ):
            raise StateConflictError("qualification Sol ledger model family changed")
        provider_family = "luna" if stage == "semantic_validator" else "sol"
        invoked = next(
            (value for value in events if value.get("state") == "transport_invoked"),
            None,
        )
        terminal = events[-1]
        terminal_state = terminal.get("state")
        charged = invoked is not None or terminal_state in {
            "prepared_not_invoked",
            "worker_started_not_invoked",
            "worker_preflight_not_invoked",
        }
        timing_start = identity if invoked is None else invoked
        records.append(
            {
                "provider": "codex",
                "provider_family": provider_family,
                "operation_id": call_id,
                "owner": owner,
                "stage": stage,
                "route": identity.get("route"),
                "model": model,
                "session_identity_sha256": identity.get("stored_thread_sha256"),
                "submitted": invoked is not None,
                "charged": charged,
                "terminal_state": terminal_state,
                "outcome": _sol_provider_operation_outcome(terminal_state),
                # The provider ledger proves the operation and duration but
                # does not bind a stage-occurrence attempt ordinal. Retry
                # status evidence retains the exact attempt counters; do not
                # infer a per-operation ordinal from ledger order.
                "attempt_number": None,
                "attempt_number_availability": "unavailable_not_ledger_bound",
                "started_at_utc": timing_start.get("recorded_at_utc"),
                "prepared_at_utc": identity.get("recorded_at_utc"),
                "completed_at_utc": terminal.get("recorded_at_utc"),
                "duration_ms": _duration_ms(timing_start, terminal),
                "prepared_to_terminal_duration_ms": _duration_ms(identity, terminal),
                "provider_receipt_sha256": terminal.get("provider_receipt_sha256"),
                "failure_receipt_sha256": terminal.get("failure_receipt_sha256"),
                "call_events_sha256": canonical_sha256(events),
            }
        )

    deepseek_prepared: dict[str, Mapping[str, Any]] = {}
    deepseek_started: dict[tuple[str, int], Mapping[str, Any]] = {}
    deepseek_completed: dict[tuple[str, int], Mapping[str, Any]] = {}
    deepseek_terminal: dict[str, Mapping[str, Any]] = {}
    for event in delta.deepseek_events:
        invocation_id = event.get("invocation_id")
        if not isinstance(invocation_id, str):
            continue
        if event.get("event") == "invocation_prepared":
            deepseek_prepared[invocation_id] = event
            continue
        if event.get("event") in {"invocation_completed", "invocation_failed"}:
            deepseek_terminal[invocation_id] = event
        operation_index = event.get("operation_index")
        if type(operation_index) is not int:
            continue
        key = (invocation_id, operation_index)
        if event.get("event") == "provider_operation_started":
            deepseek_started[key] = event
        elif event.get("event") == "provider_operation_completed":
            deepseek_completed[key] = event
    for key, started in deepseek_started.items():
        completed = deepseek_completed.get(key)
        invocation_id = key[0]
        prepared = deepseek_prepared.get(invocation_id, {})
        invocation_terminal = deepseek_terminal.get(invocation_id)
        purpose = prepared.get("purpose")
        stage = _deepseek_stage(purpose)
        timing_terminal = completed if completed is not None else invocation_terminal
        records.append(
            {
                "provider": "deepseek",
                "provider_family": "deepseek",
                "operation_id": f"{key[0]}:{key[1]}",
                "owner": purpose,
                "stage": stage,
                "route": prepared.get("route"),
                "model": "deepseek-v4-flash",
                "session_identity_sha256": text_sha256(key[0]),
                "prepared_at_utc": prepared.get("recorded_at_utc"),
                "started_at_utc": started.get("recorded_at_utc"),
                "completed_at_utc": (
                    None if timing_terminal is None else timing_terminal.get("recorded_at_utc")
                ),
                "duration_ms": _duration_ms(started, timing_terminal),
                "prepared_to_terminal_duration_ms": _duration_ms(
                    prepared,
                    timing_terminal,
                ),
                "input_tokens": None if completed is None else completed.get("input_tokens"),
                "cached_input_tokens": (
                    None if completed is None else completed.get("cached_input_tokens")
                ),
                "output_tokens": None if completed is None else completed.get("output_tokens"),
                "reasoning_tokens": (
                    None if completed is None else completed.get("reasoning_tokens")
                ),
                "finish_status": None if completed is None else completed.get("finish_status"),
                "outcome": (
                    "success"
                    if completed is not None
                    else (
                        "failure"
                        if invocation_terminal is not None
                        and invocation_terminal.get("event") == "invocation_failed"
                        else "unavailable"
                    )
                ),
                "attempt_number": None,
                "attempt_number_availability": "unavailable_not_ledger_bound",
                "terminal_state": (
                    None if invocation_terminal is None else invocation_terminal.get("event")
                ),
            }
        )
    return records


def _deepseek_stage(purpose: object) -> str:
    mapping = {
        "writer": "writer",
        "recorder": "recorder",
        "adult_scene": "adult_scene",
        "adult-scene": "adult_scene",
        "adult_filter": "adult_filter",
        "adult-filter": "adult_filter",
    }
    if not isinstance(purpose, str) or purpose not in mapping:
        raise StateConflictError("qualification DeepSeek operation purpose is unbound")
    return mapping[purpose]


def _load_sol_events(path: Path) -> tuple[Mapping[str, Any], ...]:
    events = _read_jsonl(path)
    for index, value in enumerate(events, start=1):
        if value.get("event_index") != index:
            raise StateConflictError("Sol provider ledger event order changed")
        if not isinstance(value.get("call_id"), str) or not isinstance(value.get("state"), str):
            raise StateConflictError("Sol provider ledger event is invalid")
    return events


def _load_deepseek_events(path: Path) -> tuple[Mapping[str, Any], ...]:
    events = _read_jsonl(path)
    expected_global = 0
    for value in events:
        if not isinstance(value.get("event"), str) or not isinstance(
            value.get("invocation_id"), str
        ):
            raise StateConflictError("DeepSeek provider ledger event is invalid")
        if value.get("event") == "provider_operation_started":
            expected_global += 1
            if value.get("global_operation_index") != expected_global:
                raise StateConflictError("DeepSeek provider ledger operation order changed")
    return events


def _read_jsonl(path: Path) -> tuple[Mapping[str, Any], ...]:
    if not path.is_file():
        return ()
    values: list[Mapping[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise StateConflictError("qualification provider ledger is unreadable") from exc
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StateConflictError("qualification provider ledger is malformed") from exc
        if not isinstance(value, Mapping):
            raise StateConflictError("qualification provider ledger contains a non-object")
        values.append(value)
    return tuple(values)


def _artifact_entries(
    repository_root: Path,
    paths: Sequence[Path],
    *,
    external: bool,
) -> list[dict[str, Any]]:
    discovered: dict[str, Path] = {}
    for raw in paths:
        path = raw.resolve() if external else (repository_root / raw).resolve()
        if not external and not path.is_relative_to(repository_root):
            raise ContractValidationError("qualification artifact escaped repository")
        if path.is_symlink() or not path.exists():
            raise ContractValidationError(f"qualification artifact is missing: {path}")
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_symlink():
                raise ContractValidationError("qualification artifact tree contains a symlink")
            if not candidate.is_file() or "__pycache__" in candidate.parts:
                continue
            key = (
                str(candidate.resolve())
                if external
                else candidate.relative_to(repository_root).as_posix()
            )
            discovered[key] = candidate
    entries: list[dict[str, Any]] = []
    for key, path in sorted(discovered.items(), key=lambda item: item[0].casefold()):
        data = path.read_bytes()
        entries.append(
            {
                "path": key,
                "location": "external" if external else "repository",
                "bytes": len(data),
                "sha256": bytes_sha256(data),
            }
        )
    return entries


def _validate_phase_fixture_counts(
    phase: QualificationPhase,
    fixtures: Sequence[QualificationFixtureV1],
) -> None:
    actual = {
        route.value: sum(value.expected_route is route for value in fixtures)
        for route in QualificationRoute
    }
    if actual != dict(EXPECTED_PHASE_COUNTS[phase.value]):
        raise ContractValidationError(f"{phase.value} qualification fixture count changed")


def _validate_phase_route_lineage(
    fixtures: Sequence[QualificationFixtureV1],
) -> None:
    current = QualificationRoute.ORDINARY
    for fixture in fixtures:
        if fixture.initial_route is not current:
            raise ContractValidationError(
                f"qualification route lineage changed before {fixture.fixture_id}"
            )
        current = fixture.expected_next_route
    if current is not QualificationRoute.ORDINARY:
        raise ContractValidationError("qualification campaign did not return to ordinary")


def _session_id(manifest: Mapping[str, Any], phase: QualificationPhase) -> str:
    prefix = "be" if phase is QualificationPhase.BACKEND else "st"
    return f"q{str(manifest['manifest_sha256'])[:16]}-{prefix}"


def _ordered_phase_fixtures(
    phase: QualificationPhase,
    fixtures: Sequence[QualificationFixtureV1],
) -> tuple[QualificationFixtureV1, ...]:
    available = {value.fixture_id: value for value in fixtures if value.phase is phase}
    if phase is QualificationPhase.BACKEND:
        order = (
            *(f"backend-ordinary-{index:02d}" for index in range(1, 6)),
            *(f"backend-adult-{index:02d}" for index in range(1, 6)),
            *(f"backend-ordinary-{index:02d}" for index in range(6, 11)),
            *(f"backend-adult-{index:02d}" for index in range(6, 11)),
        )
    else:
        order = (
            *(f"sillytavern-ordinary-{index:02d}" for index in range(1, 4)),
            *(f"sillytavern-adult-{index:02d}" for index in range(1, 4)),
            *(f"sillytavern-ordinary-{index:02d}" for index in range(4, 6)),
            *(f"sillytavern-adult-{index:02d}" for index in range(4, 6)),
        )
    if set(order) != set(available):
        raise ContractValidationError("qualification campaign turn identities changed")
    return tuple(available[value] for value in order)


def _duration_ms(
    started: Mapping[str, Any],
    completed: Mapping[str, Any] | None,
) -> int | None:
    if completed is None:
        return None
    try:
        start = datetime.fromisoformat(str(started["recorded_at_utc"]))
        end = datetime.fromisoformat(str(completed["recorded_at_utc"]))
    except (KeyError, TypeError, ValueError):
        return None
    return max(0, int((end - start).total_seconds() * 1000))


def _closed_failure_projection(exc: BaseException) -> dict[str, str]:
    """Return an identity-free failure receipt safe for durable evidence."""

    if isinstance(exc, ManualActionRequiredError):
        failure_type = "ManualActionRequiredError"
        failure_category = "manual_action_required"
    elif isinstance(exc, _CriticalProviderStageError):
        failure_type = "CriticalProviderStageError"
        failure_category = f"provider_stage_{exc.terminal_state}"
    elif isinstance(exc, ContractValidationError):
        failure_type = "ContractValidationError"
        failure_category = "contract_validation"
    elif isinstance(exc, StateConflictError):
        failure_type = "StateConflictError"
        failure_category = "state_conflict"
    elif isinstance(exc, TimeoutError):
        failure_type = "TimeoutError"
        failure_category = "transport_timeout"
    elif isinstance(exc, OSError):
        failure_type = "OSError"
        failure_category = "transport_io"
    elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
        failure_type = "Interrupted"
        failure_category = "interrupted"
    else:
        failure_type = "UnexpectedError"
        failure_category = "unexpected"
    identity = {
        "failure_type": failure_type,
        "failure_category": failure_category,
    }
    projection = {**identity, "failure_sha256": canonical_sha256(identity)}
    if isinstance(exc, ManualActionRequiredError):
        projection["manual_action_checkpoint_sha256"] = exc.checkpoint_sha256
    return projection


def _nonnegative_int(value: object, *, default: int) -> int:
    return value if type(value) is int and value >= 0 else default


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(canonical_bytes(payload) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


__all__ = [
    "ClientResponseV1",
    "DEEPSEEK_HTTP_OPERATION_CEILING",
    "DEEPSEEK_PER_INVOCATION_CEILING",
    "FullModelQualificationRunner",
    "ManualActionRequiredError",
    "QualificationClient",
    "QualificationFixtureV1",
    "QualificationFixtureV2",
    "QualificationFixtureV3",
    "QualificationFixtureV4",
    "QualificationFixtureV5",
    "QualificationFixtureV6",
    "QualificationFixtureV7",
    "QualificationFixtureV8",
    "QualificationFixtureV9",
    "QualificationFixtureV10",
    "QualificationFixtureV11",
    "QualificationFixtureV12",
    "QualificationFixtureV13",
    "QualificationFixtureV14",
    "QualificationFixtureV15",
    "QualificationFixtureV16",
    "QualificationFixtureV17",
    "QualificationFixtureV18",
    "QualificationFixtureV19",
    "QualificationFixtureV20",
    "QualificationFixtureV21",
    "QualificationFixtureV22",
    "QualificationFixtureV23",
    "QualificationFixtureV24",
    "QualificationFixtureV25",
    "QualificationFixtureV26",
    "QualificationFixtureV27",
    "QualificationFixtureV28",
    "QualificationFixtureV29",
    "QualificationFixtureV30",
    "QualificationFixtureV31",
    "QualificationFixtureV32",
    "QualificationFixtureV33",
    "QualificationFixtureV34",
    "QualificationFixtureV35",
    "QualificationFixtureV36",
    "QualificationFixtureV37",
    "QualificationFixtureV38",
    "QualificationFixtureV39",
    "QualificationManualActionAuthorizationV1",
    "QualificationManualActionAuthorizer",
    "QualificationManualActionRequestV1",
    "QualificationPhase",
    "QualificationRoute",
    "QUALIFICATION_EXECUTION_POLICY",
    "RETAINED_PLANNER_LATENCY_CONCERN_MS",
    "SOL_FAMILY_CEILING",
    "TERRA_CEILING",
    "build_qualification_manifest",
    "load_qualification_fixtures",
    "load_qualification_manifest",
    "qualification_request_payload",
    "qualification_fixture_manifest_metadata",
    "validate_qualification_manifest",
    "verify_qualification_artifacts",
    "write_qualification_manifest",
]
