from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cera.adult_pipeline.acceptance import AdultAcceptedTurnEnvelopeV1
from cera.adult_pipeline.contracts import (
    AdultContextFactV1,
    AdultCraftMode,
    AdultCraftQueryV1,
    AdultEntryReason,
    AdultProviderRole,
)
from cera.adult_pipeline.craft_catalog import CatalogAdultCraftRetrieval
from cera.adult_pipeline.integration import (
    AdultPipelineIntegrationV1,
    AdultScenePreparationV1,
)
from cera.adult_pipeline.pi_roles import (
    AdultRoleViewContextV1,
    LazyProtectedWriterViewMaterializer,
    PiDeepSeekAdultFilterPort,
    PiDeepSeekAdultScenePort,
    PiStructuredAdultRoleTransport,
    StructuredAdultRoleResultV1,
)
from cera.adult_pipeline.pipeline import AdultPipeline
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.operation_ledger import PiProviderOperationLedger
from cera.pi_scene.writer_view import WriterViewInputV1, WriterViewMaterializer
from cera.provider_dispatch_guard import PROVIDER_DISPATCH_DISABLED_ENV
from cera.providers.models import (
    ProviderRetryableFailureCategory,
    ProviderTransportError,
)
from cera.schema import from_mapping
from cera.serialization import canonical_json, canonical_sha256, text_sha256

PROTECTED_PROSE = "A protected candidate reaches its requested stopping boundary."


def _scene_wire() -> str:
    return canonical_json(
        {
            "decision_path": [
                {
                    "decision_key": "decision_one",
                    "character_id": "character:hana",
                    "concise_decision": "Hana makes the character-consistent next choice.",
                    "evidence_refs": ["evidence:public", "evidence:private"],
                }
            ],
            "exact_story_prose": PROTECTED_PROSE,
            "resulting_state": "The private interaction reaches a stable boundary.",
            "unresolved_threads": ["The next conversation remains open."],
            "next_route": "ordinary",
            "next_route_reason": "The adult sequence is complete for now.",
        }
    )


def _filter_wire() -> str:
    return canonical_json(
        {
            "verdict": "pass",
            "protected_record": {
                "events": [
                    {
                        "event_key": "decision_one",
                        "protected_summary": "The complete protected event is retained.",
                        "character_ids": ["character:hana"],
                        "durable_effects": ["Hana's private outlook changes."],
                        "knowledge_owner_ids": ["character:hana"],
                    }
                ],
                "current_data_uses": [
                    {
                        "evidence_ref": "evidence:public",
                        "decision_key": "decision_one",
                        "concise_use": "Current public state informed the decision.",
                    },
                    {
                        "evidence_ref": "evidence:private",
                        "decision_key": "decision_one",
                        "concise_use": "Private character context informed the decision.",
                    },
                ],
                "resulting_protected_state": "Protected continuity remains available to the adult role.",
                "unresolved_threads": ["The next conversation remains open."],
            },
            "codex_projection": {
                "events": [
                    {
                        "event_key": "decision_one",
                        "non_explicit_summary": "A private interaction reached a stable boundary.",
                        "lasting_story_meaning": "Hana's immediate outlook changed.",
                    }
                ],
                "presence_changes": [],
                "durable_effects": [
                    {
                        "effect_key": "hana_outlook",
                        "effect_kind": "character_development",
                        "source_event_key": "decision_one",
                        "subject_ids": ["character:hana"],
                        "non_explicit_effect": "Hana privately reassesses the interaction.",
                        "target_key": "development:hana_private_outlook",
                        "visibility": "character_private",
                        "knowledge_owner_id": "character:hana",
                    }
                ],
                "resulting_public_state": "The room is quiet after the private interaction.",
                "unresolved_threads": ["The next conversation remains open."],
            },
        }
    )


class _FakeStructuredTransport:
    external_provider_boundary = False

    def __init__(self) -> None:
        self.calls: list[tuple[AdultProviderRole, str, str]] = []

    def invoke_structured_role(self, **kwargs):  # type: ignore[no-untyped-def]
        role = kwargs["role"]
        view = kwargs["view"]
        source = (view.root / "USER_PROMPT.txt").read_text(encoding="utf-8")
        primary = (view.root / "ADULT_HANDOFF.json").read_text(encoding="utf-8")
        self.calls.append((role, source, primary))
        session_dir = Path(kwargs["session_dir"]).resolve()
        session_dir.mkdir(parents=True, exist_ok=True)
        session_id = f"session-{role.value}"
        return StructuredAdultRoleResultV1(
            raw_json=_scene_wire() if role is AdultProviderRole.SCENE else _filter_wire(),
            session_id=session_id,
            session_dir=session_dir,
            session_id_sha256=text_sha256(session_id),
            provider="offline-fake",
            model="offline-fake",
            provider_operations=2 if role is AdultProviderRole.SCENE else 3,
            finish_status="stop",
            request_binding_sha256=text_sha256(f"binding-{role.value}"),
            invocation_id=f"piop-test-{role.value}",
        )


