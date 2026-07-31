from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from cera.contracts import (
    Certainty,
    EvidenceAuthority,
    KnowledgeRoute,
    TruthStatus,
    Visibility,
)
from cera.errors import (
    ContractValidationError,
    ErrorCode,
    GenesisImportBlockedError,
    TransactionError,
)
from cera.genesis import (
    AdultEligibility,
    AuthorizedGenesisSource,
    COMPILER_CONTRACT_VERSION,
    EpistemicLayer,
    EvidenceAccessScope,
    EvidenceQuery,
    EvidenceRequesterRole,
    GenesisCompiler,
    GenesisInstallBundle,
    GenesisManifest,
    GenesisModule,
    GenesisModuleRef,
    GenesisPackageClass,
    GenesisRecord,
    GenesisRecordType,
    GenesisRepository,
    GenesisUnresolvedFinding,
    StoryStartPresence,
    SyntheticFixtureAuthorization,
)
from cera.ids import IdKind, TypedId
from cera.serialization import bytes_sha256, canonical_json
from cera.storage import SQLiteAuthorityStore


def ident(kind: IdKind, suffix: str) -> TypedId:
    return TypedId(kind, suffix)


class FailingGenesisStore(SQLiteAuthorityStore):
    def _after_genesis_records_insert(self, connection, bundle) -> None:
        raise RuntimeError("simulated Genesis publication failure")


class GenesisRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database_path = self.root / "authority.sqlite3"
        self.store = SQLiteAuthorityStore(
            self.database_path, allow_synthetic_genesis=True
        )
        self.repository = GenesisRepository(self.store)
        self.world_id = ident(IdKind.WORLD, "synthetic-world")
        self.branch_id = ident(IdKind.BRANCH, "synthetic-branch")
        self.alpha = ident(IdKind.CHARACTER, "alpha")
        self.beta = ident(IdKind.CHARACTER, "beta")
        self.store.create_world(self.world_id)
        self.store.create_root_branch(self.world_id, self.branch_id)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def record(
        self,
        suffix: str,
        *,
        source_id: TypedId,
        record_version: int = 1,
        record_type: GenesisRecordType = GenesisRecordType.IDENTITY,
        layer: EpistemicLayer = EpistemicLayer.OBJECTIVE_FACT,
        truth: TruthStatus = TruthStatus.OBJECTIVE,
        claim: str | None = None,
        subjects: tuple[TypedId, ...] | None = None,
        owner: TypedId | None = None,
        knowledge_owners: tuple[TypedId, ...] = (),
        visibility: Visibility = Visibility.PUBLIC,
        certainty: Certainty = Certainty.ESTABLISHED,
        adult: AdultEligibility = AdultEligibility.NOT_APPLICABLE,
        presence: StoryStartPresence = StoryStartPresence.NOT_APPLICABLE,
        relationship_from: TypedId | None = None,
        relationship_to: TypedId | None = None,
        supersedes: tuple[TypedId, ...] = (),
        tags: tuple[str, ...] = (),
        payload: object | None = None,
        content_class: str = "ordinary",
    ) -> GenesisRecord:
        return GenesisRecord(
            schema_version=GenesisRecord.SCHEMA_VERSION,
            record_id=ident(IdKind.RECORD, f"record-{suffix}"),
            record_version=record_version,
            record_type=record_type,
            epistemic_layer=layer,
            truth_status=TruthStatus.NONCANONICAL,
            claim=claim or f"Synthetic claim {suffix}",
            authority=EvidenceAuthority.SYNTHETIC_FIXTURE,
            subject_ids=subjects or (self.alpha,),
            owner_id=owner,
            knowledge_owner_ids=knowledge_owners,
            visibility=visibility,
            knowledge_route=KnowledgeRoute.CREATOR_SEED,
            certainty=certainty,
            content_class=content_class,
            adult_eligibility=adult,
            story_start_presence=presence,
            relationship_from_id=relationship_from,
            relationship_to_id=relationship_to,
            source_refs=(source_id,),
            valid_from="story_start",
            valid_to=None,
            supersedes=supersedes,
            tags=tags,
            expandable_sections=("payload", "knowledge"),
            payload_json=canonical_json(payload if payload is not None else {"value": suffix}),
        )

    def base_records(self, source_id: TypedId) -> tuple[GenesisRecord, ...]:
        return (
            self.record(
                "alpha-adult",
                source_id=source_id,
                record_type=GenesisRecordType.ADULT_ELIGIBILITY,
                claim="Alpha is a creator-confirmed adult for identity routing.",
                adult=AdultEligibility.CONFIRMED_IDENTITY_ELIGIBLE,
                tags=("character:alpha", "adult_identity"),
                payload={"age": 30},
            ),
            self.record(
                "alpha-present",
                source_id=source_id,
                record_type=GenesisRecordType.STORY_START_PLACEMENT,
                claim="Alpha is present at synthetic story start.",
                presence=StoryStartPresence.PRESENT,
                tags=("character:alpha", "story_start"),
            ),
            self.record(
                "alpha-private",
                source_id=source_id,
                record_type=GenesisRecordType.CHARACTER_STATE,
                layer=EpistemicLayer.PRIVATE_FEELING,
                truth=TruthStatus.CHARACTER_OWNED,
                claim="Alpha privately feels uneasy about an unresolved question.",
                owner=self.alpha,
                knowledge_owners=(self.alpha,),
                visibility=Visibility.OWNER_PRIVATE,
                certainty=Certainty.FEARED,
                tags=("character:alpha", "unease"),
            ),
            self.record(
                "beta-private",
                source_id=source_id,
                record_type=GenesisRecordType.CHARACTER_STATE,
                layer=EpistemicLayer.PRIVATE_BELIEF,
                truth=TruthStatus.CHARACTER_OWNED,
                claim="Beta privately suspects the unresolved question has an answer.",
                subjects=(self.beta,),
                owner=self.beta,
                knowledge_owners=(self.beta,),
                visibility=Visibility.OWNER_PRIVATE,
                certainty=Certainty.SUSPECTED,
                tags=("character:beta", "suspicion"),
            ),
            self.record(
                "unknown",
                source_id=source_id,
                record_type=GenesisRecordType.UNRESOLVED_QUESTION,
                layer=EpistemicLayer.UNRESOLVED_QUESTION,
                truth=TruthStatus.UNKNOWN,
                claim="The objective answer to the synthetic question is unknown.",
                subjects=(self.world_id,),
                visibility=Visibility.SYSTEM_PRIVATE,
                certainty=Certainty.UNKNOWN,
                tags=("unresolved",),
                content_class="system",
                payload={"status": "unknown"},
            ),
            self.record(
                "alpha-to-beta",
                source_id=source_id,
                record_type=GenesisRecordType.RELATIONSHIP_EDGE,
                layer=EpistemicLayer.PRIVATE_BELIEF,
                truth=TruthStatus.CHARACTER_OWNED,
                claim="Alpha privately respects Beta.",
                subjects=(self.alpha, self.beta),
                owner=self.alpha,
                knowledge_owners=(self.alpha,),
                visibility=Visibility.OWNER_PRIVATE,
                certainty=Certainty.BELIEVED,
                relationship_from=self.alpha,
                relationship_to=self.beta,
                tags=("relationship", "respect"),
            ),
        )

    def write_package(
        self,
        name: str,
        *,
        revision_number: int,
        parent_revision_id: TypedId | None,
        records_factory=None,
    ):
        package = self.root / name
        module_path = package / "modules" / "base.json"
        module_path.parent.mkdir(parents=True)
        source_id = ident(IdKind.SOURCE, f"fixture-source-{name}")
        records = (
            records_factory(source_id)
            if records_factory is not None
            else self.base_records(source_id)
        )
        module = GenesisModule(
            schema_version=GenesisModule.SCHEMA_VERSION,
            source_id=source_id,
            package_class=GenesisPackageClass.SYNTHETIC_FIXTURE,
            title=f"Synthetic {name}",
            records=tuple(records),
        )
        module_bytes = canonical_json(module).encode("utf-8")
        module_path.write_bytes(module_bytes)
        module_hash = bytes_sha256(module_bytes)
        revision_id = ident(IdKind.GENESIS_REVISION, f"revision-{name}")
        package_id = ident(IdKind.GENESIS_PACKAGE, "synthetic-fixture-package")
        unknown_record = next(
            (
                record
                for record in records
                if record.epistemic_layer is EpistemicLayer.UNRESOLVED_QUESTION
            ),
            None,
        )
        findings = (
            (
                GenesisUnresolvedFinding(
                    finding_id=ident(IdKind.GENESIS_FINDING, f"finding-{name}"),
                    description="Synthetic ambiguity is intentionally preserved as unknown.",
                    source_refs=(source_id,),
                    preserved_record_id=unknown_record.record_id,
                ),
            )
            if unknown_record is not None
            else ()
        )
        manifest = GenesisManifest(
            schema_version=GenesisManifest.SCHEMA_VERSION,
            package_id=package_id,
            package_class=GenesisPackageClass.SYNTHETIC_FIXTURE,
            revision_id=revision_id,
            revision_number=revision_number,
            parent_revision_id=parent_revision_id,
            modules=(
                GenesisModuleRef(
                    source_id=source_id,
                    relative_path="modules/base.json",
                    content_sha256=module_hash,
                ),
            ),
            unresolved_findings=findings,
            compiler_contract_version=COMPILER_CONTRACT_VERSION,
            world_scope="synthetic_only",
            revision_label=f"Synthetic {name}",
        )
        manifest_bytes = canonical_json(manifest).encode("utf-8")
        (package / "manifest.json").write_bytes(manifest_bytes)
        authorization = SyntheticFixtureAuthorization(
            schema_version=SyntheticFixtureAuthorization.SCHEMA_VERSION,
            authorization_id=ident(IdKind.AUTHORIZATION, f"authorization-{name}"),
            package_id=package_id,
            revision_id=revision_id,
            manifest_sha256=bytes_sha256(manifest_bytes),
            authorized_sources=(
                AuthorizedGenesisSource(
                    source_id=source_id,
                    content_sha256=module_hash,
                ),
            ),
            scope="install_synthetic_genesis_fixture",
            authorized_by="synthetic_test_harness",
        )
        return package, authorization, tuple(records)

    def install_root(self):
        package, authorization, records = self.write_package(
            "root", revision_number=1, parent_revision_id=None
        )
        stored = self.repository.compile_and_install_synthetic_fixture(
            package,
            authorization,
            transaction_id=ident(IdKind.TRANSACTION, "genesis-root"),
            idempotency_key="genesis-root",
        )
        self.store.bind_world_to_genesis(
            self.world_id,
            stored.receipt.revision_id,
            authorization.authorization_id,
        )
        return stored, authorization, records

    def query(self, revision_id, scope, **overrides) -> EvidenceQuery:
        values = {
            "world_id": self.world_id,
            "branch_id": self.branch_id,
            "generation": 0,
            "snapshot_token": ident(IdKind.SNAPSHOT, "snapshot-1"),
            "genesis_revision_id": revision_id,
            "access_scope": scope,
            "terms": (),
            "entity_ids": (self.alpha,),
            "tags": (),
            "record_types": (),
            "limit": 20,
        }
        values.update(overrides)
        return EvidenceQuery(**values)

    def test_compile_install_exact_replay_restart_and_no_branch_mutation(self) -> None:
        before = self.store.get_branch(self.branch_id)
        package, authorization, _ = self.write_package(
            "root", revision_number=1, parent_revision_id=None
        )
        transaction_id = ident(IdKind.TRANSACTION, "genesis-root")
        first = self.repository.compile_and_install_synthetic_fixture(
            package,
            authorization,
            transaction_id=transaction_id,
            idempotency_key="genesis-root",
        )
        second = self.repository.compile_and_install_synthetic_fixture(
            package,
            authorization,
            transaction_id=transaction_id,
            idempotency_key="genesis-root",
        )
        restarted = SQLiteAuthorityStore(
            self.database_path, allow_synthetic_genesis=True
        )

        self.store.bind_world_to_genesis(
            self.world_id,
            first.receipt.revision_id,
            authorization.authorization_id,
        )

        self.assertFalse(first.exact_replay)
        self.assertTrue(second.exact_replay)
        self.assertEqual(first.receipt, second.receipt)
        self.assertEqual(len(restarted.active_genesis_records(authorization.revision_id)), 6)
        self.assertEqual(restarted.get_branch(self.branch_id), before)
        self.assertEqual(restarted.table_count("artifacts"), 0)
        self.assertEqual(restarted.table_count("transaction_journal"), 0)
        self.assertEqual(
            restarted.get_world_genesis_revision(self.world_id),
            first.receipt.revision_id,
        )

    def test_tampered_or_undeclared_sources_fail_before_any_write(self) -> None:
        package, authorization, _ = self.write_package(
            "root", revision_number=1, parent_revision_id=None
        )
        module_path = package / "modules" / "base.json"
        module_path.write_text(module_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaisesRegex(ContractValidationError, "module hash"):
            self.repository.compile_and_install_synthetic_fixture(
                package,
                authorization,
                transaction_id=ident(IdKind.TRANSACTION, "tampered"),
                idempotency_key="tampered",
            )
        self.assertEqual(self.store.genesis_table_count("genesis_revisions"), 0)
        self.assertEqual(self.store.genesis_table_count("genesis_transaction_journal"), 0)

    def test_cross_character_private_state_is_filtered(self) -> None:
        stored, _, _ = self.install_root()
        revision_id = stored.receipt.revision_id
        alpha_scope = EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.CHARACTER,
            perspective_id=self.alpha,
            permitted_private_owner_ids=(self.alpha,),
        )
        hits = self.repository.search_evidence(
            self.query(revision_id, alpha_scope, terms=("privately",), entity_ids=())
        )
        claims = {hit.claim for hit in hits}
        self.assertIn("Alpha privately feels uneasy about an unresolved question.", claims)
        self.assertIn("Alpha privately respects Beta.", claims)
        self.assertNotIn(
            "Beta privately suspects the unresolved question has an answer.", claims
        )
        with self.assertRaises(ContractValidationError):
            EvidenceAccessScope(
                requester_role=EvidenceRequesterRole.CHARACTER,
                perspective_id=self.alpha,
                permitted_private_owner_ids=(self.beta,),
            )

    def test_world_binding_is_immutable_and_query_revision_must_match(self) -> None:
        stored, authorization, _ = self.install_root()
        with self.assertRaisesRegex(TransactionError, "already immutable"):
            self.store.bind_world_to_genesis(
                self.world_id,
                stored.receipt.revision_id,
                authorization.authorization_id,
            )
        mismatched_query = self.query(
            ident(IdKind.GENESIS_REVISION, "unbound-revision"),
            EvidenceAccessScope(
                requester_role=EvidenceRequesterRole.CHARACTER,
                perspective_id=self.alpha,
                permitted_private_owner_ids=(self.alpha,),
            ),
        )
        with self.assertRaisesRegex(ContractValidationError, "world binding"):
            self.repository.search_evidence(mismatched_query)

    def test_unknown_is_explicit_and_survives_restart(self) -> None:
        stored, _, _ = self.install_root()
        system_scope = EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            allow_system_private=True,
        )
        restarted_repository = GenesisRepository(
            SQLiteAuthorityStore(
                self.database_path, allow_synthetic_genesis=True
            )
        )
        hits = restarted_repository.search_evidence(
            self.query(
                stored.receipt.revision_id,
                system_scope,
                terms=("objective", "unknown"),
                entity_ids=(),
            )
        )
        self.assertEqual(len(hits), 1)
        self.assertIs(hits[0].truth_status, TruthStatus.NONCANONICAL)
        self.assertIs(hits[0].certainty, Certainty.UNKNOWN)

    def test_production_path_rejects_synthetic_and_missing_approval(self) -> None:
        package, authorization, _ = self.write_package(
            "root", revision_number=1, parent_revision_id=None
        )
        compiler = GenesisCompiler()
        with self.assertRaises(GenesisImportBlockedError) as missing:
            compiler.compile(package, None)
        self.assertIs(missing.exception.code, ErrorCode.CREATOR_AUTHORITY_UNAPPROVED)

        compiled = compiler.compile(package, authorization)
        bundle = GenesisInstallBundle(
            transaction_id=ident(IdKind.TRANSACTION, "production-reject"),
            idempotency_key="production-reject",
            compiled=compiled,
            authorization=authorization,
        )
        production_store = SQLiteAuthorityStore(self.root / "production.sqlite3")
        with self.assertRaises(GenesisImportBlockedError) as rejected:
            production_store.install_genesis_revision(bundle)
        self.assertIs(rejected.exception.code, ErrorCode.SYNTHETIC_GENESIS_REJECTED)
        self.assertEqual(
            production_store.genesis_table_count("genesis_transaction_journal"), 0
        )

    def test_dry_run_is_deterministic_and_markdown_reverse_import_is_blocked(self) -> None:
        package, authorization, _ = self.write_package(
            "root", revision_number=1, parent_revision_id=None
        )
        compiler = GenesisCompiler()
        first = compiler.dry_run(package, authorization)
        second = compiler.dry_run(package, authorization)
        self.assertEqual(first, second)
        self.assertEqual(first.plan_sha256, second.plan_sha256)
        self.assertIs(first.package_class, GenesisPackageClass.SYNTHETIC_FIXTURE)
        self.assertEqual(len(first.unresolved_findings), 1)
        with self.assertRaises(GenesisImportBlockedError) as blocked:
            compiler.import_generated_markdown("# generated")
        self.assertIs(blocked.exception.code, ErrorCode.CREATOR_AUTHORITY_UNAPPROVED)

    def test_adult_eligibility_and_presence_are_never_inferred_from_identity(self) -> None:
        source_id = ident(IdKind.SOURCE, "fixture-source-boundaries")
        identity = self.record("identity-only", source_id=source_id)
        self.assertIs(identity.adult_eligibility, AdultEligibility.NOT_APPLICABLE)
        self.assertIs(identity.story_start_presence, StoryStartPresence.NOT_APPLICABLE)
        unknown_adult = self.record(
            "adult-unknown",
            source_id=source_id,
            record_type=GenesisRecordType.ADULT_ELIGIBILITY,
            layer=EpistemicLayer.UNRESOLVED_QUESTION,
            certainty=Certainty.UNKNOWN,
            adult=AdultEligibility.UNKNOWN,
            payload={"status": "unknown"},
        )
        self.assertIs(unknown_adult.adult_eligibility, AdultEligibility.UNKNOWN)
        with self.assertRaisesRegex(ContractValidationError, "age >= 18"):
            self.record(
                "adult-invalid",
                source_id=source_id,
                record_type=GenesisRecordType.ADULT_ELIGIBILITY,
                adult=AdultEligibility.CONFIRMED_IDENTITY_ELIGIBLE,
                payload={"status": "claimed_without_age"},
            )

    def test_unknown_manifest_field_is_rejected_before_storage(self) -> None:
        package, authorization, _ = self.write_package(
            "root", revision_number=1, parent_revision_id=None
        )
        manifest_path = package / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["unexpected"] = True
        raw = canonical_json(payload).encode("utf-8")
        manifest_path.write_bytes(raw)
        authorization = replace(
            authorization,
            manifest_sha256=bytes_sha256(raw),
        )
        with self.assertRaisesRegex(ContractValidationError, "unknown fields"):
            GenesisCompiler().compile(package, authorization)
        self.assertEqual(self.store.genesis_table_count("genesis_revisions"), 0)

    def test_supersession_excludes_old_record_but_preserves_audit_history(self) -> None:
        root, _, root_records = self.install_root()
        old_record = next(
            record for record in root_records if record.record_id.value == "record-alpha-private"
        )

        def correction(source_id):
            return (
                self.record(
                    "alpha-private-v2",
                    source_id=source_id,
                    record_version=2,
                    record_type=GenesisRecordType.CHARACTER_STATE,
                    layer=EpistemicLayer.PRIVATE_FEELING,
                    truth=TruthStatus.CHARACTER_OWNED,
                    claim="Alpha's private unease has a corrected bounded description.",
                    owner=self.alpha,
                    knowledge_owners=(self.alpha,),
                    visibility=Visibility.OWNER_PRIVATE,
                    certainty=Certainty.FEARED,
                    supersedes=(old_record.record_id,),
                    tags=("character:alpha", "unease"),
                ),
            )

        package, authorization, replacement_records = self.write_package(
            "revision-2",
            revision_number=2,
            parent_revision_id=root.receipt.revision_id,
            records_factory=correction,
        )
        revision = self.repository.compile_and_install_synthetic_fixture(
            package,
            authorization,
            transaction_id=ident(IdKind.TRANSACTION, "genesis-revision-2"),
            idempotency_key="genesis-revision-2",
        )
        active_ids = {
            record.record_id
            for record in self.store.active_genesis_records(revision.receipt.revision_id)
        }
        history_ids = {
            record.record_id
            for record in self.store.genesis_record_history(revision.receipt.revision_id)
        }
        self.assertNotIn(old_record.record_id, active_ids)
        self.assertIn(replacement_records[0].record_id, active_ids)
        self.assertIn(old_record.record_id, history_ids)

    def test_failed_publication_preserves_prior_revision_and_story_branch(self) -> None:
        root, _, root_records = self.install_root()
        before_branch = self.store.get_branch(self.branch_id)

        def correction(source_id):
            return (
                self.record(
                    "corrected-present",
                    source_id=source_id,
                    record_version=2,
                    record_type=GenesisRecordType.STORY_START_PLACEMENT,
                    claim="Synthetic corrected placement.",
                    presence=StoryStartPresence.ABSENT,
                    supersedes=(root_records[1].record_id,),
                ),
            )

        package, authorization, _ = self.write_package(
            "crash",
            revision_number=2,
            parent_revision_id=root.receipt.revision_id,
            records_factory=correction,
        )
        compiled = GenesisCompiler().compile(package, authorization)
        bundle = GenesisInstallBundle(
            transaction_id=ident(IdKind.TRANSACTION, "genesis-crash"),
            idempotency_key="genesis-crash",
            compiled=compiled,
            authorization=authorization,
        )
        failing = FailingGenesisStore(
            self.database_path, allow_synthetic_genesis=True
        )
        failing.prepare_synthetic_genesis_fixture(bundle)
        with self.assertRaisesRegex(TransactionError, "simulated Genesis"):
            failing.finalize_synthetic_genesis_fixture(bundle)

        self.assertEqual(self.store.genesis_table_count("genesis_revisions"), 1)
        self.assertEqual(
            len(self.store.active_genesis_records(root.receipt.revision_id)), 6
        )
        self.assertEqual(self.store.get_branch(self.branch_id), before_branch)
        recovery = SQLiteAuthorityStore(
            self.database_path, allow_synthetic_genesis=True
        ).recover_genesis_prepared(
            reason="synthetic restart"
        )
        self.assertEqual(recovery.rolled_back_transaction_ids, (bundle.transaction_id,))

    def test_conflicting_identity_reuse_is_rejected(self) -> None:
        package, authorization, _ = self.write_package(
            "root", revision_number=1, parent_revision_id=None
        )
        compiled = GenesisCompiler().compile(package, authorization)
        original = GenesisInstallBundle(
            transaction_id=ident(IdKind.TRANSACTION, "genesis-root"),
            idempotency_key="genesis-root",
            compiled=compiled,
            authorization=authorization,
        )
        self.store.prepare_synthetic_genesis_fixture(original)
        conflicting = replace(original, idempotency_key="different-key")
        with self.assertRaisesRegex(TransactionError, "reused differently"):
            self.store.prepare_synthetic_genesis_fixture(conflicting)

    def test_generated_views_are_regenerable_and_never_authority(self) -> None:
        stored, _, _ = self.install_root()
        system_scope = EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.SYSTEM_REASONER,
            perspective_id=None,
            permitted_private_owner_ids=(self.alpha, self.beta),
            allow_system_private=True,
        )
        original = self.repository.render_markdown_view(stored.receipt.revision_id)
        altered = original.replace("Derived, regenerable", "AUTHORITATIVE")
        regenerated = self.repository.render_markdown_view(stored.receipt.revision_id)
        catalog = self.repository.render_catalog_json(
            stored.receipt.revision_id, system_scope
        )
        self.assertNotEqual(altered, regenerated)
        self.assertEqual(original, regenerated)
        self.assertIn("not authority", regenerated)
        self.assertIn(str(stored.receipt.revision_id), catalog)

    def test_evidence_expansion_rechecks_privacy_and_sections(self) -> None:
        stored, _, _ = self.install_root()
        alpha_scope = EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.CHARACTER,
            perspective_id=self.alpha,
            permitted_private_owner_ids=(self.alpha,),
        )
        hits = self.repository.search_evidence(
            self.query(
                stored.receipt.revision_id,
                alpha_scope,
                terms=("uneasy",),
                entity_ids=(),
            )
        )
        expanded = self.repository.expand_evidence(
            genesis_revision_id=stored.receipt.revision_id,
            evidence_ids=(hits[0].evidence_id,),
            requested_sections=("claim", "knowledge", "provenance"),
            access_scope=alpha_scope,
        )
        self.assertIn("owner_private", expanded[0].sections_json)
        beta_scope = EvidenceAccessScope(
            requester_role=EvidenceRequesterRole.CHARACTER,
            perspective_id=self.beta,
            permitted_private_owner_ids=(self.beta,),
        )
        with self.assertRaisesRegex(ContractValidationError, "privacy scope"):
            self.repository.expand_evidence(
                genesis_revision_id=stored.receipt.revision_id,
                evidence_ids=(hits[0].evidence_id,),
                requested_sections=("claim",),
                access_scope=beta_scope,
            )


if __name__ == "__main__":
    unittest.main()
