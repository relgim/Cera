from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.continuous.contracts import (
    CharacterRoleLedgerV1,
    DiagnosticGroundingStatus,
    DiagnosticViolationClassification,
    FinalFieldName,
    FinalFieldScopeV1,
    FinalInformationVisibility,
    FinalSequenceItemV1,
    FinalSequenceV1,
    LOCAL_KEY_JSON_PATTERN,
    ProtectedSemanticAdjudicationV1,
    ProtectedSemanticRelationKind,
    ReaderVerdictStatus,
    StoryRealizationKind,
    StoryRealizationSegmentV1,
    ValidatorSemanticStatus,
    ValidatorTaskMode,
)
from cera.continuous.provider import (
    CONTINUOUS_READER_ADAPTER_VERSION,
    CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
    CodexContinuousReaderPort,
    CodexContinuousValidatorPort,
    ContinuousSemanticValidatorDraftV4,
    ContinuousSemanticValidatorDraftV6,
    ContinuousSemanticValidatorDraftV7,
    ContinuousSemanticValidatorDraftV12,
    ContinuousSemanticValidatorDraftV13,
    ProviderAcceptedDecisionKind,
    ProviderAcceptedTurnDecisionDraftV5,
    ProviderAcceptedTurnDecisionDraftV6,
    ProviderConcernCreatorReviewDraftV1,
    ProviderConcernDecisionKind,
    ProviderConcernReviewDisposition,
    ProviderConcernTurnDecisionDraftV5,
    ProviderConcernTurnDecisionDraftV6,
    ProviderDiagnosticIssueOwner,
    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1,
    ProviderDiagnosticStorySegmentDraftV1,
    ProviderEventRecordDraftV1,
    ProviderFinalSequenceDraftV2,
    ProviderProtectedSemanticAdjudicationDraftV1,
    ProviderAcceptedReaderDecisionDraftV1,
    ProviderAcceptedReaderVerdictStatus,
    ProviderInconclusiveReaderDecisionDraftV1,
    ProviderInconclusiveReaderVerdictStatus,
    ProviderReaderIssueReferenceDraftV1,
    ProviderReaderVerdictDraftV1,
    ProviderReaderVerdictDraftV2,
    ProviderRejectedReaderDecisionDraftV1,
    ProviderRejectedReaderVerdictStatus,
    ProviderRejectedSemanticStatus,
    ProviderRejectedTurnDecisionDraftV2,
    ProviderRejectedTurnDecisionDraftV4,
    ProviderWriterRecallEligibility,
    _inject_python_owned_persistence_hashes,
    continuous_reader_route,
    continuous_reader_verdict_json_schema,
    continuous_scene_writer_draft_json_schema,
    continuous_semantic_validator_draft_json_schema,
    continuous_validator_route,
    historical_provider_reader_verdict_json_schema,
    historical_reader_verdict_json_schema,
    rich_planner_sequence_json_schema,
)
from cera.creator_review.models import (
    CreatorReviewAssessment,
    CreatorReviewSeverity,
    PublicationEligibility,
    ReviewIssueOwner,
)
from cera.errors import ContractValidationError
from cera.providers import (
    ProviderSchemaDialect,
    project_provider_output_schema,
    validate_provider_output_schema,
)
from cera.schema import from_mapping
from cera.serialization import canonical_sha256, text_sha256, to_primitive
from cera.continuous.record_policy import PERSISTENCE_POLICY_SHA256
from cera.continuous.prompting import (
    CONTINUOUS_READER_PROMPT_VERSION,
    READER_STABLE_INSTRUCTIONS,
)


STORY = "Hana set down the teacup."


def _roles() -> CharacterRoleLedgerV1:
    return CharacterRoleLedgerV1(
        action_owner_ids=("character:hana_hanezawa",),
        addressed_ids=("character:ted",),
    )