class _ExternalStructuredTransport(_FakeStructuredTransport):
    external_provider_boundary = True


class _FakePiAdapter:
    external_provider_boundary = False
    provider = "deepseek"
    model = "deepseek-v4-flash"
    timeout_seconds = 60
    readable_debug = None

    def __init__(self, ledger: PiProviderOperationLedger, output: str) -> None:
        self.operation_ledger = ledger
        self.output = output
        self.command: tuple[str, ...] | None = None

    def command_for(self, request, *, system_prompt=None):  # type: ignore[no-untyped-def]
        self.command = ("fake-pi", "--system-prompt", system_prompt or "", request.prompt)
        return self.command

    def _process_runner(self, command, cwd, environment, timeout, on_line):  # type: ignore[no-untyped-def]
        del command, cwd, environment, timeout
        events = [
            {"type": "session", "id": "structured-session"},
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": {"input": 10, "output": 1},
                    "stopReason": "toolUse",
                    "content": [],
                },
            },
            {"type": "tool_execution_start", "toolName": "context", "toolCallId": "one"},
            {
                "type": "tool_execution_end",
                "toolName": "context",
                "toolCallId": "one",
                "isError": False,
            },
            {"type": "turn_start"},
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "usage": {"input": 12, "cacheRead": 8, "output": 20},
                    "stopReason": "stop",
                    "content": [{"type": "text", "text": self.output}],
                },
            },
        ]
        lines = [json.dumps(value) for value in events]
        for line in lines:
            on_line(line)
        return SimpleNamespace(returncode=0, stdout="\n".join(lines), stderr="")


class AdultPiIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.context = AdultRoleViewContextV1(
            world_id="world:adult-test",
            branch_id="branch:adult-test",
            scene_id="scene:adult-test",
            turn_id="turn:adult-test",
            candidate_id="candidate:adult-test",
            current_state={"location": "room"},
            characters={"character:hana": {"name": "Hana", "age": 38}},
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _integration(self) -> AdultPipelineIntegrationV1:
        transport = _FakeStructuredTransport()
        materializer = WriterViewMaterializer(self.root / "protected" / "views")
        session_root = self.root / "protected" / "sessions"
        scene = PiDeepSeekAdultScenePort(
            transport=transport,
            materializer=materializer,
            context=self.context,
            session_root=session_root,
        )
        filter_port = PiDeepSeekAdultFilterPort(
            transport=transport,
            materializer=materializer,
            context=self.context,
            session_root=session_root,
        )
        return AdultPipelineIntegrationV1(
            pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
            scene_port=scene,
            filter_port=filter_port,
            craft_retrieval=CatalogAdultCraftRetrieval(
                Path(__file__).parents[1] / "adult" / "catalog" / "adult_craft_v1"
            ),
        )

    def _request(self, integration: AdultPipelineIntegrationV1):  # type: ignore[no-untyped-def]
        return integration.prepare_scene_request(
            AdultScenePreparationV1(
                entry_reason=AdultEntryReason.CODEX_ADULT_HANDOFF,
                adult_handoff="Adult logic takes over at the exact current boundary.",
                exact_current_source="Continue the current private interaction.",
                accepted_safe_continuity="Hana is present in the current room.",
                accepted_protected_continuity=None,
                autonomy_mode="both",
                depth_mode="auto",
                current_context=(
                    AdultContextFactV1(
                        evidence_ref="evidence:public",
                        subject_id="character:hana",
                        authoritative_fact="Hana is present.",
                        visibility="public",
                    ),
                    AdultContextFactV1(
                        evidence_ref="evidence:private",
                        subject_id="character:hana",
                        authoritative_fact="Hana has private current context.",
                        visibility="adult_role_private",
                    ),
                ),
                craft_mode=AdultCraftMode.OFF,
                craft_concept_keys=(),
                craft_keyword_keys=(),
                hard_boundaries=("Preserve current branch authority.",),
            )
        )

    def test_full_fake_route_creates_restart_safe_acceptance_custody(self) -> None:
        integration = self._integration()
        request = self._request(integration)
        with patch.dict(os.environ, {PROVIDER_DISPATCH_DISABLED_ENV: "1"}, clear=False):
            execution = integration.execute(
                request_id="request:adult-test",
                candidate_id="candidate:adult-test",
                world_id=self.context.world_id,
                branch_id=self.context.branch_id,
                accepted_head_sha256=text_sha256("parent-head"),
                scene_request=request,
            )

        self.assertTrue(execution.result.eligible_for_atomic_acceptance)
        self.assertEqual(execution.result.scene.invocation.receipt.provider_operations, 2)
        self.assertEqual(execution.result.filtered.invocation.receipt.provider_operations, 3)
        self.assertNotEqual(
            execution.scene_session.session_id_sha256,
            execution.filter_execution.session_id_sha256,
        )
        transport = integration.scene_port.transport
        assert isinstance(transport, _FakeStructuredTransport)
        filter_primary = transport.calls[1][2]
        self.assertIn(PROTECTED_PROSE, filter_primary)
        for forbidden in (
            "world_id",
            "branch_id",
            "accepted_head",
            "transaction",
            "session_path",
            "sha256",
        ):
            self.assertNotIn(forbidden, filter_primary)
        envelope = execution.acceptance_envelope(
            accepted_turn_id=self.context.turn_id,
            parent_accepted_turn_id="turn:parent",
            scene_id=self.context.scene_id,
            generation=2,
        )
        self.assertEqual(envelope.exact_current_source, request.exact_current_source)
        self.assertEqual(envelope.promotion_bundle.exact_story_prose, PROTECTED_PROSE)
        self.assertEqual(envelope.recording_status, "complete_preaccept_filter")
        payload = json.loads(canonical_json(envelope))
        recovered = from_mapping(AdultAcceptedTurnEnvelopeV1, payload)
        self.assertEqual(recovered.envelope_sha256, envelope.envelope_sha256)
        self.assertTrue(
            (self.root / "protected" / "sessions" / "ADULT_SCENE_SESSION_BINDING.json").is_file()
        )
        self.assertTrue(
            (self.root / "protected" / "sessions" / "ADULT_FILTER_EXECUTION_BINDING.json").is_file()
        )

    def test_filter_wire_has_no_python_custody_fields_and_cannot_add_them(self) -> None:
        integration = self._integration()
        request = self._request(integration)
        transport = integration.scene_port.transport
        assert isinstance(transport, _FakeStructuredTransport)
        original = transport.invoke_structured_role

        def invoke_with_filter_custody(**kwargs):  # type: ignore[no-untyped-def]
            if kwargs["role"] is AdultProviderRole.SCENE:
                return original(**kwargs)  # type: ignore[no-untyped-call]
            session_dir = Path(kwargs["session_dir"]).resolve()
            session_dir.mkdir(parents=True, exist_ok=True)
            raw = json.loads(_filter_wire())
            raw["transaction_sha256"] = text_sha256("forbidden")
            return StructuredAdultRoleResultV1(
                raw_json=canonical_json(raw),
                session_id="filter-extra-field",
                session_dir=session_dir,
                session_id_sha256=text_sha256("filter-extra-field"),
                provider="offline-fake",
                model="offline-fake",
                provider_operations=1,
                finish_status="stop",
                request_binding_sha256=text_sha256("binding"),
                invocation_id="piop-test-filter-extra-field",
            )

        transport.invoke_structured_role = invoke_with_filter_custody  # type: ignore[method-assign]
        with self.assertRaises(ProviderTransportError) as raised:
            integration.execute(
                request_id="request:adult-test",
                candidate_id="candidate:adult-test",
                world_id=self.context.world_id,
                branch_id=self.context.branch_id,
                accepted_head_sha256=None,
                scene_request=request,
            )
        self.assertIs(
            raised.exception.retryable_failure_category,
            ProviderRetryableFailureCategory.PROVIDER_OUTPUT_INVALID,
        )

    def test_catalog_off_on_ex_are_bounded_and_never_have_route_authority(self) -> None:
        retrieval = CatalogAdultCraftRetrieval(
            Path(__file__).parents[1] / "adult" / "catalog" / "adult_craft_v1"
        )
        for mode in AdultCraftMode:
            query = AdultCraftQueryV1(
                schema_version=AdultCraftQueryV1.SCHEMA_VERSION,
                mode=mode,
                concept_keys=() if mode is AdultCraftMode.OFF else ("bodily_fluids",),
                keyword_keys=() if mode is AdultCraftMode.OFF else ("sound_effect",),
            )
            selected = retrieval.retrieve_adult_craft(query)
            self.assertEqual(selected.query_sha256, canonical_sha256(query))
            self.assertLessEqual(len(selected.excerpts), 12)
            self.assertNotIn("route", selected.__dataclass_fields__)
            if mode is AdultCraftMode.OFF:
                self.assertEqual(selected.excerpts, ())
            else:
                self.assertTrue(selected.excerpts)

    def test_provider_guard_precedes_protected_view_and_session_mutation(self) -> None:
        protected = self.root / "guarded-protected"
        materializer = LazyProtectedWriterViewMaterializer(protected / "views")
        transport = _ExternalStructuredTransport()
        scene = PiDeepSeekAdultScenePort(
            transport=transport,
            materializer=materializer,
            context=self.context,
            session_root=protected / "sessions",
        )
        filter_port = PiDeepSeekAdultFilterPort(
            transport=transport,
            materializer=materializer,
            context=self.context,
            session_root=protected / "sessions",
        )
        integration = AdultPipelineIntegrationV1(
            pipeline=AdultPipeline(scene=scene, filter_port=filter_port),
            scene_port=scene,
            filter_port=filter_port,
            craft_retrieval=CatalogAdultCraftRetrieval(
                Path(__file__).parents[1] / "adult" / "catalog" / "adult_craft_v1"
            ),
        )
        request = self._request(integration)
        with patch.dict(os.environ, {PROVIDER_DISPATCH_DISABLED_ENV: "1"}, clear=False):
            with self.assertRaisesRegex(StateConflictError, "external provider dispatch"):
                integration.execute(
                    request_id="request:adult-test",
                    candidate_id="candidate:adult-test",
                    world_id=self.context.world_id,
                    branch_id=self.context.branch_id,
                    accepted_head_sha256=None,
                    scene_request=request,
                )
        self.assertFalse(protected.exists())
        self.assertEqual(transport.calls, [])

    def test_concrete_structured_pi_transport_counts_the_complete_tool_loop(self) -> None:
        materializer = WriterViewMaterializer(self.root / "pi-view")
        view = materializer.materialize(
            WriterViewInputV1(
                world_id=self.context.world_id,
                branch_id=self.context.branch_id,
                scene_id=self.context.scene_id,
                turn_id=self.context.turn_id,
                candidate_id=self.context.candidate_id,
                route=SceneRoute.ADULT,
                user_prompt="Exact source.",
                primary_authority={"handoff": "exact"},
                current_state={"location": "room"},
                characters={},
                relationships={},
                recent_prose=(),
                relevant_memories={},
                voice_examples={},
                craft_index={"mode": "off"},
                accepted_records=(),
            )
        )
        ledger = PiProviderOperationLedger(
            (self.root / "ledger.jsonl").resolve(),
            maximum_operations=10,
            maximum_operations_per_invocation=4,
        )
        fake = _FakePiAdapter(ledger, _scene_wire())
        transport = PiStructuredAdultRoleTransport(fake)  # type: ignore[arg-type]
        result = transport.invoke_structured_role(
            role=AdultProviderRole.SCENE,
            view=view,
            candidate_id=self.context.candidate_id,
            session_dir=self.root / "pi-session",
            system_prompt="Return JSON.",
            prompt="Run.",
            accepted_parent_session=None,
        )
        self.assertEqual(result.provider_operations, 2)
        self.assertEqual(ledger.operation_count, 2)
        self.assertIn("--system-prompt", fake.command or ())

    def test_acceptance_envelope_detects_parent_head_drift(self) -> None:
        integration = self._integration()
        execution = integration.execute(
            request_id="request:adult-test",
            candidate_id="candidate:adult-test",
            world_id=self.context.world_id,
            branch_id=self.context.branch_id,
            accepted_head_sha256=text_sha256("parent-head"),
            scene_request=self._request(integration),
        )
        envelope = execution.acceptance_envelope(
            accepted_turn_id=self.context.turn_id,
            parent_accepted_turn_id="turn:parent",
            scene_id=self.context.scene_id,
            generation=2,
        )
        with self.assertRaisesRegex(ContractValidationError, "parent head changed"):
            replace(envelope, parent_accepted_head_sha256=text_sha256("other-head"))


if __name__ == "__main__":
    unittest.main()
