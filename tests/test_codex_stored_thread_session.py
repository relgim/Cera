from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from cera.errors import StateConflictError
from cera.ids import IdKind, TypedId
from cera.providers.codex_runtime_policy import (
    CODEX_AUTO_COMPACT_TOKEN_LIMIT,
    CODEX_CHATGPT_BASE_URL,
    codex_model_catalog_path,
    codex_model_instructions_path,
)
from cera.reasoner_session import (
    CodexStoredThreadSessionPort,
    ContextAuthorityDelta,
    OpenAICodexStoredThreadBackend,
    ProviderThreadRole,
    ProviderThreadStorageMode,
    ReasonerSessionCompatibility,
    SessionReconstructionBundle,
    SessionRole,
    SessionTurnMode,
)
from cera.reasoner_session.codex_stored import _stored_runtime_config
from cera.serialization import text_sha256
from tests.provider_fakes import OfflineOpenAICodexStoredThreadBackend

HASH_A = "a" * 64
HASH_B = "b" * 64
NOW = "2026-07-31T12:00:00.000000+00:00"


def tid(kind: IdKind, value: str) -> TypedId:
    return TypedId(kind, value)


@dataclass
class FakeStoredBackend:
    session_epoch_id: str = "fake-app-server-epoch"

    def __post_init__(self) -> None:
        self.sequence = 0
        self.parents: dict[str, str | None] = {}
        self.deleted: set[str] = set()
        self.archived: set[str] = set()

    def start_stored_thread(self) -> str:
        self.sequence += 1
        value = f"stored-{self.sequence}"
        self.parents[value] = None
        return value

    def fork_stored_thread(self, parent_thread_id: str) -> str:
        if not self.resume_stored_thread(parent_thread_id):
            raise StateConflictError("missing parent")
        self.sequence += 1
        value = f"stored-{self.sequence}"
        self.parents[value] = parent_thread_id
        return value

    def resume_stored_thread(self, thread_id: str) -> bool:
        return (
            thread_id in self.parents
            and thread_id not in self.deleted
            and thread_id not in self.archived
        )

    def archive_stored_thread(self, thread_id: str) -> None:
        if not self.resume_stored_thread(thread_id):
            raise StateConflictError("cannot archive missing thread")
        self.archived.add(thread_id)

    def archive_stored_leaf(self, thread_id: str) -> None:
        if not self.resume_stored_thread(thread_id):
            raise StateConflictError("cannot archive missing thread")
        self.archived.add(thread_id)


class _FakeRpcClient:
    def __init__(self, start_calls: list[dict[str, object]]) -> None:
        self.calls: list[tuple[str, dict[str, str], object]] = []
        self.start_calls = start_calls

    def thread_start(self, params):
        self.start_calls.append(params)
        return SimpleNamespace(thread=SimpleNamespace(id="native-root"))

    def request(self, method, params, *, response_model):
        self.calls.append((method, params, response_model))
        if method == "config/read":
            return SimpleNamespace(
                config=SimpleNamespace(
                    model_extra={
                        "chatgpt_base_url": CODEX_CHATGPT_BASE_URL,
                        "experimental_compact_prompt_file": None,
                        "experimental_thread_config_endpoint": None,
                        "features": {
                            "network_proxy": False,
                            "remote_compaction_v2": False,
                            "respect_system_proxy": False,
                        },
                        "model_catalog_json": str(codex_model_catalog_path()),
                        "model_instructions_file": str(codex_model_instructions_path()),
                        "model_providers": {},
                        "mcp_servers": {
                            "node_repl": {"enabled": False},
                            "openaiDeveloperDocs": {"enabled": False},
                        },
                        "openai_base_url": "",
                    },
                    model_auto_compact_token_limit=(CODEX_AUTO_COMPACT_TOKEN_LIMIT),
                    model_auto_compact_token_limit_scope=SimpleNamespace(value="total"),
                    model_provider="openai",
                ),
                layers=[],
            )
        if method == "mcpServerStatus/list":
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        name="node_repl",
                        tools={},
                        resources=[],
                        resource_templates=[],
                        server_info=None,
                    ),
                    SimpleNamespace(
                        name="openaiDeveloperDocs",
                        tools={},
                        resources=[],
                        resource_templates=[],
                        server_info=None,
                    ),
                ],
                next_cursor=None,
            )
        return SimpleNamespace()

    def thread_set_name(self, thread_id, name):
        self.calls.append(("thread/name/set", {"threadId": thread_id, "name": name}, None))


