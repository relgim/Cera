from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

from cera.composer import (
    ComposerContextAssembler,
    ComposerContextKind,
    ComposerCoordinator,
    ComposerSourcePacket,
    ComposerSourceUnit,
    CompositionMode,
    RealizationKind,
    SceneComposerRequest,
)
from cera.contracts import BeatState, Visibility
from cera.evaluation import RealGenesisSandbox
from cera.evidence import EvidenceFetchRequest, EvidenceSearchRequest
from cera.genesis.compiler import GenesisCompiler
from cera.genesis.hanezawa_builder import (
    CHARACTER_IDS,
    V1_2_BUILD_SPEC,
    V1_2_EXPECTED_SOURCE_HASHES,
    build_hanezawa_genesis_v1_2,
    default_repository_paths,
    default_v1_2_repository_paths,
)
from cera.genesis.models import CreatorAuthorization, GenesisRecordType
from cera.genesis.repository import GenesisRepository
from cera.ids import IdKind, TypedId
from cera.kernel import TurnKernel
from cera.reasoner import ReasonerCoordinator
from cera.schema import from_mapping
from cera.serialization import bytes_sha256
from cera.storage import SQLiteAuthorityStore
import tests.test_real_genesis_integration as real_support


ROOT = Path(__file__).resolve().parents[1]
PATHS = default_v1_2_repository_paths(ROOT)
PARENT_PATHS = default_repository_paths(ROOT)
PACKAGE_MANIFEST = (
    ROOT
    / "genesis"
    / "cera_authority"
    / "HANEZAWA_V1_2_FINAL_ARTIFACT_MANIFEST.json"
)
PROMPT_SOURCE = (
    ROOT
    / "config"
    / "cera"
    / "prompts"
    / "character_specific_speech_realization_v1_2.txt"
)


