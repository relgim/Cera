"""Explicitly marked offline subclasses for provider-boundary tests.

These classes may only wrap locally substituted process, HTTP, or SDK seams.
Production concrete adapters remain unconditionally external and fail closed
under the provider-free dispatch guard.
"""

from __future__ import annotations

from cera.pi_scene.pi_adapter import PiSceneAdapter
from cera.providers.codex import (
    PersistentNoMcpCodexRunner,
    StoredCodexThreadRunner,
    _SubprocessCodexRunner,
)
from cera.providers.codex_exec import CodexExecRunner
from cera.providers.deepseek import DeepSeekChatTransport
from cera.reasoner_session.codex_stored import OpenAICodexStoredThreadBackend


class OfflineDeepSeekChatTransport(DeepSeekChatTransport):
    external_provider_boundary = False

    def __init__(self, *args, external_provider_boundary: bool = False, **kwargs) -> None:
        if external_provider_boundary is not False:
            raise ValueError("offline DeepSeek transport marker must remain false")
        super().__init__(*args, **kwargs)


class OfflineCodexExecRunner(CodexExecRunner):
    external_provider_boundary = False


class OfflineSubprocessCodexRunner(_SubprocessCodexRunner):
    external_provider_boundary = False


class OfflineStoredCodexThreadRunner(StoredCodexThreadRunner):
    external_provider_boundary = False


class OfflinePersistentNoMcpCodexRunner(PersistentNoMcpCodexRunner):
    external_provider_boundary = False


class OfflinePiSceneAdapter(PiSceneAdapter):
    external_provider_boundary = False


class OfflineOpenAICodexStoredThreadBackend(OpenAICodexStoredThreadBackend):
    external_provider_boundary = False