def _canonical_v4() -> ContinuousSemanticValidatorDraftV4:
    roles = _roles()
    segment = StoryRealizationSegmentV1(
        schema_version=StoryRealizationSegmentV1.SCHEMA_VERSION,
        segment_key="segment_entire_story",
        kind=StoryRealizationKind.ACTION,
        output_start=0,
        output_end=len(STORY),
        exact_text=STORY,
        roles=roles,
    )
    scopes = tuple(
        FinalFieldScopeV1(
            field_name=value,
            visibility=FinalInformationVisibility.PUBLIC,
            knowledge_owner_id=None,
            story_segment_keys=(segment.segment_key,),
            roles=roles,
        )
        for value in FinalFieldName
    )
    sequence = FinalSequenceV1(
        schema_version=FinalSequenceV1.SCHEMA_VERSION,
        sequence_id="sequence:hash_custody",
        accepted_turn_id="turn:hash_custody",
        items=(
            FinalSequenceItemV1(
                item_key="hash_custody_item",
                planner_beat_keys=("planner_beat",),
                story_segment_keys=(segment.segment_key,),
                realized_event=STORY,
                valid_deepseek_additions=("The motion remained measured.",),
                omitted_or_contradicted_details=(),
                private_state_owner_ids=(),
                knowledge_changes=("The action is publicly visible.",),
                material_changes=("The teacup is on the table.",),
                resulting_state="Hana awaits Ted's next supplied choice.",
                roles=roles,
                field_scopes=scopes,
            ),
        ),
        final_stop_state="Hana awaits Ted's next supplied choice.",
    )
    return ContinuousSemanticValidatorDraftV4(
        schema_version=ContinuousSemanticValidatorDraftV4.SCHEMA_VERSION,
        package_id="package:hash_custody",
        world_id="world:hash_custody",
        branch_id="branch:main",
        task_mode=ValidatorTaskMode.FINALIZE_TURN,
        semantic_status=ValidatorSemanticStatus.ACCEPTED,
        reason_codes=(),
        story_segments=(segment,),
        complete_final_sequence=ProviderFinalSequenceDraftV2.from_final_sequence(
            sequence
        ),
        creator_review=CreatorReviewAssessment(
            schema_version=CreatorReviewAssessment.SCHEMA_VERSION,
            severity=CreatorReviewSeverity.GOOD,
            publication_eligibility=PublicationEligibility.ACCEPT_ALLOWED,
            issue_owner=ReviewIssueOwner.NONE,
            reason_codes=(),
            creator_reason="The candidate preserves the complete bounded plan.",
            verifier_status="accepted",
        ),
        protected_semantic_adjudications=(
            ProtectedSemanticAdjudicationV1(
                schema_version=ProtectedSemanticAdjudicationV1.SCHEMA_VERSION,
                adjudication_key="adjudication_entire_story",
                segment_key=segment.segment_key,
                output_start=0,
                output_end=len(STORY),
                exact_text_sha256=text_sha256(STORY),
                protected_user_id="character:ted",
                relation=ProtectedSemanticRelationKind.ADDRESSED_BY_NPC,
                npc_assertion_owner_ids=("character:hana_hanezawa",),
                protected_user_source_claim_keys=(),
            ),
        ),
        event_record=ProviderEventRecordDraftV1(
            event_id="event:hash_custody",
            accepted_turn_id="turn:hash_custody",
            scene_id="scene:hash_custody",
            summary=STORY,
            final_sequence_item_keys=("hash_custody_item",),
            protected_user_source_claim_keys=(),
        ),
        optional_scene_summary=None,
    )


def _accepted_wire() -> ContinuousSemanticValidatorDraftV13:
    return ContinuousSemanticValidatorDraftV13.from_v4(_canonical_v4())


