from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from unittest.mock import patch

from cera.errors import ContractValidationError, StateConflictError
from cera.semantic_validation import (
    LUNA_VALIDATOR_BASE_INSTRUCTIONS,
    LUNA_VALIDATOR_PROFILE,
    FreshLunaValidatorFactory,
    SemanticValidationVerdictV1,
    SemanticVerdict,
    luna_validator_route,
)

from .test_semantic_validation_contracts import _custody, _request


@dataclass
class _FakeLunaBackend:
    verdict: SemanticValidationVerdictV1
    resumable_after_archive: bool = False
    external_provider_boundary: bool = False
    starts: list[tuple[str, str]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    archives: list[str] = field(default_factory=list)

    def start_fresh_thread(self, *, base_instructions: str, profile: str) -> str:
        self.starts.append((base_instructions, profile))
        return f"thread:luna-{len(self.starts)}"

    def run_validator_once(self, *, thread_id: str, request):
        del request
        self.calls.append(thread_id)
        return self.verdict

    def archive(self, thread_id: str) -> None:
        self.archives.append(thread_id)

    def is_resumable(self, thread_id: str) -> bool:
        return self.resumable_after_archive and thread_id in self.archives


def _pass() -> SemanticValidationVerdictV1:
    return SemanticValidationVerdictV1(
        schema_version=SemanticValidationVerdictV1.SCHEMA_VERSION,
        verdict=SemanticVerdict.PASS,
        conflict=None,
    )


class SemanticValidationSessionTests(unittest.TestCase):
    def test_each_candidate_uses_a_fresh_archived_thread(self) -> None:
        backend = _FakeLunaBackend(_pass())
        factory = FreshLunaValidatorFactory(backend)
        request = _request()
        first = factory.validate(request, _custody(request))
        second = factory.validate(request, _custody(request))
        self.assertEqual(first.verdict, _pass())
        self.assertEqual(second.verdict, _pass())
        self.assertEqual(
            backend.starts,
            [
                (LUNA_VALIDATOR_BASE_INSTRUCTIONS, LUNA_VALIDATOR_PROFILE),
                (LUNA_VALIDATOR_BASE_INSTRUCTIONS, LUNA_VALIDATOR_PROFILE),
            ],
        )
        self.assertEqual(backend.calls, ["thread:luna-1", "thread:luna-2"])
        self.assertEqual(backend.archives, ["thread:luna-1", "thread:luna-2"])

    def test_provider_failure_still_archives_candidate(self) -> None:
        class FailingBackend(_FakeLunaBackend):
            def run_validator_once(self, *, thread_id: str, request):
                del thread_id, request
                raise StateConflictError("provider failed")

        backend = FailingBackend(_pass())
        request = _request()
        with self.assertRaisesRegex(StateConflictError, "provider failed"):
            FreshLunaValidatorFactory(backend).validate(request, _custody(request))
        self.assertEqual(backend.archives, ["thread:luna-1"])

    def test_archive_must_make_thread_nonresumable(self) -> None:
        backend = _FakeLunaBackend(_pass(), resumable_after_archive=True)
        request = _request()
        with self.assertRaisesRegex(ContractValidationError, "remains resumable"):
            FreshLunaValidatorFactory(backend).validate(request, _custody(request))

    def test_dispatch_guard_blocks_before_single_use_state_changes(self) -> None:
        from cera.semantic_validation.session import FreshLunaValidatorSession

        backend = _FakeLunaBackend(_pass())
        backend.external_provider_boundary = True
        request = _request()
        session = FreshLunaValidatorSession(backend, "thread:luna-guarded")
        with patch.dict("os.environ", {"CERA_PROVIDER_DISPATCH_DISABLED": "1"}):
            with self.assertRaisesRegex(StateConflictError, "dispatch is disabled"):
                session.validate(request, _custody(request))
        backend.external_provider_boundary = False
        result = session.validate(request, _custody(request))
        self.assertEqual(result.verdict, _pass())

    def test_route_is_luna_xhigh_without_retry_or_fallback(self) -> None:
        route = luna_validator_route()
        self.assertEqual(route.model_name, "gpt-5.6-luna")
        self.assertEqual(route.reasoning_effort, "xhigh")
        self.assertEqual(route.automatic_retry_count, 0)
        self.assertFalse(route.fallback_enabled)
        self.assertFalse(route.production_enabled)


if __name__ == "__main__":
    unittest.main()