class _FakeCodex:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.fork_calls: list[tuple[str, dict[str, object]]] = []
        self.resume_calls: list[tuple[str, dict[str, object]]] = []
        self.archive_calls: list[str] = []
        self._client = _FakeRpcClient(self.start_calls)

    def thread_fork(self, thread_id, **kwargs):
        self.fork_calls.append((thread_id, kwargs))
        return SimpleNamespace(id="native-child")

    def thread_resume(self, thread_id, **kwargs):
        self.resume_calls.append((thread_id, kwargs))
        return SimpleNamespace(id=thread_id)

    def thread_archive(self, thread_id):
        self.archive_calls.append(thread_id)

    def thread_list(self, **_kwargs):
        active = tuple(
            thread_id
            for thread_id in ("native-root", "native-child")
            if thread_id not in self.archive_calls
        )
        return SimpleNamespace(
            data=[SimpleNamespace(id=thread_id) for thread_id in active],
            next_cursor=None,
        )


class CodexStoredThreadSessionTests(unittest.TestCase):
    def compatibility(self) -> ReasonerSessionCompatibility:
        return ReasonerSessionCompatibility(
            schema_version=ReasonerSessionCompatibility.SCHEMA_VERSION,
            world_id=tid(IdKind.WORLD, "stored-world"),
            branch_id=tid(IdKind.BRANCH, "stored-main"),
            role=SessionRole.SCENE_REASONER,
            provider="openai_codex",
            model="gpt-5.6-sol",
            reasoning_effort="medium",
            service_tier="default",
            transport_version="codex-app-server-0.144.4-stored",
            adapter_version="cera.codex_scene_reasoner.v25",
            prompt_version="cera.codex_scene_reasoner_prompt.v25",
            provider_schema_sha256=HASH_A,
            base_instruction_sha256=HASH_B,
            tool_contract_version="cera.reasoner_evidence_mcp.v7",
            genesis_revision_id=tid(IdKind.GENESIS_REVISION, "stored-genesis"),
            authority_policy_version="authority-v1",
            privacy_projection_version="privacy-v1",
            protected_user_id=tid(IdKind.CHARACTER, "ted"),
            autonomy_profile_version="autonomy-v1",
        )

    def reconstruction(
        self, compatibility: ReasonerSessionCompatibility
    ) -> SessionReconstructionBundle:
        return SessionReconstructionBundle(
            schema_version=SessionReconstructionBundle.SCHEMA_VERSION,
            bundle_id=tid(IdKind.CONTEXT_DELTA, "stored-reconstruction"),
            compatibility_sha256=compatibility.compatibility_sha256,
            world_id=compatibility.world_id,
            branch_id=compatibility.branch_id,
            accepted_head_artifact_id=None,
            generation=0,
            authority_revision=0,
            evidence_snapshot_token=tid(IdKind.SNAPSHOT, "stored-snapshot"),
            evidence=(),
            active_constraints=(),
            accepted_receipt_ids=(),
            authoritative_context_sha256=HASH_A,
            created_at=NOW,
        )

    def delta(
        self,
        compatibility: ReasonerSessionCompatibility,
        session_id: TypedId,
        parent_checkpoint_id: TypedId,
    ) -> ContextAuthorityDelta:
        return ContextAuthorityDelta(
            schema_version=ContextAuthorityDelta.SCHEMA_VERSION,
            delta_id=tid(IdKind.CONTEXT_DELTA, "stored-delta"),
            session_id=session_id,
            parent_checkpoint_id=parent_checkpoint_id,
            world_id=compatibility.world_id,
            branch_id=compatibility.branch_id,
            request_id=tid(IdKind.REQUEST, "stored-request"),
            turn_mode=SessionTurnMode.APPEND,
            replaces_artifact_id=None,
            generation=0,
            accepted_head_artifact_id=None,
            authority_revision=0,
            evidence_snapshot_token=tid(IdKind.SNAPSHOT, "stored-snapshot"),
            current_evidence=(),
            revoked_evidence_ids=(),
            active_constraints=(),
            source_sha256=HASH_A,
            turn_packet_sha256=HASH_B,
            created_at=NOW,
        )

    def test_candidate_is_a_stored_leaf_and_rejection_archives_only_that_leaf(self) -> None:
        backend = FakeStoredBackend()
        port = CodexStoredThreadSessionPort(backend, base_instruction_sha256=HASH_B)
        compatibility = self.compatibility()
        root = port.create_session(compatibility, self.reconstruction(compatibility))
        candidate = port.fork_candidate(
            root,
            self.delta(
                compatibility,
                tid(IdKind.SESSION, "stored-session"),
                tid(IdKind.CHECKPOINT, "stored-root"),
            ),
        )
        descriptor = port.describe_thread(candidate)
        self.assertEqual(descriptor.storage_mode, ProviderThreadStorageMode.STORED_LOCAL)
        self.assertEqual(descriptor.role, ProviderThreadRole.CANDIDATE)
        self.assertEqual(
            descriptor.parent_provider_thread_id_sha256,
            text_sha256(root.provider_thread_id),
        )

        port.mark_rejected(candidate, object())
        self.assertFalse(backend.resume_stored_thread(candidate.provider_thread_id))
        self.assertTrue(backend.resume_stored_thread(root.provider_thread_id))
        self.assertTrue(port.describe_thread(candidate).raw_context_retained)
        self.assertEqual(port.provider_calls, 0)

    def test_restart_resumes_the_same_stored_thread_and_missing_is_explicit(self) -> None:
        backend = FakeStoredBackend()
        compatibility = self.compatibility()
        first = CodexStoredThreadSessionPort(backend, base_instruction_sha256=HASH_B)
        root = first.create_session(compatibility, self.reconstruction(compatibility))

        restarted = CodexStoredThreadSessionPort(backend, base_instruction_sha256=HASH_B)
        self.assertEqual(restarted.try_resume(root), root)
        backend.archived.add(root.provider_thread_id)
        self.assertIsNone(restarted.try_resume(root))

    def test_archiving_candidate_does_not_archive_parent(self) -> None:
        backend = FakeStoredBackend()
        port = CodexStoredThreadSessionPort(backend, base_instruction_sha256=HASH_B)
        compatibility = self.compatibility()
        root = port.create_session(compatibility, self.reconstruction(compatibility))
        candidate = port.fork_candidate(
            root,
            self.delta(
                compatibility,
                tid(IdKind.SESSION, "stored-session"),
                tid(IdKind.CHECKPOINT, "stored-root"),
            ),
        )
        backend.archive_stored_leaf(candidate.provider_thread_id)
        self.assertTrue(backend.resume_stored_thread(root.provider_thread_id))

    def test_openai_backend_materializes_non_ephemeral_threads_and_archives_leaf(self) -> None:
        codex = _FakeCodex()
        backend = OfflineOpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-sol",
            cwd=str(Path(__file__).resolve().parents[1]),
            base_instructions="stable CERA instructions",
        )
        self.assertEqual(backend.start_stored_thread(), "native-root")
        self.assertFalse(codex.start_calls[0]["ephemeral"])
        self.assertEqual(codex.start_calls[0]["environments"], [])
        self.assertEqual(backend.fork_stored_thread("native-root"), "native-child")
        self.assertFalse(codex.fork_calls[0][1]["ephemeral"])
        self.assertTrue(backend.resume_stored_thread("native-child"))
        self.assertTrue(backend.stored_thread_is_selectable("native-child"))
        expected_config = _stored_runtime_config()
        self.assertEqual(codex.start_calls[0]["config"], expected_config)
        self.assertEqual(codex.fork_calls[0][1]["config"], expected_config)
        self.assertTrue(all(value[1]["config"] == expected_config for value in codex.resume_calls))
        backend.append_model_visible_context("native-child", "[TURN ACCEPTED]\nturn:001")
        backend.archive_stored_leaf("native-child")
        self.assertFalse(backend.stored_thread_is_selectable("native-child"))
        self.assertEqual(codex.archive_calls, ["native-child"])
        rpc_methods = [value[0] for value in codex._client.calls]
        self.assertGreaterEqual(rpc_methods.count("config/read"), 3)
        self.assertGreaterEqual(rpc_methods.count("mcpServerStatus/list"), 3)
        self.assertEqual(
            [
                value
                for value in rpc_methods
                if value not in {"config/read", "mcpServerStatus/list"}
            ],
            ["thread/name/set", "thread/name/set", "thread/inject_items"],
        )
        injected = next(
            params
            for method, params, _response_model in codex._client.calls
            if method == "thread/inject_items"
        )
        self.assertEqual(injected["threadId"], "native-child")
        self.assertEqual(
            injected["items"][0]["content"][0]["text"],
            "[TURN ACCEPTED]\nturn:001",
        )

    def test_openai_backend_treats_archived_resume_rejection_as_not_resumable(self) -> None:
        codex = _FakeCodex()

        def archived_resume(_thread_id, **_kwargs):
            raise RuntimeError("Invalid request: thread is archived")

        codex.thread_resume = archived_resume
        backend = OfflineOpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-sol",
            cwd=str(Path(__file__).resolve().parents[1]),
            base_instructions="stable CERA instructions",
        )
        self.assertFalse(backend.resume_stored_thread("native-child"))

    def test_openai_backend_rejects_unqualified_sdk_version(self) -> None:
        with self.assertRaisesRegex(StateConflictError, "compatibility"):
            OpenAICodexStoredThreadBackend(
                codex=_FakeCodex(),
                model="gpt-5.6-sol",
                cwd=str(Path(__file__).resolve().parents[1]),
                base_instructions="stable CERA instructions",
                transport_version="0.0.0-unqualified",
            )

    def test_base_instruction_hash_mismatch_fails_before_thread_creation(self) -> None:
        backend = FakeStoredBackend()
        port = CodexStoredThreadSessionPort(backend, base_instruction_sha256=HASH_A)
        compatibility = self.compatibility()
        with self.assertRaisesRegex(StateConflictError, "base instructions"):
            port.create_session(compatibility, self.reconstruction(compatibility))
        self.assertEqual(backend.parents, {})


if __name__ == "__main__":
    unittest.main()
