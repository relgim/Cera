from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from cera.cognition import (
    AutonomyApplicationV1,
    CharacterAutonomyMode,
    CognitionPlanV1,
    CognitionValidationContextV1,
    DecisionItemLinkV1,
    DecisionRecordV1,
    KnowledgeCertainty,
    LogicRoute,
    ObserverFrameV1,
    PerceivedFactV1,
    ResponseLayersV1,
    UserDirectionDisposition,
    validate_cognition_plan,
)
from cera.continuous.evidence import RequestEvidenceBindingRegistry
from cera.continuous.sessions import ContinuousSessionRole
from cera.continuous.world_mcp import (
    WORLD_MCP_SERVER_NAME,
    ContinuousWorldMcpBridge,
    ContinuousWorldToolDispatcher,
)
from cera.errors import ContractValidationError, StateConflictError
from cera.pi_scene.context import initial_hanezawa_doorway_seed
from cera.pi_scene.contracts import SceneRoute
from cera.pi_scene.http_contracts import LeanSceneRequestControlsV1
from cera.pi_scene.retrieval import BranchRetrievalService
from cera.pi_scene.retrieval_tools import (
    NAMED_RETRIEVAL_TOOLS,
    BoundNamedRetrievalTools,
    NamedRetrievalRequestBindingV1,
    RetrievalProviderRole,
    current_dossier_accepted_head,
)
from cera.pi_scene.store import LeanSceneStore
from cera.pi_scene.world_runtime import PiSceneChatWorldResolver
from cera.pi_scene.world_workspace import PiSceneWorldWorkspaceManager
from cera.sequence_first.contracts import (
    ItemKind,
    ProtectedSourceClaimV1,
    SequenceDraftV1,
    SequenceFirstTurnSemanticInputV1,
    SequenceItemV1,
)
from cera.serialization import canonical_sha256, domain_sha256, text_sha256

ROOT = Path(__file__).resolve().parents[1]
GENESIS_ROOT = ROOT / "genesis" / "packages"
SAKURA = "character:sakura_hanezawa"
HANA = "character:hana_hanezawa"


def _controls(session_id: str) -> LeanSceneRequestControlsV1:
    return LeanSceneRequestControlsV1(
        schema_version=LeanSceneRequestControlsV1.SCHEMA_VERSION,
        session_id=session_id,
        character_autonomy="both",
    )


def _turn() -> SequenceFirstTurnSemanticInputV1:
    return SequenceFirstTurnSemanticInputV1(
        exact_current_source="Ted asks who is at the door.",
        current_source_key="source:current",
        protected_source_claims=(
            ProtectedSourceClaimV1(
                claim_key="current_request",
                exact_text="Ted asks who is at the door.",
            ),
        ),
        known_character_ids=("character:ted", SAKURA),
        accepted_present_character_ids=("character:ted", SAKURA),
        explicitly_authorized_remote_character_ids=(),
        current_public_scene_state="Ted and Sakura are by the closed door.",
        prior_realized_sequence=None,
        character_deltas=(),
        evidence_records=(),
        approved_targets=(),
        unresolved_threads=(),
        hard_boundaries=(),
    )


