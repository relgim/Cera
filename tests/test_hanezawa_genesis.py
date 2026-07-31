from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import tempfile
import unittest

from cera.genesis.compiler import GenesisCompiler
from cera.genesis.hanezawa_builder import (
    CHARACTER_IDS,
    CORE_FILENAME,
    EXPECTED_SOURCE_HASHES,
    PROMPT_OVERLAY_FILENAME,
    VISUAL_FILENAME,
    build_hanezawa_genesis,
    default_repository_paths,
)
from cera.genesis.models import (
    AdultEligibility,
    CreatorAuthorization,
    GenesisRecordType,
    StoryStartPresence,
)
from cera.genesis.repository import GenesisRepository
from cera.ids import IdKind, TypedId
from cera.schema import from_mapping
from cera.serialization import bytes_sha256
from cera.storage import SQLiteAuthorityStore


ROOT = Path(__file__).resolve().parents[1]
PATHS = default_repository_paths(ROOT)


class HanezawaGenesisValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.authorization = from_mapping(
            CreatorAuthorization,
            json.loads(PATHS["authorization_path"].read_text(encoding="utf-8")),
        )
        cls.compiled = GenesisCompiler().compile(
            PATHS["package_root"], cls.authorization
        )
        cls.records = cls.compiled.records
        cls.payloads = {record.record_id: json.loads(record.payload_json) for record in cls.records}
        superseded = {
            old_id for record in cls.records for old_id in record.supersedes
        }
        cls.active_records = tuple(
            record for record in cls.records if record.record_id not in superseded
        )
        cls.corpus = "\n".join(
            record.claim + "\n" + record.payload_json for record in cls.active_records
        ).casefold()

    @classmethod
    def typed(cls, record_type: GenesisRecordType):
        return tuple(record for record in cls.active_records if record.record_type is record_type)

    @classmethod
    def event(cls, event_id: str):
        return next(
            record
            for record in cls.typed(GenesisRecordType.FORMATIVE_EVENT)
            if cls.payloads[record.record_id].get("event_id") == event_id
        )

    @classmethod
    def memory(cls, memory_id: str):
        return next(
            record
            for record in cls.typed(GenesisRecordType.MEMORY_SEED)
            if cls.payloads[record.record_id].get("memory_id") == memory_id
        )

    @classmethod
    def relation(cls, from_name: str, to_name: str):
        from_id = CHARACTER_IDS[from_name]
        to_id = CHARACTER_IDS.get(to_name)
        return next(
            record
            for record in cls.typed(GenesisRecordType.RELATIONSHIP_EDGE)
            if record.relationship_from_id == from_id
            and (to_id is None or record.relationship_to_id == to_id)
            and (to_name != "Ted" or str(record.relationship_to_id) == "character:ted")
        )

    def assertCorpusContains(self, value: str) -> None:
        self.assertIn(value.casefold(), self.corpus)

    def test_source_artifacts_match_creator_hashes(self) -> None:
        for filename, expected in EXPECTED_SOURCE_HASHES.items():
            with self.subTest(filename=filename):
                self.assertEqual(
                    bytes_sha256((PATHS["source_root"] / filename).read_bytes()),
                    expected,
                )
        visual = json.loads((PATHS["source_root"] / VISUAL_FILENAME).read_text(encoding="utf-8"))
        overlay = json.loads((PATHS["source_root"] / PROMPT_OVERLAY_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(len(visual["characters"]), 7)
        self.assertTrue(overlay["negative_prompt_is_exact"])

    def test_manifest_compiles_and_dry_run_is_stable(self) -> None:
        plan = GenesisCompiler().dry_run(PATHS["package_root"], self.authorization)
        self.assertEqual(plan.revision_id, self.compiled.manifest.revision_id)
        self.assertEqual(len(plan.record_hashes), len(self.records))
        self.assertEqual(len(plan.record_hashes), len(set(plan.record_hashes)))

    def test_structural_counts(self) -> None:
        counts = Counter(record.record_type for record in self.records)
        self.assertEqual(counts[GenesisRecordType.FORMATIVE_EVENT], 72)
        self.assertEqual(counts[GenesisRecordType.MEMORY_SEED], 70)
        self.assertEqual(counts[GenesisRecordType.RELATIONSHIP_EDGE], 49)
        self.assertEqual(counts[GenesisRecordType.ADULT_ELIGIBILITY], 7)
        self.assertEqual(counts[GenesisRecordType.VISUAL_CANON], 7)
        self.assertEqual(
            sum(
                "seven_deadly_sins" in record.tags
                for record in self.typed(GenesisRecordType.CHARACTER_STATE)
            ),
            49,
        )
        visual_records = self.typed(GenesisRecordType.VISUAL_CANON)
        self.assertEqual(
            sum(
                len(self.payloads[record.record_id]["stable_visual"]["wardrobe_presets"])
                for record in visual_records
            ),
            51,
        )

    def test_rebuild_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rebuilt = build_hanezawa_genesis(
                source_root=PATHS["source_root"],
                package_root=root / "hanezawa_core_v1_1",
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
            self.assertEqual(
                rebuilt.authorization_path.read_bytes(),
                PATHS["authorization_path"].read_bytes(),
            )

    def test_disposable_sqlite_install_and_exact_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SQLiteAuthorityStore(Path(temporary) / "genesis-validation.sqlite3")
            repository = GenesisRepository(store)
            transaction_id = TypedId(IdKind.TRANSACTION, "hanezawa-v1-1-validation")
            first = repository.compile_and_install(
                PATHS["package_root"],
                self.authorization,
                transaction_id=transaction_id,
                idempotency_key="hanezawa-v1.1-validation",
            )
            second = repository.compile_and_install(
                PATHS["package_root"],
                self.authorization,
                transaction_id=transaction_id,
                idempotency_key="hanezawa-v1.1-validation",
            )
            active = store.active_genesis_records(first.receipt.revision_id)
            self.assertFalse(first.exact_replay)
            self.assertTrue(second.exact_replay)
            self.assertEqual(first.receipt, second.receipt)
            self.assertEqual(len(active), len(self.active_records))
            self.assertEqual(store.genesis_table_count("genesis_records"), len(self.records))

    def test_anima_creator_prompt_overlay(self) -> None:
        policy = next(
            record
            for record in self.typed(GenesisRecordType.CREATOR_PREFERENCE)
            if self.payloads[record.record_id].get("policy_id") == "anima_prompt_policy_v1"
        )
        payload = self.payloads[policy.record_id]
        self.assertEqual(
            payload["positive_prefix"],
            "masterpiece, very aesthetic, @Ani2rel, v0q1d, Cyaniji,",
        )
        self.assertNotIn("loli", payload["negative_prompt"].casefold())
        self.assertTrue(payload["negative_prompt_is_exact"])
        self.assertFalse(payload["append_identity_drift_negatives"])

    def test_direct_creator_conflicts_are_superseded(self) -> None:
        unknown_claims = "\n".join(
            record.claim for record in self.typed(GenesisRecordType.UNRESOLVED_QUESTION)
        ).casefold()
        self.assertNotIn("biological father", unknown_claims)
        self.assertNotIn("chiyo", unknown_claims)
        self.assertCorpusContains("biological father of Mia and Yuuni")
        self.assertCorpusContains("Chiyo is excluded from active CERA Genesis")

    # The following thirty tests mirror the numbered acceptance contract in V1.1.
    def test_acceptance_01_hana_identity_presence_authority_and_route(self) -> None:
        hana = CHARACTER_IDS["Hana"]
        adult = next(r for r in self.typed(GenesisRecordType.ADULT_ELIGIBILITY) if hana in r.subject_ids)
        placed = next(r for r in self.typed(GenesisRecordType.STORY_START_PLACEMENT) if hana in r.subject_ids)
        self.assertEqual(self.payloads[adult.record_id]["age"], 38)
        self.assertIs(adult.adult_eligibility, AdultEligibility.CONFIRMED_IDENTITY_ELIGIBLE)
        self.assertIs(placed.story_start_presence, StoryStartPresence.PRESENT)
        self.assertCorpusContains("final family authority")

    def test_acceptance_02_ted_is_not_hanezawa_or_automatic_family(self) -> None:
        self.assertCorpusContains("Ted is not Hanezawa")
        self.assertCorpusContains("not automatically family")

    def test_acceptance_03_daughters_address_hana_as_mother(self) -> None:
        self.assertCorpusContains("Mother or Mom")

    def test_acceptance_04_affair_truth_and_hana_belief_are_separate(self) -> None:
        self.assertIn("is cheating", self.event("EN07").claim)
        self.assertIn("believes him faithful", self.corpus)
        self.assertIn("unease", self.memory("H-M08").payload_json.casefold())

    def test_acceptance_05_only_sakura_and_enne_know_affair(self) -> None:
        event = self.event("EN08")
        self.assertEqual(
            set(event.knowledge_owner_ids),
            {CHARACTER_IDS["Sakura"], CHARACTER_IDS["Enne"]},
        )
        self.assertNotIn(CHARACTER_IDS["Hana"], event.knowledge_owner_ids)

    def test_acceptance_06_tomi_finance_secret_knowledge(self) -> None:
        event = self.event("ST03")
        self.assertEqual(
            set(event.knowledge_owner_ids),
            {CHARACTER_IDS["Hana"], CHARACTER_IDS["Sakura"], CHARACTER_IDS["Aoi"]},
        )

    def test_acceptance_07_surveillance_knowledge(self) -> None:
        event = self.event("ST06")
        self.assertEqual(
            set(event.knowledge_owner_ids),
            {CHARACTER_IDS["Hana"], CHARACTER_IDS["Sakura"], CHARACTER_IDS["Enne"]},
        )

    def test_acceptance_08_surveillance_deadline_asymmetry(self) -> None:
        self.assertCorpusContains("no fixed deadline")
        self.assertCorpusContains("Hana believes surveillance is genuinely short-term")

    def test_acceptance_09_husband_question_respects_knowledge_and_route(self) -> None:
        self.assertNotIn(CHARACTER_IDS["Hana"], self.event("EN07").knowledge_owner_ids)
        hana_owned = "\n".join(
            record.claim
            for record in self.active_records
            if record.owner_id == CHARACTER_IDS["Hana"]
        ).casefold()
        self.assertNotIn("knows her husband is cheating", hana_owned)

    def test_acceptance_10_tomi_possible_partner_not_auto_attraction(self) -> None:
        relation = self.relation("Tomi", "Ted")
        self.assertIn("possible", relation.payload_json.casefold())
        self.assertIn("without established attraction", self.corpus)

    def test_acceptance_11_enne_male_image_experiment_is_not_future_determinism(self) -> None:
        self.assertCorpusContains("produced no meaningful arousal")
        self.assertCorpusContains("Real physical proximity remains untested")

    def test_acceptance_12_aoi_contingencies_not_executed(self) -> None:
        self.assertCorpusContains("contingency planning only")

    def test_acceptance_13_aoi_family_love_is_genuine(self) -> None:
        self.assertCorpusContains("family kindness is genuine")

    def test_acceptance_14_yuuni_adult_popstar_and_mentally_capable(self) -> None:
        yuuni = CHARACTER_IDS["Yuuni"]
        adult = next(r for r in self.typed(GenesisRecordType.ADULT_ELIGIBILITY) if yuuni in r.subject_ids)
        self.assertEqual(self.payloads[adult.record_id]["age"], 18)
        self.assertCorpusContains("aspiring popstar")
        self.assertCorpusContains("not mentally childlike")

    def test_acceptance_15_yuuni_insight_can_be_right_and_motive_wrong(self) -> None:
        self.assertCorpusContains("insight can be accurate while her motive interpretation is wrong")

    def test_acceptance_16_yuuni_bluntness_is_not_strategy(self) -> None:
        self.assertCorpusContains("without being strategic manipulation")

    def test_acceptance_17_sakura_general_and_enne_specialized_intelligence(self) -> None:
        self.assertCorpusContains("Sakura is generally smarter than Enne")
        self.assertCorpusContains("specialized technical depth")

    def test_acceptance_18_sakura_bias_does_not_force_response(self) -> None:
        self.assertCorpusContains("bias interpretation without forcing the same response")

    def test_acceptance_19_sakura_exception_does_not_cure_ideology(self) -> None:
        self.assertCorpusContains("without ideological cure")

    def test_acceptance_20_mia_warmth_and_reasoning_coexist(self) -> None:
        self.assertCorpusContains("Mia's warmth and reasoning coexist")

    def test_acceptance_21_mia_response_is_not_consent_or_public_truth(self) -> None:
        self.assertCorpusContains("does not establish desire, consent, or public knowledge")

    def test_acceptance_22_every_character_has_seven_sins(self) -> None:
        for name in CHARACTER_IDS:
            with self.subTest(name=name):
                self.assertEqual(
                    sum(
                        name.casefold() in record.tags and "seven_deadly_sins" in record.tags
                        for record in self.typed(GenesisRecordType.CHARACTER_STATE)
                    ),
                    7,
                )

    def test_acceptance_23_development_axes_are_distinct_capacities(self) -> None:
        self.assertCorpusContains("distinct pure-love, possessive, and M potential")
        self.assertCorpusContains("without a trope switch")

    def test_acceptance_24_extreme_development_preserves_identity(self) -> None:
        self.assertCorpusContains("never erases current voice or family identity")

    def test_acceptance_25_severe_harm_can_be_irreversible(self) -> None:
        self.assertCorpusContains("irreversible family rejection")

    def test_acceptance_26_repair_rituals_are_not_automatic(self) -> None:
        self.assertCorpusContains("Repair rituals are habits, not automatic scene fallbacks")

    def test_acceptance_27_household_rule_sets_remain_distinct(self) -> None:
        rules = self.typed(GenesisRecordType.HOUSEHOLD_RULE)
        headings = {self.payloads[record.record_id]["heading"] for record in rules}
        self.assertIn("3.1 Rules that existed before Ted", headings)
        self.assertIn("3.2 Public rules created because Ted moved in", headings)

    def test_acceptance_28_surveillance_is_not_a_public_rule(self) -> None:
        surveillance = next(
            record
            for record in self.typed(GenesisRecordType.HOUSEHOLD_RULE)
            if "surveillance" in self.payloads[record.record_id]["heading"].casefold()
        )
        self.assertEqual(surveillance.visibility.value, "system_private")

    def test_acceptance_29_genesis_does_not_own_ted_private_state(self) -> None:
        self.assertCorpusContains("Ted's feelings and intentions remain user-authored")
        self.assertCorpusContains("Ted's private preferences remain user-authored")

    def test_acceptance_30_genesis_does_not_schedule_future_plot(self) -> None:
        self.assertCorpusContains("not future plot beats")
        self.assertCorpusContains("No future plot is scheduled by Genesis")


if __name__ == "__main__":
    unittest.main()
