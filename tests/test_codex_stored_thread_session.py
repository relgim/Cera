from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
import unittest

from cera.errors import StateConflictError
from cera.ids import IdKind, TypedId
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
from cera.serialization import text_sha256
from cera.providers.codex_worker import _runtime_config_and_environment


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
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], object]] = []

    def request(self, method, params, *, response_model):
        self.calls.append((method, params, response_model))
        return SimpleNamespace()

    def thread_set_name(self, thread_id, name):
        self.calls.append(("thread/name/set", {"threadId": thread_id, "name": name}, None))


class _FakeCodex:
    def __init__(self) -> None:
        self.start_calls: list[dict[str, object]] = []
        self.fork_calls: list[tuple[str, dict[str, object]]] = []
        self.resume_calls: list[tuple[str, dict[str, object]]] = []
        self.archive_calls: list[str] = []
        self._client = _FakeRpcClient()

    def thread_start(self, **kwargs):
        self.start_calls.append(kwargs)
        return SimpleNamespace(id="native-root")

    def thread_fork(self, thread_id, **kwargs):
        self.fork_calls.append((thread_id, kwargs))
        return SimpleNamespace(id="native-child")

    def thread_resume(self, thread_id, **kwargs):
        self.resume_calls.append((thread_id, kwargs))
        return SimpleNamespace(id=thread_id)

    def thread_archive(self, thread_id):
        self.archive_calls.append(thread_id)


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
        port = CodexStoredThreadSessionPort(
            backend, base_instruction_sha256=HASH_B
        )
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
        first = CodexStoredThreadSessionPort(
            backend, base_instruction_sha256=HASH_B
        )
        root = first.create_session(compatibility, self.reconstruction(compatibility))

        restarted = CodexStoredThreadSessionPort(
            backend, base_instruction_sha256=HASH_B
        )
        self.assertEqual(restarted.try_resume(root), root)
        backend.archived.add(root.provider_thread_id)
        self.assertIsNone(restarted.try_resume(root))

    def test_archiving_candidate_does_not_archive_parent(self) -> None:
        backend = FakeStoredBackend()
        port = CodexStoredThreadSessionPort(
            backend, base_instruction_sha256=HASH_B
        )
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
        backend = OpenAICodexStoredThreadBackend(
            codex=codex,
            model="gpt-5.6-sol",
            cwd=r"D:\AIChatBot\Cera\.tmp\stored-test",
            base_instructions="stable CERA instructions",
        )
        self.assertEqual(backend.start_stored_thread(), "native-root")
        self.assertFalse(codex.start_calls[0]["ephemeral"])
        self.assertEqual(
            backend.fork_stored_thread("native-root"), "native-child"
        )
        self.assertFalse(codex.fork_calls[0][1]["ephemeral"])
        self.assertTrue(backend.resume_stored_thread("native-child"))
        backend.archive_stored_leaf("native-child")
        self.assertEqual(codex.archive_calls, ["native-child"])
        self.assertEqual(
            [value[0] for value in codex._client.calls],
            ["thread/name/set", "thread/name/set"],
        )

    def test_openai_backend_rejects_unqualified_sdk_version(self) -> None:
        with self.assertRaisesRegex(StateConflictError, "compatibility"):
            OpenAICodexStoredThreadBackend(
                codex=_FakeCodex(),
                model="gpt-5.6-sol",
                cwd=r"D:\AIChatBot\Cera\.tmp\stored-test",
                base_instructions="stable CERA instructions",
                transport_version="0.0.0-unqualified",
            )

    def test_base_instruction_hash_mismatch_fails_before_thread_creation(self) -> None:
        backend = FakeStoredBackend()
        port = CodexStoredThreadSessionPort(
            backend, base_instruction_sha256=HASH_A
        )
        compatibility = self.compatibility()
        with self.assertRaisesRegex(StateConflictError, "base instructions"):
            port.create_session(compatibility, self.reconstruction(compatibility))
        self.assertEqual(backend.parents, {})

    def test_request_bound_mcp_environment_is_rebuilt_per_stored_turn(self) -> None:
        def binding(token: str, port: int) -> dict[str, object]:
            return {
                "server_name": "cera_request_evidence",
                "url": f"http://127.0.0.1:{port}/mcp",
                "bearer_token_environment_variable": "CERA_REQUEST_EVIDENCE_TOKEN",
                "bearer_token": token,
                "enabled_tools": ["cera_get_turn_snapshot"],
                "binding_sha256": HASH_A,
                "required": True,
                "startup_timeout_seconds": 10,
                "tool_timeout_seconds": 10,
                "minimum_tool_calls": 0,
                "maximum_tool_calls": 12,
            }

        first_config, first_env = _runtime_config_and_environment(
            binding("first-secret", 41001)
        )
        second_config, second_env = _runtime_config_and_environment(
            binding("second-secret", 41002)
        )
        self.assertEqual(first_env, {"CERA_REQUEST_EVIDENCE_TOKEN": "first-secret"})
        self.assertEqual(second_env, {"CERA_REQUEST_EVIDENCE_TOKEN": "second-secret"})
        self.assertNotIn("first-secret", repr(second_config))
        self.assertNotIn("second-secret", repr(second_config))
        self.assertNotEqual(first_config["mcp_servers"], second_config["mcp_servers"])


if __name__ == "__main__":
    unittest.main()