def _plan(evidence_key: str) -> CognitionPlanV1:
    sequence = SequenceDraftV1(
        items=(
            SequenceItemV1(
                item_key="sakura_checks",
                kind=ItemKind.ACTION,
                concise_meaning="Sakura checks before changing the threshold.",
                owner_response_semantics="She verifies before opening.",
                owner_id=SAKURA,
                evidence_keys=("source:current",),
            ),
        ),
        durable_changes=(),
        presence_changes=(),
        resulting_public_state="The threshold remains closed during verification.",
        unresolved_threads=("The visitor remains unverified.",),
        stopping_boundary="Stop after Sakura asks for identifying information.",
    )
    decision = DecisionRecordV1(
        decision_key="sakura_verifies",
        owner_id=SAKURA,
        causal_trigger_refs=(evidence_key,),
        observer_frame=ObserverFrameV1(
            directly_perceived=(
                PerceivedFactV1(
                    source_ref=evidence_key,
                    concise_perception="She has reason to protect the threshold.",
                    certainty=KnowledgeCertainty.HIGH,
                ),
            ),
            inferred_meanings=("Verification is prudent.",),
            unavailable_or_ambiguous=("The visitor's identity is unresolved.",),
        ),
        perceived_event_meaning="The request could change household access.",
        knowledge_certainty=KnowledgeCertainty.HIGH,
        personal_and_social_meaning="Household protection outweighs convenience.",
        response_layers=ResponseLayersV1(
            immediate_involuntary_reaction="Her attention sharpens.",
            conscious_interpretation="The visitor is not yet verified.",
            subconscious_pressure="Protective habit favors caution.",
            considered_judgment="A question is safer than opening.",
        ),
        selected_intent="Verify before changing access.",
        concise_decision_basis="The exact character context supports caution.",
        decisive_factor_refs=(evidence_key,),
        material_pressures=(),
        autonomy_application=AutonomyApplicationV1(
            mind_precedence_applied=True,
            body_precedence_applied=True,
            user_direction_disposition=UserDirectionDisposition.PROPOSED_OUTCOME,
            overwhelming_pressure_kind=None,
            concise_effect="Sakura's logic controls her response.",
        ),
        anticipated_immediate_effect="The door remains closed while she verifies.",
        close_alternative=None,
        uncertainty=KnowledgeCertainty.MODERATE,
    )
    return CognitionPlanV1(
        sequence=sequence,
        decision_records=(decision,),
        decision_item_links=(
            DecisionItemLinkV1(
                item_key="sakura_checks",
                decision_key="sakura_verifies",
            ),
        ),
        provisional_dependencies=(),
    )


class NamedRetrievalToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.world_root = self.root / "worlds"
        self.manager = PiSceneWorldWorkspaceManager(self.world_root, GENESIS_ROOT)
        self.resolver = PiSceneChatWorldResolver(
            manager=self.manager,
            store=LeanSceneStore(self.world_root),
            scope_root=self.root / "scopes",
            base_seed=initial_hanezawa_doorway_seed(),
        )
        resolved = self.resolver.resolve_turn(
            route=SceneRoute.ORDINARY,
            source="Hello?",
            messages=({"role": "user", "content": "Hello?"},),
            controls=_controls("chat-retrieval"),
        )
        self.workspace = resolved.scope.workspace
        self.public_record: dict[str, object] | None = None
        for path in sorted(
            (self.workspace.branch_root / "ACTIVE" / "GenesisRecords").rglob("*.json")
        ):
            projection = json.loads(path.read_text(encoding="utf-8"))
            record = projection.get("record")
            if (
                str(projection.get("visibility", "public")).casefold() == "public"
                and isinstance(record, dict)
                and isinstance(record.get("record_id"), str)
            ):
                self.public_record = record
                break
        if self.public_record is None:
            self.fail("accepted Genesis fixture has no public exact record")

    def _dispatcher(
        self,
        request_id: str,
        *private_character_ids: str,
        role: RetrievalProviderRole = RetrievalProviderRole.PLANNER,
    ):
        return self.manager.mcp_factory(self.workspace).dispatcher(
            request_id=request_id,
            role=role,
            private_character_ids=tuple(private_character_ids),
            maximum_calls=16,
        )

    def test_named_surface_is_bounded_semantic_and_keeps_generic_tools(self) -> None:
        dispatcher = self._dispatcher("request:named-surface", SAKURA)
        self.assertEqual(dispatcher.tool_names, NAMED_RETRIEVAL_TOOLS)

        turn = dispatcher.invoke("get_turn_context", {"character_ids": [SAKURA]})
        self.assertEqual(
            [value["character_id"] for value in turn["data"]["character_dossiers"]],
            [SAKURA],
        )
        self.assertIn(
            turn["data"]["character_dossiers"][0]["evidence_ref"],
            turn["evidence_refs"],
        )
        self.assertNotIn("world_id", turn["data"])
        self.assertGreaterEqual(len(turn["evidence_refs"]), 3)
        self.assertTrue(
            all(value.startswith("binding_record_") for value in turn["evidence_refs"])
        )

        searched = dispatcher.invoke(
            "search_evidence",
            {
                "terms": [self.public_record["record_id"]],
                "character_id": None,
                "limit": 5,
            },
        )
        self.assertEqual(
            searched["data"]["records"][0]["record_id"],
            self.public_record["record_id"],
        )
        self.assertIn(
            searched["data"]["records"][0]["evidence_ref"],
            searched["evidence_refs"],
        )
        exact = dispatcher.invoke(
            "get_exact_record", {"record_id": self.public_record["record_id"]}
        )
        self.assertEqual(exact["data"]["record"], self.public_record)

        calls = (
            ("get_character_context", {"character_id": SAKURA}),
            ("get_relationship_context", {"character_id": SAKURA}),
            ("get_memory_context", {"character_id": SAKURA}),
            ("get_thread_context", {}),
            ("get_voice_examples", {"character_id": SAKURA}),
            ("get_craft_context", {}),
        )
        for index, (tool_name, arguments) in enumerate(calls, start=1):
            isolated = self._dispatcher(f"request:surface-{index}", SAKURA)
            self.assertEqual(isolated.invoke(tool_name, arguments)["tool"], tool_name)

    def test_request_mcp_advertises_the_named_tools_only_for_named_binding(self) -> None:
        named = self._dispatcher("request:mcp-binding", SAKURA)
        with ContinuousWorldMcpBridge(named) as bridge:
            self.assertEqual(bridge.runtime_binding.enabled_tools, named.tool_names)

        generic = self.manager.mcp_factory(self.workspace).dispatcher(maximum_calls=4)
        self.assertEqual(
            generic.tool_names,
            ("cera_world_list", "cera_world_search", "cera_world_read"),
        )
        self.assertEqual(
            generic.binding_sha256,
            domain_sha256(
                "cera.continuous_world_mcp.v1",
                {
                    "branch_root_sha256": text_sha256(
                        str(self.workspace.branch_root.resolve()).casefold()
                    ),
                    "world_id": self.workspace.world_id,
                    "branch_id": self.workspace.branch_id,
                    "role": "planner",
                    "current_turn_id": None,
                    "tools": generic.tool_names,
                    "maximum_calls": 4,
                },
            ),
        )

    def test_private_owner_is_rejected_before_search_term_matching(self) -> None:
        dispatcher = self._dispatcher("request:private-scope", SAKURA)
        handler = dispatcher.additional_tool_handler
        assert handler is not None
        with patch.object(handler.service, "search_evidence") as searched:  # type: ignore[attr-defined]
            with self.assertRaisesRegex(PermissionError, "private retrieval scope"):
                dispatcher.invoke(
                    "search_evidence",
                    {"terms": ["secret"], "character_id": HANA, "limit": 5},
                )
        searched.assert_not_called()

        recorder = self._dispatcher(
            "request:recorder-role",
            SAKURA,
            role=RetrievalProviderRole.RECORDER,
        )
        recorder_handler = recorder.additional_tool_handler
        assert recorder_handler is not None
        with patch.object(  # type: ignore[attr-defined]
            recorder_handler.service,
            "search_evidence",
        ) as recorder_search:
            with self.assertRaisesRegex(PermissionError, "not authorized for this role"):
                recorder.invoke(
                    "search_evidence",
                    {"terms": ["secret"], "character_id": SAKURA, "limit": 5},
                )
        recorder_search.assert_not_called()

        with self.assertRaisesRegex(ContractValidationError, "request is invalid"):
            dispatcher.invoke(
                "cera_world_search",
                {
                    "terms": ["secret"],
                    "knowledge_owner_id": HANA,
                    "limit": 5,
                },
            )

    def test_discovered_key_is_citable_only_in_its_exact_request(self) -> None:
        first = self._dispatcher("request:evidence-a", SAKURA)
        first_result = first.invoke(
            "get_character_context", {"character_id": SAKURA}
        )
        first_key = first_result["evidence_refs"][0]
        validate_cognition_plan(
            _plan(first_key),
            turn=_turn(),
            context=CognitionValidationContextV1(
                autonomy_mode=CharacterAutonomyMode.BOTH,
                logic_route=LogicRoute.ORDINARY,
                available_evidence_refs=("source:current", first_key),
            ),
        )

        second = self._dispatcher("request:evidence-b", SAKURA)
        second_result = second.invoke(
            "get_character_context", {"character_id": SAKURA}
        )
        second_key = second_result["evidence_refs"][0]
        self.assertNotEqual(first_key, second_key)
        with self.assertRaisesRegex(ContractValidationError, "unavailable evidence"):
            validate_cognition_plan(
                _plan(first_key),
                turn=_turn(),
                context=CognitionValidationContextV1(
                    autonomy_mode=CharacterAutonomyMode.BOTH,
                    logic_route=LogicRoute.ORDINARY,
                    available_evidence_refs=("source:current", second_key),
                ),
            )

        other_owner = self._dispatcher("request:evidence-owner", HANA)
        with self.assertRaisesRegex(PermissionError, "private retrieval scope"):
            other_owner.invoke(
                "get_character_context", {"character_id": SAKURA}
            )

    def test_stale_accepted_checkpoint_fails_before_a_second_result(self) -> None:
        dispatcher = self._dispatcher("request:stale-head", SAKURA)
        dispatcher.invoke("get_character_context", {"character_id": SAKURA})
        index_path = (
            self.workspace.branch_root
            / "DERIVED"
            / "CurrentCharacterDossiers"
            / "INDEX.json"
        )
        index = json.loads(index_path.read_text(encoding="utf-8"))
        index["accepted_head_sha256"] = "a" * 64
        payload = {key: value for key, value in index.items() if key != "index_sha256"}
        index["index_sha256"] = canonical_sha256(payload)
        index_path.write_text(json.dumps(index), encoding="utf-8")
        with self.assertRaisesRegex(StateConflictError, "accepted checkpoint"):
            dispatcher.invoke("get_character_context", {"character_id": SAKURA})

    def test_failed_tool_result_cannot_allocate_a_citable_evidence_key(self) -> None:
        request_id = "request:failed-result"
        registry = RequestEvidenceBindingRegistry(
            world_id=self.workspace.world_id,
            branch_id=self.workspace.branch_id,
            turn_id=request_id,
        )
        service = BranchRetrievalService(self.workspace, maximum_calls=4)
        handler = BoundNamedRetrievalTools(
            service,
            binding=NamedRetrievalRequestBindingV1(
                schema_version=NamedRetrievalRequestBindingV1.SCHEMA_VERSION,
                request_id=request_id,
                world_id=self.workspace.world_id,
                branch_id=self.workspace.branch_id,
                accepted_head_sha256=current_dossier_accepted_head(service),
                role=RetrievalProviderRole.PLANNER,
                private_character_ids=(SAKURA,),
                maximum_calls=4,
                maximum_returned_bytes=4_096,
            ),
            evidence_registry=registry,
        )
        dispatcher = ContinuousWorldToolDispatcher(
            self.workspace.branch_root,
            ContinuousSessionRole.PLANNER,
            world_id=self.workspace.world_id,
            branch_id=self.workspace.branch_id,
            current_turn_id=request_id,
            maximum_calls=4,
            evidence_registry=registry,
            allowed_private_character_ids=(SAKURA,),
            additional_tool_handler=handler,
        )
        with self.assertRaisesRegex(StateConflictError, "returned-byte ceiling"):
            dispatcher.invoke("get_character_context", {"character_id": SAKURA})
        self.assertEqual(len(registry.bindings), 1)
        receipt = ContinuousWorldMcpBridge(dispatcher).finalize(
            SimpleNamespace(
                tool_names=("get_character_context",),
                tool_server_names=(WORLD_MCP_SERVER_NAME,),
            )
        )
        self.assertEqual(receipt["evidence_bindings"], [])


if __name__ == "__main__":
    unittest.main()
