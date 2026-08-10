from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from cera.errors import StateConflictError
from cera.pi_scene._world_workspace_files import lean_scene_branch_root
from cera.pi_scene.provider_stage_retry import ProviderStage
from cera.pi_scene.provider_stage_retry_blob import TrustedLocalProtectedStageBlobStore
from cera.pi_scene.provider_stage_retry_ordinary import (
    OrdinaryProviderStageRetryRuntimeV1,
    serialize_planner_result,
)
from cera.pi_scene.provider_stage_retry_ordinary_custody import (
    ProtectedOrdinaryStageRetryCustodyStoreV1,
)
from cera.pi_scene.provider_stage_retry_ordinary_retrieval import (
    ActiveDerivedPlannerRetrievalManifestPortV1,
    PlannerManifestRevalidatingOwnerFactoryV1,
)
from cera.pi_scene.provider_stage_retry_runtime import (
    ProviderStageRetryRuntimeServiceV1,
    ProviderStageRuntimeAdapterV1,
    backend_action_from_envelope,
)
from cera.pi_scene.runtime import PlannerTurnOutputV1, ProviderStageRetryPendingError
from cera.storage.sqlite_store import SQLiteAuthorityStore

from .test_pi_scene_lean_v1 import sequence
from .test_provider_stage_retry_ordinary import (
    _Binder,
    _configuration,
    _context,
    _failure,
    _InvocationCounts,
    _OwnerFactory,
    _payload,
    _planner_request,
    _Reconciler,
    _success,
)


class OrdinaryPlannerRetrievalManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime_root = self.root / "world-runtime"
        self.runtime_root.mkdir()
        base_context = _context()
        self.branch_root = lean_scene_branch_root(
            self.runtime_root,
            base_context.binding.world_id,
            base_context.binding.branch_id,
        )
        (self.branch_root / "ACTIVE").mkdir(parents=True)
        (self.branch_root / "DERIVED").mkdir()
        (self.branch_root / "ACTIVE" / "WORLD_STATE.json").write_text(
            '{"accepted":"before"}',
            encoding="utf-8",
        )
        (self.branch_root / "DERIVED" / "INDEX.json").write_text(
            '{"entries":[]}',
            encoding="utf-8",
        )
        self.snapshot_port = ActiveDerivedPlannerRetrievalManifestPortV1(self.runtime_root)
        retrieval = self.snapshot_port.capture(base_context.binding)
        self.context = replace(base_context, planner_retrieval=retrieval)
        self.custody = ProtectedOrdinaryStageRetryCustodyStoreV1(self.root / "ordinary-custody")
        self.custody.freeze_request(
            normalized_request=_payload(),
            binding=self.context.binding,
            generation=self.context.generation,
            turn_input=self.context.turn_input,
            retrieval_snapshot=retrieval.retrieval_snapshot,
            tool_result_bundle=retrieval.tool_result_bundle,
        )

        counts = _InvocationCounts()
        self.counts = counts
        planner_result = PlannerTurnOutputV1(sequence=sequence(), provider_operations=1)
        self.factories = {
            stage: _OwnerFactory(
                stage,
                {
                    1: _success(
                        f"{stage.value}:1",
                        (
                            serialize_planner_result(planner_result)
                            if stage is ProviderStage.PLANNER
                            else f"unused:{stage.value}".encode()
                        ),
                    )
                },
                counts,
            )
            for stage in ProviderStage
        }
        registrations = []
        for stage in ProviderStage:
            factory = self.factories[stage]
            owner_factory = (
                PlannerManifestRevalidatingOwnerFactoryV1(
                    wrapped=factory,
                    custody_store=self.custody,
                    snapshot_port=self.snapshot_port,
                )
                if stage is ProviderStage.PLANNER
                else factory
            )
            registrations.append(
                ProviderStageRuntimeAdapterV1(
                    stage=stage,
                    owner_factory=owner_factory,
                    ambiguity_reconciler=_Reconciler(),
                    downstream_binder=_Binder(),
                )
            )
        self.service = ProviderStageRetryRuntimeServiceV1(
            authority_store=SQLiteAuthorityStore(self.root / "authority.sqlite3"),
            protected_blob_store=TrustedLocalProtectedStageBlobStore(self.root / "protected-stage"),
            registrations=registrations,
        )
        self.integration = OrdinaryProviderStageRetryRuntimeV1(
            service=self.service,
            custody_store=self.custody,
            configurations={stage: _configuration(stage) for stage in ProviderStage},
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_capture_covers_active_and_derived_and_revalidates_exact_bytes(self) -> None:
        retrieval = self.context.planner_retrieval
        self.snapshot_port.revalidate(
            self.context.binding,
            retrieval.retrieval_snapshot,
        )
        tool_result_bundle = retrieval.tool_result_bundle
        self.assertIsInstance(tool_result_bundle, Mapping)
        assert isinstance(tool_result_bundle, Mapping)
        self.assertEqual(
            tool_result_bundle["mode"],
            "active_derived_manifest",
        )
        (self.branch_root / "DERIVED" / "INDEX.json").write_text(
            '{"entries":["changed"]}',
            encoding="utf-8",
        )
        with self.assertRaisesRegex(StateConflictError, "snapshot drifted"):
            self.snapshot_port.revalidate(
                self.context.binding,
                retrieval.retrieval_snapshot,
            )

    def test_retry_owner_enters_recovery_required_on_manifest_drift(self) -> None:
        planner_result = PlannerTurnOutputV1(
            sequence=sequence("after-retry"),
            provider_operations=1,
        )
        planner_factory = self.factories[ProviderStage.PLANNER]
        planner_factory.outcomes = {
            1: _failure("planner:first"),
            2: _success("planner:retry", serialize_planner_result(planner_result)),
        }
        with self.integration.bind_request(self.context):
            with self.assertRaises(ProviderStageRetryPendingError) as captured:
                self.integration.run_planner(
                    _planner_request(),
                    accepted_state_sha256=("a" * 64),
                    authority_binding={"accepted_head_sha256": None},
                )
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 1)

        (self.branch_root / "ACTIVE" / "WORLD_STATE.json").write_text(
            '{"accepted":"drifted"}',
            encoding="utf-8",
        )
        action = backend_action_from_envelope(captured.exception.envelope)
        projection = self.integration.execute_action(action)
        self.assertFalse(projection.result_ready)
        self.assertEqual(projection.envelope["status"]["state"], "recovery_required")
        self.assertEqual(self.counts.by_stage[ProviderStage.PLANNER], 1)


if __name__ == "__main__":
    unittest.main()