class HanezawaGenesisV12ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.authorization = from_mapping(
            CreatorAuthorization,
            json.loads(
                PATHS["authorization_path"].read_text(encoding="utf-8")
            ),
        )
        cls.compiled = GenesisCompiler().compile(
            PATHS["package_root"],
            cls.authorization,
        )
        cls.records = cls.compiled.records
        cls.payloads = {
            record.record_id: json.loads(record.payload_json)
            for record in cls.records
        }
        superseded_in_revision = {
            old_id
            for record in cls.records
            for old_id in record.supersedes
            if any(old_id == candidate.record_id for candidate in cls.records)
        }
        cls.active_revision_records = tuple(
            record
            for record in cls.records
            if record.record_id not in superseded_in_revision
        )
        cls.corpus = "\n".join(
            f"{record.claim}\n{record.payload_json}"
            for record in cls.active_revision_records
        ).casefold()

    @classmethod
    def by_payload(cls, key: str, value: object):
        return next(
            record
            for record in cls.records
            if cls.payloads[record.record_id].get(key) == value
        )

    @classmethod
    def event(cls, event_id: str):
        return cls.by_payload("event_id", event_id)

    @classmethod
    def memory(cls, memory_id: str):
        return cls.by_payload("memory_id", memory_id)

    @classmethod
    def expression_modes(cls):
        return tuple(
            record for record in cls.records if "response_mode" in record.tags
        )

    def test_v1_2_sources_and_package_manifest_are_hash_bound(self) -> None:
        for filename, expected in V1_2_EXPECTED_SOURCE_HASHES.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    bytes_sha256(
                        (PATHS["source_root"] / filename).read_bytes()
                    ),
                    expected,
                )
        self.assertEqual(
            bytes_sha256(
                (
                    PATHS["source_root"]
                    / "HANEZAWA_CORE_GENESIS_V1_1.md"
                ).read_bytes()
            ),
            "edf16b1204a0b8c656b0418e39b41c3306a995dc70a324c939b507483f783053",
        )
        package = json.loads(PACKAGE_MANIFEST.read_text(encoding="utf-8"))
        for artifact in package["artifacts"]:
            path = ROOT / Path(artifact["path"])
            self.assertTrue(path.is_file(), artifact["path"])
            self.assertEqual(bytes_sha256(path.read_bytes()), artifact["sha256"])
            self.assertEqual(path.stat().st_size, artifact["bytes"])

    def test_revision_manifest_counts_and_parent(self) -> None:
        counts = Counter(record.record_type for record in self.records)
        self.assertEqual(self.compiled.manifest.revision_number, 2)
        self.assertEqual(
            self.compiled.manifest.parent_revision_id,
            V1_2_BUILD_SPEC.parent_revision_id,
        )
        self.assertEqual(counts[GenesisRecordType.FORMATIVE_EVENT], 79)
        self.assertEqual(counts[GenesisRecordType.MEMORY_SEED], 84)
        self.assertEqual(counts[GenesisRecordType.RELATIONSHIP_EDGE], 49)
        self.assertEqual(counts[GenesisRecordType.ADULT_ELIGIBILITY], 7)
        self.assertEqual(counts[GenesisRecordType.VISUAL_CANON], 7)
        self.assertEqual(
            sum("embodied_identity_profile" in record.tags for record in self.records),
            7,
        )
        self.assertEqual(len(self.expression_modes()), 77)
        self.assertEqual(
            sum(
                len(self.payloads[record.record_id]["style_examples"])
                for record in self.expression_modes()
            ),
            154,
        )

    def test_rebuild_is_byte_deterministic_and_v1_1_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rebuilt = build_hanezawa_genesis_v1_2(
                source_root=PATHS["source_root"],
                package_root=root / "package",
                authorization_path=root / "authorization.json",
                view_root=root / "views",
                source_integrity_path=root / "source_integrity.json",
            )
            for source in rebuilt.package_root.rglob("*"):
                if source.is_file():
                    relative = source.relative_to(rebuilt.package_root)
                    self.assertEqual(
                        source.read_bytes(),
                        (PATHS["package_root"] / relative).read_bytes(),
                        str(relative),
                    )
            for source in rebuilt.view_root.rglob("*"):
                if source.is_file():
                    relative = source.relative_to(rebuilt.view_root)
                    self.assertEqual(
                        source.read_bytes(),
                        (PATHS["view_root"] / relative).read_bytes(),
                        str(relative),
                    )
            self.assertEqual(
                rebuilt.authorization_path.read_bytes(),
                PATHS["authorization_path"].read_bytes(),
            )
            self.assertEqual(
                rebuilt.source_integrity_path.read_bytes(),
                PATHS["source_integrity_path"].read_bytes(),
            )
        parent_authorization = from_mapping(
            CreatorAuthorization,
            json.loads(
                PARENT_PATHS["authorization_path"].read_text(encoding="utf-8")
            ),
        )
        parent = GenesisCompiler().compile(
            PARENT_PATHS["package_root"],
            parent_authorization,
        )
        self.assertEqual(
            parent.manifest_sha256,
            "99e070eb66e926fc38a53102db1fd1915ae06d672157d2672de9f83b9907c6dc",
        )
        self.assertEqual(
            parent.bundle_sha256,
            "f4663b67b868f454159026670575d2508deb9ea866d88564a629050477b5ae4b",
        )

    def test_disposable_parent_child_install_retires_parent_snapshot(self) -> None:
        parent_authorization = from_mapping(
            CreatorAuthorization,
            json.loads(
                PARENT_PATHS["authorization_path"].read_text(encoding="utf-8")
            ),
        )
        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteAuthorityStore(Path(temporary) / "v1-2.sqlite3")
            repository = GenesisRepository(store)
            parent = repository.compile_and_install(
                PARENT_PATHS["package_root"],
                parent_authorization,
                transaction_id=TypedId(IdKind.TRANSACTION, "v1-2-parent"),
                idempotency_key="v1-2-parent",
            )
            child = repository.compile_and_install(
                PATHS["package_root"],
                self.authorization,
                transaction_id=TypedId(IdKind.TRANSACTION, "v1-2-child"),
                idempotency_key="v1-2-child",
            )
            replay = repository.compile_and_install(
                PATHS["package_root"],
                self.authorization,
                transaction_id=TypedId(IdKind.TRANSACTION, "v1-2-child"),
                idempotency_key="v1-2-child",
            )
            parent_ids = {
                record.record_id
                for record in store.active_genesis_records(
                    parent.receipt.revision_id
                )
            }
            active = store.active_genesis_records(child.receipt.revision_id)
            self.assertTrue(replay.exact_replay)
            self.assertEqual(replay.receipt, child.receipt)
            self.assertEqual(len(active), len(self.active_revision_records))
            self.assertFalse(parent_ids & {record.record_id for record in active})

    # V1.1 tests cover deterministic cases 1-30. These mirror V1.2 cases 31-50.
    def test_acceptance_31_attraction_is_not_automatically_objectification(self) -> None:
        self.assertIn(
            "attraction is not automatically objectification",
            self.corpus,
        )

    def test_acceptance_32_objectification_grants_no_consent_or_route(self) -> None:
        self.assertIn("objectification cannot establish arousal", self.corpus)
        self.assertIn("route selection", self.corpus)

    def test_acceptance_33_refusal_survives_love_response_and_latent_m(self) -> None:
        self.assertIn("refusal remains refusal", self.corpus)
        self.assertIn("latent m", self.corpus.replace("-", " "))
        self.assertIn("consent", self.corpus)

    def test_acceptance_34_examples_are_non_executable(self) -> None:
        for record in self.expression_modes():
            payload = self.payloads[record.record_id]
            self.assertTrue(payload["examples_are_non_executable"])
            self.assertTrue(
                payload["examples_must_not_be_copied_or_lightly_paraphrased"]
            )

    def test_acceptance_35_hana_aging_blame_is_owner_private_not_fact(self) -> None:
        record = self.by_payload("belief_id", "hana_aging_self_blame")
        self.assertEqual(record.owner_id, CHARACTER_IDS["Hana"])
        self.assertIs(record.visibility, Visibility.OWNER_PRIVATE)
        correction = self.by_payload(
            "objective_cause_assignment",
            "husband_choice",
        )
        self.assertIn("did not cause", correction.claim.casefold())

    def test_acceptance_36_unaware_daughters_support_without_affair_leak(self) -> None:
        records = tuple(
            record
            for record in self.records
            if "aging_support" in record.tags
            and "pre_disclosure" in record.tags
        )
        self.assertEqual(len(records), 6)
        for name in ("Mia", "Tomi", "Aoi", "Yuuni"):
            record = next(
                value for value in records if name.casefold() in value.tags
            )
            self.assertIn(
                "must not receive or reveal affair knowledge",
                self.payloads[record.record_id]["knowledge_constraint"],
            )

    def test_acceptance_37_post_disclosure_examples_are_conditionally_gated(self) -> None:
        records = tuple(
            record
            for record in self.records
            if "aging_support" in record.tags
            and "requires_valid_affair_disclosure" in record.tags
        )
        self.assertEqual(len(records), 6)
        self.assertTrue(
            all(
                self.payloads[record.record_id]["activation_requirement"]
                == "requires_valid_affair_disclosure"
                for record in records
            )
        )

    def test_acceptance_38_hana_intent_and_mia_shame_are_distinct_memories(self) -> None:
        hana = self.memory("H-M13")
        mia = self.memory("M-M11")
        self.assertEqual(hana.owner_id, CHARACTER_IDS["Hana"])
        self.assertEqual(mia.owner_id, CHARACTER_IDS["Mia"])
        self.assertNotEqual(hana.record_id, mia.record_id)
        self.assertIn("does not automatically know", hana.payload_json.casefold())
        self.assertIn("private verdict", mia.payload_json.casefold())

    def test_acceptance_39_mia_developmental_memory_is_private_and_non_graphic(self) -> None:
        record = self.memory("M-M11")
        self.assertEqual(record.owner_id, CHARACTER_IDS["Mia"])
        self.assertIs(record.visibility, Visibility.OWNER_PRIVATE)
        self.assertNotIn("graphic", record.claim.casefold())
        self.assertIn("MI10", record.payload_json)

    def test_acceptance_40_enne_personhood_and_future_response_remain_distinct(self) -> None:
        profile = self.by_payload("profile_id", "embodied_identity:enne")
        payload = self.payloads[profile.record_id]
        self.assertIn("anatomy", json.dumps(payload).casefold())
        self.assertIn("identity", json.dumps(payload).casefold())
        self.assertIn("unresolved", self.event("EN06").payload_json.casefold())

    def test_acceptance_41_tomi_objectification_preserves_athletic_identity(self) -> None:
        event = self.event("TA09")
        self.assertIn("athletic result", event.claim.casefold())
        memory = self.memory("T-M11")
        self.assertIn("achievement", memory.payload_json.casefold())
        self.assertIn("attraction is acceptable", memory.payload_json.casefold())

    def test_acceptance_42_aoi_presentation_does_not_establish_consent(self) -> None:
        profile = self.by_payload("profile_id", "embodied_identity:aoi")
        self.assertIn("presentation for permission", profile.payload_json.casefold())
        self.assertIn("actual desire", profile.payload_json.casefold())

    def test_acceptance_43_yuuni_adult_refusal_is_fully_authoritative(self) -> None:
        record = self.by_payload(
            "response_mode_id",
            "yuuni:sexual_refusal",
        )
        self.assertEqual(record.subject_ids, (CHARACTER_IDS["Yuuni"],))
        adult = next(
            value
            for value in self.records
            if value.record_type is GenesisRecordType.ADULT_ELIGIBILITY
            and CHARACTER_IDS["Yuuni"] in value.subject_ids
        )
        self.assertEqual(self.payloads[adult.record_id]["age"], 18)

    def test_acceptance_44_trust_fracture_modes_are_distinct(self) -> None:
        for name in CHARACTER_IDS:
            modes = {
                self.payloads[record.record_id]["response_mode"]
                for record in self.expression_modes()
                if name.casefold() in record.tags
            }
            self.assertTrue(
                {
                    "partial_trust_fracture",
                    "complete_trust_destruction",
                }.issubset(modes)
            )

    def test_acceptance_45_complete_trust_destruction_may_be_irreversible(self) -> None:
        self.assertIn("complete trust destruction may remain irreversible", self.corpus)

    def test_acceptance_46_rhetorical_domains_are_character_specific(self) -> None:
        signatures = tuple(
            record for record in self.records if "rhetorical_signature" in record.tags
        )
        self.assertEqual(len(signatures), 7)
        domains = {
            self.payloads[record.record_id]["metaphor_domains"]
            for record in signatures
        }
        self.assertEqual(len(domains), 7)

    def test_acceptance_47_only_selected_speakers_receive_expression_context(self) -> None:
        with RealGenesisSandbox.create(ROOT, revision="v1_2") as sandbox:
            harness = real_support.RealGenesisReasonerComposerTests("runTest")
            harness.sandbox = sandbox
            harness.kernel = TurnKernel(sandbox.service)
            harness.reasoner = ReasonerCoordinator(
                sandbox.service,
                harness.kernel,
            )
            harness.composer = ComposerCoordinator()
            harness.ted = sandbox.protected_user_id()
            responders = (CHARACTER_IDS["Sakura"], CHARACTER_IDS["Tomi"])
            records = tuple(
                self.by_payload("response_mode_id", mode)
                for mode in (
                    "sakura:moral_correction",
                    "tomi:self_dignity_defense",
                )
            )
            source = "Ted asks Sakura and Tomi to answer a demeaning body comment."
            prepared, reasoner_request, reasoner_result = (
                harness.reasoner_request_and_result(
                    "v1-2-expression-context",
                    responders,
                    source,
                    records,
                    fetch_sections=(
                        "response_mode",
                        "metaphor_domains",
                        "fidelity_rule",
                        "style_examples",
                    ),
                )
            )
            unit = ComposerSourceUnit(
                source_unit_id=prepared.request.source_units[0].source_unit_id,
                classification=prepared.request.source_units[0].classification,
                exact_text=source,
                protected_user_allowed_kinds=(RealizationKind.ACTION,),
                required_state=BeatState.ATTEMPTED,
                participant_ids=responders,
            )
            base = SceneComposerRequest(
                schema_version=SceneComposerRequest.SCHEMA_VERSION,
                prepared_turn=prepared,
                reasoner_outcome=reasoner_result.outcome,
                reasoner_receipt=reasoner_result.receipt,
                source_packet=ComposerSourcePacket(
                    mode=CompositionMode.ORDINARY,
                    source_sha256=prepared.request.source_sha256,
                    ordinary_units=(unit,),
                    protected_envelope=None,
                    reasoner_safe_ledger_sha256=(
                        reasoner_request.source_view.source_view_sha256
                    ),
                ),
                selected_npc_ids=responders,
                scene_scope="Provider-free V1.2 expression projection.",
                response_profile_version="hanezawa-v1-2-expression-v1",
                continuity_references=(),
                creator_event_coverage_required=False,
                hard_boundaries=(
                    "Preserve the selected meaning.",
                    "Do not copy Genesis examples.",
                ),
            )
            assembled = ComposerContextAssembler(sandbox.service).assemble(
                base,
                reasoner_result,
            )
            expression = tuple(
                block
                for block in assembled.request.realization_context.blocks
                if block.kind is ComposerContextKind.CHARACTER_EXPRESSION
            )
            self.assertEqual(len(expression), 2)
            self.assertEqual(
                {
                    block.applicable_character_ids[0]
                    for block in expression
                },
                set(responders),
            )
            self.assertNotIn(
                CHARACTER_IDS["Yuuni"],
                {
                    character_id
                    for block in assembled.request.realization_context.blocks
                    for character_id in block.applicable_character_ids
                },
            )

    def test_acceptance_48_prompt_is_hash_bound_and_forbids_copying(self) -> None:
        self.assertEqual(
            bytes_sha256(PROMPT_SOURCE.read_bytes()),
            "e61c8b9a287e6be1223e4a2a2aebcbd8d5da5e5433bee8de781d833149cc11cf",
        )
        prompt = PROMPT_SOURCE.read_text(encoding="utf-8").casefold()
        self.assertIn("never copy", prompt)
        self.assertIn("refusal", prompt)
        self.assertIn("partial trust fracture", prompt)

    def test_acceptance_49_examples_do_not_author_ted_or_future_events(self) -> None:
        contract = self.by_payload(
            "heading",
            "16.14 Runtime projection and DeepSeek Writer realization standard",
        )
        text = self.payloads[contract.record_id]["source_text"].casefold()
        self.assertIn("does not schedule", self.corpus)
        self.assertIn("infer ted", text)

    def test_acceptance_50_family_defense_preserves_target_agency(self) -> None:
        family_modes = tuple(
            record
            for record in self.expression_modes()
            if "family_defense" in record.tags
        )
        self.assertEqual(len(family_modes), 7)
        self.assertIn(
            "protection does not erase the target's agency",
            self.corpus,
        )

    def test_owner_private_expression_is_not_retrievable_by_another_character(self) -> None:
        with RealGenesisSandbox.create(ROOT, revision="v1_2") as sandbox:
            hana_record = self.by_payload(
                "belief_id",
                "hana_aging_self_blame",
            )
            mia_snapshot = sandbox.open_snapshot(
                "mia-cannot-read-hana-aging",
                scope=sandbox.character_scope("Mia"),
            )
            result = sandbox.service.search_evidence(
                mia_snapshot,
                EvidenceSearchRequest(
                    terms=("younger", "bride", "aging"),
                    entity_ids=(CHARACTER_IDS["Hana"],),
                    tags=("aging_self_blame",),
                    limit=5,
                ),
            )
            self.assertNotIn(
                hana_record.record_id,
                {value.metadata.record_id for value in result.references},
            )

    def test_expression_record_exact_sections_are_bounded_and_resolvable(self) -> None:
        with RealGenesisSandbox.create(ROOT, revision="v1_2") as sandbox:
            record = self.by_payload(
                "response_mode_id",
                "hana:partial_trust_fracture",
            )
            snapshot = sandbox.open_snapshot("hana-expression-exact")
            evidence_id = sandbox.evidence_id(snapshot, record.record_id)
            fetched = sandbox.service.fetch_evidence(
                snapshot,
                EvidenceFetchRequest(
                    (evidence_id,),
                    (
                        "response_mode",
                        "metaphor_domains",
                        "fidelity_rule",
                        "style_examples",
                    ),
                ),
            )
            sections = json.loads(fetched.exact_records[0].sections_json)
            self.assertEqual(
                sections["response_mode"],
                "partial_trust_fracture",
            )
            self.assertEqual(len(sections["style_examples"]), 2)
            self.assertLess(fetched.receipt.cumulative_snapshot_bytes, 16_384)

    def test_prompt_manifest_and_blind_review_artifacts_are_separated(self) -> None:
        prompt_manifest = json.loads(
            (
                ROOT / "config" / "cera" / "prompts" / "MANIFEST.json"
            ).read_text(encoding="utf-8")
        )
        entry = prompt_manifest["artifacts"][0]
        self.assertEqual(
            bytes_sha256((ROOT / entry["path"]).read_bytes()),
            entry["sha256"],
        )
        self.assertFalse(entry["runtime_filesystem_dependency"])

        suite_root = (
            ROOT / "evaluation" / "suites" / "hanezawa_voice_v1_2"
        )
        blind = json.loads(
            (suite_root / "BLIND_REVIEW_SPEC.json").read_text(encoding="utf-8")
        )
        answer = json.loads(
            (suite_root / "ANSWER_KEY.json").read_text(encoding="utf-8")
        )
        serialized_blind = json.dumps(blind).casefold()
        for name in CHARACTER_IDS:
            self.assertNotIn(name.casefold(), serialized_blind)
            self.assertNotIn(
                str(CHARACTER_IDS[name]).casefold(),
                serialized_blind,
            )
        self.assertEqual(blind["provider_calls"], 0)
        self.assertEqual(blind["story_authority_writes"], 0)
        self.assertFalse(answer["candidate_outputs_present"])
        self.assertFalse(answer["prompt_context_eligible"])
        self.assertEqual(len(answer["slot_mapping"]), 7)
        signatures = {
            self.payloads[record.record_id]["speaker_id"]: self.payloads[
                record.record_id
            ]["metaphor_domains"]
            for record in self.records
            if "rhetorical_signature" in record.tags
        }
        for slot, character_id in answer["slot_mapping"].items():
            self.assertEqual(
                answer["expected_signature_domains"][slot],
                signatures[character_id],
            )


if __name__ == "__main__":
    unittest.main()
