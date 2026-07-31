from __future__ import annotations

import unittest

from cera.composer import ArtifactPublicationMode, PurePresentationRenderer, RendererProfile
from cera.genesis.hanezawa_builder import CHARACTER_IDS
from cera.ids import IdKind
from cera.runtime import OrdinaryApplicationRequest, ProviderFreeOrdinaryApplication
from cera.serialization import canonical_json
from cera.storage import PostPublicationWorkStatus
import tests.test_ordinary_application as application_support
import tests.test_post_publication as post_support
import tests.test_real_genesis_integration as real_support


class TwentyRunAcceptanceTests(unittest.TestCase):
    """One auditable 20-turn provider-free acceptance exercise."""

    def setUp(self) -> None:
        self.harness = post_support.PostPublicationTests("runTest")
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)

    @property
    def live(self):
        return self.harness.live

    @property
    def store(self):
        return self.live.real.sandbox.store

    @staticmethod
    def renderer():
        return PurePresentationRenderer((RendererProfile("twenty-run", "v1"),))

    def test_twenty_sequential_application_runs(self) -> None:
        names_by_run = (
            ("Hana",),
            ("Sakura",),
            ("Mia",),
            ("Enne",),
            ("Tomi",),
            ("Aoi",),
            ("Yuuni",),
            ("Hana", "Mia"),
            ("Sakura", "Enne"),
            ("Tomi", "Aoi"),
            ("Mia", "Yuuni"),
            ("Hana", "Sakura"),
            ("Enne", "Tomi"),
            ("Aoi", "Yuuni"),
            ("Hana", "Sakura", "Mia"),
            ("Enne", "Tomi", "Aoi"),
            ("Hana",),
            ("Hana", "Mia"),
            ("Sakura", "Yuuni"),
            ("Hana", "Enne", "Aoi"),
        )
        self.assertEqual(len(names_by_run), 20)
        trial_summaries: list[dict[str, object]] = []
        expected_head = None
        fork_id = None
        fork_expected_artifacts = ()

        for index, names in enumerate(names_by_run, start=1):
            responders = tuple(CHARACTER_IDS[name] for name in names)
            suffix = f"twenty-run-{index:02d}"
            kwargs: dict[str, object] = {
                "responders": responders,
                "source": (
                    f"Ted begins ordinary household exchange {index} with "
                    + ", ".join(names)
                    + "."
                ),
                "expected_generation": index - 1,
                "expected_parent_artifact_id": expected_head,
                "include_search": index in {4, 9, 13},
            }
            mode = "append"
            if index == 17:
                memory = self.live.real.sandbox.record_with_payload_value(
                    "memory_id", "H-M08"
                )
                kwargs.update(
                    {
                        "records": (memory,),
                        "source": "Ted asks Hana about the years of missed calls.",
                        "fetch_sections": (
                            "remembered_content",
                            "learned_meaning_or_belief_pressure",
                            "possible_retrieval_cues",
                        ),
                        "include_search": True,
                    }
                )
                mode = "indirect_memory_append"
            elif index == 18:
                assert expected_head is not None
                replacement_parent = self.store.get_artifact_parent_id(expected_head)
                kwargs.update(
                    {
                        "publication_mode": ArtifactPublicationMode.REGENERATE,
                        "publication_parent_artifact_id": replacement_parent,
                        "replaces_artifact_id": expected_head,
                        "source": (
                            "Ted requests a different ordinary continuation of the "
                            "same household moment."
                        ),
                    }
                )
                mode = "regenerate"

            args = self.live.ordinary_case(suffix, **kwargs)
            consolidator = self.harness.no_change_port(suffix)
            request = OrdinaryApplicationRequest(
                schema_version=OrdinaryApplicationRequest.SCHEMA_VERSION,
                reasoner_request=args[0],
                composer_plan=args[2],
                renderer_profile="twenty-run",
                consolidation_requested=True,
            )
            application = ProviderFreeOrdinaryApplication(
                store=self.store,
                pipeline=self.live.pipeline,
                reasoner_port=args[1],
                composer_port=args[3],
                renderer=self.renderer(),
                consolidator=consolidator,
            )
            result = application.execute(request)

            self.assertFalse(result.receipt.exact_replay)
            self.assertEqual(result.receipt.adapter_call_count, 2)
            self.assertEqual(result.receipt.downstream_failure_count, 0)
            self.assertEqual(result.operational_report.terminal_count, 3)
            self.assertEqual(result.operational_report.failed_count, 0)
            self.assertEqual(result.operational_report.pending_count, 0)
            self.assertTrue(
                all(
                    value.status
                    in {
                        PostPublicationWorkStatus.COMPLETED,
                        PostPublicationWorkStatus.NO_CHANGE,
                    }
                    for value in result.operational_report.work_items
                )
            )
            self.assertEqual(args[4].calls, 1)
            self.assertEqual(len(args[5].calls), 1)
            self.assertEqual(consolidator.invocation_count, 1)
            self.assertEqual(
                self.store.get_branch(self.live.real.sandbox.branch_id).generation,
                index,
            )

            replay = ProviderFreeOrdinaryApplication(
                store=self.store,
                pipeline=self.live.pipeline,
                reasoner_port=application_support.ExplodingPort(),
                composer_port=application_support.ExplodingPort(),
            ).execute(request)
            self.assertTrue(replay.receipt.exact_replay)
            self.assertEqual(replay.receipt.adapter_call_count, 0)
            self.assertEqual(replay.accepted_artifact, result.accepted_artifact)
            self.assertEqual(args[4].calls, 1)
            self.assertEqual(len(args[5].calls), 1)
            self.assertEqual(consolidator.invocation_count, 1)

            expected_head = result.accepted_artifact.artifact_id
            if index == 5:
                fork_id = real_support.ident(IdKind.BRANCH, "twenty-run-fork")
                self.store.fork_branch(self.live.real.sandbox.branch_id, fork_id)
                fork_expected_artifacts = self.store.visible_artifact_ids(fork_id)
                self.assertEqual(len(fork_expected_artifacts), 5)
            if fork_id is not None:
                self.assertEqual(
                    self.store.visible_artifact_ids(fork_id),
                    fork_expected_artifacts,
                )

            trial_summaries.append(
                {
                    "run": index,
                    "mode": mode,
                    "responders": names,
                    "artifact_id": result.accepted_artifact.artifact_id,
                    "generation": result.commit_receipt.generation_after,
                    "adapter_calls": result.receipt.adapter_call_count,
                    "replay_adapter_calls": replay.receipt.adapter_call_count,
                    "work_terminal": result.operational_report.terminal_count,
                    "work_failed": result.operational_report.failed_count,
                }
            )

        self.assertIsNotNone(fork_id)
        self.assertEqual(self.store.table_count("artifacts"), 20)
        self.assertEqual(self.store.table_count("authority_records"), 40)
        self.assertEqual(self.store.table_count("post_publication_work"), 60)
        self.assertEqual(self.store.table_count("post_publication_attempts"), 60)
        self.assertEqual(self.store.table_count("consolidation_receipts"), 0)
        self.assertEqual(self.store.integrity_check(), ("ok",))
        self.assertEqual(self.store.foreign_key_check(), ())
        self.assertEqual(len(trial_summaries), 20)
        self.assertEqual(sum(value["adapter_calls"] for value in trial_summaries), 40)
        self.assertEqual(
            sum(value["replay_adapter_calls"] for value in trial_summaries),
            0,
        )
        self.assertEqual(sum(value["work_failed"] for value in trial_summaries), 0)
        print(
            "CERA_20_RUN_RESULT="
            + canonical_json(
                {
                    "schema_version": "cera.twenty_run_acceptance_result.v1",
                    "runs": trial_summaries,
                    "committed_artifacts": 20,
                    "direct_events": 20,
                    "post_publication_work_items": 60,
                    "post_publication_attempts": 60,
                    "new_adapter_calls": 40,
                    "replay_adapter_calls": 0,
                    "failures": 0,
                    "integrity_check": "ok",
                    "foreign_key_findings": 0,
                }
            )
        )


if __name__ == "__main__":
    unittest.main()