def _walk_named_properties(value: object, name: str, path: str = "$"):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key == name:
                found.append((child_path, child))
            found.extend(_walk_named_properties(child, name, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(
                _walk_named_properties(child, name, f"{path}[{index}]")
            )
    return found


def _all_property_paths(value: object, path: str = "$"):
    found = []
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            for key, child in properties.items():
                child_path = f"{path}.{key}"
                found.append((child_path, child))
                found.extend(_all_property_paths(child, child_path))
        items = value.get("items")
        if isinstance(items, dict):
            found.extend(_all_property_paths(items, path + "[]"))
        for union_name in ("anyOf", "oneOf"):
            for index, child in enumerate(value.get(union_name, ())):
                found.extend(
                    _all_property_paths(
                        child,
                        f"{path}.{union_name}[{index}]",
                    )
                )
    return found


class _Transport:
    def __init__(self, *, route, payload: dict, thread_id: str) -> None:
        self.route = route
        self.payload = payload
        self.runner = SimpleNamespace(provider_thread_id=thread_id)

    def invoke(self, _prompt: str, **kwargs):
        for name in (
            "on_worker_started",
            "on_worker_preflight",
            "on_transport_invoke",
        ):
            callback = kwargs.get(name)
            if callback is not None:
                callback()
        return SimpleNamespace(
            parsed_json=deepcopy(self.payload),
            receipt={"status": "completed"},
            operation_telemetry={"status": "completed"},
            tool_call_count=0,
            failed_tool_call_count=0,
            tool_names=(),
            tool_server_names=(),
        )


class RuntimeModelV3ValidatorHashCustodyTests(unittest.TestCase):
    def test_active_adjudication_wire_omits_and_rejects_hash(self) -> None:
        schema = continuous_semantic_validator_draft_json_schema()
        rendered = json.dumps(schema, sort_keys=True)
        self.assertNotIn("exact_text_sha256", rendered)
        payload = to_primitive(_accepted_wire())
        adjudication = payload["decision"]["protected_semantic_adjudications"][0]
        self.assertNotIn("exact_text_sha256", adjudication)
        adjudication["exact_text_sha256"] = text_sha256(STORY)
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(payload)
        with self.assertRaises(ContractValidationError):
            from_mapping(ContinuousSemanticValidatorDraftV13, payload)

    def test_python_derives_canonical_adjudication_hash(self) -> None:
        wire = from_mapping(
            ContinuousSemanticValidatorDraftV13,
            to_primitive(_accepted_wire()),
        )
        result = wire.compile(writer_story_text=STORY)
        adjudication = result.protected_semantic_adjudications[0]
        self.assertEqual(adjudication.exact_text_sha256, text_sha256(STORY))
        self.assertEqual(
            adjudication,
            _canonical_v4().protected_semantic_adjudications[0],
        )

    def test_all_five_turn_statuses_schema_decode_and_compile(self) -> None:
        accepted = _accepted_wire()
        accepted_decision = accepted.decision
        self.assertIsInstance(
            accepted_decision,
            ProviderAcceptedTurnDecisionDraftV6,
        )
        concern = ProviderConcernTurnDecisionDraftV6(
            schema_version=ProviderConcernTurnDecisionDraftV6.SCHEMA_VERSION,
            decision_kind=ProviderConcernDecisionKind.CONCERN,
            realization_segments=accepted_decision.realization_segments,
            complete_final_sequence=accepted_decision.complete_final_sequence,
            creator_review=ProviderConcernCreatorReviewDraftV1(
                schema_version=ProviderConcernCreatorReviewDraftV1.SCHEMA_VERSION,
                disposition=(
                    ProviderConcernReviewDisposition.CONCERN_ACCEPT_ALLOWED
                ),
                issue_owner=ProviderDiagnosticIssueOwner.PROMPT_MATERIAL,
                primary_reason_code="minor_pacing_concern",
                additional_reason_codes=(),
                creator_reason="The candidate is usable but pacing is compressed.",
                verifier_status="concern",
            ),
            protected_semantic_adjudications=(
                accepted_decision.protected_semantic_adjudications
            ),
            event_record=accepted_decision.event_record,
        )
        cases = [
            (accepted_decision, ValidatorSemanticStatus.ACCEPTED, True),
            (concern, ValidatorSemanticStatus.CONCERN, True),
        ]
        for status in (
            ProviderRejectedSemanticStatus.REJECTED,
            ProviderRejectedSemanticStatus.INCONCLUSIVE,
            ProviderRejectedSemanticStatus.ERROR,
        ):
            cases.append(
                (
                    ProviderRejectedTurnDecisionDraftV4(
                        schema_version=(
                            ProviderRejectedTurnDecisionDraftV4.SCHEMA_VERSION
                        ),
                        semantic_status=status,
                        primary_reason_code=f"seeded_{status.value}",
                        additional_reason_codes=(),
                        diagnostic_story_segments=(
                            ProviderDiagnosticStorySegmentDraftV1(
                                schema_version=(
                                    ProviderDiagnosticStorySegmentDraftV1.SCHEMA_VERSION
                                ),
                                segment_key="segment_entire_story",
                                kind=StoryRealizationKind.ACTION,
                                output_start=0,
                                output_end=len(STORY),
                                roles=_roles(),
                                grounding_status=(
                                    DiagnosticGroundingStatus.GROUNDED
                                ),
                                protected_user_source_claim_keys=(),
                            ),
                        ),
                        diagnostic_protected_semantic_adjudications=(
                            ProviderDiagnosticProtectedSemanticAdjudicationDraftV1(
                                schema_version=(
                                    ProviderDiagnosticProtectedSemanticAdjudicationDraftV1.SCHEMA_VERSION
                                ),
                                adjudication_key="adjudication_entire_story",
                                segment_key="segment_entire_story",
                                output_start=0,
                                output_end=len(STORY),
                                protected_user_id="character:ted",
                                relation=(
                                    ProtectedSemanticRelationKind.ADDRESSED_BY_NPC
                                ),
                                grounding_status=(
                                    DiagnosticGroundingStatus.GROUNDED
                                ),
                                violation_classification=(
                                    DiagnosticViolationClassification.NONE
                                ),
                                npc_assertion_owner_ids=(
                                    "character:hana_hanezawa",
                                ),
                                protected_user_source_claim_keys=(),
                            ),
                        ),
                        writer_recall_eligibility=(
                            ProviderWriterRecallEligibility.INELIGIBLE
                        ),
                        writer_recall_violations=(),
                    ),
                    ValidatorSemanticStatus(status.value),
                    False,
                )
            )
        schema = continuous_semantic_validator_draft_json_schema()
        for decision, expected_status, finalizes in cases:
            with self.subTest(status=expected_status.value):
                wire = replace(accepted, decision=decision)
                payload = to_primitive(wire)
                Draft202012Validator(schema).validate(payload)
                decoded = from_mapping(ContinuousSemanticValidatorDraftV13, payload)
                compiled = decoded.compile(writer_story_text=STORY)
                self.assertIs(compiled.semantic_status, expected_status)
                self.assertEqual(
                    compiled.finalization_package is not None,
                    finalizes,
                )
                if finalizes:
                    self.assertEqual(
                        compiled.protected_semantic_adjudications[
                            0
                        ].exact_text_sha256,
                        text_sha256(STORY),
                    )
                    self.assertFalse(compiled.diagnostic_story_segments)
                else:
                    self.assertEqual(
                        compiled.diagnostic_story_segments[0].exact_text_sha256,
                        text_sha256(STORY),
                    )
                    self.assertFalse(compiled.story_segments)

    def test_invalid_adjudication_and_segment_spans_fail_closed(self) -> None:
        accepted = _accepted_wire()
        decision = accepted.decision
        adjudication = decision.protected_semantic_adjudications[0]
        segment = decision.realization_segments[0]
        cases = {
            "unknown": replace(adjudication, segment_key="unknown_segment"),
            "empty": replace(adjudication, output_end=adjudication.output_start),
            "out_of_bounds": replace(adjudication, output_end=len(STORY) + 1),
        }
        for name, invalid in cases.items():
            with self.subTest(name=name), self.assertRaises(
                ContractValidationError
            ):
                replace(
                    accepted,
                    decision=replace(
                        decision,
                        protected_semantic_adjudications=(invalid,),
                    ),
                ).compile(writer_story_text=STORY)

        shorter = replace(
            segment,
            output_end=len(STORY) - 1,
        )
        with self.assertRaisesRegex(ContractValidationError, "do not cover"):
            replace(
                accepted,
                decision=replace(
                    decision,
                    realization_segments=(shorter,),
                ),
            ).compile(writer_story_text=STORY)

        shifted = replace(segment, output_start=1)
        with self.assertRaisesRegex(ContractValidationError, "not gap-free"):
            replace(
                accepted,
                decision=replace(decision, realization_segments=(shifted,)),
            ).compile(writer_story_text=STORY)

    def test_typed_writer_text_is_required_and_is_not_recovered_from_prompt(self) -> None:
        with self.assertRaisesRegex(ContractValidationError, "typed immutable"):
            _accepted_wire().compile(writer_story_text=None)
        altered = "X" + STORY[1:]
        compiled = _accepted_wire().compile(writer_story_text=altered)
        self.assertEqual(compiled.story_segments[0].exact_text, altered)

    def test_validator_port_derives_hash_and_terminalizes_ledger(self) -> None:
        payload = to_primitive(_accepted_wire())
        observed = []
        with TemporaryDirectory() as directory:
            ledger = ContinuousProviderCallLedger(Path(directory) / "ledger.jsonl")
            port = CodexContinuousValidatorPort(
                _Transport(
                    route=continuous_validator_route(
                        model="gpt-5.6-sol",
                        effort="medium",
                    ),
                    payload=payload,
                    thread_id="thread:validator_hash_custody",
                ),
                call_ledger=ledger,
                raw_result_observer=observed.append,
            )
            result = port.validate(
                "The prompt intentionally is not parsed for Writer text.",
                writer_story_text=STORY,
            )
            self.assertEqual(
                result.value.protected_semantic_adjudications[0].exact_text_sha256,
                text_sha256(STORY),
            )
            self.assertEqual(observed, [payload])
            self.assertEqual(ledger.events[-1]["state"], "typed_accepted")

            failing = CodexContinuousValidatorPort(
                _Transport(
                    route=continuous_validator_route(
                        model="gpt-5.6-sol",
                        effort="medium",
                    ),
                    payload=payload,
                    thread_id="thread:validator_hash_custody_failure",
                ),
                call_ledger=ledger,
            )
            with self.assertRaises(ContractValidationError):
                failing.validate("Prompt contains no usable bytes.", writer_story_text="Altered")
            self.assertEqual(
                ledger.events[-1]["state"],
                "provider_completed_post_validation_failed",
            )

    def test_active_validator_hash_and_schema_version_satisfiability(self) -> None:
        neutral = continuous_semantic_validator_draft_json_schema()
        projected = project_provider_output_schema(
            neutral,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        validate_provider_output_schema(
            projected,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        for schema in (neutral, projected):
            hashes = [
                (path, value)
                for path, value in _all_property_paths(schema)
                if path.endswith("_sha256")
            ]
            self.assertFalse(hashes)
            versions = _walk_named_properties(schema, "schema_version")
            self.assertTrue(versions)
            self.assertTrue(
                all(
                    set(value) == {"type", "const"}
                    and value["type"] == "string"
                    for _, value in versions
                )
            )
        self.assertEqual(
            tuple(value.value for value in FinalFieldName),
            (
                "realized_event",
                "valid_deepseek_additions",
                "knowledge_changes",
                "material_changes",
                "resulting_state",
            ),
        )
        self.assertEqual(
            CONTINUOUS_VALIDATOR_ADAPTER_VERSION,
            "cera.continuous_validator_adapter.v29",
        )

    def test_active_schema_rejects_provider_authored_prior_value_hash(self) -> None:
        schema = continuous_semantic_validator_draft_json_schema()
        payload = to_primitive(_accepted_wire())
        directive = {
            "schema_version": "cera.persistence_directive.v2",
            "directive_key": "record_hash_custody",
            "target_file": "Characters/Hana.json",
            "target_record_class": "character",
            "persistence_policy_sha256": PERSISTENCE_POLICY_SHA256,
            "target_record_id": "character:hana_hanezawa",
            "target_subject_ids": ["character:hana_hanezawa"],
            "expected_file_revision": 1,
            "operation": "add",
            "field_path": "/turn_claims/turn-hash-custody",
            "source_value_index": 0,
        }
        scope = payload["decision"]["complete_final_sequence"]["items"][0][
            "field_scopes"
        ][0]
        scope["persistence_directives"] = [directive]
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(payload)
        with self.assertRaises(ContractValidationError):
            from_mapping(ContinuousSemanticValidatorDraftV13, payload)

    def test_python_injects_add_and_exact_replace_prior_hashes(self) -> None:
        base_directive = {
            "target_file": "Characters/Hana.json",
            "field_path": "/reasoning_summary",
        }
        payload = {
            "decision": {
                "complete_final_sequence": {
                    "items": [
                        {
                            "field_scopes": [
                                {"persistence_directives": [base_directive]}
                            ]
                        }
                    ]
                }
            }
        }
        base_directive["operation"] = "add"
        injected = _inject_python_owned_persistence_hashes(
            payload,
            world_bridge=None,
        )
        injected_directive = injected["decision"]["complete_final_sequence"][
            "items"
        ][0]["field_scopes"][0]["persistence_directives"][0]
        self.assertIsNone(injected_directive["expected_prior_value_sha256"])
        self.assertNotIn("expected_prior_value_sha256", base_directive)

        with TemporaryDirectory() as directory:
            branch_root = Path(directory)
            target = branch_root / "ACTIVE" / "Characters" / "Hana.json"
            target.parent.mkdir(parents=True)
            prior = {"trust": "guarded", "notes": ["uneasy"]}
            target.write_text(
                json.dumps({"reasoning_summary": prior}),
                encoding="utf-8",
            )
            base_directive["operation"] = "replace"
            bridge = SimpleNamespace(
                dispatcher=SimpleNamespace(branch_root=branch_root)
            )
            injected = _inject_python_owned_persistence_hashes(
                payload,
                world_bridge=bridge,
            )
            injected_directive = injected["decision"][
                "complete_final_sequence"
            ]["items"][0]["field_scopes"][0]["persistence_directives"][0]
            self.assertEqual(
                injected_directive["expected_prior_value_sha256"],
                canonical_sha256(prior),
            )

    def test_python_rejects_provider_authored_prior_hash_before_typed_decode(self) -> None:
        payload = {
            "decision": {
                "complete_final_sequence": {
                    "items": [
                        {
                            "field_scopes": [
                                {
                                    "persistence_directives": [
                                        {
                                            "operation": "add",
                                            "expected_prior_value_sha256": (
                                                text_sha256("invented")
                                            ),
                                        }
                                    ]
                                }
                            ]
                        }
                    ]
                }
            }
        }
        with self.assertRaisesRegex(ContractValidationError, "provider authored"):
            _inject_python_owned_persistence_hashes(payload, world_bridge=None)

    def test_historical_validator_hash_reader_remains_available(self) -> None:
        historical = ContinuousSemanticValidatorDraftV6.from_v4(_canonical_v4())
        decoded = from_mapping(
            ContinuousSemanticValidatorDraftV6,
            to_primitive(historical),
        )
        self.assertEqual(
            decoded.compile().protected_semantic_adjudications[0].exact_text_sha256,
            text_sha256(STORY),
        )


class RuntimeModelV3ActiveProviderSurfaceAuditTests(unittest.TestCase):
    def test_every_active_version_and_hash_field_has_python_owned_custody(self) -> None:
        factories = {
            "planner": rich_planner_sequence_json_schema,
            "writer": continuous_scene_writer_draft_json_schema,
            "validator": continuous_semantic_validator_draft_json_schema,
            "reader": continuous_reader_verdict_json_schema,
        }
        for role, factory in factories.items():
            neutral = factory()
            projected = project_provider_output_schema(
                neutral,
                ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            ).provider_schema
            validate_provider_output_schema(
                projected,
                ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            )
            for surface_name, schema in (
                ("neutral", neutral),
                ("openai", projected),
            ):
                with self.subTest(role=role, surface=surface_name):
                    versions = _walk_named_properties(schema, "schema_version")
                    self.assertTrue(versions)
                    self.assertTrue(
                        all(
                            value.get("type") == "string"
                            and isinstance(value.get("const"), str)
                            and set(value) == {"type", "const"}
                            for _, value in versions
                        )
                    )
                    hashes = [
                        (path, value)
                        for path, value in _all_property_paths(schema)
                        if path.endswith("_sha256")
                    ]
                    self.assertTrue(
                        all(
                            path.endswith("persistence_policy_sha256")
                            and value
                            == {
                                "type": "string",
                                "const": PERSISTENCE_POLICY_SHA256,
                            }
                            for path, value in hashes
                        )
                    )


class RuntimeModelV3ReaderHashCustodyTests(unittest.TestCase):
    def _draft(
        self,
        status: ReaderVerdictStatus,
    ) -> ProviderReaderVerdictDraftV2:
        if status is ReaderVerdictStatus.ACCEPTED:
            decision = ProviderAcceptedReaderDecisionDraftV1(
                schema_version=ProviderAcceptedReaderDecisionDraftV1.SCHEMA_VERSION,
                verdict=ProviderAcceptedReaderVerdictStatus.ACCEPTED,
            )
        elif status is ReaderVerdictStatus.REJECTED:
            decision = ProviderRejectedReaderDecisionDraftV1(
                schema_version=ProviderRejectedReaderDecisionDraftV1.SCHEMA_VERSION,
                verdict=ProviderRejectedReaderVerdictStatus.REJECTED,
                reason_codes=("premature_closure",),
                issues=(
                ProviderReaderIssueReferenceDraftV1(
                    schema_version=(
                        ProviderReaderIssueReferenceDraftV1.SCHEMA_VERSION
                    ),
                    issue_code="premature_closure",
                    output_start=0,
                    output_end=len(STORY),
                    explanation="The scene closes before the planned exchange develops.",
                ),
                ),
            )
        else:
            decision = ProviderInconclusiveReaderDecisionDraftV1(
                schema_version=(
                    ProviderInconclusiveReaderDecisionDraftV1.SCHEMA_VERSION
                ),
                verdict=ProviderInconclusiveReaderVerdictStatus.INCONCLUSIVE,
                reason_codes=("insufficient_context",),
            )
        return ProviderReaderVerdictDraftV2(
            schema_version=ProviderReaderVerdictDraftV2.SCHEMA_VERSION,
            verdict_id=f"reader:{status.value}",
            world_id="world:hash_custody",
            branch_id="branch:main",
            turn_id="turn:hash_custody",
            candidate_id="candidate:hash_custody",
            decision=decision,
            scene_completeness_score=(
                30 if status is ReaderVerdictStatus.REJECTED else 90
            ),
            character_voice_score=90,
            dialogue_pacing_score=(
                40 if status is ReaderVerdictStatus.REJECTED else 90
            ),
            readability_score=90,
        )

    def test_active_reader_wire_omits_and_rejects_both_hash_classes(self) -> None:
        schema = continuous_reader_verdict_json_schema()
        rendered = json.dumps(schema, sort_keys=True)
        self.assertNotIn("story_text_sha256", rendered)
        self.assertNotIn("exact_text_sha256", rendered)
        payload = to_primitive(self._draft(ReaderVerdictStatus.REJECTED))
        payload["story_text_sha256"] = text_sha256(STORY)
        payload["decision"]["issues"][0]["exact_text_sha256"] = text_sha256(STORY)
        with self.assertRaises(ValidationError):
            Draft202012Validator(schema).validate(payload)
        with self.assertRaises(ContractValidationError):
            from_mapping(ProviderReaderVerdictDraftV2, payload)

    def test_all_reader_branches_derive_exact_hashes_without_rewrite(self) -> None:
        schema = continuous_reader_verdict_json_schema()
        forbidden = {
            "story_text",
            "replacement_prose",
            "rewritten_story_text",
            "events",
            "memory",
            "persistence",
        }
        self.assertFalse(forbidden & set(schema["properties"]))
        for status in ReaderVerdictStatus:
            with self.subTest(status=status.value):
                payload = to_primitive(self._draft(status))
                Draft202012Validator(schema).validate(payload)
                draft = from_mapping(ProviderReaderVerdictDraftV2, payload)
                verdict = draft.compile(writer_story_text=STORY)
                self.assertIs(verdict.verdict, status)
                self.assertEqual(verdict.story_text_sha256, text_sha256(STORY))
                for issue in verdict.issues:
                    self.assertEqual(issue.exact_text_sha256, text_sha256(STORY))

    def test_reader_invalid_spans_and_missing_typed_text_fail_closed(self) -> None:
        rejected = self._draft(ReaderVerdictStatus.REJECTED)
        self.assertIsInstance(rejected.decision, ProviderRejectedReaderDecisionDraftV1)
        issue = rejected.decision.issues[0]
        for name, invalid in {
            "empty": replace(issue, output_end=issue.output_start),
            "out_of_bounds": replace(issue, output_end=len(STORY) + 1),
        }.items():
            with self.subTest(name=name), self.assertRaises(
                ContractValidationError
            ):
                replace(
                    rejected,
                    decision=replace(rejected.decision, issues=(invalid,)),
                ).compile(
                    writer_story_text=STORY
                )
        with self.assertRaisesRegex(ContractValidationError, "requires immutable"):
            rejected.compile(writer_story_text="")

    def test_reader_port_derives_hashes_and_preserves_raw_and_ledger(self) -> None:
        payload = to_primitive(self._draft(ReaderVerdictStatus.REJECTED))
        observed = []
        with TemporaryDirectory() as directory:
            ledger = ContinuousProviderCallLedger(Path(directory) / "ledger.jsonl")
            route = continuous_reader_route(model="gpt-5.6-sol", effort="medium")
            port = CodexContinuousReaderPort(
                lambda: _Transport(
                    route=route,
                    payload=payload,
                    thread_id="thread:reader_hash_custody",
                ),
                call_ledger=ledger,
                raw_result_observer=observed.append,
            )
            result = port.review(
                "The prompt is not the byte-custody input.",
                writer_story_text=STORY,
            )
            self.assertEqual(result.value.story_text_sha256, text_sha256(STORY))
            self.assertEqual(
                result.value.issues[0].exact_text_sha256,
                text_sha256(STORY),
            )
            self.assertEqual(observed, [payload])
            self.assertEqual(ledger.events[-1]["state"], "typed_accepted")

    def test_reader_schemas_preflight_and_historical_canonical_reader_remains(self) -> None:
        neutral = continuous_reader_verdict_json_schema()
        projected = project_provider_output_schema(
            neutral,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        validate_provider_output_schema(
            projected,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        )
        for schema in (neutral, projected):
            self.assertFalse(
                [
                    path
                    for path, _ in _all_property_paths(schema)
                    if path.endswith("_sha256")
                ]
            )
            versions = _walk_named_properties(schema, "schema_version")
            self.assertTrue(versions)
            self.assertTrue(
                all(set(value) == {"type", "const"} for _, value in versions)
            )
        historical = historical_reader_verdict_json_schema()
        self.assertIn("story_text_sha256", historical["properties"])
        issue_schema = historical["properties"]["issues"]["items"]
        self.assertIn("exact_text_sha256", issue_schema["properties"])
        self.assertEqual(
            CONTINUOUS_READER_ADAPTER_VERSION,
            "cera.continuous_reader_adapter.v4",
        )
        self.assertEqual(
            CONTINUOUS_READER_PROMPT_VERSION,
            "cera.continuous_reader_prompt.v5",
        )
        self.assertIn(LOCAL_KEY_JSON_PATTERN[1:-1], READER_STABLE_INSTRUCTIONS)

    def test_reader_schema_and_python_share_closed_local_key_surface(self) -> None:
        neutral = continuous_reader_verdict_json_schema()
        projected = project_provider_output_schema(
            neutral,
            ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
        ).provider_schema
        for schema in (neutral, projected):
            branches = schema["properties"]["decision"]["anyOf"]
            rejected = next(
                value
                for value in branches
                if "issues" in value["properties"]
            )["properties"]
            inconclusive = next(
                value
                for value in branches
                if "reason_codes" in value["properties"]
                and "issues" not in value["properties"]
            )["properties"]
            self.assertEqual(
                rejected["reason_codes"]["items"]["pattern"],
                LOCAL_KEY_JSON_PATTERN,
            )
            self.assertEqual(
                rejected["issues"]["items"]["properties"]["issue_code"][
                    "pattern"
                ],
                LOCAL_KEY_JSON_PATTERN,
            )
            self.assertEqual(
                inconclusive["reason_codes"]["items"]["pattern"],
                LOCAL_KEY_JSON_PATTERN,
            )
            validate_provider_output_schema(
                schema,
                ProviderSchemaDialect.OPENAI_STRUCTURED_OUTPUT_V1,
            )

        payload = to_primitive(self._draft(ReaderVerdictStatus.REJECTED))
        payload["decision"]["reason_codes"] = ["PROTECTED_USER_BOUNDARY_VIOLATION"]
        payload["decision"]["issues"][0]["issue_code"] = (
            "PROTECTED_USER_ACTION_INVENTED"
        )
        for schema in (neutral, projected):
            with self.assertRaises(ValidationError):
                Draft202012Validator(schema).validate(payload)
        with self.assertRaises(ContractValidationError):
            from_mapping(ProviderReaderVerdictDraftV2, payload).compile(
                writer_story_text=STORY
            )

        payload["decision"]["reason_codes"] = [
            "protected_user_boundary_violation"
        ]
        payload["decision"]["issues"][0]["issue_code"] = (
            "protected_user_action_invented"
        )
        for schema in (neutral, projected):
            Draft202012Validator(schema).validate(payload)
        compiled = from_mapping(ProviderReaderVerdictDraftV2, payload).compile(
            writer_story_text=STORY
        )
        self.assertEqual(
            compiled.reason_codes,
            ("protected_user_boundary_violation",),
        )
        self.assertEqual(
            compiled.issues[0].issue_code,
            "protected_user_action_invented",
        )

    def test_flat_v1_acceptance_reason_mismatch_is_historical_only(self) -> None:
        payload = {
            "schema_version": ProviderReaderVerdictDraftV1.SCHEMA_VERSION,
            "verdict_id": "reader:historical_mismatch",
            "world_id": "world:hash_custody",
            "branch_id": "branch:main",
            "turn_id": "turn:hash_custody",
            "candidate_id": "candidate:hash_custody",
            "verdict": "accepted",
            "reason_codes": ["minimum_quality_met"],
            "issues": [],
            "scene_completeness_score": 100,
            "character_voice_score": 100,
            "dialogue_pacing_score": 100,
            "readability_score": 100,
        }
        Draft202012Validator(
            historical_provider_reader_verdict_json_schema()
        ).validate(payload)
        with self.assertRaisesRegex(
            ContractValidationError,
            "accepted Reader verdict cannot carry rejection issues",
        ):
            from_mapping(ProviderReaderVerdictDraftV1, payload).compile(
                writer_story_text=STORY
            )
        with self.assertRaises(ValidationError):
            Draft202012Validator(continuous_reader_verdict_json_schema()).validate(
                payload
            )


if __name__ == "__main__":
    unittest.main()
