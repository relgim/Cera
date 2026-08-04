"""Run the exact D-200 ten-stage continuous integration canary.

This harness is intentionally one-shot and terminal. It never retries or
substitutes a provider, never touches the active SillyTavern route, and writes
only to the ignored disposable continuous-world root and the exact review-cycle
Job 4 source artifacts.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
from dataclasses import replace
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from typing import Any, Callable
import unittest

from cera.continuous.codex_stored import CodexContinuousStoredSessionPort
from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.diagnostics import ContinuousRootDiagnosticRecorder
from cera.continuous.evidence import (
    RequestEvidenceBindingRegistry,
    StableAcceptedContextReferenceStore,
    build_character_summary_envelope,
)
from cera.continuous.ingress import (
    ContinuousIngressAuthorityStore,
    PreparedContinuousIngressBridge,
    build_default_prepared_classifier_registry,
)
from cera.continuous.job4_terminal import (
    ContinuousJob4CapabilityContainerV1,
    ContinuousJob4PostconditionsV1,
    ContinuousJob4TerminalEvidence,
    ContinuousJob4TerminalEvidenceV1,
    ContinuousJob4TerminalEvidenceV2,
    ContinuousJob4TerminalEvidenceV3,
    ContinuousJob4TerminalEvidenceV4,
    ContinuousJob4TerminalEvidenceV5,
    ContinuousJob4TerminalEvidenceV6,
    decode_continuous_job4_terminal_evidence,
    rebuild_failed_continuous_job4_terminal_evidence,
)
from cera.continuous.thread_lineage import (
    ContinuousThreadLineageLedger,
    ContinuousThreadLineageReceiptV1,
)
from cera.continuous.job4_transaction import (
    ContinuousJob4TerminalTransactionV1,
    ContinuousJob4TransactionError,
)
from cera.continuous.shadow_ingress import (
    ContinuousSillyTavernShadowRequestBridge,
)
from cera.continuous.record_policy import PERSISTENCE_POLICY_SHA256
from cera.continuous.contracts import (
    AcceptedTurnPairV1,
    CharacterSummaryEnvelopeV1,
    FrozenContinuousIngressFixtureV1,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    ReaderVerdictStatus,
    ReaderVerdictV1,
    RichPlannerSequenceV1,
)
from cera.continuous.prompting import (
    CONTINUOUS_PLANNER_PROMPT_VERSION,
    CONTINUOUS_VALIDATOR_PROMPT_VERSION,
    PLANNER_STABLE_INSTRUCTIONS,
    VALIDATOR_STABLE_INSTRUCTIONS,
)
from cera.continuous.packets import (
    build_accepted_lean_continuation_authority,
    build_continuous_planner_turn_packet,
)
from cera.continuous.provider import (
    CodexContinuousPlannerPort,
    CodexContinuousReaderPort,
    CodexContinuousValidatorPort,
    DeepSeekContinuousComposerPort,
    ContinuousSemanticValidatorDraftV8,
    continuous_deepseek_route,
    continuous_planner_route,
    continuous_validator_route,
)
from cera.continuous.sessions import (
    ContinuousReconstructionAcceptedTurnV1,
    ContinuousSessionCompatibilityV1,
    ContinuousSessionCoordinator,
    ContinuousSessionInitializationKind,
    ContinuousSessionReconstructionBundleV1,
    ContinuousSessionRole,
    ContinuousThreadArchiveEvidenceV1,
    InMemoryContinuousStoredSessionPort,
    assert_separate_role_sessions,
    continuous_branch_privacy_boundary_sha256,
    unavailable_thread_archive_evidence,
)
from cera.errors import StateConflictError
from cera.continuous.scripted_job4 import (
    SCRIPTED_JOB4_FIXTURE_ID,
    SCRIPTED_JOB4_FIXTURE_SHA256,
    ScriptedJob4FixtureRuntime,
)
from cera.continuous.runtime import (
    ContinuousShadowTurnCoordinator,
    ContinuousTurnRequestV1,
)
from cera.continuous.world import (
    ContinuousWorldStore,
)
from cera.continuous.world_mcp import (
    ContinuousWorldMcpBridge,
    ContinuousWorldToolDispatcher,
)
from cera.creator_review.models import CreatorReviewAction
from cera.active_runtime_validation import active_runtime_status
from cera.evaluation import RealGenesisSandbox
from cera.evidence import EvidenceWorldMode
from cera.ids import IdKind, TypedId
from cera.ingress import RawTurnEnvelope, RawTurnIngressFacade
from cera.kernel import PreflightAuthority, RequestedContentClass, TurnKernel
from cera.providers import CodexSDKTransport, DeepSeekChatTransport, StoredCodexThreadRunner
from cera.providers.codex_worker import _BASE_INSTRUCTIONS_BY_ROLE
from cera.reasoner_session import OpenAICodexStoredThreadBackend
from cera.reasoner import SeedDossierAssembler
from cera.sillytavern.models import (
    CERA_VIRTUAL_MODEL,
    ChatMessage,
    SillyTavernChatRequest,
)
from cera.sillytavern.continuous_test import (
    AcceptedContinuousTestTurn,
    PreparedContinuousTestTurn,
)
from cera.serialization import (
    bytes_sha256,
    canonical_bytes,
    canonical_json,
    canonical_sha256,
    text_sha256,
    to_primitive,
)


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_FAILED_IDENTITIES = {
    (
        "2026-08-01-continuous-planner-validator-v1-cycle-001",
        "continuous-planner-validator-three-turn-scene-change-canary-v1",
    ),
}
WORLD_ID = "hanezawa-job4"
BRANCH_ID = "canary-main"
TURN_MESSAGES = (
    "Hello, my name is Ted. Is this the Hanezawa residence?",
    "I'm the tenant who was supposed to arrive today.",
    (
        'Several days later, Ted is in the kitchen with Mia and asks, '
        '"Is Sakura always that cautious with visitors?"'
    ),
)


def _accepted_story_marker(turn_number: int) -> str:
    return {
        1: "Sakura requests bounded proof.",
        2: "Sakura keeps the threshold controlled.",
        3: "Mia answers cautiously in the later scene.",
    }[turn_number]


def canary_source_units(turn_number: int) -> tuple[IngressSourceUnitV1, ...]:
    """Frozen ingress classifications for this exact disposable canary."""

    text = TURN_MESSAGES[turn_number - 1]
    if turn_number in {1, 2}:
        return (
            IngressSourceUnitV1(
                schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
                source_unit_key=f"source_unit_turn_{turn_number}_dialogue",
                kind=IngressSourceUnitKind.DIALOGUE,
                source_start=0,
                source_end=len(text),
                exact_text=text,
                actor_id=None,
                speaker_id="character:ted",
                classification_basis="explicit_ingress_speaker",
            ),
        )
    quote_start = text.index('"') + 1
    quote_end = text.rindex('"')
    action_end = quote_start - 1
    return (
        IngressSourceUnitV1(
            schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
            source_unit_key="source_unit_turn_3_action",
            kind=IngressSourceUnitKind.ACTION,
            source_start=0,
            source_end=action_end,
            exact_text=text[:action_end],
            actor_id="character:ted",
            speaker_id=None,
            classification_basis="explicit_ingress_actor",
        ),
        IngressSourceUnitV1(
            schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
            source_unit_key="source_unit_turn_3_open_quote",
            kind=IngressSourceUnitKind.NARRATION,
            source_start=action_end,
            source_end=quote_start,
            exact_text=text[action_end:quote_start],
            actor_id=None,
            speaker_id=None,
            classification_basis="explicit_ingress_narration",
        ),
        IngressSourceUnitV1(
            schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
            source_unit_key="source_unit_turn_3_dialogue",
            kind=IngressSourceUnitKind.DIALOGUE,
            source_start=quote_start,
            source_end=quote_end,
            exact_text=text[quote_start:quote_end],
            actor_id=None,
            speaker_id="character:ted",
            classification_basis="explicit_ingress_speaker",
        ),
        IngressSourceUnitV1(
            schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
            source_unit_key="source_unit_turn_3_close_quote",
            kind=IngressSourceUnitKind.NARRATION,
            source_start=quote_end,
            source_end=len(text),
            exact_text=text[quote_end:],
            actor_id=None,
            speaker_id=None,
            classification_basis="explicit_ingress_narration",
        ),
    )


def canary_ingress_fixtures() -> tuple[FrozenContinuousIngressFixtureV1, ...]:
    """Closed repository-owned registry for the three exact canary turns."""

    return tuple(
        FrozenContinuousIngressFixtureV1(
            schema_version=FrozenContinuousIngressFixtureV1.SCHEMA_VERSION,
            fixture_id=f"cera.fixture.continuous_job4.turn_{turn_number}",
            fixture_schema_id="cera.fixture_registry.continuous_job4.v1",
            world_id=WORLD_ID,
            branch_id=BRANCH_ID,
            session_id="session:continuous_job4",
            request_id=f"request:turn-{turn_number:03d}",
            turn_id=f"turn-{turn_number:03d}",
            idempotency_key_sha256=text_sha256(
                f"continuous-job4-turn-{turn_number:03d}"
            ),
            raw_source=TURN_MESSAGES[turn_number - 1],
            protected_user_id="character:ted",
            source_units=canary_source_units(turn_number),
        )
        for turn_number in (1, 2, 3)
    )


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def validate_job4_identity(
    manifest: dict[str, Any],
    *,
    expected_cycle_id: str,
    expected_task_id: str,
    expected_authorization_sha256: str,
    maximum_provider_calls: int,
) -> None:
    """Fail closed before setup when a canary identity is stale or reusable."""

    if (
        manifest.get("cycle_id") != expected_cycle_id
        or manifest.get("job4", {}).get("task_id") != expected_task_id
        or manifest.get("job4", {}).get("authorization_record_sha256")
        != expected_authorization_sha256
    ):
        raise ValueError("Job 4 cycle identity changed")
    if (expected_cycle_id, expected_task_id) in HISTORICAL_FAILED_IDENTITIES:
        raise ValueError("historical failed Job 4 identity is immutable and cannot be rerun")
    if maximum_provider_calls != 10:
        raise ValueError("Job 4 provider-call ceiling changed")


def validate_declared_unittest_ids(test_ids: tuple[str, ...]) -> dict[str, int]:
    """Resolve every audit test identity before a checkpoint is published."""

    if not test_ids or len(test_ids) != len(set(test_ids)):
        raise ValueError("Job 4 unittest identities are empty or duplicated")
    loader = unittest.TestLoader()
    resolved: dict[str, int] = {}
    for test_id in test_ids:
        suite = loader.loadTestsFromName(test_id)
        flattened: list[unittest.TestCase] = []

        def visit(value) -> None:
            for child in value:
                if isinstance(child, unittest.TestSuite):
                    visit(child)
                else:
                    flattened.append(child)

        visit(suite)
        if (
            len(flattened) != 1
            or flattened[0].__class__.__name__ == "_FailedTest"
            or flattened[0].id() != test_id
        ):
            raise ValueError(f"Job 4 unittest identity did not resolve: {test_id}")
        resolved[test_id] = 1
    return resolved


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(canonical_bytes(value) + b"\n")
    temporary.replace(path)


def git_head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class StablePrefixTransport:
    """Prove stable role instructions stay in the stored thread, not turns."""

    def __init__(self, transport: CodexSDKTransport, stable: str) -> None:
        self.transport = transport
        self.route = transport.route
        self.stable = stable

    def invoke(self, prompt: str, **kwargs):
        prefix = self.stable + "\n\n"
        if prompt.startswith(prefix):
            return self.transport.invoke(prompt[len(prefix) :], **kwargs)
        if self.stable in prompt:
            raise RuntimeError(
                "continuous stable instructions were duplicated in a turn prompt"
            )
        return self.transport.invoke(prompt, **kwargs)


def provider_debug(result: Any) -> dict[str, Any]:
    return {
        "provider_receipt": to_primitive(result.provider_receipt),
        "operation_telemetry": to_primitive(result.operation_telemetry),
        "tool_call_count": result.tool_call_count,
        "failed_tool_call_count": result.failed_tool_call_count,
        "world_tool_debug": result.world_tool_debug,
    }


def snapshot_files(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): text_sha256(path.read_text(encoding="utf-8"))
        for path in sorted(value for value in root.rglob("*") if value.is_file())
    }


def exact_diff(before: dict[str, str], after: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {"path": path, "before_sha256": before.get(path), "after_sha256": after.get(path)}
        for path in sorted(set(before).union(after))
        if before.get(path) != after.get(path)
    ]


def source_character_summary(
    root: Path,
    character: str,
    *,
    world: ContinuousWorldStore,
    world_file_revision: int,
    latest_changes: tuple[str, ...] = (),
    world_id: str = WORLD_ID,
    branch_id: str = BRANCH_ID,
) -> CharacterSummaryEnvelopeV1:
    del root, world_file_revision, latest_changes
    return build_character_summary_envelope(
        branch_root=world.branch_root(world_id, branch_id),
        source_path=f"ACTIVE/Characters/{character.capitalize()}.json",
        character_id=f"character:{character}_hanezawa",
    )


def seed_world(
    store: ContinuousWorldStore,
    root: Path,
    *,
    world_id: str = WORLD_ID,
    branch_id: str = BRANCH_ID,
) -> None:
    package = root / "genesis" / "packages" / "hanezawa_core_v1_2" / "modules"
    for name in ("hana", "sakura", "mia", "enne", "tomi", "aoi", "yuuni"):
        source = package / "characters" / f"{name}.json"
        module = json.loads(source.read_text(encoding="utf-8"))
        desired = {
            "Core premise",
            "Speech system",
            "Initial stance toward Ted",
            "Fidelity invariants",
        }
        sections = []
        for record in module["records"]:
            payload = json.loads(record["payload_json"])
            if payload.get("heading") in desired and isinstance(payload.get("source_text"), str):
                sections.append(payload["source_text"])
        if len(sections) != len(desired):
            raise RuntimeError(f"{name} concise summary source sections are incomplete")
        store.seed_active_json(
            world_id,
            branch_id,
            f"Characters/{name.capitalize()}.json",
            {
                "schema_version": "cera.continuous_genesis_character.v1",
                "character_id": f"character:{name}_hanezawa",
                "source_path": source.relative_to(root).as_posix(),
                "source_sha256": bytes_sha256(source.read_bytes()),
                "genesis_records": json.loads(source.read_text(encoding="utf-8"))["records"],
                "reasoning_summary": "\n\n".join(sections),
                "latest_accepted_changes": [],
                "accepted_state": {
                    "knowledge": {},
                    "relationships": {},
                    "development": {},
                    "material": {},
                },
            },
        )
    for relative, target in (
        ("relationships_family.json", "Relationships/Family.json"),
        ("household.json", "Rules/Household.json"),
        ("ted_boundary.json", "Rules/TedBoundary.json"),
    ):
        source = package / relative
        store.seed_active_json(
            world_id,
            branch_id,
            target,
            {
                "schema_version": "cera.continuous_genesis_module.v1",
                "source_path": source.relative_to(root).as_posix(),
                "source_sha256": bytes_sha256(source.read_bytes()),
                "genesis_records": json.loads(source.read_text(encoding="utf-8"))["records"],
            },
        )


def compatibility(
    world: ContinuousWorldStore,
    role: ContinuousSessionRole,
    *,
    world_id: str = WORLD_ID,
    branch_id: str = BRANCH_ID,
) -> ContinuousSessionCompatibilityV1:
    return ContinuousSessionCompatibilityV1(
        schema_version=ContinuousSessionCompatibilityV1.SCHEMA_VERSION,
        world_id=world_id,
        branch_id=branch_id,
        role=role,
        provider="openai_codex",
        model="gpt-5.6-sol" if role is ContinuousSessionRole.PLANNER else "gpt-5.6-terra",
        reasoning_effort="medium" if role is ContinuousSessionRole.PLANNER else "high",
        prompt_version=(
            CONTINUOUS_PLANNER_PROMPT_VERSION
            if role is ContinuousSessionRole.PLANNER
            else CONTINUOUS_VALIDATOR_PROMPT_VERSION
        ),
        output_schema_version=(
            RichPlannerSequenceV1.SCHEMA_VERSION
            if role is ContinuousSessionRole.PLANNER
            else ContinuousSemanticValidatorDraftV8.SCHEMA_VERSION
        ),
        world_directory_identity_sha256=world.world_identity_sha256(
            world_id, branch_id
        ),
        authority_policy_version="cera.owner_architecture.v2+d204",
        privacy_policy_version="cera.privacy.v1",
        protected_user_policy_version="cera.continuous_protected_user_policy.v8",
        session_policy_version="cera.continuous_session_policy.v9_d200",
        ingress_classifier_registry_sha256=(
            build_default_prepared_classifier_registry().registry_sha256
        ),
        persistence_policy_sha256=PERSISTENCE_POLICY_SHA256,
    )


def read_world_revision(
    world: ContinuousWorldStore,
    character: str,
    *,
    world_id: str = WORLD_ID,
    branch_id: str = BRANCH_ID,
) -> int:
    path = (
        world.branch_root(world_id, branch_id)
        / "ACTIVE"
        / "Characters"
        / f"{character}.json"
    )
    return int(json.loads(path.read_text(encoding="utf-8"))["_cera_revision"])


class ProCorrectionStop(RuntimeError):
    pass


class _ScriptedExecutionComplete(RuntimeError):
    """Internal non-error jump from the isolated scripted branch to cleanup."""


_PROVIDER_FREE_TEST_FAILPOINTS = frozenset(
    {
        "source_hash",
        "disposable_copy",
        "sqlite_open",
        "sqlite_integrity",
        "sqlite_foreign_keys",
        "call_ledger_construction",
        "world_construction",
        "world_seeding",
        "lifecycle_directory",
        "active_profile",
        "sdk_import_pre_submission",
        "account_inspection_pre_submission",
        "backend_construction_pre_submission",
        "session_construction_pre_submission",
        "stored_thread_construction_pre_submission",
        "after_primary_planner_creation",
        "archive_request",
        "archive_non_resumability",
        "archive_active_selection",
        "after_physical_fork_creation",
        "after_summary_delivery_reconstruction",
        "after_child_branch_validation_before_descriptor_append",
        "after_child_descriptor_append",
        "after_child_accepted_reference_save:1",
        "after_child_accepted_reference_save:2",
        "before_child_snapshot_persistence",
        "after_child_snapshot_persistence",
        "after_physical_reconstruction_creation",
        "after_reconstruction_summary_delivery",
        "after_reconstruction_accepted_reference_save:1",
        "after_reconstruction_accepted_reference_save:2",
        "after_reconstruction_adoption_before_return",
        "after_child_adoption_before_return",
        "accepted_session_synchronization",
        "accepted_final_sequence_injection",
        "detail_serialization",
        "terminal_evidence_serialization",
        "report_construction",
        "canonical_projection",
        "result_serialization",
        "report_write",
        "result_write",
        "terminal_artifact_write",
        "publication_commit_marker",
    }
)


class _ProviderFreeTestFaultInjector:
    """One-shot CLI cut points for provider-free lifecycle tests only."""

    def __init__(self, selected: str | None) -> None:
        if selected is not None and selected not in _PROVIDER_FREE_TEST_FAILPOINTS:
            raise ValueError("provider-free Job 4 failpoint is unsupported")
        self.selected = selected
        self.consumed = False

    def hit(self, name: str) -> None:
        if self.selected == name and not self.consumed:
            self.consumed = True
            raise RuntimeError(f"provider-free Job 4 injected failure: {name}")


class _FaultInjectedContinuousSessionPort(InMemoryContinuousStoredSessionPort):
    """Narrow scripted seam for archive and injection failure qualification."""

    def __init__(self, injector: _ProviderFreeTestFaultInjector) -> None:
        super().__init__()
        self.injector = injector

    @staticmethod
    def _planner(handle: Any) -> bool:
        return "-planner-" in handle.provider_thread_id

    def archive(self, handle: Any, reason: str) -> None:
        if self._planner(handle):
            self.injector.hit("archive_request")
        super().archive(handle, reason)

    def resume(self, handle: Any) -> bool:
        if (
            self._planner(handle)
            and self.injector.selected == "archive_non_resumability"
            and not self.injector.consumed
            and handle.provider_thread_id not in self._valid
        ):
            self.injector.consumed = True
            return True
        return super().resume(handle)

    def selectable_as_active_or_accepted_ancestry(self, handle: Any) -> bool:
        if (
            self._planner(handle)
            and self.injector.selected == "archive_active_selection"
            and not self.injector.consumed
        ):
            self.injector.consumed = True
            return True
        return super().selectable_as_active_or_accepted_ancestry(handle)

    def append_context(self, handle: Any, text: str) -> None:
        if self._planner(handle):
            self.injector.hit("accepted_final_sequence_injection")
        super().append_context(handle, text)


class _HarnessPlannerPort:
    def __init__(self, harness: "JobHarness") -> None:
        self.harness = harness

    def plan(self, prompt: str):
        number = self.harness._active_turn_number
        turn_id = self.harness._active_turn_id
        return self.harness.provider_call(
            f"turn-{number}-planner",
            "planner",
            lambda: self.harness.codex_planner(prompt, turn_id),
        )


class _HarnessComposerPort:
    def __init__(self, harness: "JobHarness") -> None:
        self.harness = harness

    def compose(self, prompt: str):
        number = self.harness._active_turn_number
        return self.harness.provider_call(
            f"turn-{number}-deepseek",
            "composer",
            lambda: self.harness.deepseek(prompt),
        )


class _HarnessValidatorPort:
    def __init__(self, harness: "JobHarness") -> None:
        self.harness = harness

    def validate(self, prompt: str, **kwargs):
        number = self.harness._active_turn_number
        turn_id = self.harness._active_turn_id
        return self.harness.provider_call(
            self.harness._active_validator_label or f"turn-{number}-validator",
            "validator",
            lambda: self.harness.codex_validator(
                prompt,
                turn_id,
                writer_story_text=kwargs.get("writer_story_text"),
                accepted_pairs=tuple(kwargs.get("accepted_pairs", ())),
            ),
        )


class _HarnessReaderPort:
    """Provider-free Reader used only by the historical scripted harness."""

    def __init__(self, harness: "JobHarness") -> None:
        self.harness = harness
        self._counter = 0

    def review(self, prompt: str, *, writer_story_text: str):
        self._counter += 1
        request = json.loads(prompt.rsplit("[READER REQUEST]\n", 1)[1])
        verdict = ReaderVerdictV1(
            schema_version=ReaderVerdictV1.SCHEMA_VERSION,
            verdict_id=f"reader:job4_{self._counter}",
            world_id=request["world_id"],
            branch_id=request["branch_id"],
            turn_id=request["turn_id"],
            candidate_id=request["candidate_id"],
            story_text_sha256=text_sha256(writer_story_text),
            verdict=ReaderVerdictStatus.ACCEPTED,
            reason_codes=(),
            issues=(),
            scene_completeness_score=90,
            character_voice_score=90,
            dialogue_pacing_score=90,
            readability_score=90,
        )
        return type(
            "ProviderFreeReaderResult",
            (),
            {
                "value": verdict,
                "provider_receipt": None,
                "operation_telemetry": None,
                "tool_call_count": 0,
                "failed_tool_call_count": 0,
                "world_tool_debug": None,
                "physical_session_sha256": text_sha256(
                    f"reader:{self._counter}:{request['candidate_id']}"
                ),
            },
        )()


class _HarnessLiveReaderPort:
    """Opt-in fresh Reader used by Runtime Model V3 live qualification."""

    def __init__(self, harness: "JobHarness") -> None:
        self.harness = harness

    def review(self, prompt: str, *, writer_story_text: str):
        number = self.harness._active_turn_number
        return self.harness.provider_call(
            f"turn-{number}-reader",
            "reader",
            lambda: self.harness.codex_reader(
                prompt,
                writer_story_text=writer_story_text,
            ),
        )


_JOB4_COMPOSER_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})


def _validate_composer_route_identity(
    receipt: Any,
    *,
    expected_model: str,
    expected_external_calls: int,
) -> None:
    if expected_model not in _JOB4_COMPOSER_MODELS:
        raise ValueError("Job 4 Composer model is not in the closed qualification set")
    if (
        receipt.requested_model != expected_model
        or receipt.external_provider_calls != expected_external_calls
    ):
        raise RuntimeError("DeepSeek canary route identity changed")


class JobHarness:
    def __init__(
        self,
        *,
        source_root: Path,
        cycle: Path,
        world: ContinuousWorldStore,
        planner_session: ContinuousSessionCoordinator,
        validator_session: ContinuousSessionCoordinator,
        planner_handle: str,
        validator_handle: str,
        lifecycle_root: Path,
        call_ledger: ContinuousProviderCallLedger,
        root_diagnostic: ContinuousRootDiagnosticRecorder | None = None,
        planner_transport_factory: Callable[[Path, str], Any] | None = None,
        validator_transport_factory: Callable[[Path, str], Any] | None = None,
        composer_transport_factory: Callable[[], Any] | None = None,
        reader_transport_factory: Callable[[Path], Any] | None = None,
        composer_model: str = "deepseek-v4-flash",
        validator_model: str = "gpt-5.6-terra",
        validator_effort: str = "high",
        reader_model: str = "gpt-5.6-sol",
        reader_effort: str = "medium",
        scripted_provider_free: bool = False,
        thread_lifecycle_failpoint: Callable[[str], None] | None = None,
        world_id: str = WORLD_ID,
        branch_id: str = BRANCH_ID,
        ingress_authority: ContinuousIngressAuthorityStore | None = None,
    ) -> None:
        self.source_root = source_root
        self.cycle = cycle
        self.world = world
        self.planner_session = planner_session
        self.validator_session = validator_session
        self.planner_handle = planner_handle
        self.validator_handle = validator_handle
        self.lifecycle_root = lifecycle_root
        self.call_ledger = call_ledger
        self.call_index_offset = call_ledger.dispatched_call_count
        self.root_diagnostic = root_diagnostic
        self.planner_transport_factory = planner_transport_factory
        self.validator_transport_factory = validator_transport_factory
        self.composer_transport_factory = composer_transport_factory
        self.reader_transport_factory = reader_transport_factory
        if composer_model not in _JOB4_COMPOSER_MODELS:
            raise ValueError("Job 4 Composer model is not in the closed qualification set")
        self.composer_model = composer_model
        self.validator_model = validator_model
        self.validator_effort = validator_effort
        self.reader_model = reader_model
        self.reader_effort = reader_effort
        self.scripted_provider_free = scripted_provider_free
        self.world_id = world_id
        self.branch_id = branch_id
        self.accepted_pairs: list[AcceptedTurnPairV1] = []
        self.call_records: list[dict[str, Any]] = []
        self.poll_records: list[dict[str, Any]] = []
        self.provider_calls = 0
        self.scripted_transport_invocations = 0
        self.scene_change_envelope = None
        self._active_turn_number = 0
        self._active_turn_id = ""
        self._active_validator_label: str | None = None
        self._http_pending: dict[
            int, tuple[Any, PreparedContinuousTestTurn]
        ] = {}
        self.http_turn_results: list[dict[str, Any]] = []
        self.ingress_authority = ingress_authority or ContinuousIngressAuthorityStore(
            lifecycle_root / "ingress_authority",
            fixture_registry=canary_ingress_fixtures(),
        )
        self.coordinator = ContinuousShadowTurnCoordinator(
            world=world,
            planner_session=planner_session,
            validator_session=validator_session,
            planner=_HarnessPlannerPort(self),
            composer=_HarnessComposerPort(self),
            validator=_HarnessValidatorPort(self),
            reader=(
                _HarnessLiveReaderPort(self)
                if reader_transport_factory is not None
                else _HarnessReaderPort(self)
            ),
            ingress_authority=self.ingress_authority,
            thread_lifecycle_failpoint=thread_lifecycle_failpoint,
        )

    def ingress_reference(self, turn_number: int) -> dict[str, str]:
        turn_id = f"turn-{turn_number:03d}"
        idempotency_key = f"continuous-job4-{turn_id}"
        receipt = self.ingress_authority.issue_frozen_fixture(
            fixture_id=f"cera.fixture.continuous_job4.turn_{turn_number}",
            idempotency_key=idempotency_key,
        )
        return {
            "session_id": receipt.session_id,
            "request_id": receipt.request_id,
            "idempotency_key_sha256": text_sha256(idempotency_key),
            "ingress_receipt_id": receipt.receipt_id,
            "ingress_receipt_sha256": receipt.receipt_sha256,
        }

    def poll_pro(self, boundary: str) -> None:
        response = self.cycle / "inbox" / "PRO_RESPONSE.md"
        record: dict[str, Any] = {"boundary": boundary, "observed_at": utc_now()}
        if not response.is_file():
            record["state"] = "absent"
            self.poll_records.append(record)
            return
        try:
            from tools.pro_review_cycle_core import parse_response, response_mismatches, sha256_bytes

            data = response.read_bytes()
            template_hash = sha256_bytes(
                (self.cycle / "outbox" / "PRO_RESPONSE_TEMPLATE.md").read_bytes()
            )
            manifest = json.loads((self.cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8"))
            parsed = parse_response(
                data,
                template_hash,
                require_planning_sections=manifest["cycle_sequence"] >= 7,
            )
            mismatches = response_mismatches(manifest, parsed)
            if mismatches:
                record.update({"state": "identity_mismatch", "mismatches": mismatches})
            else:
                record.update(
                    {
                        "state": "valid",
                        "response_sha256": sha256_bytes(data),
                        "disposition": parsed["review_disposition"],
                    }
                )
                self.poll_records.append(record)
                if parsed["review_disposition"] in {"corrections_required", "blocked"}:
                    raise ProCorrectionStop(
                        f"Pro requested {parsed['review_disposition']} at {boundary}"
                    )
                return
        except ProCorrectionStop:
            raise
        except Exception as exc:
            record.update({"state": "not_yet_valid", "error_type": type(exc).__name__})
        self.poll_records.append(record)

    def provider_call(self, label: str, owner: str, operation: Callable[[], Any]) -> Any:
        self.poll_pro(f"before:{label}")
        print(f"JOB4 call {len(self.call_records) + 1}/10 start: {label}", flush=True)
        started = time.perf_counter_ns()
        record: dict[str, Any] = {
            "index": len(self.call_records) + 1,
            "label": label,
            "owner": owner,
            "started_at": utc_now(),
            "status": "started",
        }
        try:
            result = operation()
            dispatched = self.call_ledger.dispatched_call_count
            if self.scripted_provider_free:
                self.scripted_transport_invocations = dispatched
                self.provider_calls = 0
            else:
                self.provider_calls = dispatched
            telemetry = getattr(result, "operation_telemetry", None)
            expected_thread_hash = {
                "planner": text_sha256(self.planner_handle),
                "validator": text_sha256(self.validator_handle),
            }.get(owner)
            if expected_thread_hash is not None:
                if telemetry is None:
                    raise RuntimeError("Codex call omitted required operation telemetry")
                if telemetry.provider_thread_id_sha256 != expected_thread_hash:
                    raise RuntimeError("Codex provider thread continuity hash changed")
                expected_model, expected_effort = {
                    "planner": ("gpt-5.6-sol", "medium"),
                    "validator": (self.validator_model, self.validator_effort),
                }[owner]
                if (
                    telemetry.model != expected_model
                    or telemetry.reasoning_effort != expected_effort
                    or telemetry.fast_mode_enabled
                ):
                    raise RuntimeError("Codex canary route identity changed")
                record["expected_provider_thread_sha256"] = expected_thread_hash
            elif owner == "reader":
                if telemetry is None:
                    raise RuntimeError("Reader call omitted required operation telemetry")
                if (
                    telemetry.model != self.reader_model
                    or telemetry.reasoning_effort != self.reader_effort
                    or telemetry.fast_mode_enabled
                ):
                    raise RuntimeError("Codex Reader route identity changed")
                if result.physical_session_sha256 != telemetry.provider_thread_id_sha256:
                    raise RuntimeError("Codex Reader physical-session evidence changed")
                record["expected_provider_thread_sha256"] = (
                    telemetry.provider_thread_id_sha256
                )
            elif owner == "composer":
                receipt = result.provider_receipt
                expected_external_calls = 0 if self.scripted_provider_free else 1
                _validate_composer_route_identity(
                    receipt,
                    expected_model=self.composer_model,
                    expected_external_calls=expected_external_calls,
                )
            record.update(
                {
                    "status": (
                        "scripted_provider_free_passed"
                        if self.scripted_provider_free
                        else "passed"
                    ),
                    "duration_ns": time.perf_counter_ns() - started,
                    "result": provider_debug(result),
                }
            )
            self.call_records.append(record)
            self.poll_pro(f"after:{label}")
            print(f"JOB4 call {record['index']}/10 passed: {label}", flush=True)
            return result
        except ProCorrectionStop:
            record.update(
                {"status": "passed_then_stopped_by_pro", "duration_ns": time.perf_counter_ns() - started}
            )
            if record not in self.call_records:
                self.call_records.append(record)
            raise
        except BaseException as exc:
            before_count = (
                self.scripted_transport_invocations
                if self.scripted_provider_free
                else self.provider_calls
            )
            dispatched = self.call_ledger.dispatched_call_count
            if self.scripted_provider_free:
                self.scripted_transport_invocations = dispatched
                self.provider_calls = 0
            else:
                self.provider_calls = dispatched
            observed = 0 if self.scripted_provider_free else dispatched - before_count
            record.update(
                {
                    "status": "failed",
                    "duration_ns": time.perf_counter_ns() - started,
                    "error_type": type(exc).__name__,
                    "external_provider_calls_observed": observed,
                    "safe_diagnostics": list(getattr(exc, "safe_diagnostics", ())),
                    "provider_call_receipt": to_primitive(
                        getattr(exc, "provider_call_receipt", None)
                    ),
                }
            )
            self.call_records.append(record)
            try:
                self.poll_pro(f"after-failure:{label}")
            except ProCorrectionStop:
                pass
            print(f"JOB4 call {record['index']}/10 failed: {label}", flush=True)
            raise

    def codex_planner(self, prompt: str, turn_id: str):
        workspace = self.lifecycle_root / (
            f"call_{self.call_index_offset + len(self.call_records) + 1:02d}_planner"
        )
        self._create_workspace(workspace, "create_planner_call_workspace")
        dispatcher = ContinuousWorldToolDispatcher(
            self.world.branch_root(self.world_id, self.branch_id),
            ContinuousSessionRole.PLANNER,
            current_turn_id=turn_id,
        )
        with ContinuousWorldMcpBridge(dispatcher) as bridge:
            transport = (
                self.planner_transport_factory(workspace, self.planner_handle)
                if self.planner_transport_factory is not None
                else StablePrefixTransport(
                    CodexSDKTransport(
                        continuous_planner_route(effort="medium"),
                        workspace=workspace,
                        runner=StoredCodexThreadRunner(self.planner_handle),
                    ),
                    PLANNER_STABLE_INSTRUCTIONS,
                )
            )
            return CodexContinuousPlannerPort(
                transport, world_bridge=bridge, call_ledger=self.call_ledger
            ).plan(prompt)

    def codex_validator(
        self,
        prompt: str,
        turn_id: str,
        *,
        writer_story_text: str | None,
        accepted_pairs: tuple[AcceptedTurnPairV1, ...] = (),
    ):
        workspace = self.lifecycle_root / (
            f"call_{self.call_index_offset + len(self.call_records) + 1:02d}_validator"
        )
        self._create_workspace(workspace, "create_validator_call_workspace")
        dispatcher = ContinuousWorldToolDispatcher(
            self.world.branch_root(self.world_id, self.branch_id),
            ContinuousSessionRole.VALIDATOR,
            current_turn_id=turn_id,
        )
        with ContinuousWorldMcpBridge(dispatcher) as bridge:
            transport = (
                self.validator_transport_factory(workspace, self.validator_handle)
                if self.validator_transport_factory is not None
                else StablePrefixTransport(
                    CodexSDKTransport(
                        continuous_validator_route(
                            model=self.validator_model,
                            effort=self.validator_effort,
                        ),
                        workspace=workspace,
                        runner=StoredCodexThreadRunner(self.validator_handle),
                    ),
                    VALIDATOR_STABLE_INSTRUCTIONS,
                )
            )
            return CodexContinuousValidatorPort(
                transport, world_bridge=bridge, call_ledger=self.call_ledger
            ).validate(
                prompt,
                writer_story_text=writer_story_text,
                accepted_pairs=accepted_pairs,
            )

    def codex_reader(self, prompt: str, *, writer_story_text: str):
        if self.reader_transport_factory is None:
            raise RuntimeError("live Reader transport factory is unavailable")
        workspace = self.lifecycle_root / (
            f"call_{self.call_index_offset + len(self.call_records) + 1:02d}_reader"
        )
        self._create_workspace(workspace, "create_reader_call_workspace")
        return CodexContinuousReaderPort(
            lambda: self.reader_transport_factory(workspace),
            call_ledger=self.call_ledger,
        ).review(prompt, writer_story_text=writer_story_text)

    def _create_workspace(self, workspace: Path, operation: str) -> None:
        if self.root_diagnostic is None:
            workspace.mkdir()
            return
        self.root_diagnostic.run("lifecycle_directory", operation, workspace.mkdir)

    def deepseek(self, prompt: str):
        return DeepSeekContinuousComposerPort(
            (
                self.composer_transport_factory()
                if self.composer_transport_factory is not None
                else DeepSeekChatTransport(continuous_deepseek_route())
            ),
            call_ledger=self.call_ledger,
        ).compose(prompt)

    def prepare_http_turn(self, turn_number: int) -> PreparedContinuousTestTurn:
        """Prepare one frozen fixture turn without crossing the creator gate."""

        if turn_number in self._http_pending:
            raise StateConflictError("continuous HTTP turn is already pending")
        if turn_number != len(self.accepted_pairs) + 1:
            raise StateConflictError("continuous HTTP turn order changed")
        before_calls = len(self.call_records)
        self._active_turn_number = turn_number
        self._active_turn_id = f"turn-{turn_number:03d}"
        self._active_validator_label = None
        if turn_number == 3:
            if len(self.accepted_pairs) != 2:
                raise StateConflictError("scene summary lacks two accepted turns")
            self.summarize_scene()
            self._active_turn_number = turn_number
            self._active_turn_id = "turn-003"
            self._active_validator_label = None
        summaries: tuple[CharacterSummaryEnvelopeV1, ...]
        if turn_number == 1:
            summaries = (
                source_character_summary(
                    ROOT,
                    "sakura",
                    world=self.world,
                    world_file_revision=read_world_revision(self.world, "Sakura"),
                ),
            )
        elif turn_number == 3:
            summaries = (
                source_character_summary(
                    ROOT,
                    "mia",
                    world=self.world,
                    world_file_revision=read_world_revision(self.world, "Mia"),
                ),
            )
        else:
            summaries = ()
        request = ContinuousTurnRequestV1(
            world_id=self.world_id,
            branch_id=self.branch_id,
            scene_id="scene-002" if turn_number == 3 else "scene-001",
            turn_id=self._active_turn_id,
            user_message=TURN_MESSAGES[turn_number - 1],
            **self.ingress_reference(turn_number),
            character_summaries=summaries,
            cera_scene_change=turn_number == 3,
        )
        if turn_number == 3:
            if self.scene_change_envelope is None:
                raise StateConflictError("validated scene summary is unavailable")
            candidate = self.coordinator.prepare_after_validated_scene_change(
                request,
                scene_change_envelope=self.scene_change_envelope,
                summary_provider_calls=1,
            )
        else:
            candidate = self.coordinator.prepare(request)
        package = candidate.validator_package
        assessment = package.creator_review
        if assessment is None:
            raise StateConflictError("continuous HTTP candidate lacks a review assessment")
        accept_allowed = package.permits_disposable_acceptance(
            CreatorReviewAction.ACCEPT
        )
        calls = len(self.call_records) - before_calls
        expected_calls = (4 if turn_number == 3 else 3) + int(
            self.reader_transport_factory is not None
        )
        if calls != expected_calls:
            raise StateConflictError("continuous HTTP provider schedule changed")
        prepared = PreparedContinuousTestTurn(
            turn_number=turn_number,
            turn_id=self._active_turn_id,
            candidate_text=candidate.deepseek_story_text,
            candidate_sha256=candidate.candidate_sha256,
            candidate_text_sha256=text_sha256(candidate.deepseek_story_text),
            sequence_beats=tuple(
                beat.observable_action_or_dialogue_direction
                for beat in candidate.planner_sequence.beats
            ),
            sequence_plan_sha256=candidate.planner_sequence.sequence_sha256,
            validator_package_id=package.package_id,
            validator_package_sha256=package.package_sha256,
            validator_semantic_status=package.semantic_status.value,
            assessment_schema_version=assessment.schema_version,
            assessment_severity=assessment.severity,
            assessment_publication_eligibility=assessment.publication_eligibility,
            assessment_issue_owner=assessment.issue_owner,
            assessment_reason_codes=assessment.reason_codes,
            assessment_creator_reason=assessment.creator_reason,
            assessment_verifier_status=assessment.verifier_status,
            assessment_receipt_sha256=assessment.assessment_sha256,
            provider_calls=calls,
            accept_allowed=accept_allowed,
        )
        self._http_pending[turn_number] = (candidate, prepared)
        return prepared

    def accept_http_turn(
        self, prepared: PreparedContinuousTestTurn
    ) -> AcceptedContinuousTestTurn:
        """Cross strict disposable acceptance only after the HTTP decision."""

        turn_number = prepared.turn_number
        try:
            candidate, bound = self._http_pending[turn_number]
        except KeyError as exc:
            raise StateConflictError("continuous HTTP candidate is unavailable") from exc
        package = candidate.validator_package
        assessment = package.creator_review
        if bound != prepared:
            raise StateConflictError("continuous HTTP review record is stale or substituted")
        if assessment is None:
            raise StateConflictError("continuous HTTP review assessment disappeared")
        actual_bindings = {
            "turn_id": f"turn-{turn_number:03d}",
            "candidate_text": candidate.deepseek_story_text,
            "candidate_sha256": candidate.candidate_sha256,
            "candidate_text_sha256": text_sha256(candidate.deepseek_story_text),
            "sequence_plan_sha256": candidate.planner_sequence.sequence_sha256,
            "validator_package_id": package.package_id,
            "validator_package_sha256": package.package_sha256,
            "validator_semantic_status": package.semantic_status.value,
            "assessment_schema_version": assessment.schema_version,
            "assessment_severity": assessment.severity,
            "assessment_publication_eligibility": assessment.publication_eligibility,
            "assessment_issue_owner": assessment.issue_owner,
            "assessment_reason_codes": assessment.reason_codes,
            "assessment_creator_reason": assessment.creator_reason,
            "assessment_verifier_status": assessment.verifier_status,
            "assessment_receipt_sha256": assessment.assessment_sha256,
            "accept_allowed": package.permits_disposable_acceptance(
                CreatorReviewAction.ACCEPT
            ),
        }
        if any(getattr(prepared, key) != value for key, value in actual_bindings.items()):
            raise StateConflictError("continuous HTTP review binding changed before Accept")
        if not package.permits_disposable_acceptance(
            CreatorReviewAction.ACCEPT
        ):
            raise StateConflictError("continuous HTTP candidate is not accept eligible")
        self._http_pending.pop(turn_number)
        receipt = self.coordinator.apply_creator_action(
            f"turn-{turn_number:03d}", CreatorReviewAction.ACCEPT
        )
        pair = self.world.accepted_turn_pairs(
            self.world_id, self.branch_id, (f"turn-{turn_number:03d}",)
        )[0]
        self.accepted_pairs.append(pair)
        result = {
            "turn_id": f"turn-{turn_number:03d}",
            "candidate_sha256": candidate.candidate_sha256,
            "validator_package_sha256": candidate.validator_package.package_sha256,
            "promotion_receipt_sha256": receipt.receipt_sha256,
            "context_mode": candidate.context_mode.value,
            "planner_packet_kind": candidate.planner_authority_packet_kind,
            "compact_accepted_head_receipt_sha256": (
                candidate.compact_accepted_head_receipt_sha256
            ),
            "accepted_final_injected": True,
        }
        self.http_turn_results.append(result)
        return AcceptedContinuousTestTurn(
            turn_number=turn_number,
            artifact_id=receipt.receipt_sha256,
            generation=turn_number,
            promotion_receipt_sha256=receipt.receipt_sha256,
            review_binding_sha256=prepared.review_binding_sha256,
        )

    # The canary executes turns through the exact shared
    # ContinuousShadowTurnCoordinator used by runtime instead of maintaining a
    # second orchestration implementation.
    def run_turn(
        self,
        *,
        turn_number: int,
        scene_id: str,
        summaries: tuple[CharacterSummaryEnvelopeV1, ...],
        scene_change_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._active_turn_number = turn_number
        self._active_turn_id = f"turn-{turn_number:03d}"
        self._active_validator_label = None
        request = ContinuousTurnRequestV1(
            world_id=self.world_id,
            branch_id=self.branch_id,
            scene_id=scene_id,
            turn_id=self._active_turn_id,
            user_message=TURN_MESSAGES[turn_number - 1],
            **self.ingress_reference(turn_number),
            character_summaries=summaries,
            cera_scene_change=scene_change_context is not None,
        )
        if scene_change_context is None:
            candidate = self.coordinator.prepare(request)
        else:
            if self.scene_change_envelope is None:
                raise RuntimeError("validated scene-change envelope is unavailable")
            candidate = self.coordinator.prepare_after_validated_scene_change(
                request,
                scene_change_envelope=self.scene_change_envelope,
                summary_provider_calls=1,
            )
        package = candidate.validator_package
        if not package.permits_disposable_acceptance(CreatorReviewAction.ACCEPT):
            raise RuntimeError("Validator package did not qualify for disposable acceptance")
        receipt = self.coordinator.apply_creator_action(
            self._active_turn_id, CreatorReviewAction.ACCEPT
        )
        pair = self.world.accepted_turn_pairs(
            self.world_id, self.branch_id, (self._active_turn_id,)
        )[0]
        self.accepted_pairs.append(pair)
        usage = json.loads(
            (candidate.debug_root / "usage.json").read_text(encoding="utf-8")
        )
        planner_usage = tuple(usage.get("planner_prompt_components", ()))
        summary_bytes = sum(
            int(value.get("byte_count", 0))
            for value in planner_usage
            if value.get("component") == "character_summaries"
        )
        total_bytes = sum(int(value.get("byte_count", 0)) for value in planner_usage)
        planner_prompt_text = (
            candidate.debug_root / "planner_raw_prompt.txt"
        ).read_text(encoding="utf-8")
        composer_prompt_text = json.loads(
            (candidate.debug_root / "deepseek_request.json").read_text(
                encoding="utf-8"
            )
        )["prompt"]
        validator_prompt_text = json.loads(
            (candidate.debug_root / "validator_request.json").read_text(
                encoding="utf-8"
            )
        )["prompt"]
        planner_packet = json.loads(
            (candidate.debug_root / "planner_authority_packet.json").read_text(
                encoding="utf-8"
            )
        )
        replay = json.loads(
            (candidate.debug_root / "replay_input.json").read_text(
                encoding="utf-8"
            )
        )
        cited_validator_closure = json.loads(
            (
                candidate.debug_root
                / "validator_cited_accepted_evidence.json"
            ).read_text(encoding="utf-8")
        )
        return {
            "turn_id": self._active_turn_id,
            "sequence_sha256": candidate.planner_sequence.sequence_sha256,
            "story_sha256": text_sha256(candidate.deepseek_story_text),
            "validator_package_sha256": package.package_sha256,
            "promotion_receipt_sha256": receipt.receipt_sha256,
            "planner_prompt_usage": planner_usage,
            "composer_prompt_usage": tuple(usage.get("composer_prompt_components", ())),
            "validator_prompt_usage": tuple(usage.get("validator_prompt_components", ())),
            "character_summary_share": 0.0 if total_bytes == 0 else summary_bytes / total_bytes,
            "beat_count": len(candidate.planner_sequence.beats),
            "rich_beat_field_coverage": all(
                all(
                    (
                        beat.roles.assertion_owner_ids,
                        beat.evidence_grounded_perception,
                        beat.immediate_goal,
                        beat.relevant_character_pressures,
                        beat.selected_tactic,
                        beat.causal_explanation,
                        beat.observable_action_or_dialogue_direction,
                        beat.private_state_guidance,
                        beat.physical_material_continuity,
                        beat.resulting_state,
                        beat.deepseek_realization_space,
                        beat.source_evidence_bindings,
                    )
                )
                for beat in candidate.planner_sequence.beats
            ),
            "operation_count": len(package.world_edit_operations),
            "changed_files": list(receipt.changed_files),
            "created_fields": list(receipt.created_fields),
            "debug_complete": not candidate.debug_root.joinpath("errors.json").is_symlink(),
            "accepted_final_injected": True,
            "context_mode": candidate.context_mode.value,
            "planner_packet_schema_version": (
                candidate.planner_authority_packet_schema_version
            ),
            "planner_packet_kind": candidate.planner_authority_packet_kind,
            "planner_packet_sha256": candidate.planner_authority_packet_sha256,
            "planner_packet_bytes": candidate.planner_authority_packet_bytes,
            "planner_packet_replay_matches": (
                replay["planner_authority_packet"] == planner_packet
                and replay["planner_authority_packet_sha256"]
                == canonical_sha256(planner_packet)
                and candidate.planner_authority_packet_sha256
                == canonical_sha256(planner_packet)
            ),
            "validator_cited_accepted_evidence_sha256": (
                candidate.validator_cited_accepted_evidence_sha256
            ),
            "validator_cited_closure_replay_matches": (
                replay["validator_cited_accepted_evidence"]
                == cited_validator_closure
                and candidate.validator_cited_accepted_evidence_sha256
                == canonical_sha256(cited_validator_closure)
            ),
            "compact_accepted_head_receipt_sha256": (
                candidate.compact_accepted_head_receipt_sha256
            ),
            "character_summary_delivery_receipt_sha256s": list(
                candidate.character_summary_delivery_receipt_sha256s
            ),
            "actual_submitted_prompts": usage.get(
                "actual_submitted_prompts", {}
            ),
            "accepted_context_injection": usage.get(
                "accepted_context_injection"
            ),
            "lean_absence_checks": {
                "stable_instructions_absent": (
                    PLANNER_STABLE_INSTRUCTIONS not in planner_prompt_text
                ),
                "prior_user_message_absent": (
                    turn_number == 1
                    or TURN_MESSAGES[turn_number - 2]
                    not in planner_prompt_text
                ),
                "prior_complete_sequence_absent": (
                    turn_number == 1
                    or _accepted_story_marker(turn_number - 1)
                    not in planner_prompt_text
                ),
                "accepted_fact_payload_absent": (
                    '"field_value"' not in planner_prompt_text
                ),
                "unchanged_character_summary_absent": (
                    turn_number != 2
                    or not planner_packet["character_summary_bindings"]
                ),
                "composer_prior_projection_absent": (
                    "[OWNER-SCOPED ACCEPTED-SESSION PROJECTIONS]\n[]"
                    in composer_prompt_text
                ),
                "validator_prior_projection_absent": (
                    '"accepted_session_projections"'
                    not in validator_prompt_text
                ),
            },
        }

    def summarize_scene(self) -> dict[str, Any]:
        self._active_turn_number = 3
        self._active_turn_id = "turn-003"
        self._active_validator_label = "scene-1-validator-summary"
        request = ContinuousTurnRequestV1(
            world_id=self.world_id,
            branch_id=self.branch_id,
            scene_id="scene-002",
            turn_id="turn-003",
            user_message=TURN_MESSAGES[2],
            **self.ingress_reference(3),
            cera_scene_change=True,
        )
        summary = self.coordinator.prepare_scene_change_summary(
            request,
            completed_scene_id="scene-001",
            accepted_turn_ids=tuple(value.accepted_turn_id for value in self.accepted_pairs[:2]),
        )
        self.scene_change_envelope = summary.scene_change_envelope
        package = summary.scene_summary_package
        return {
            "package_sha256": package.package_sha256,
            "summary_sha256": canonical_sha256(package.optional_scene_summary),
            "accepted_turn_ids": list(
                package.optional_scene_summary.accepted_turn_ids
            ),
            "exact_pair_count": len(
                package.optional_scene_summary.last_five_exact_pairs
            ),
            "new_prompt_excluded": (
                TURN_MESSAGES[2]
                not in package.optional_scene_summary.shortest_complete_summary
            ),
            "same_validator_thread_sha256": text_sha256(self.validator_handle),
        }

    def audit_accepted_checkpoint_fork(self) -> dict[str, Any]:
        """Exercise the preferred child-thread fork without a provider call."""

        parent = self.planner_session
        parent_handle = parent.ensure_session()
        child_branch = "canary-fork-child"
        child_compatibility = replace(
            parent.compatibility,
            branch_id=child_branch,
            world_directory_identity_sha256=self.world.branch_directory_identity_sha256(
                self.world_id, child_branch
            ),
        )
        materialization_receipt = self.coordinator.materialize_planner_branch(
            target_compatibility=child_compatibility
        )
        child_root = self.world.branch_root(self.world_id, child_branch)
        branch_receipt = parent.build_branch_fork_receipt(
            child_compatibility,
            branch_materialization_receipt_sha256=(
                materialization_receipt.receipt_sha256
            ),
        )
        expected_privacy = continuous_branch_privacy_boundary_sha256(
            parent_compatibility=parent.compatibility,
            child_compatibility=child_compatibility,
            accepted_checkpoint_turn_id=(
                branch_receipt.accepted_checkpoint_turn_id
            ),
            accepted_ancestry_sha256=branch_receipt.accepted_ancestry_sha256,
            parent_provider_thread_sha256=(
                parent_handle.provider_thread_id_sha256
            ),
        )
        if branch_receipt.privacy_boundary_sha256 != expected_privacy:
            raise RuntimeError("provider-fork privacy identity changed")
        forked = self.coordinator.fork_planner_session_for_branch(
            target_compatibility=child_compatibility,
            materialization_receipt=materialization_receipt,
            branch_receipt=branch_receipt,
        )
        child = forked.coordinator
        child_handle = child.ensure_session()
        initialization = child.initialization_receipt
        if (
            initialization.packet_kind
            is not ContinuousSessionInitializationKind.ACCEPTED_CHECKPOINT_FORK_INITIALIZATION
            or child_handle.provider_thread_id_sha256
            == parent_handle.provider_thread_id_sha256
        ):
            raise RuntimeError("provider fork did not create a distinct child thread")
        accepted_turn_id = self.accepted_pairs[-1].accepted_turn_id
        child_head, child_references = StableAcceptedContextReferenceStore(
            child_root
        ).load(
            accepted_turn_id,
            provider_thread_sha256=child_handle.provider_thread_id_sha256,
        )
        registry = RequestEvidenceBindingRegistry(
            world_id=self.world_id,
            branch_id=child_branch,
            turn_id="turn-fork-lean-preflight",
        )
        source = registry.allocate_current_source(
            source_identity="current_user_source:turn-fork-lean-preflight",
            source_text="Continue.",
            protected_user_allowance_scope="exact supplied source",
            source_units=(),
        )
        mechanical = registry.allocate_mechanical_connective_allowance()
        for reference in child_references:
            registry.allocate_stable_accepted_context_reference(
                reference,
                current_provider_thread_sha256=(
                    child_handle.provider_thread_id_sha256
                ),
                current_accepted_ancestry_sha256=(
                    child_head.accepted_ancestry_sha256
                ),
            )
        lean_authority = build_accepted_lean_continuation_authority(
            receipt=child_head,
            references=child_references,
            final_sequence=self.accepted_pairs[-1].complete_final_sequence,
        )
        child_packet = build_continuous_planner_turn_packet(
            world_id=self.world_id,
            branch_id=child_branch,
            session_id="session-fork-child",
            request_id="request-fork-child-lean",
            scene_id=child_head.scene_id,
            turn_id="turn-fork-lean-preflight",
            context_mode="lean_continuous",
            current_user_message="Continue.",
            request_local_evidence_bindings=registry.prompt_manifest(),
            current_source_binding_key=source.binding_key,
            mechanical_connective_binding_key=mechanical.binding_key,
            protected_user_source_claims=(),
            ingress_source_units=(),
            ingress_custody={
                "receipt_id": "ingress_receipt:fork-child-preflight",
                "receipt_sha256": text_sha256("fork-child receipt"),
                "raw_source_sha256": text_sha256("Continue."),
                "protected_user_id": "character:ted",
                "source_unit_keys": (),
            },
            character_summary_bindings=(),
            compact_accepted_head_receipt=child_head,
            stable_accepted_reference_keys=child_head.stable_reference_keys,
            lean_continuation_authority=lean_authority,
            projection_assisted_trigger=None,
            projection_reference_keys=(),
            projection_facts=(),
            scene_change_envelope_sha256=None,
        )
        parent_head, parent_references = StableAcceptedContextReferenceStore(
            self.world.branch_root(self.world_id, self.branch_id)
        ).load(
            accepted_turn_id,
            provider_thread_sha256=parent_handle.provider_thread_id_sha256,
        )
        parent_key_rejected = False
        try:
            RequestEvidenceBindingRegistry(
                world_id=self.world_id,
                branch_id=child_branch,
                turn_id="turn-parent-key-rejection",
            ).allocate_stable_accepted_context_reference(
                parent_references[0],
                current_provider_thread_sha256=(
                    child_handle.provider_thread_id_sha256
                ),
                current_accepted_ancestry_sha256=(
                    parent_head.accepted_ancestry_sha256
                ),
            )
        except StateConflictError:
            parent_key_rejected = True
        sibling_key_rejected = False
        try:
            RequestEvidenceBindingRegistry(
                world_id=self.world_id,
                branch_id="canary-fork-sibling",
                turn_id="turn-sibling-key-rejection",
            ).allocate_stable_accepted_context_reference(
                child_references[0],
                current_provider_thread_sha256=(
                    child_handle.provider_thread_id_sha256
                ),
                current_accepted_ancestry_sha256=(
                    child_head.accepted_ancestry_sha256
                ),
            )
        except StateConflictError:
            sibling_key_rejected = True
        if not parent_key_rejected or not sibling_key_rejected:
            raise RuntimeError("provider-fork foreign reference rejection changed")
        child_deliveries = child.snapshot().character_summary_deliveries
        if any(
            value.provider_thread_sha256 != child_handle.provider_thread_id_sha256
            for value in child_deliveries
        ):
            raise RuntimeError("provider-fork summary custody changed")
        child_snapshot_sha256 = child.snapshot().snapshot_sha256
        auxiliary_archive = self.coordinator.archive_auxiliary_planner_session(
            forked,
            reason="provider_free_auxiliary_fork_complete",
        )
        return {
            "status": "passed",
            "branch_receipt_sha256": branch_receipt.receipt_sha256,
            "privacy_boundary_sha256": branch_receipt.privacy_boundary_sha256,
            "initialization_packet_kind": initialization.packet_kind.value,
            "parent_planner_thread_sha256": (
                parent_handle.provider_thread_id_sha256
            ),
            "child_planner_thread_sha256": (
                child_handle.provider_thread_id_sha256
            ),
            "child_stable_reference_keys": child_head.stable_reference_keys,
            "child_first_lean_packet_kind": child_packet.packet_kind.value,
            "child_first_lean_packet_sha256": child_packet.packet_sha256,
            "transfer_receipt_sha256": (
                forked.transfer_receipt.operation_receipt_sha256
            ),
            "session_snapshot_sha256": child_snapshot_sha256,
            "summary_delivery_receipt_sha256s": tuple(
                value.receipt_sha256 for value in child_deliveries
            ),
            "parent_key_rejected": parent_key_rejected,
            "sibling_key_rejected": sibling_key_rejected,
            "external_provider_calls": 0,
            "auxiliary_archive_evidence": auxiliary_archive.to_dict(),
        }

    def reconstruct_lost_planner_thread(self) -> dict[str, Any]:
        """Exercise real new-thread reconstruction without another model call."""

        prior = self.planner_session
        prior_handle = prior.ensure_session()
        store = StableAcceptedContextReferenceStore(
            self.world.branch_root(self.world_id, self.branch_id)
        )
        accepted_tail = []
        for pair in self.accepted_pairs[-2:]:
            envelope = self.world.accepted_final_envelope(
                self.world_id,
                self.branch_id,
                pair.accepted_turn_id,
            )
            journal = self.world.acceptance_synchronization_record(
                self.world_id,
                self.branch_id,
                pair.accepted_turn_id,
            )
            _receipt, references = store.load(
                pair.accepted_turn_id,
                provider_thread_sha256=(
                    prior_handle.provider_thread_id_sha256
                ),
            )
            accepted_tail.append(
                ContinuousReconstructionAcceptedTurnV1(
                    envelope=envelope,
                    synchronization_receipt_sha256=str(
                        journal["synchronization_receipt_sha256"]
                    ),
                    stable_reference_descriptors=tuple(
                        value.injection_descriptor() for value in references
                    ),
                )
            )
        ancestry = canonical_sha256(
            {
                "world_id": self.world_id,
                "branch_id": self.branch_id,
                "accepted_tail": tuple(
                    (
                        value.envelope.accepted_turn_id,
                        value.envelope.envelope_sha256,
                        value.synchronization_receipt_sha256,
                    )
                    for value in accepted_tail
                ),
            }
        )
        bundle = ContinuousSessionReconstructionBundleV1(
            schema_version=(
                ContinuousSessionReconstructionBundleV1.SCHEMA_VERSION
            ),
            world_id=self.world_id,
            branch_id=self.branch_id,
            accepted_tail=tuple(accepted_tail),
            character_summaries=(),
            accepted_ancestry_sha256=ancestry,
            reconstruction_reason="lost_thread",
        )
        prior.port.archive(prior_handle, "scripted_lost_thread")
        initialization = self.coordinator.reconstruct_planner_session(
            bundle=bundle
        )
        parent_archive = self.coordinator.last_reconstruction_prior_archive
        if parent_archive is None:
            raise RuntimeError("reconstruction omitted prior-thread archival custody")
        if not parent_archive.verified:
            raise RuntimeError(
                "reconstruction parent thread archival could not be verified"
            )
        self.planner_session = self.coordinator.planner_session
        self.planner_handle = (
            self.planner_session.ensure_session().provider_thread_id
        )
        if self.planner_handle == prior_handle.provider_thread_id:
            raise RuntimeError("reconstruction reused the lost physical thread")
        return {
            "status": "passed",
            "reason": bundle.reconstruction_reason,
            "bundle_sha256": bundle.bundle_sha256,
            "initialization_receipt": to_primitive(initialization),
            "old_planner_thread_sha256": (
                prior_handle.provider_thread_id_sha256
            ),
            "new_planner_thread_sha256": text_sha256(self.planner_handle),
            "parent_archive_evidence": parent_archive.to_dict(),
            "accepted_tail_turn_ids": [
                value.envelope.accepted_turn_id for value in accepted_tail
            ],
            "next_context_mode": "lean_continuous",
        }

def build_report(result: dict[str, Any], *, task_id: str | None = None) -> str:
    terminal = decode_continuous_job4_terminal_evidence(
        result["terminal_evidence"]
    )
    if result.get("terminal_evidence_sha256") != terminal.sha256:
        raise ValueError("terminal evidence hash changed before report creation")
    effects = terminal.effect_evidence.canonical_effects
    calls = result.get("calls", [])
    lines = [
        "# Continuous Planner/Validator Job 4 Report",
        "",
        f"**Task:** `{task_id or result.get('task_id', 'unspecified')}`  ",
        f"**Status:** `{result['status']}`  ",
        f"**Provider calls observed:** {result['provider_calls']} / 10 (external)  ",
        f"**Scripted transport invocations:** {result.get('scripted_transport_invocations', 0)}  ",
        "**Retry/fallback:** 0 / 0",
        "",
        "## Route and isolation",
        "",
        "- Planner: `gpt-5.6-sol`, medium, Fast disabled.",
        "- Composer: `deepseek-v4-flash`, thinking disabled.",
        "- Validator: `gpt-5.6-terra`, high, Fast disabled.",
        f"- Planner thread hash: `{result.get('planner_thread_sha256')}`.",
        f"- Planner thread history: `{result.get('planner_thread_sha256s')}`.",
        f"- Validator thread hash: `{result.get('validator_thread_sha256')}`.",
        f"- Separate threads: `{result.get('separate_thread_ids')}`.",
        f"- Codex continuity hashes verified: `{result.get('continuous_thread_hashes_verified')}`.",
        f"- Stored threads archived: `{result.get('thread_archival')}`.",
        f"- Stored-thread archive evidence: `{result.get('thread_archival_evidence')}`.",
        "",
        "## Calls",
        "",
        "| # | Stage | Owner | Status | Wall seconds |",
        "|---:|---|---|---|---:|",
    ]
    for call in calls:
        lines.append(
            f"| {call['index']} | {call['label']} | {call['owner']} | {call['status']} | {call.get('duration_ns', 0) / 1_000_000_000:.3f} |"
        )
    lines.extend(
        (
            "",
            "## Verification",
            "",
            f"- Accepted disposable turns: {len(result.get('turns', []))}.",
            f"- Scene summary: `{result.get('scene_summary', {}).get('summary_sha256')}`.",
            f"- Turn 3 prompt excluded from Scene 1 summary: `{result.get('scene_summary', {}).get('new_prompt_excluded')}`.",
            f"- Source SQLite unchanged: `{result.get('source_database_unchanged')}`.",
            f"- Active route unchanged: `{result.get('active_route_unchanged')}`.",
            f"- Every accepted final sequence injected: `{all(value.get('accepted_final_injected') for value in result.get('turns', []))}`.",
            f"- D-200 lean-context verification: `{canonical_json(result.get('lean_context_verification'))}`.",
            f"- Lost-thread reconstruction: `{canonical_json(result.get('reconstruction'))}`.",
            f"- Terminal evidence SHA-256: `{terminal.sha256}`.",
            f"- Terminal evidence artifact: `source/JOB4_TERMINAL_EVIDENCE.json`.",
            f"- Capability custody SHA-256: `{result.get('capability_ledger_sha256')}`.",
            "- Capability boundary SHA-256: "
            f"`{result.get('capability_boundary_evidence_sha256')}`.",
            f"- Canonical effects: `{canonical_json(effects)}`.",
            f"- Mandatory terminal failures: `{canonical_json(list(terminal.failure_codes))}`.",
            "- Complete raw prompts, outputs, tool traces, candidate snapshots, diffs, edit logs, receipts, usage, timings, errors, and replay inputs remain under the ignored disposable runtime root.",
            "",
            "## Terminal failure",
            "",
            canonical_json(result.get("failure")),
            "",
        )
    )
    if isinstance(terminal, ContinuousJob4TerminalEvidenceV6):
        diagnostics = terminal.test_diagnostics
        lines.extend(
            (
                "## Selected-test terminal evidence",
                "",
                f"- Record count: `{len(diagnostics.records)}`.",
                f"- Selected-test identity root SHA-256: `{diagnostics.selection_root_sha256}`.",
                f"- Ordered records root SHA-256: `{diagnostics.records_root_sha256}`.",
                f"- Diagnostic artifact SHA-256: `{diagnostics.sha256}`.",
                "",
                "| # | Test ID | Status | Elapsed seconds | Record SHA-256 |",
                "|---:|---|---|---:|---|",
            )
        )
        for record in diagnostics.records:
            lines.append(
                f"| {record.index} | `{record.test_id}` | {record.status} | "
                f"{record.elapsed_ns / 1_000_000_000:.6f} | "
                f"`{record.record_sha256}` |"
            )
        lines.append("")
    return "\n".join(lines)


def build_canonical_job4_result(
    result: dict[str, Any],
    *,
    task_id: str,
    report_sha256: str,
) -> dict[str, Any]:
    """Project detailed canary evidence into the closed repository-cycle DTO."""

    terminal = decode_continuous_job4_terminal_evidence(
        result.get("terminal_evidence")
    )
    custody_terminal = (
        terminal.base_terminal_evidence
        if isinstance(terminal, ContinuousJob4TerminalEvidenceV6)
        else terminal
    )
    if result.get("terminal_evidence_sha256") != terminal.sha256:
        raise ValueError("terminal evidence hash is missing or inconsistent")
    effects = terminal.effect_evidence.canonical_effects
    duplicate_claims = {
        "status": terminal.status,
        "provider_calls": effects["provider_calls"],
        "story_database_writes": effects["story_database_writes"],
        "active_route_changes": effects["active_route_changes"],
        "deployment_remote_or_push_effects": effects[
            "deployment_remote_or_push_effects"
        ],
        "scripted_transport_invocations": (
            terminal.postconditions.scripted_transport_invocations
        ),
        "execution_mode": terminal.postconditions.execution_mode,
    }
    for field, expected in duplicate_claims.items():
        if result.get(field) != expected:
            raise ValueError(f"detailed result contradicts terminal evidence: {field}")
    if isinstance(
        custody_terminal,
        (
            ContinuousJob4TerminalEvidenceV2,
            ContinuousJob4TerminalEvidenceV3,
            ContinuousJob4TerminalEvidenceV4,
            ContinuousJob4TerminalEvidenceV5,
        ),
    ):
        if (
            result.get("capability_ledger")
            != custody_terminal.capability_ledger.to_dict()
            or result.get("capability_ledger_sha256")
            != custody_terminal.capability_ledger.sha256
        ):
            raise ValueError("detailed capability custody contradicts terminal evidence")
    if isinstance(
        custody_terminal,
        (ContinuousJob4TerminalEvidenceV4, ContinuousJob4TerminalEvidenceV5),
    ):
        if (
            result.get("capability_boundary_evidence")
            != custody_terminal.capability_boundary_evidence.to_dict()
            or result.get("capability_boundary_evidence_sha256")
            != custody_terminal.capability_boundary_evidence.sha256
        ):
            raise ValueError(
                "detailed capability boundary contradicts terminal evidence"
            )
    if isinstance(custody_terminal, ContinuousJob4TerminalEvidenceV5):
        if (
            result.get("thread_lineage")
            != custody_terminal.thread_lineage.to_dict()
            or result.get("thread_lineage_sha256")
            != custody_terminal.thread_lineage.receipt_sha256
        ):
            raise ValueError(
                "detailed thread lineage contradicts terminal evidence"
            )
    if result.get("source_database_unchanged") != (
        terminal.postconditions.source_database_sha256_before is not None
        and terminal.postconditions.source_database_sha256_before
        == terminal.postconditions.source_database_sha256_after
    ):
        raise ValueError("source database summary contradicts terminal evidence")
    if result.get("copy_database_unchanged") != (
        terminal.postconditions.disposable_database_sha256_before is not None
        and terminal.postconditions.disposable_database_sha256_before
        == terminal.postconditions.disposable_database_sha256_after
    ):
        raise ValueError("disposable database summary contradicts terminal evidence")
    if result.get("active_route_unchanged") != (
        terminal.postconditions.active_route_changes == 0
    ):
        raise ValueError("active route summary contradicts terminal evidence")
    if result.get("thread_archival") != dict(
        terminal.postconditions.thread_archival
    ):
        raise ValueError("thread archival summary contradicts terminal evidence")
    archival_evidence = (
        {
            role: custody_terminal.thread_archival_evidence[role].to_dict()
            for role in ("planner", "validator")
        }
        if isinstance(
            custody_terminal,
            (
                ContinuousJob4TerminalEvidenceV3,
                ContinuousJob4TerminalEvidenceV4,
                ContinuousJob4TerminalEvidenceV5,
            ),
        )
        else result.get("thread_archival_evidence")
    )
    if (
        isinstance(
            custody_terminal,
            (
                ContinuousJob4TerminalEvidenceV3,
                ContinuousJob4TerminalEvidenceV4,
                ContinuousJob4TerminalEvidenceV5,
            ),
        )
        and result.get("thread_archival_evidence") != archival_evidence
    ):
        raise ValueError(
            "detailed thread archival custody contradicts terminal evidence"
        )
    if archival_evidence is not None:
        if not isinstance(archival_evidence, dict) or set(archival_evidence) != {
            "planner",
            "validator",
        }:
            raise ValueError("thread archival evidence is malformed")
        for role, verified in terminal.postconditions.thread_archival.items():
            evidence = ContinuousThreadArchiveEvidenceV1.from_dict(
                archival_evidence.get(role)
            )
            if evidence.role.value != role or evidence.verified is not verified:
                raise ValueError(
                    "thread archival evidence contradicts postconditions"
                )
    status = terminal.status
    provider_calls = effects["provider_calls"]
    scripted_invocations = terminal.postconditions.scripted_transport_invocations
    mode = terminal.postconditions.execution_mode
    summary = (
        f"mode={mode}; external_provider_calls={provider_calls}; "
        f"scripted_transport_invocations={scripted_invocations}; "
        f"terminal_status={status}; terminal_evidence_sha256={terminal.sha256}"
    )
    payload = {
        "schema_version": "cera.pro_review_job4_result.v2",
        "cycle_id": result["cycle_id"],
        "task_id": task_id,
        "status": status,
        "report_relative_path": "source/JOB4_REPORT.md",
        "report_sha256": report_sha256,
        "terminal_evidence_relative_path": (
            "source/JOB4_TERMINAL_EVIDENCE.json"
        ),
        "terminal_evidence_sha256": terminal.sha256,
        "effects": effects,
        "verification": [
            {
                "command": "continuous-lean-context-provider-free-integration-audit-v1",
                "status": (
                    "passed"
                    if terminal.execution_status == "completed"
                    else "failed"
                ),
                "summary": summary,
            },
            {
                "command": "mandatory-terminal-postcondition-and-effect-check",
                "status": "passed" if status == "completed" else "failed",
                "summary": (
                    "all mandatory terminal evidence passed"
                    if status == "completed"
                    else "failure_codes=" + ",".join(terminal.failure_codes)
                ),
            },
        ],
    }
    if isinstance(terminal, ContinuousJob4TerminalEvidenceV6):
        diagnostics = terminal.test_diagnostics
        if (
            result.get("test_diagnostics") != diagnostics.to_dict()
            or result.get("test_diagnostics_sha256") != diagnostics.sha256
            or result.get("test_records_root_sha256")
            != diagnostics.records_root_sha256
        ):
            raise ValueError(
                "detailed test diagnostics contradict terminal evidence"
            )
        payload.update(
            {
                "schema_version": "cera.pro_review_job4_result.v3",
                "test_diagnostics": diagnostics.to_dict(),
                "test_diagnostics_sha256": diagnostics.sha256,
                "test_records_root_sha256": diagnostics.records_root_sha256,
                "test_record_count": len(diagnostics.records),
            }
        )
        payload["verification"].append(
            {
                "command": "ordered-selected-test-terminal-evidence-v1",
                "status": "passed" if diagnostics.all_acceptable else "failed",
                "summary": (
                    f"records={len(diagnostics.records)}; "
                    f"records_root_sha256={diagnostics.records_root_sha256}; "
                    f"diagnostics_sha256={diagnostics.sha256}"
                ),
            }
        )
    return payload


def _complete_thread_archival_evidence(
    result: dict[str, Any],
) -> dict[str, ContinuousThreadArchiveEvidenceV1]:
    """Normalize both role records without converting absence into success."""

    raw = result.get("thread_archival_evidence")
    shape_valid = isinstance(raw, dict) and set(raw) == {"planner", "validator"}
    supplied = raw if shape_valid else {}
    evidence: dict[str, ContinuousThreadArchiveEvidenceV1] = {}
    for role in (ContinuousSessionRole.PLANNER, ContinuousSessionRole.VALIDATOR):
        candidate = supplied.get(role.value)
        try:
            decoded = ContinuousThreadArchiveEvidenceV1.from_dict(candidate)
            if decoded.role is not role:
                raise ValueError("archive evidence role changed")
        except Exception as exc:
            if raw is not None and not shape_valid:
                error_type = "ArchiveEvidenceMapFieldsChanged"
            elif candidate is None:
                error_type = "MissingArchiveEvidence"
            else:
                error_type = type(exc).__name__
            decoded = unavailable_thread_archive_evidence(
                role,
                error_type=error_type,
            )
        evidence[role.value] = decoded
    result["thread_archival_evidence"] = {
        role: evidence[role].to_dict() for role in ("planner", "validator")
    }
    result["thread_archival"] = {
        role: evidence[role].verified for role in ("planner", "validator")
    }
    return evidence


def _complete_thread_lineage_evidence(
    result: dict[str, Any],
    *,
    world: ContinuousWorldStore | None,
) -> ContinuousThreadLineageReceiptV1:
    """Decode the frozen total map or derive a terminally failed missing map."""

    raw = result.get("thread_lineage")
    try:
        receipt = ContinuousThreadLineageReceiptV1.from_dict(raw)
    except Exception:
        ledger = ContinuousThreadLineageLedger()
        planner_hashes = tuple(
            dict.fromkeys(
                value
                for value in result.get("planner_thread_sha256s", ())
                if isinstance(value, str)
                and len(value) == 64
                and all(character in "0123456789abcdef" for character in value)
            )
        )
        parent = None
        for index, thread_sha256 in enumerate(planner_hashes):
            ledger.register_thread(
                role="planner",
                purpose=(
                    "primary_planner" if index == 0 else "planner_reconstruction"
                ),
                world_id=WORLD_ID,
                branch_id=BRANCH_ID,
                session_compatibility_sha256=(
                    compatibility(
                        world, ContinuousSessionRole.PLANNER
                    ).compatibility_sha256
                    if world is not None
                    else text_sha256("unavailable planner compatibility")
                ),
                provider_thread_sha256=thread_sha256,
                parent_provider_thread_sha256=parent,
                creation_operation="create" if index == 0 else "reconstruct",
            )
            parent = thread_sha256
        validator_hash = result.get("validator_thread_sha256")
        if (
            isinstance(validator_hash, str)
            and len(validator_hash) == 64
            and all(character in "0123456789abcdef" for character in validator_hash)
        ):
            ledger.register_thread(
                role="validator",
                purpose="primary_validator",
                world_id=WORLD_ID,
                branch_id=BRANCH_ID,
                session_compatibility_sha256=(
                    compatibility(
                        world, ContinuousSessionRole.VALIDATOR
                    ).compatibility_sha256
                    if world is not None
                    else text_sha256("unavailable validator compatibility")
                ),
                provider_thread_sha256=validator_hash,
                parent_provider_thread_sha256=None,
                creation_operation="create",
            )
        receipt = ledger.freeze(authorized_active_threads={})
    result["thread_lineage"] = receipt.to_dict()
    result["thread_lineage_sha256"] = receipt.receipt_sha256
    return receipt


def execute_job4_schedule(
    *,
    harness: JobHarness,
    world: ContinuousWorldStore,
    planner_session: ContinuousSessionCoordinator,
    planner_handle: str,
    validator_handle: str,
    result: dict[str, Any],
    scripted_provider_free: bool,
) -> None:
    """Run the one exact ten-stage schedule shared by both CLI modes."""

    result.update(
        {
            "planner_thread_sha256": text_sha256(planner_handle),
            "planner_thread_sha256_initial": text_sha256(planner_handle),
            "planner_thread_sha256s": [text_sha256(planner_handle)],
            "validator_thread_sha256": text_sha256(validator_handle),
            "separate_thread_ids": planner_handle != validator_handle,
        }
    )
    sakura_summary = source_character_summary(
        ROOT,
        "sakura",
        world=world,
        world_file_revision=read_world_revision(world, "Sakura"),
    )
    turn_one = harness.run_turn(
        turn_number=1,
        scene_id="scene-001",
        summaries=(sakura_summary,),
    )
    result["turns"].append(turn_one)
    turn_two = harness.run_turn(
        turn_number=2,
        scene_id="scene-001",
        summaries=(),
    )
    result["turns"].append(turn_two)
    if (
        turn_one["context_mode"] != "lean_continuous"
        or turn_one["planner_packet_kind"] != "first_turn_initialization"
        or turn_one["compact_accepted_head_receipt_sha256"] is not None
        or not turn_one["character_summary_delivery_receipt_sha256s"]
        or turn_two["context_mode"] != "lean_continuous"
        or turn_two["planner_packet_kind"] != "lean_continuous_continuation"
        or turn_two["compact_accepted_head_receipt_sha256"] is None
        or turn_two["character_summary_delivery_receipt_sha256s"]
        or not turn_one["planner_packet_replay_matches"]
        or not turn_two["planner_packet_replay_matches"]
        or not turn_two["validator_cited_closure_replay_matches"]
        or not all(turn_two["lean_absence_checks"].values())
    ):
        raise RuntimeError("D-200 Turn 1/Turn 2 lean-context contract changed")
    result["provider_fork"] = harness.audit_accepted_checkpoint_fork()
    result["scene_summary"] = harness.summarize_scene()
    if scripted_provider_free:
        result["reconstruction"] = harness.reconstruct_lost_planner_thread()
        result["planner_thread_sha256"] = text_sha256(
            harness.planner_handle
        )
        result["planner_thread_sha256s"].append(
            result["planner_thread_sha256"]
        )
    mia_summary = source_character_summary(
        ROOT,
        "mia",
        world=world,
        world_file_revision=read_world_revision(world, "Mia"),
    )
    turn_three = harness.run_turn(
        turn_number=3,
        scene_id="scene-002",
        summaries=(mia_summary,),
        scene_change_context=to_primitive(harness.scene_change_envelope),
    )
    result["turns"].append(turn_three)
    if scripted_provider_free and (
        result["reconstruction"]["status"] != "passed"
        or result["reconstruction"]["next_context_mode"]
        != "lean_continuous"
        or turn_three["context_mode"] != "lean_continuous"
        or turn_three["planner_packet_kind"] != "scene_change"
        or turn_three["compact_accepted_head_receipt_sha256"] is None
        or not turn_three["planner_packet_replay_matches"]
        or not turn_three["validator_cited_closure_replay_matches"]
        or not all(turn_three["lean_absence_checks"].values())
    ):
        raise RuntimeError("D-200 reconstruction-to-lean contract changed")
    result["lean_context_verification"] = {
        "turn_1_initial_summary_delivered": bool(
            turn_one["character_summary_delivery_receipt_sha256s"]
        ),
        "turn_2_prohibited_components_absent": all(
            turn_two["lean_absence_checks"].values()
        ),
        "turn_2_compact_head_present": (
            turn_two["compact_accepted_head_receipt_sha256"] is not None
        ),
        "downstream_prior_projections_absent": all(
            turn["lean_absence_checks"][key]
            for turn in result["turns"]
            for key in (
                "composer_prior_projection_absent",
                "validator_prior_projection_absent",
            )
        ),
        "accepted_injections_recorded": all(
            turn["accepted_context_injection"] is not None
            for turn in result["turns"]
        ),
        "closed_packet_identities": tuple(
            turn["planner_packet_kind"] for turn in result["turns"]
        )
        == (
            "first_turn_initialization",
            "lean_continuous_continuation",
            "scene_change",
        ),
        "packet_and_validator_closure_replay_bound": all(
            turn["planner_packet_replay_matches"]
            and turn["validator_cited_closure_replay_matches"]
            for turn in result["turns"]
        ),
        "reconstruction_exercised": scripted_provider_free,
        "provider_fork_exercised": (
            result["provider_fork"]["status"] == "passed"
            and result["provider_fork"]["child_first_lean_packet_kind"]
            == "lean_continuous_continuation"
        ),
    }
    if len(harness.call_records) != 10:
        raise RuntimeError("successful Job 4 did not use the exact ten-call schedule")
    if scripted_provider_free:
        if harness.provider_calls != 0 or harness.scripted_transport_invocations != 10:
            raise RuntimeError(
                "scripted Job 4 did not preserve zero external and ten local invocations"
            )
    elif harness.provider_calls != 10:
        raise RuntimeError("successful Job 4 provider-call accounting changed")
    result["accepted_session_synchronized"] = not bool(
        harness.planner_session.unsynchronized_accepted_turn_ids
    )
    if not result["accepted_session_synchronized"]:
        raise RuntimeError("accepted Planner context remained unsynchronized")
    result["execution_status"] = "completed"


def verify_prepared_shadow_ingress(lifecycle_root: Path) -> dict[str, Any]:
    """Cross the actual raw/prepared/Silly-compatible shadow boundary once."""

    with RealGenesisSandbox.create(ROOT, revision="v1_2") as sandbox:
        message = "Ted asks Hana whether this is the Hanezawa residence."
        ted = sandbox.protected_user_id()
        hana = sandbox.character_id("Hana")
        envelope = RawTurnEnvelope(
            schema_version=RawTurnEnvelope.SCHEMA_VERSION,
            world_id=sandbox.world_id,
            request_id=TypedId(IdKind.REQUEST, "continuous-job4-shadow-ingress"),
            session_id=TypedId(IdKind.SESSION, "continuous-job4-shadow-session"),
            branch_id=sandbox.branch_id,
            expected_generation=0,
            expected_parent_artifact_id=None,
            genesis_revision_id=sandbox.revision_id,
            protected_user_id=ted,
            present_character_ids=(ted, hana),
            eligible_responder_ids=(hana,),
            raw_message=message,
            idempotency_key="continuous-job4-shadow-ingress",
            world_mode=EvidenceWorldMode.REAL,
            access_scope=sandbox.system_scope(),
            preflight_authority=PreflightAuthority(RequestedContentClass.ORDINARY),
            hard_boundaries=(
                "Do not author the protected user.",
                "Do not transfer owner-private evidence.",
            ),
        )
        registry = build_default_prepared_classifier_registry()
        authority_root = lifecycle_root / "prepared_shadow_ingress_authority"
        authority = ContinuousIngressAuthorityStore(
            authority_root,
            prepared_classifier_registry=registry,
        )
        shadow = ContinuousSillyTavernShadowRequestBridge(
            raw_ingress=RawTurnIngressFacade(
                turn_kernel=TurnKernel(sandbox.service),
                seed_assembler=SeedDossierAssembler(sandbox.service),
            ),
            prepared_ingress=PreparedContinuousIngressBridge(
                authority=authority,
                classifier_registry=registry,
            ),
        )
        prepared = shadow.prepare(
            chat_request=SillyTavernChatRequest(
                model=CERA_VIRTUAL_MODEL,
                messages=(ChatMessage(role="user", content=message),),
                stream=False,
            ),
            envelope=envelope,
            scene_id="scene:shadow-ingress",
            turn_id="turn-shadow-ingress",
        )
        restarted = ContinuousIngressAuthorityStore(
            authority_root,
            prepared_classifier_registry=build_default_prepared_classifier_registry(),
        ).resolve(
            receipt_id=prepared.ingress_receipt_id,
            receipt_sha256=prepared.ingress_receipt_sha256,
        )
        if restarted.receipt_sha256 != prepared.request.ingress_receipt_sha256:
            raise RuntimeError("prepared shadow ingress restart identity changed")
        return {
            "status": "passed",
            "classifier_registry_sha256": registry.registry_sha256,
            "ingress_receipt_sha256": restarted.receipt_sha256,
            "authority_kind": restarted.authority_kind.value,
            "source_unit_kinds": tuple(value.kind.value for value in restarted.source_units),
            "external_provider_calls": 0,
            "story_writes": 0,
        }


def _terminalize_known_thread_sessions(
    *,
    thread_lineage: ContinuousThreadLineageLedger,
    planner_session: ContinuousSessionCoordinator | None,
    validator_session: ContinuousSessionCoordinator | None,
    reason: str,
) -> tuple[
    dict[str, bool],
    dict[str, dict[str, object]],
    ContinuousThreadLineageReceiptV1,
]:
    """Archive every physically created primary handle, then freeze the ledger."""

    archived: dict[str, bool] = {}
    archival_evidence: dict[str, dict[str, object]] = {}
    for role, coordinator in (
        ("planner", planner_session),
        ("validator", validator_session),
    ):
        role_value = ContinuousSessionRole(role)
        if coordinator is None or coordinator.handle is None:
            evidence = unavailable_thread_archive_evidence(
                role_value,
                error_type="ThreadNotCreated",
            )
        elif coordinator._terminally_archived:
            archived_copy = thread_lineage.archive_evidence_for(
                coordinator.handle.provider_thread_id_sha256
            )
            if archived_copy is None:
                evidence = unavailable_thread_archive_evidence(
                    role_value,
                    error_type="ArchiveEvidenceUnavailable",
                )
            else:
                evidence = ContinuousThreadArchiveEvidenceV1.from_dict(
                    archived_copy.to_dict()
                )
        else:
            evidence = coordinator.archive_and_verify_terminal(reason)
        archival_evidence[role] = evidence.to_dict()
        archived[role] = evidence.verified
    lineage_receipt = thread_lineage.freeze(authorized_active_threads={})
    return archived, archival_evidence, lineage_receipt


def execute_scripted_job4(
    *,
    cycle: Path,
    world: ContinuousWorldStore,
    lifecycle_root: Path,
    call_ledger: ContinuousProviderCallLedger,
    root_diagnostic: ContinuousRootDiagnosticRecorder,
    result: dict[str, Any],
    fault_injector: _ProviderFreeTestFaultInjector,
    harness_holder: dict[str, JobHarness] | None = None,
) -> JobHarness:
    """Cross the actual executable path with closed local transports only."""

    result["prepared_shadow_ingress"] = root_diagnostic.run(
        "prepared_shadow_ingress",
        "verify_repository_controlled_shadow_ingress",
        lambda: verify_prepared_shadow_ingress(lifecycle_root),
    )
    session_port = _FaultInjectedContinuousSessionPort(fault_injector)
    planner_session = root_diagnostic.run(
        "session_construction",
        "construct_scripted_planner_session",
        lambda: ContinuousSessionCoordinator(
            compatibility(world, ContinuousSessionRole.PLANNER), session_port
        ),
    )
    validator_session = root_diagnostic.run(
        "session_construction",
        "construct_scripted_validator_session",
        lambda: ContinuousSessionCoordinator(
            compatibility(world, ContinuousSessionRole.VALIDATOR), session_port
        ),
    )
    thread_lineage = ContinuousThreadLineageLedger()
    planner_session.attach_thread_lineage(
        thread_lineage,
        purpose="primary_planner",
    )
    validator_session.attach_thread_lineage(
        thread_lineage,
        purpose="primary_validator",
    )
    planner_session.install_base_instructions(PLANNER_STABLE_INSTRUCTIONS)
    harness: JobHarness | None = None
    try:
        planner_handle = root_diagnostic.run(
            "stored_thread_construction",
            "ensure_scripted_planner_thread",
            lambda: planner_session.ensure_session().provider_thread_id,
        )
        fault_injector.hit("after_primary_planner_creation")
        validator_handle = root_diagnostic.run(
            "stored_thread_construction",
            "ensure_scripted_validator_thread",
            lambda: validator_session.ensure_session().provider_thread_id,
        )
        root_diagnostic.run(
            "role_separation",
            "assert_scripted_role_separation",
            lambda: assert_separate_role_sessions(planner_session, validator_session),
        )
        fixture = ScriptedJob4FixtureRuntime(world_id=WORLD_ID, branch_id=BRANCH_ID)
        harness = JobHarness(
            source_root=ROOT,
            cycle=cycle,
            world=world,
            planner_session=planner_session,
            validator_session=validator_session,
            planner_handle=planner_handle,
            validator_handle=validator_handle,
            lifecycle_root=lifecycle_root,
            call_ledger=call_ledger,
            root_diagnostic=root_diagnostic,
            planner_transport_factory=fixture.planner_transport,
            validator_transport_factory=fixture.validator_transport,
            composer_transport_factory=fixture.composer_transport,
            scripted_provider_free=True,
            thread_lifecycle_failpoint=fault_injector.hit,
        )
        if harness_holder is not None:
            harness_holder["harness"] = harness
        fixture.bind(harness)
        execute_job4_schedule(
            harness=harness,
            world=world,
            planner_session=planner_session,
            planner_handle=planner_handle,
            validator_handle=validator_handle,
            result=result,
            scripted_provider_free=True,
        )
    finally:
        archived, archival_evidence, lineage_receipt = (
            _terminalize_known_thread_sessions(
                thread_lineage=thread_lineage,
                planner_session=(
                    planner_session if harness is None else harness.planner_session
                ),
                validator_session=validator_session,
                reason="provider_free_job4_complete",
            )
        )
        result["thread_archival"] = archived
        result["thread_archival_evidence"] = archival_evidence
        result["thread_lineage"] = lineage_receipt.to_dict()
        result["thread_lineage_sha256"] = lineage_receipt.receipt_sha256
        if not all(archived.values()) or lineage_receipt.status != "verified":
            raise RuntimeError("scripted canary thread lifecycle closure failed")
    if harness is None:
        raise RuntimeError("scripted Job 4 harness was not constructed")
    return harness


def _record_terminal_failure(
    result: dict[str, Any],
    error: BaseException,
    *,
    stage: str,
    message: str = "Terminal one-shot Job 4 failure; inspect privacy-safe stage evidence.",
) -> None:
    result["execution_status"] = "failed"
    if result.get("failure") is None:
        result["failure"] = {
            "stage": stage,
            "error_type": type(error).__name__,
            "message": message,
        }


def _apply_terminal_evidence(
    result: dict[str, Any],
    *,
    terminal: ContinuousJob4TerminalEvidence,
) -> ContinuousJob4TerminalEvidence:
    effects = terminal.effect_evidence.canonical_effects
    result["execution_status"] = terminal.execution_status
    result["provider_calls"] = effects["provider_calls"]
    result["operational_counters"] = (
        terminal.effect_evidence.operational_counters.to_dict()
    )
    result["terminal_evidence"] = terminal.to_dict()
    result["terminal_evidence_sha256"] = terminal.sha256
    result["status"] = terminal.status
    result["story_database_writes"] = effects["story_database_writes"]
    result["active_route_changes"] = effects["active_route_changes"]
    result["deployment_remote_or_push_effects"] = effects[
        "deployment_remote_or_push_effects"
    ]
    result["active_route_unchanged"] = effects["active_route_changes"] == 0
    result["finished_at"] = utc_now()
    if isinstance(terminal, ContinuousJob4TerminalEvidenceV6):
        diagnostics = terminal.test_diagnostics
        result["test_diagnostics"] = diagnostics.to_dict()
        result["test_diagnostics_sha256"] = diagnostics.sha256
        result["test_records_root_sha256"] = diagnostics.records_root_sha256
    return terminal


def _emergency_report(result: dict[str, Any], *, task_id: str) -> str:
    terminal = decode_continuous_job4_terminal_evidence(
        result["terminal_evidence"]
    )
    return "\n".join(
        (
            "# Continuous Planner/Validator Job 4 Terminal Report",
            "",
            f"**Task:** `{task_id}`  ",
            "**Status:** `failed`  ",
            f"**Provider calls observed:** {terminal.effect_evidence.provider_calls}  ",
            "**Retry/fallback:** 0 / 0",
            "",
            "Terminal publication encountered a bounded finalization failure.",
            "No semantic or provider work may be repeated under this identity.",
            f"Terminal evidence SHA-256: `{terminal.sha256}`.",
            f"Canonical effects: `{canonical_json(terminal.effect_evidence.canonical_effects)}`.",
            f"Failure codes: `{canonical_json(list(terminal.failure_codes))}`.",
            "",
        )
    )


def _emergency_canonical_result(
    result: dict[str, Any], *, task_id: str, report_sha256: str
) -> dict[str, Any]:
    terminal = decode_continuous_job4_terminal_evidence(
        result["terminal_evidence"]
    )
    payload = {
        "schema_version": "cera.pro_review_job4_result.v2",
        "cycle_id": result["cycle_id"],
        "task_id": task_id,
        "status": "failed",
        "report_relative_path": "source/JOB4_REPORT.md",
        "report_sha256": report_sha256,
        "terminal_evidence_relative_path": (
            "source/JOB4_TERMINAL_EVIDENCE.json"
        ),
        "terminal_evidence_sha256": terminal.sha256,
        "effects": terminal.effect_evidence.canonical_effects,
        "verification": [
            {
                "command": "continuous-job4-terminal-publication",
                "status": "failed",
                "summary": (
                    "terminalized_finalization_failure; terminal_evidence_sha256="
                    + terminal.sha256
                ),
            }
        ],
    }
    if isinstance(terminal, ContinuousJob4TerminalEvidenceV6):
        diagnostics = terminal.test_diagnostics
        payload.update(
            {
                "schema_version": "cera.pro_review_job4_result.v3",
                "test_diagnostics": diagnostics.to_dict(),
                "test_diagnostics_sha256": diagnostics.sha256,
                "test_records_root_sha256": diagnostics.records_root_sha256,
                "test_record_count": len(diagnostics.records),
            }
        )
    return payload


def freeze_terminal_publication(
    transaction: ContinuousJob4TerminalTransactionV1,
    result: dict[str, Any],
    *,
    task_id: str,
    recovery_terminalization: bool,
    fault_injector: _ProviderFreeTestFaultInjector | None = None,
    report_builder: Callable[[dict[str, Any], str], str] | None = None,
) -> dict[str, Any]:
    if fault_injector is None:
        fault_injector = _ProviderFreeTestFaultInjector(None)
    publication_cut = (
        fault_injector.selected
        if fault_injector.selected
        in {
            "report_write",
            "result_write",
            "terminal_artifact_write",
            "publication_commit_marker",
        }
        and not fault_injector.consumed
        else None
    )
    if publication_cut is not None:
        _record_terminal_failure(
            result,
            RuntimeError(f"injected terminal publication cut: {publication_cut}"),
            stage=publication_cut,
            message="Terminal publication cut was frozen as a failed one-shot result.",
        )
        terminal = decode_continuous_job4_terminal_evidence(
            result["terminal_evidence"]
        )
        _apply_terminal_evidence(
            result,
            terminal=rebuild_failed_continuous_job4_terminal_evidence(terminal),
        )
    try:
        fault_injector.hit("terminal_evidence_serialization")
        terminal = decode_continuous_job4_terminal_evidence(
            result["terminal_evidence"]
        )
        terminal_evidence_bytes = canonical_bytes(terminal.to_dict())
        if bytes_sha256(terminal_evidence_bytes) != terminal.sha256:
            raise RuntimeError("terminal evidence byte identity changed before freeze")
        fault_injector.hit("report_construction")
        report = (
            build_report(result, task_id=task_id)
            if report_builder is None
            else report_builder(result, task_id)
        )
        report_bytes = report.encode("utf-8")
        fault_injector.hit("canonical_projection")
        canonical_result = build_canonical_job4_result(
            result,
            task_id=task_id,
            report_sha256=bytes_sha256(report_bytes),
        )
        fault_injector.hit("detail_serialization")
        detail_bytes = canonical_bytes(result) + b"\n"
        fault_injector.hit("result_serialization")
        result_bytes = canonical_bytes(canonical_result) + b"\n"
    except BaseException as exc:
        _record_terminal_failure(
            result,
            exc,
            stage="terminal_publication_construction",
            message="Terminal publication construction failed and was terminalized.",
        )
        terminal = decode_continuous_job4_terminal_evidence(
            result["terminal_evidence"]
        )
        failed_terminal = rebuild_failed_continuous_job4_terminal_evidence(
            terminal
        )
        _apply_terminal_evidence(result, terminal=failed_terminal)
        result["terminal_publication_failure"] = {
            "error_type": type(exc).__name__,
            "semantic_work_repeated": False,
        }
        report = _emergency_report(result, task_id=task_id)
        report_bytes = report.encode("utf-8")
        canonical_result = _emergency_canonical_result(
            result,
            task_id=task_id,
            report_sha256=bytes_sha256(report_bytes),
        )
        detail_bytes = canonical_bytes(result) + b"\n"
        result_bytes = canonical_bytes(canonical_result) + b"\n"
        terminal_evidence_bytes = canonical_bytes(failed_terminal.to_dict())
    transaction.freeze(
        detail_bytes=detail_bytes,
        terminal_evidence_bytes=terminal_evidence_bytes,
        report_bytes=report_bytes,
        result_bytes=result_bytes,
        recovery_terminalization=recovery_terminalization,
    )
    if publication_cut is not None:
        fault_injector.consumed = True
    transaction.publish_frozen(test_cut_point=publication_cut)
    return canonical_result


# Historical tests and repository consumers imported the private name before
# this became the shared correction-runner publication path.
_freeze_terminal_publication = freeze_terminal_publication


def _recovered_ledger_counts(path: Path, *, scripted: bool) -> tuple[int, int, int]:
    """Conservatively recover counts without constructing or executing a port."""

    if not path.is_file():
        return 0, 0, 0
    try:
        events = tuple(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 0, 0, 0
    last: dict[str, str] = {}
    invoked: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        call_id = event.get("call_id")
        state = event.get("state")
        if not isinstance(call_id, str) or not isinstance(state, str):
            continue
        last[call_id] = state
        if state == "transport_invoked":
            invoked.add(call_id)
    invoked.update(
        call_id
        for call_id, state in last.items()
        if state
        in {
            "prepared_not_invoked",
            "worker_started_not_invoked",
            "worker_preflight_not_invoked",
        }
    )
    ledger_dispatches = len(invoked)
    return (
        0 if scripted else ledger_dispatches,
        ledger_dispatches if scripted else 0,
        ledger_dispatches,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    confirmation = parser.add_mutually_exclusive_group(required=True)
    confirmation.add_argument("--confirm-live", action="store_true")
    confirmation.add_argument(
        "--confirm-provider-free-scripted-v7", action="store_true"
    )
    confirmation.add_argument(
        "--confirm-provider-free-scripted-v8", action="store_true"
    )
    confirmation.add_argument(
        "--confirm-provider-free-scripted-v9", action="store_true"
    )
    confirmation.add_argument(
        "--confirm-provider-free-scripted-v10", action="store_true"
    )
    parser.add_argument("--expected-scripted-fixture-sha256")
    parser.add_argument("--cycle-directory", type=Path, required=True)
    parser.add_argument("--source-database", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--expected-checkpoint-sha", required=True)
    parser.add_argument("--expected-cycle-id", required=True)
    parser.add_argument("--expected-task-id", required=True)
    parser.add_argument("--expected-authorization-sha256", required=True)
    parser.add_argument("--maximum-provider-calls", type=int, required=True)
    parser.add_argument(
        "--provider-free-test-failpoint",
        choices=sorted(_PROVIDER_FREE_TEST_FAILPOINTS),
    )
    args = parser.parse_args()
    scripted_provider_free = (
        args.confirm_provider_free_scripted_v7
        or args.confirm_provider_free_scripted_v8
        or args.confirm_provider_free_scripted_v9
        or args.confirm_provider_free_scripted_v10
    )
    scripted_mode_version = (
        "v10"
        if args.confirm_provider_free_scripted_v10
        else "v9"
        if args.confirm_provider_free_scripted_v9
        else "v8"
        if args.confirm_provider_free_scripted_v8
        else "v7"
    )
    execution_mode = (
        f"provider_free_scripted_{scripted_mode_version}"
        if scripted_provider_free
        else "live_one_shot"
    )
    if scripted_provider_free:
        if args.expected_scripted_fixture_sha256 != SCRIPTED_JOB4_FIXTURE_SHA256:
            parser.error(
                "--expected-scripted-fixture-sha256 must match the frozen scripted fixture"
            )
        if args.provider_free_test_failpoint is not None and (
            not (
                args.confirm_provider_free_scripted_v8
                or args.confirm_provider_free_scripted_v9
                or args.confirm_provider_free_scripted_v10
            )
            or args.maximum_provider_calls != 10
        ):
            parser.error(
                "provider-free test failpoints require scripted v8/v9/v10 and the ten-stage ceiling"
            )
    elif args.expected_scripted_fixture_sha256 is not None:
        parser.error("scripted fixture identity is forbidden in live mode")
    elif args.provider_free_test_failpoint is not None:
        parser.error("provider-free test failpoints are forbidden in live mode")
    fault_injector = _ProviderFreeTestFaultInjector(
        args.provider_free_test_failpoint
    )
    cycle = args.cycle_directory.resolve()
    source_db = args.source_database.resolve()
    runtime_root = args.runtime_root.resolve()
    if git_head(ROOT) != args.expected_checkpoint_sha:
        raise SystemExit("isolated worktree HEAD does not match the frozen checkpoint")
    manifest = json.loads((cycle / "CYCLE_MANIFEST.json").read_text(encoding="utf-8"))
    try:
        validate_job4_identity(
            manifest,
            expected_cycle_id=args.expected_cycle_id,
            expected_task_id=args.expected_task_id,
            expected_authorization_sha256=args.expected_authorization_sha256,
            maximum_provider_calls=args.maximum_provider_calls,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if not (cycle / "receipts" / "TRIGGER_SENT.json").is_file():
        raise SystemExit("Job 4 cannot start before the Pro trigger receipt")

    # This is the first mutable operation after immutable authorization.  Every
    # later setup, execution, cleanup, and publication operation is owned by
    # this one-shot transaction.
    try:
        transaction = ContinuousJob4TerminalTransactionV1.begin(
            cycle_directory=cycle,
            cycle_id=args.expected_cycle_id,
            task_id=args.expected_task_id,
            authorization_sha256=args.expected_authorization_sha256,
            runtime_root=runtime_root,
        )
    except ContinuousJob4TransactionError as error:
        raise SystemExit(str(error)) from error
    if transaction.state == "committed":
        raise SystemExit("Job 4 terminal publication is already committed")
    if transaction.state == "frozen":
        transaction.publish_frozen()
        return 0 if transaction.frozen_result().get("status") == "completed" else 1

    recovery_terminalization = not transaction.is_new
    capability_container = ContinuousJob4CapabilityContainerV1.restricted(
        entrypoint_id="continuous_planner_validator_job4",
        entrypoint_path=Path(__file__),
    )
    capability_ledger = capability_container.evidence
    capability_boundary_evidence = capability_container.boundary_evidence
    provider_calls = 0
    scripted_transport_invocations = 0
    ledger_dispatches = 0
    if recovery_terminalization:
        (
            provider_calls,
            scripted_transport_invocations,
            ledger_dispatches,
        ) = _recovered_ledger_counts(
            runtime_root / "PROVIDER_CALL_LEDGER.jsonl",
            scripted=scripted_provider_free,
        )
    result: dict[str, Any] = {
        "schema_version": "cera.continuous_planner_validator_job4_detail.v4",
        "cycle_id": args.expected_cycle_id,
        "task_id": args.expected_task_id,
        "authorization_sha256": args.expected_authorization_sha256,
        "started_at": utc_now(),
        "status": "running",
        "execution_status": "running",
        "execution_mode": execution_mode,
        "scripted_fixture_id": (
            SCRIPTED_JOB4_FIXTURE_ID if scripted_provider_free else None
        ),
        "scripted_fixture_sha256": (
            SCRIPTED_JOB4_FIXTURE_SHA256 if scripted_provider_free else None
        ),
        "provider_calls": provider_calls,
        "scripted_transport_invocations": scripted_transport_invocations,
        "retry_count": 0,
        "fallback_count": 0,
        "operational_counters": capability_ledger.operational_counters.to_dict(),
        "capability_ledger": capability_ledger.to_dict(),
        "capability_ledger_sha256": capability_ledger.sha256,
        "capability_boundary_evidence": capability_boundary_evidence.to_dict(),
        "capability_boundary_evidence_sha256": (
            capability_boundary_evidence.sha256
        ),
        "active_runtime_before": None,
        "active_runtime_before_error_type": None,
        "turns": [],
        "calls": [],
        "pro_polls": [],
        "source_database_sha256_before": None,
        "copy_database_sha256_before": None,
        "copy_database_integrity": None,
        "copy_database_foreign_key_findings": None,
        "runtime_root": str(runtime_root),
        "root_terminal_transaction": {
            "schema_version": transaction.SCHEMA_VERSION,
            "semantic_reentry_allowed": False,
            "recovery_terminalization": recovery_terminalization,
        },
    }
    root_diagnostic: ContinuousRootDiagnosticRecorder | None = None
    call_ledger: ContinuousProviderCallLedger | None = None
    copied_db = runtime_root / "hanezawa_human_test_disposable.sqlite3"
    source_hash_before: str | None = None
    copy_hash_before: str | None = None
    integrity: str | None = None
    foreign_key_count: int | None = None
    world: ContinuousWorldStore | None = None
    branch_root: Path | None = None
    lifecycle_root = runtime_root / "provider_workspaces"
    active_runtime_before: dict[str, Any] | None = None
    active_runtime_before_error_type: str | None = None
    planner_session: ContinuousSessionCoordinator | None = None
    validator_session: ContinuousSessionCoordinator | None = None
    job_thread_lineage: ContinuousThreadLineageLedger | None = None
    planner_handle = None
    validator_handle = None
    harness = None
    scripted_harness_holder: dict[str, JobHarness] = {}

    if recovery_terminalization:
        _record_terminal_failure(
            result,
            RuntimeError("interrupted terminal transaction"),
            stage="restart_recovery",
            message=(
                "An interrupted Job 4 identity was terminalized without repeating "
                "semantic or provider work."
            ),
        )
    else:
        try:
            capability_container.require_enforced()
            if (
                (cycle / "source" / "JOB4_REPORT.md").exists()
                or (cycle / "source" / "JOB4_RESULT.json").exists()
                or runtime_root.exists()
            ):
                raise RuntimeError("refusing to overwrite pre-existing Job 4 evidence")
            root_diagnostic = ContinuousRootDiagnosticRecorder(runtime_root)
            if not source_db.is_file():
                error = FileNotFoundError(
                    "Hanezawa human-test SQLite source is unavailable"
                )
                root_diagnostic.record_failure(
                    "source_database", "inspect_source_database", error
                )
                raise error
            fault_injector.hit("call_ledger_construction")
            call_ledger = ContinuousProviderCallLedger(
                runtime_root / "PROVIDER_CALL_LEDGER.jsonl",
                maximum_calls=args.maximum_provider_calls,
            )
            fault_injector.hit("source_hash")
            source_hash_before = root_diagnostic.run(
                "source_database",
                "hash_source_database",
                lambda: bytes_sha256(source_db.read_bytes()),
            )
            result["source_database_sha256_before"] = source_hash_before
            fault_injector.hit("disposable_copy")
            root_diagnostic.run(
                "source_database",
                "copy_disposable_database",
                lambda: shutil.copy2(source_db, copied_db),
            )
            copy_hash_before = root_diagnostic.run(
                "source_database",
                "hash_disposable_database_before",
                lambda: bytes_sha256(copied_db.read_bytes()),
            )
            result["copy_database_sha256_before"] = copy_hash_before
            fault_injector.hit("sqlite_open")
            connection = root_diagnostic.run(
                "source_database",
                "open_disposable_database_read_only",
                lambda: sqlite3.connect(
                    f"file:{copied_db.as_posix()}?mode=ro", uri=True
                ),
            )
            try:
                fault_injector.hit("sqlite_integrity")
                integrity = root_diagnostic.run(
                    "source_database",
                    "check_disposable_database_integrity",
                    lambda: connection.execute("PRAGMA integrity_check").fetchone()[0],
                )
                fault_injector.hit("sqlite_foreign_keys")
                foreign_keys = root_diagnostic.run(
                    "source_database",
                    "check_disposable_database_foreign_keys",
                    lambda: connection.execute("PRAGMA foreign_key_check").fetchall(),
                )
                foreign_key_count = len(foreign_keys)
            finally:
                try:
                    root_diagnostic.run(
                        "source_database",
                        "close_disposable_database",
                        connection.close,
                    )
                except Exception:
                    connection.close()
            result["copy_database_integrity"] = integrity
            result["copy_database_foreign_key_findings"] = foreign_key_count
            fault_injector.hit("world_construction")
            world = root_diagnostic.run(
                "world_construction",
                "construct_continuous_world_store",
                lambda: ContinuousWorldStore(runtime_root / "worlds"),
            )
            fault_injector.hit("world_seeding")
            root_diagnostic.run(
                "world_seeding", "seed_disposable_world", lambda: seed_world(world, ROOT)
            )
            branch_root = world.branch_root(WORLD_ID, BRANCH_ID)
            fault_injector.hit("lifecycle_directory")
            root_diagnostic.run(
                "lifecycle_directory",
                "create_provider_workspace_root",
                lifecycle_root.mkdir,
            )
            try:
                fault_injector.hit("active_profile")
                active_runtime_before = root_diagnostic.run(
                    "active_profile",
                    "inspect_active_runtime_profile",
                    active_runtime_status,
                )
            except Exception as exc:
                active_runtime_before_error_type = type(exc).__name__
            result["active_runtime_before"] = active_runtime_before
            result["active_runtime_before_error_type"] = (
                active_runtime_before_error_type
            )
            if active_runtime_before_error_type is not None:
                raise RuntimeError(
                    "active runtime profile inspection failed before Job 4"
                )
            if scripted_provider_free:
                for pre_submission_failpoint in (
                    "sdk_import_pre_submission",
                    "account_inspection_pre_submission",
                    "backend_construction_pre_submission",
                    "session_construction_pre_submission",
                    "stored_thread_construction_pre_submission",
                ):
                    fault_injector.hit(pre_submission_failpoint)
                harness = execute_scripted_job4(
                    cycle=cycle,
                    world=world,
                    lifecycle_root=lifecycle_root,
                    call_ledger=call_ledger,
                    root_diagnostic=root_diagnostic,
                    result=result,
                    fault_injector=fault_injector,
                    harness_holder=scripted_harness_holder,
                )
                if args.provider_free_test_failpoint == (
                    "accepted_session_synchronization"
                ):
                    result["accepted_session_synchronized"] = False
                fault_injector.hit("accepted_session_synchronization")
                planner_handle = harness.planner_handle
                validator_handle = harness.validator_handle
                raise _ScriptedExecutionComplete()

            def import_codex_sdk():
                from openai_codex import Codex, CodexConfig

                return Codex, CodexConfig

            Codex, CodexConfig = root_diagnostic.run(
                "sdk_capability", "import_codex_sdk", import_codex_sdk
            )
            with ExitStack() as stack:
                codex_context = root_diagnostic.run(
                    "backend_construction",
                    "construct_codex_client",
                    lambda: Codex(
                        CodexConfig(config_overrides=("mcp_servers={}",), env={})
                    ),
                )
                codex = root_diagnostic.run(
                    "backend_context",
                    "enter_codex_client_context",
                    lambda: stack.enter_context(codex_context),
                )
                account = root_diagnostic.run(
                    "account_inspection", "inspect_codex_account", codex.account
                )
                if account.account is None:
                    raise RuntimeError("ChatGPT Codex session is unavailable")
                planner_backend = root_diagnostic.run(
                    "backend_construction",
                    "construct_planner_backend",
                    lambda: OpenAICodexStoredThreadBackend(
                        codex=codex,
                        model="gpt-5.6-sol",
                        cwd=str(lifecycle_root),
                        base_instructions=(
                            _BASE_INSTRUCTIONS_BY_ROLE["scene_reasoner"]
                            + "\n\n"
                            + PLANNER_STABLE_INSTRUCTIONS
                        ),
                        service_name="cera_continuous_job4_planner",
                    ),
                )
                validator_backend = root_diagnostic.run(
                    "backend_construction",
                    "construct_validator_backend",
                    lambda: OpenAICodexStoredThreadBackend(
                        codex=codex,
                        model="gpt-5.6-terra",
                        cwd=str(lifecycle_root),
                        base_instructions=(
                            _BASE_INSTRUCTIONS_BY_ROLE[
                                "scene_realization_verifier"
                            ]
                            + "\n\n"
                            + VALIDATOR_STABLE_INSTRUCTIONS
                        ),
                        service_name="cera_continuous_job4_validator",
                    ),
                )
                planner_session = root_diagnostic.run(
                    "session_construction",
                    "construct_planner_session",
                    lambda: ContinuousSessionCoordinator(
                        compatibility(world, ContinuousSessionRole.PLANNER),
                        CodexContinuousStoredSessionPort(planner_backend),
                        base_instructions=planner_backend.base_instructions,
                    ),
                )
                validator_session = root_diagnostic.run(
                    "session_construction",
                    "construct_validator_session",
                    lambda: ContinuousSessionCoordinator(
                        compatibility(world, ContinuousSessionRole.VALIDATOR),
                        CodexContinuousStoredSessionPort(validator_backend),
                        base_instructions=validator_backend.base_instructions,
                    ),
                )
                job_thread_lineage = ContinuousThreadLineageLedger()
                planner_session.attach_thread_lineage(
                    job_thread_lineage,
                    purpose="primary_planner",
                )
                validator_session.attach_thread_lineage(
                    job_thread_lineage,
                    purpose="primary_validator",
                )
                planner_handle = root_diagnostic.run(
                    "stored_thread_construction",
                    "ensure_planner_stored_thread",
                    lambda: planner_session.ensure_session().provider_thread_id,
                )
                validator_handle = root_diagnostic.run(
                    "stored_thread_construction",
                    "ensure_validator_stored_thread",
                    lambda: validator_session.ensure_session().provider_thread_id,
                )
                root_diagnostic.run(
                    "role_separation",
                    "assert_materialized_role_separation",
                    lambda: assert_separate_role_sessions(
                        planner_session, validator_session
                    ),
                )
                harness = JobHarness(
                    source_root=ROOT,
                    cycle=cycle,
                    world=world,
                    planner_session=planner_session,
                    validator_session=validator_session,
                    planner_handle=planner_handle,
                    validator_handle=validator_handle,
                    lifecycle_root=lifecycle_root,
                    call_ledger=call_ledger,
                    root_diagnostic=root_diagnostic,
                )
                try:
                    execute_job4_schedule(
                        harness=harness,
                        world=world,
                        planner_session=planner_session,
                        planner_handle=planner_handle,
                        validator_handle=validator_handle,
                        result=result,
                        scripted_provider_free=False,
                    )
                finally:
                    archived, archival_evidence, lineage_receipt = (
                        _terminalize_known_thread_sessions(
                            thread_lineage=job_thread_lineage,
                            planner_session=(
                                planner_session
                                if harness is None
                                else harness.planner_session
                            ),
                            validator_session=validator_session,
                            reason="live_job4_complete",
                        )
                    )
                    result["thread_archival"] = archived
                    result["thread_archival_evidence"] = archival_evidence
                    result["thread_lineage"] = lineage_receipt.to_dict()
                    result["thread_lineage_sha256"] = (
                        lineage_receipt.receipt_sha256
                    )
                    if not all(archived.values()):
                        raise RuntimeError("stored canary thread archival failed")
        except _ScriptedExecutionComplete:
            pass
        except BaseException as exc:
            if harness is None:
                harness = scripted_harness_holder.get("harness")
            if (
                not scripted_provider_free
                and job_thread_lineage is not None
                and "thread_lineage" not in result
            ):
                try:
                    archived, archival_evidence, lineage_receipt = (
                        _terminalize_known_thread_sessions(
                            thread_lineage=job_thread_lineage,
                            planner_session=(
                                planner_session
                                if harness is None
                                else harness.planner_session
                            ),
                            validator_session=validator_session,
                            reason="live_job4_setup_or_execution_failed",
                        )
                    )
                    result["thread_archival"] = archived
                    result["thread_archival_evidence"] = archival_evidence
                    result["thread_lineage"] = lineage_receipt.to_dict()
                    result["thread_lineage_sha256"] = (
                        lineage_receipt.receipt_sha256
                    )
                except BaseException as cleanup_error:
                    exc = StateConflictError(
                        "live Job 4 failed and physical thread closure failed"
                    )
                    exc.__cause__ = cleanup_error
            if root_diagnostic is not None:
                try:
                    root_diagnostic.record_failure(
                        "terminal_lifecycle", "terminalize_job4_failure", exc
                    )
                except Exception:
                    pass
            _record_terminal_failure(
                result,
                exc,
                stage=(
                    harness.call_records[-1]["label"]
                    if harness is not None and harness.call_records
                    else "pre_provider"
                ),
            )

    if harness is not None:
        result["calls"] = harness.call_records
        result["pro_polls"] = harness.poll_records
        provider_calls = harness.provider_calls
        scripted_transport_invocations = harness.scripted_transport_invocations
        result["provider_calls"] = provider_calls
        result["scripted_transport_invocations"] = scripted_transport_invocations
        result["continuous_thread_hashes_verified"] = all(
            call.get("result", {})
            .get("operation_telemetry", {})
            .get("provider_thread_id_sha256")
            == call.get("expected_provider_thread_sha256")
            for call in harness.call_records
            if call.get("owner") in {"planner", "validator"}
            and call.get("status") in {"passed", "scripted_provider_free_passed"}
        )
    if call_ledger is not None:
        ledger_dispatches = call_ledger.dispatched_call_count
    try:
        result["source_database_sha256_after"] = bytes_sha256(source_db.read_bytes())
        result["source_database_hash_after_error_type"] = None
    except Exception as exc:
        result["source_database_sha256_after"] = None
        result["source_database_hash_after_error_type"] = type(exc).__name__
    try:
        result["copy_database_sha256_after"] = bytes_sha256(copied_db.read_bytes())
        result["copy_database_hash_after_error_type"] = None
    except Exception as exc:
        result["copy_database_sha256_after"] = None
        result["copy_database_hash_after_error_type"] = type(exc).__name__
    result["source_database_unchanged"] = (
        source_hash_before is not None
        and result["source_database_sha256_after"] == source_hash_before
    )
    result["copy_database_unchanged"] = (
        copy_hash_before is not None
        and result["copy_database_sha256_after"] == copy_hash_before
    )
    active_runtime_after_sha256: str | None = None
    active_profile_inspection_status = "failed"
    try:
        result["active_runtime_after"] = active_runtime_status()
        result["active_runtime_after_error_type"] = None
        candidate = result["active_runtime_after"].get("profile_sha256")
        if (
            active_runtime_before is not None
            and isinstance(candidate, str)
            and len(candidate) == 64
        ):
            active_runtime_after_sha256 = candidate
            active_profile_inspection_status = "verified"
    except Exception as exc:
        result["active_runtime_after"] = {
            "valid": False,
            "diagnostic": "active runtime validation failed after Job 4",
        }
        result["active_runtime_after_error_type"] = type(exc).__name__
    active_runtime_before_sha256 = (
        active_runtime_before.get("profile_sha256")
        if active_runtime_before is not None
        else None
    )
    if not isinstance(active_runtime_before_sha256, str) or len(
        active_runtime_before_sha256
    ) != 64:
        active_runtime_before_sha256 = None
        active_profile_inspection_status = "failed"
    thread_archival_evidence = _complete_thread_archival_evidence(result)
    thread_lineage = _complete_thread_lineage_evidence(result, world=world)
    thread_archival = {
        role: thread_archival_evidence[role].verified
        for role in ("planner", "validator")
    }
    result["accepted_final_sequences_injected"] = (
        len(result.get("turns", [])) == 3
        and all(
            value.get("accepted_final_injected") for value in result.get("turns", [])
        )
    )
    if world is not None and branch_root is not None:
        try:
            result["world_active_sha256"] = world.tree_sha256(
                branch_root / "ACTIVE"
            )
            result["world_active_hash_error_type"] = None
        except Exception as exc:
            result["world_active_sha256"] = None
            result["world_active_hash_error_type"] = type(exc).__name__
            _record_terminal_failure(
                result,
                exc,
                stage="terminal_world_evidence",
                message="Terminal world evidence could not be verified.",
            )
    else:
        result["world_active_sha256"] = None
        result["world_active_hash_error_type"] = "Unavailable"
    postconditions = ContinuousJob4PostconditionsV1(
        execution_mode=execution_mode,
        source_database_sha256_before=source_hash_before,
        source_database_sha256_after=result["source_database_sha256_after"],
        disposable_database_sha256_before=copy_hash_before,
        disposable_database_sha256_after=result["copy_database_sha256_after"],
        database_integrity_check=integrity,
        database_foreign_key_findings=foreign_key_count,
        active_profile_sha256_before=active_runtime_before_sha256,
        active_profile_sha256_after=active_runtime_after_sha256,
        active_profile_inspection_status=active_profile_inspection_status,
        thread_archival=thread_archival,
        accepted_session_synchronized=bool(
            result.get("accepted_session_synchronized", False)
        ),
        accepted_final_sequences_injected=result[
            "accepted_final_sequences_injected"
        ],
        call_ledger_dispatches=ledger_dispatches,
        scripted_transport_invocations=scripted_transport_invocations,
    )
    execution_status = result.get("execution_status")
    if execution_status not in {"completed", "failed"}:
        execution_status = "failed"
    capability_ledger = capability_container.evidence
    capability_boundary_evidence = capability_container.boundary_evidence
    result["operational_counters"] = (
        capability_ledger.operational_counters.to_dict()
    )
    result["capability_ledger"] = capability_ledger.to_dict()
    result["capability_ledger_sha256"] = capability_ledger.sha256
    result["capability_boundary_evidence"] = (
        capability_boundary_evidence.to_dict()
    )
    result["capability_boundary_evidence_sha256"] = (
        capability_boundary_evidence.sha256
    )
    terminal = ContinuousJob4TerminalEvidenceV5.build(
        execution_status=execution_status,
        provider_calls=provider_calls,
        capability_ledger=capability_ledger,
        capability_boundary_evidence=capability_boundary_evidence,
        postconditions=postconditions,
        thread_archival_evidence=thread_archival_evidence,
        thread_lineage=thread_lineage,
    )
    _apply_terminal_evidence(result, terminal=terminal)
    canonical_result = freeze_terminal_publication(
        transaction,
        result,
        task_id=args.expected_task_id,
        recovery_terminalization=recovery_terminalization,
        fault_injector=fault_injector,
    )
    return 0 if canonical_result["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
