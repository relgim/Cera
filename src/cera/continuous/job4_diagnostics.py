"""Privacy-safe, hash-bound per-test evidence for governed Job 4 audits."""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from pathlib import Path, PurePosixPath
import re
import time
import traceback
from types import TracebackType
from typing import Any, Mapping, Sequence, TextIO
import unittest

from cera.serialization import canonical_sha256, text_sha256


_SHA256 = re.compile(r"[0-9a-f]{64}")
_TEST_ID = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+){2,}")
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_.<>]*")
_SAFE_SKIP_REASON = re.compile(r"[A-Za-z0-9 _.,:;()\[\]/+-]{1,256}")
_ALLOWED_SOURCE_PREFIXES = (
    "src/",
    "scripts/",
    "tools/",
    "tests/",
    ".chatgpt/pro-review/cycles/",
)
_TERMINAL_STATUSES = {"passed", "failed", "error", "skipped"}


def _closed(raw: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{label} must be an object")
    value = dict(raw)
    if set(value) != keys:
        missing = sorted(keys - set(value))
        extra = sorted(set(value) - keys)
        raise ValueError(
            f"{label} fields changed: missing={missing}, extra={extra}"
        )
    return value


def _sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _optional_string(value: object, label: str, *, limit: int = 512) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"{label} must be null or a bounded non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class ContinuousJob4SourceFrameV1:
    relative_path: str
    function: str
    line: int

    SCHEMA_VERSION = "cera.continuous_job4_source_frame.v1"

    def __post_init__(self) -> None:
        path = PurePosixPath(self.relative_path)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in self.relative_path
            or len(self.relative_path) > 320
            or not any(self.relative_path.startswith(prefix) for prefix in _ALLOWED_SOURCE_PREFIXES)
        ):
            raise ValueError("Job 4 source frame is outside the repository evidence scope")
        if not isinstance(self.function, str) or _SYMBOL.fullmatch(self.function) is None:
            raise ValueError("Job 4 source frame function is invalid")
        if not isinstance(self.line, int) or isinstance(self.line, bool) or self.line < 1:
            raise ValueError("Job 4 source frame line is invalid")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "relative_path": self.relative_path,
            "function": self.function,
            "line": self.line,
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4SourceFrameV1":
        value = _closed(
            raw,
            {"schema_version", "relative_path", "function", "line"},
            "Job 4 source frame",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("Job 4 source frame schema changed")
        return cls(
            relative_path=value["relative_path"],
            function=value["function"],
            line=value["line"],
        )


@dataclass(frozen=True, slots=True)
class ContinuousJob4TestRecordV1:
    index: int
    test_id: str
    status: str
    elapsed_ns: int
    skip_reason: str | None = None
    skip_reason_sha256: str | None = None
    exception_type: str | None = None
    message_sha256: str | None = None
    source_frames: tuple[ContinuousJob4SourceFrameV1, ...] = ()

    SCHEMA_VERSION = "cera.continuous_job4_test_record.v1"

    def __post_init__(self) -> None:
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 1:
            raise ValueError("Job 4 test record index is invalid")
        if not isinstance(self.test_id, str) or _TEST_ID.fullmatch(self.test_id) is None:
            raise ValueError("Job 4 test ID is not fully qualified")
        if self.status not in _TERMINAL_STATUSES:
            raise ValueError("Job 4 test terminal status is invalid")
        if (
            not isinstance(self.elapsed_ns, int)
            or isinstance(self.elapsed_ns, bool)
            or self.elapsed_ns < 0
        ):
            raise ValueError("Job 4 test elapsed time is invalid")
        reason = _optional_string(self.skip_reason, "skip_reason", limit=256)
        exception_type = _optional_string(
            self.exception_type, "exception_type", limit=256
        )
        for frame in self.source_frames:
            if not isinstance(frame, ContinuousJob4SourceFrameV1):
                raise ValueError("Job 4 test source frame is invalid")
        if len(self.source_frames) > 16:
            raise ValueError("Job 4 test source frames are unbounded")
        if self.status == "skipped":
            if reason is None or self.skip_reason_sha256 is None:
                raise ValueError("skipped Job 4 test lacks a reason binding")
            _sha256(self.skip_reason_sha256, "skip_reason_sha256")
            if any(
                value is not None
                for value in (exception_type, self.message_sha256)
            ) or self.source_frames:
                raise ValueError("skipped Job 4 test contains failure evidence")
        elif reason is not None or self.skip_reason_sha256 is not None:
            raise ValueError("non-skipped Job 4 test contains skip evidence")
        if self.status in {"failed", "error"}:
            if exception_type is None or self.message_sha256 is None:
                raise ValueError("failed Job 4 test lacks exception evidence")
            _sha256(self.message_sha256, "message_sha256")
            if not self.source_frames:
                raise ValueError("failed Job 4 test lacks repository source frames")
        elif exception_type is not None or self.message_sha256 is not None:
            raise ValueError("successful Job 4 test contains exception evidence")

    def _unsigned_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "index": self.index,
            "test_id": self.test_id,
            "status": self.status,
            "elapsed_ns": self.elapsed_ns,
            "skip_reason": self.skip_reason,
            "skip_reason_sha256": self.skip_reason_sha256,
            "exception_type": self.exception_type,
            "message_sha256": self.message_sha256,
            "source_frames": [frame.to_dict() for frame in self.source_frames],
        }

    @property
    def record_sha256(self) -> str:
        return canonical_sha256(self._unsigned_dict())

    def to_dict(self) -> dict[str, Any]:
        return {**self._unsigned_dict(), "record_sha256": self.record_sha256}

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4TestRecordV1":
        value = _closed(
            raw,
            {
                "schema_version",
                "index",
                "test_id",
                "status",
                "elapsed_ns",
                "skip_reason",
                "skip_reason_sha256",
                "exception_type",
                "message_sha256",
                "source_frames",
                "record_sha256",
            },
            "Job 4 test record",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("Job 4 test record schema changed")
        if not isinstance(value["source_frames"], list):
            raise ValueError("Job 4 test source frames must be an array")
        record = cls(
            index=value["index"],
            test_id=value["test_id"],
            status=value["status"],
            elapsed_ns=value["elapsed_ns"],
            skip_reason=value["skip_reason"],
            skip_reason_sha256=value["skip_reason_sha256"],
            exception_type=value["exception_type"],
            message_sha256=value["message_sha256"],
            source_frames=tuple(
                ContinuousJob4SourceFrameV1.from_dict(frame)
                for frame in value["source_frames"]
            ),
        )
        if _sha256(value["record_sha256"], "record_sha256") != record.record_sha256:
            raise ValueError("Job 4 test record self-hash changed")
        return record


@dataclass(frozen=True, slots=True)
class ContinuousJob4TestDiagnosticsV1:
    records: tuple[ContinuousJob4TestRecordV1, ...]
    selected_test_ids: tuple[str, ...]

    SCHEMA_VERSION = "cera.continuous_job4_test_diagnostics.v1"
    ROOT_SCHEMA_VERSION = "cera.continuous_job4_test_records_root.v1"
    SELECTION_ROOT_SCHEMA_VERSION = (
        "cera.continuous_job4_selected_test_ids_root.v1"
    )

    def __post_init__(self) -> None:
        if not self.records:
            raise ValueError("Job 4 test diagnostics cannot be empty")
        if (
            not self.selected_test_ids
            or len(self.selected_test_ids) != len(set(self.selected_test_ids))
            or any(
                not isinstance(test_id, str)
                or _TEST_ID.fullmatch(test_id) is None
                for test_id in self.selected_test_ids
            )
        ):
            raise ValueError("Job 4 selected test identities are invalid")
        ids: set[str] = set()
        for expected_index, record in enumerate(self.records, 1):
            if not isinstance(record, ContinuousJob4TestRecordV1):
                raise ValueError("Job 4 diagnostic record is invalid")
            if record.index != expected_index:
                raise ValueError("Job 4 diagnostic records are reordered")
            if record.test_id in ids:
                raise ValueError("Job 4 diagnostic test IDs are duplicated")
            ids.add(record.test_id)
        if tuple(record.test_id for record in self.records) != self.selected_test_ids:
            raise ValueError("Job 4 diagnostic test selection was substituted")

    @property
    def records_root_sha256(self) -> str:
        return canonical_sha256(
            {
                "schema_version": self.ROOT_SCHEMA_VERSION,
                "record_sha256s": [record.record_sha256 for record in self.records],
            }
        )

    @property
    def selection_root_sha256(self) -> str:
        return canonical_sha256(
            {
                "schema_version": self.SELECTION_ROOT_SCHEMA_VERSION,
                "selected_test_ids": list(self.selected_test_ids),
            }
        )

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    @property
    def status_counts(self) -> dict[str, int]:
        return {
            status: sum(record.status == status for record in self.records)
            for status in ("passed", "failed", "error", "skipped")
        }

    @property
    def all_acceptable(self) -> bool:
        return not any(
            record.status in {"failed", "error"} for record in self.records
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "record_count": len(self.records),
            "selected_test_ids": list(self.selected_test_ids),
            "selection_root_sha256": self.selection_root_sha256,
            "records_root_sha256": self.records_root_sha256,
            "status_counts": self.status_counts,
            "records": [record.to_dict() for record in self.records],
        }

    @classmethod
    def from_dict(cls, raw: object) -> "ContinuousJob4TestDiagnosticsV1":
        value = _closed(
            raw,
            {
                "schema_version",
                "record_count",
                "selected_test_ids",
                "selection_root_sha256",
                "records_root_sha256",
                "status_counts",
                "records",
            },
            "Job 4 test diagnostics",
        )
        if value["schema_version"] != cls.SCHEMA_VERSION:
            raise ValueError("Job 4 test diagnostics schema changed")
        if not isinstance(value["records"], list):
            raise ValueError("Job 4 diagnostic records must be an array")
        if not isinstance(value["selected_test_ids"], list):
            raise ValueError("Job 4 selected test IDs must be an array")
        diagnostics = cls(
            records=tuple(
                ContinuousJob4TestRecordV1.from_dict(item)
                for item in value["records"]
            ),
            selected_test_ids=tuple(value["selected_test_ids"]),
        )
        if value["record_count"] != len(diagnostics.records):
            raise ValueError("Job 4 diagnostic record count changed")
        if (
            _sha256(value["selection_root_sha256"], "selection_root_sha256")
            != diagnostics.selection_root_sha256
        ):
            raise ValueError("Job 4 selected test identity root changed")
        if (
            _sha256(value["records_root_sha256"], "records_root_sha256")
            != diagnostics.records_root_sha256
        ):
            raise ValueError("Job 4 diagnostic records root changed")
        if value["status_counts"] != diagnostics.status_counts:
            raise ValueError("Job 4 diagnostic status counts changed")
        return diagnostics


def _safe_skip_reason(reason: str) -> str:
    if _SAFE_SKIP_REASON.fullmatch(reason) is not None:
        return reason
    return "redacted_non_public_skip_reason"


def _repository_frames(
    traceback_value: TracebackType | None,
    *,
    repository_root: Path,
    test: unittest.case.TestCase,
) -> tuple[ContinuousJob4SourceFrameV1, ...]:
    frames: list[ContinuousJob4SourceFrameV1] = []
    if traceback_value is not None:
        for frame, line in traceback.walk_tb(traceback_value):
            try:
                relative = Path(frame.f_code.co_filename).resolve().relative_to(
                    repository_root
                )
                candidate = ContinuousJob4SourceFrameV1(
                    relative_path=relative.as_posix(),
                    function=frame.f_code.co_name,
                    line=line,
                )
            except (OSError, ValueError):
                continue
            frames.append(candidate)
    if not frames:
        source = inspect.getsourcefile(test.__class__)
        if source is not None:
            try:
                relative = Path(source).resolve().relative_to(repository_root)
                _, line = inspect.getsourcelines(test.__class__)
                frames.append(
                    ContinuousJob4SourceFrameV1(
                        relative_path=relative.as_posix(),
                        function=test.__class__.__qualname__,
                        line=line,
                    )
                )
            except (OSError, TypeError, ValueError):
                pass
    return tuple(frames[-16:])


class RecordingJob4TestResult(unittest.TextTestResult):
    """Text result that emits exactly one terminal record for every test."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.repository_root = Path.cwd().resolve()
        self.passed: set[str] = set()
        self._started_ns: dict[int, int] = {}
        self._outcomes: dict[int, dict[str, Any]] = {}
        self._records: list[ContinuousJob4TestRecordV1] = []

    def startTest(self, test: unittest.case.TestCase) -> None:
        self._started_ns[id(test)] = time.perf_counter_ns()
        super().startTest(test)

    def stopTest(self, test: unittest.case.TestCase) -> None:
        key = id(test)
        started = self._started_ns.pop(key, time.perf_counter_ns())
        elapsed = max(0, time.perf_counter_ns() - started)
        outcome = self._outcomes.pop(key, {"status": "passed"})
        record = ContinuousJob4TestRecordV1(
            index=len(self._records) + 1,
            test_id=test.id(),
            status=outcome["status"],
            elapsed_ns=elapsed,
            skip_reason=outcome.get("skip_reason"),
            skip_reason_sha256=outcome.get("skip_reason_sha256"),
            exception_type=outcome.get("exception_type"),
            message_sha256=outcome.get("message_sha256"),
            source_frames=outcome.get("source_frames", ()),
        )
        self._records.append(record)
        super().stopTest(test)

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        super().addSuccess(test)
        self.passed.add(test.id())
        self._outcomes[id(test)] = {"status": "passed"}

    def _exception_outcome(
        self,
        test: unittest.case.TestCase,
        err: tuple[type[BaseException], BaseException, TracebackType],
        *,
        status: str,
    ) -> None:
        exception_type, exception, traceback_value = err
        self._outcomes[id(test)] = {
            "status": status,
            "exception_type": (
                f"{exception_type.__module__}.{exception_type.__qualname__}"
            ),
            "message_sha256": text_sha256(str(exception)),
            "source_frames": _repository_frames(
                traceback_value,
                repository_root=self.repository_root,
                test=test,
            ),
        }

    def addFailure(
        self,
        test: unittest.case.TestCase,
        err: tuple[type[BaseException], BaseException, TracebackType],
    ) -> None:
        super().addFailure(test, err)
        self._exception_outcome(test, err, status="failed")

    def addError(
        self,
        test: unittest.case.TestCase,
        err: tuple[type[BaseException], BaseException, TracebackType],
    ) -> None:
        super().addError(test, err)
        self._exception_outcome(test, err, status="error")

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self._outcomes[id(test)] = {
            "status": "skipped",
            "skip_reason": _safe_skip_reason(reason),
            "skip_reason_sha256": text_sha256(reason),
        }

    def addExpectedFailure(
        self,
        test: unittest.case.TestCase,
        err: tuple[type[BaseException], BaseException, TracebackType],
    ) -> None:
        super().addExpectedFailure(test, err)
        reason = "declared_expected_failure"
        self._outcomes[id(test)] = {
            "status": "skipped",
            "skip_reason": reason,
            "skip_reason_sha256": text_sha256(reason),
        }

    def addUnexpectedSuccess(self, test: unittest.case.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        source_frames = _repository_frames(
            None,
            repository_root=self.repository_root,
            test=test,
        )
        self._outcomes[id(test)] = {
            "status": "failed",
            "exception_type": "unittest.case._UnexpectedSuccess",
            "message_sha256": text_sha256("unexpected success"),
            "source_frames": source_frames,
        }

    @property
    def diagnostics(self) -> ContinuousJob4TestDiagnosticsV1:
        records = tuple(self._records)
        return ContinuousJob4TestDiagnosticsV1(
            records=records,
            selected_test_ids=tuple(record.test_id for record in records),
        )


def run_job4_unittest_diagnostics(
    test_ids: Sequence[str],
    *,
    verbosity: int = 2,
    stream: TextIO | None = None,
) -> tuple[unittest.result.TestResult, ContinuousJob4TestDiagnosticsV1]:
    """Resolve, run, and return closed diagnostic evidence for one test matrix."""

    declared = tuple(test_ids)
    if not declared or len(declared) != len(set(declared)):
        raise ValueError("Job 4 unittest identities are empty or duplicated")
    loader = unittest.TestLoader()
    suites: list[unittest.TestSuite] = []
    expected_test_ids: list[str] = []
    for target in declared:
        suite = loader.loadTestsFromName(target)
        flattened: list[unittest.case.TestCase] = []

        def visit(value: unittest.TestSuite) -> None:
            for child in value:
                if isinstance(child, unittest.TestSuite):
                    visit(child)
                else:
                    flattened.append(child)

        visit(suite)
        if (
            not flattened
            or any(
                test.__class__.__name__ == "_FailedTest" for test in flattened
            )
        ):
            raise ValueError(f"Job 4 unittest identity did not resolve: {target}")
        resolved_ids = [test.id() for test in flattened]
        if any(_TEST_ID.fullmatch(test_id) is None for test_id in resolved_ids):
            raise ValueError(f"Job 4 unittest identity is invalid: {target}")
        expected_test_ids.extend(resolved_ids)
        suites.append(suite)
    if len(expected_test_ids) != len(set(expected_test_ids)):
        raise ValueError("Job 4 resolved unittest identities are duplicated")
    result = unittest.TextTestRunner(
        stream=stream,
        verbosity=verbosity,
        resultclass=RecordingJob4TestResult,
    ).run(unittest.TestSuite(suites))
    if not isinstance(result, RecordingJob4TestResult):
        raise RuntimeError("Job 4 diagnostic recorder was substituted")
    observed = result.diagnostics
    try:
        diagnostics = ContinuousJob4TestDiagnosticsV1(
            records=observed.records,
            selected_test_ids=tuple(expected_test_ids),
        )
    except ValueError as exc:
        raise RuntimeError("Job 4 diagnostic execution order changed") from exc
    return result, diagnostics
