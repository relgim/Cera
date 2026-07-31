"""Conservatively audit live calls made by governed CERA qualifications.

Every provider stage that reached a ``started`` audit-journal entry counts as
one dispatch even when a timeout prevented a provider receipt.  This may
over-count a pre-dispatch local failure, but it cannot under-count creator call
authority.  A persistent-transport probe call is counted when its immutable
evidence says dispatch began.  A later governed correction or comparison probe
is counted from its explicit provider category and dispatch-started marker. Preparation
failures without one of those markers count as zero.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from cera.serialization import canonical_json


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "evaluation" / "evidence"
DEFAULT_SOL_LIMIT = 500
DEFAULT_DEEPSEEK_LIMIT = 500
CONTINUOUS_NAME = re.compile(
    r"^continuous_ten_turn_qualification_\d{4}-\d{2}-\d{2}_v(\d+)$"
)
PERSISTENT_PROBE_NAME = re.compile(
    r"^persistent_codex_transport_probe_\d{4}-\d{2}-\d{2}_v(\d+)$"
)
PROVIDER_STAGES = {
    "scene_reasoner": "sol",
    "scene_composer": "deepseek",
    "scene_realization_verification": "sol",
}
GOVERNED_GOAL_AUTHORITIES = frozenset({"D-150", "D-167"})


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"qualification evidence must be an object: {path}")
    return value


def audit(
    *,
    sol_limit: int,
    deepseek_limit: int,
    evidence_root: Path = EVIDENCE_ROOT,
) -> dict[str, Any]:
    if sol_limit < 0 or deepseek_limit < 0:
        raise ValueError("provider limits cannot be negative")
    if not evidence_root.is_dir():
        raise ValueError(f"qualification evidence root is unavailable: {evidence_root}")
    runs: list[dict[str, Any]] = []
    transport_probes: list[dict[str, Any]] = []
    goal_probes: list[dict[str, Any]] = []
    sol_dispatches = 0
    deepseek_dispatches = 0
    candidates: list[tuple[int, Path]] = []
    probe_candidates: list[tuple[int, Path]] = []
    goal_probe_candidates: list[Path] = []
    for path in evidence_root.iterdir():
        if not path.is_dir():
            continue
        match = CONTINUOUS_NAME.fullmatch(path.name)
        if match is not None:
            candidates.append((int(match.group(1)), path))
            continue
        match = PERSISTENT_PROBE_NAME.fullmatch(path.name)
        if match is not None:
            probe_candidates.append((int(match.group(1)), path))
            continue
        summary_path = path / "summary.json"
        if summary_path.is_file():
            summary = _load_object(summary_path)
            if summary.get("goal_authority_id") in GOVERNED_GOAL_AUTHORITIES:
                goal_probe_candidates.append(path)

    for version, run_path in sorted(candidates):
        stages: set[tuple[int, str]] = set()
        turns_path = run_path / "turns"
        for evidence_path in sorted(turns_path.glob("*.json")):
            payload = _load_object(evidence_path)
            index = payload.get("index")
            if type(index) is not int or index < 1:
                raise ValueError(f"turn evidence lacks a valid index: {evidence_path}")
            if payload.get("status") == "passed":
                stages.update((index, stage) for stage in PROVIDER_STAGES)
                continue
            safe_failure = payload.get("safe_failure")
            if not isinstance(safe_failure, dict):
                continue
            journal = safe_failure.get("stage_journal", ())
            if not isinstance(journal, list):
                raise ValueError(f"failure stage journal is malformed: {evidence_path}")
            for entry in journal:
                if not isinstance(entry, dict) or entry.get("status") != "started":
                    continue
                stage = entry.get("stage")
                if stage in PROVIDER_STAGES:
                    stages.add((index, stage))

        run_sol = sum(PROVIDER_STAGES[stage] == "sol" for _, stage in stages)
        run_deepseek = sum(
            PROVIDER_STAGES[stage] == "deepseek" for _, stage in stages
        )
        sol_dispatches += run_sol
        deepseek_dispatches += run_deepseek
        summary = _load_object(run_path / "summary.json")
        runs.append(
            {
                "version": version,
                "qualification_id": summary.get("qualification_id"),
                "status": summary.get("status"),
                "sol_dispatches": run_sol,
                "deepseek_dispatches": run_deepseek,
            }
        )

    for version, probe_path in sorted(probe_candidates):
        summary = _load_object(probe_path / "summary.json")
        calls = summary.get("calls")
        if not isinstance(calls, list):
            raise ValueError(f"persistent probe calls are malformed: {probe_path}")
        seen_indexes: set[int] = set()
        seen_evidence_identities: set[str] = set()
        probe_sol = 0
        for position, call in enumerate(calls, start=1):
            if not isinstance(call, dict):
                raise ValueError(
                    f"persistent probe call {position} is malformed: {probe_path}"
                )
            index = call.get("index")
            evidence_identity = call.get("evidence_identity")
            role = call.get("role")
            dispatch_started = call.get("dispatch_started")
            if type(index) is not int or index < 1 or index in seen_indexes:
                raise ValueError(
                    f"persistent probe call index is invalid: {probe_path}"
                )
            if (
                not isinstance(evidence_identity, str)
                or not evidence_identity.strip()
                or evidence_identity in seen_evidence_identities
            ):
                raise ValueError(
                    f"persistent probe evidence identity is invalid: {probe_path}"
                )
            if role not in {"scene_reasoner", "scene_realization_verifier"}:
                raise ValueError(f"persistent probe role is invalid: {probe_path}")
            if type(dispatch_started) is not bool:
                raise ValueError(
                    f"persistent probe dispatch marker is invalid: {probe_path}"
                )
            seen_indexes.add(index)
            seen_evidence_identities.add(evidence_identity)
            if dispatch_started:
                probe_sol += 1
        sol_dispatches += probe_sol
        transport_probes.append(
            {
                "version": version,
                "qualification_id": summary.get("qualification_id"),
                "status": summary.get("status"),
                "sol_dispatches": probe_sol,
                "deepseek_dispatches": 0,
            }
        )

    for probe_path in sorted(goal_probe_candidates):
        summary = _load_object(probe_path / "summary.json")
        provider_stages = summary.get("provider_stages")
        if provider_stages is not None:
            if not isinstance(provider_stages, list) or not provider_stages:
                raise ValueError(
                    f"governed probe provider stages are invalid: {probe_path}"
                )
            seen_stage_names: set[str] = set()
            counts = {"sol": 0, "deepseek": 0}
            for stage in provider_stages:
                if not isinstance(stage, dict):
                    raise ValueError(
                        f"governed probe provider stage is malformed: {probe_path}"
                    )
                stage_name = stage.get("stage")
                category = stage.get("provider_category")
                dispatch_started = stage.get("dispatch_started")
                if (
                    not isinstance(stage_name, str)
                    or not stage_name.strip()
                    or stage_name in seen_stage_names
                    or category not in counts
                    or type(dispatch_started) is not bool
                ):
                    raise ValueError(
                        f"governed probe provider stage is invalid: {probe_path}"
                    )
                seen_stage_names.add(stage_name)
                if dispatch_started:
                    counts[category] += 1
            sol_dispatches += counts["sol"]
            deepseek_dispatches += counts["deepseek"]
            goal_probes.append(
                {
                    "evidence_directory": probe_path.name,
                    "status": summary.get("status"),
                    "provider_dispatches": counts,
                    "dispatches": counts["sol"] + counts["deepseek"],
                }
            )
            continue
        provider_category = summary.get("provider_category")
        dispatch_started = summary.get("dispatch_started")
        if provider_category not in {"sol", "deepseek"}:
            raise ValueError(
                f"governed probe provider category is invalid: {probe_path}"
            )
        if type(dispatch_started) is not bool:
            raise ValueError(
                f"governed probe dispatch marker is invalid: {probe_path}"
            )
        dispatches = 1 if dispatch_started else 0
        if provider_category == "sol":
            sol_dispatches += dispatches
        else:
            deepseek_dispatches += dispatches
        goal_probes.append(
            {
                "evidence_directory": probe_path.name,
                "status": summary.get("status"),
                "provider_category": provider_category,
                "dispatches": dispatches,
            }
        )

    sol_remaining = sol_limit - sol_dispatches
    deepseek_remaining = deepseek_limit - deepseek_dispatches
    return {
        "schema_version": "cera.continuous_live_call_budget_audit.v2",
        "accounting_policy": (
            "provider_stage_started_or_governed_probe_dispatch_started_"
            "counts_as_one_dispatch"
        ),
        "runs": runs,
        "transport_probes": transport_probes,
        "goal_probes": goal_probes,
        "totals": {
            "sol_dispatches": sol_dispatches,
            "deepseek_dispatches": deepseek_dispatches,
        },
        "limits": {"sol": sol_limit, "deepseek": deepseek_limit},
        "remaining": {"sol": sol_remaining, "deepseek": deepseek_remaining},
        "next_ten_turn_requirement": {"sol": 20, "deepseek": 10},
        "may_begin_next_ten_turn_route": (
            sol_remaining >= 20 and deepseek_remaining >= 10
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sol-limit", type=int, default=DEFAULT_SOL_LIMIT)
    parser.add_argument(
        "--deepseek-limit", type=int, default=DEFAULT_DEEPSEEK_LIMIT
    )
    args = parser.parse_args()
    if args.sol_limit < 0 or args.deepseek_limit < 0:
        parser.error("provider limits cannot be negative")
    print(canonical_json(audit(sol_limit=args.sol_limit, deepseek_limit=args.deepseek_limit)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
