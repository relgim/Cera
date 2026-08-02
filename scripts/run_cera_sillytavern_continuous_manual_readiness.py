#!/usr/bin/env python3
"""Provider-free two-run readiness proof for the ordinary manual CERA route."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cera.continuous.call_ledger import ContinuousProviderCallLedger
from cera.creator_review import CreatorReviewAction
from cera.serialization import (
    bytes_sha256,
    canonical_sha256,
    re_is_sha256,
)
from cera.sillytavern.campaign import (
    CONTINUOUS_V3_CALL_SCHEDULE,
    CONTINUOUS_V3_V2_RUN_IDENTITIES,
)
from cera.sillytavern.continuous_manual import (
    CONTINUOUS_V3_MANUAL_PROFILE_ID,
    ContinuousManualStateStore,
)
from cera.sillytavern.models import CERA_CONTINUOUS_V3_MANUAL_MODEL
from scripts.run_cera_sillytavern_continuous_manual import (
    PROJECT_ROOT,
    _atomic_json,
    _http_json,
    _state_identity,
    manual_execution_manifest,
    manual_status,
    reset_manual_root,
    start_manual_root,
    stop_manual_root,
    verify_isolation,
)


READINESS_ID = "continuous-sillytavern-v2-provider-free-two-run-readiness-v1"
DEFAULT_READINESS_ATTEMPT_ID = f"{READINESS_ID}-attempt-001"
FUTURE_LIVE_V2_RUN_IDS = CONTINUOUS_V3_V2_RUN_IDENTITIES
READINESS_PROMPTS = (
    "Ted knocks and asks Sakura whether this is the Hanezawa residence.",
    "Continue the doorway conversation with only the current character.",
    "The next morning, Ted is in the kitchen with Mia and asks about Sakura.",
)

_AUTHORITY_BINDINGS = {
    ".chatgpt/pro-review/checkpoints/2026-08-02-continuous-sillytavern-overnight-v2-001/PROGRESSION_1_RESULT.md": "bb81ab8b23ed380c0c60158993f109a89401f12d6d066a6ab6baf699286e1a06",
    ".chatgpt/pro-review/checkpoints/2026-08-02-continuous-sillytavern-overnight-v2-001/PROGRESSION_2_RESULT.md": "b76856f62b292be485235e6b1306f6dfb0addad741bdd8721f5734c58a96ac90",
    ".chatgpt/pro-review/cycles/2026-08-02-continuous-sillytavern-two-run-v1-cycle-001/accepted/PRO_RESPONSE.md": "c20c0ad2726908e8015cd2b88f0fcb645d272e6ac4c755e1e636b5835aaa4e11",
    ".chatgpt/pro-review/cycles/2026-08-02-continuous-sillytavern-two-run-v1-cycle-001/receipts/RESPONSE_CONSUMED.json": "4973cca7344eeea1b6ebbd3fe31883399f856b7c608b4ac1ea7f10ec3a42939e",
    ".chatgpt/pro-review/cycles/2026-08-02-continuous-sillytavern-two-run-v1-cycle-001/transaction/job4/frozen/recovery/JOB4_DETAIL.json": "66a516caf8880982cf91e7198854ff0060ed84b7340153762a56de7d2acd7b0e",
    ".chatgpt/pro-review/cycles/2026-08-02-continuous-sillytavern-two-run-v1-cycle-001/artifacts/JOB4_TERMINAL_EVIDENCE.json": "fbd5d10fbf979f576aea294b6e6239aafb2f01507ac1c656cf5f1a5b9eb9fa54",
    ".chatgpt/pro-review/cycles/2026-08-02-continuous-sillytavern-two-run-v1-cycle-001/artifacts/CYCLE_COMPLETION_NOTIFICATION_RECEIPT.json": "b4473a63a461efb3d094714cf3eebd6dff6d58e86a17cfecc21b09b5827a961e",
}


def _authority_bindings() -> dict[str, str]:
    observed: dict[str, str] = {}
    for relative, expected in _AUTHORITY_BINDINGS.items():
        path = PROJECT_ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"required readiness authority is unavailable: {relative}")
        actual = bytes_sha256(path.read_bytes())
        if actual != expected:
            raise RuntimeError(f"required readiness authority changed: {relative}")
        observed[relative] = actual
    detail_path = PROJECT_ROOT / next(
        relative for relative in _AUTHORITY_BINDINGS if relative.endswith("JOB4_DETAIL.json")
    )
    detail = json.loads(detail_path.read_text(encoding="utf-8"))
    if (
        detail.get("immutable_run_id")
        != "2026-08-02-continuous-sillytavern-two-run-v1-run-001"
        or detail.get("immutable_run_preserved") is not True
        or detail.get("provider_calls") != 1
        or detail.get("provider_call_breakdown")
        != {
            "composer_deepseek_v4_flash_non_thinking": 0,
            "planner_sol_medium": 1,
            "validator_terra_high": 0,
        }
        or detail.get("remaining_original_cycle_call_ceiling") != 39
    ):
        raise RuntimeError("immutable Cycle 21 Run 001 debit changed")
    return observed


def readiness_run_ids(attempt_id: str) -> tuple[str, str]:
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{7,159}", attempt_id):
        raise RuntimeError("provider-free readiness attempt identity is invalid")
    return (f"{attempt_id}-run-001", f"{attempt_id}-run-002")


def readiness_manifest(
    *, attempt_id: str = DEFAULT_READINESS_ATTEMPT_ID
) -> dict[str, Any]:
    route = manual_execution_manifest()
    run_ids = readiness_run_ids(attempt_id)
    payload: dict[str, Any] = {
        "schema_version": "cera.continuous_manual_readiness_manifest.v1",
        "readiness_id": READINESS_ID,
        "attempt_id": attempt_id,
        "provider_free": True,
        "external_provider_calls_authorized": 0,
        "manual_route_execution_identity_sha256": route[
            "execution_identity_sha256"
        ],
        "manual_route_manifest": route,
        "authority_bindings": _authority_bindings(),
        "progression_commits": {
            "progression_1": "2b29675352cf971be95a32a19683513f51b69cbc",
            "progression_2": "302bf33550007742ab988ba97581dd157bc284e9",
        },
        "immutable_live_predecessor": {
            "run_id": "2026-08-02-continuous-sillytavern-two-run-v1-run-001",
            "state": "failed",
            "codex_family_calls": 1,
            "deepseek_calls": 0,
            "reuse_permitted": False,
        },
        "global_budgets": {
            "codex_family_ceiling": 800,
            "codex_family_spent": 1,
            "codex_family_remaining": 799,
            "deepseek_ceiling": 800,
            "deepseek_spent": 0,
            "deepseek_remaining": 800,
        },
        "future_live_v2": {
            "run_ids": FUTURE_LIVE_V2_RUN_IDS,
            "next_unused_run_id": FUTURE_LIVE_V2_RUN_IDS[0],
            "per_run_schedule": CONTINUOUS_V3_CALL_SCHEDULE,
            "two_pass_codex_family_calls": 14,
            "two_pass_deepseek_calls": 6,
            "restart_between_passing_runs_required": True,
            "in_place_retry_permitted": False,
            "provider_call_authority_active": False,
        },
        "provider_free_readiness": {
            "run_ids": run_ids,
            "prompts": READINESS_PROMPTS,
            "per_run_local_schedule": CONTINUOUS_V3_CALL_SCHEDULE,
            "expected_local_invocations": 20,
            "expected_external_provider_calls": 0,
        },
        "notification_transport_order": (
            "native_exact_thread",
            "verified_exact_chatgpt_browser_conversation",
            "durable_transport_blocked_receipt",
        ),
    }
    return {**payload, "readiness_manifest_sha256": canonical_sha256(payload)}


def _validate_review(review: dict[str, Any]) -> None:
    active_cast = review.get("active_character_ids")
    if (
        review.get("state") != "review_ready"
        or review.get("provisional") is not True
        or review.get("accept_enabled") is not True
        or review.get("decision_pending") is not False
        or review.get("assessment", {}).get("severity") != "good"
        or review.get("assessment", {}).get("publication_eligibility")
        != "accept_allowed"
        or not isinstance(active_cast, list)
        or not active_cast
        or len(set(active_cast)) != len(active_cast)
        or any(
            not isinstance(value, str) or not value.startswith("character:")
            for value in active_cast
        )
    ):
        raise RuntimeError("provider-free readiness review is not strictly Accept eligible")
    for field in (
        "candidate_sha256",
        "candidate_text_sha256",
        "sequence_plan_sha256",
        "validator_package_sha256",
        "review_binding_sha256",
        "raw_source_sha256",
        "ingress_receipt_sha256",
        "execution_identity_sha256",
    ):
        if not re_is_sha256(review.get(field)):
            raise RuntimeError(f"provider-free readiness review lacks {field}")
    if not re_is_sha256(
        review.get("assessment", {}).get("assessment_receipt_sha256")
    ):
        raise RuntimeError("provider-free readiness assessment receipt is invalid")


def _snapshot_tree(source: Path, destination: Path) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError("refusing to overwrite readiness run evidence")
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = {
        path.relative_to(source).as_posix(): bytes_sha256(path.read_bytes())
        for path in source.rglob("*")
        if path.is_file()
    }
    with ZipFile(destination, "x", compression=ZIP_DEFLATED) as archive:
        for relative in sorted(files):
            archive.write(source / Path(relative), arcname=relative)
    return {
        "archive_path": destination.name,
        "archive_sha256": bytes_sha256(destination.read_bytes()),
        "file_count": len(files),
        "files": files,
        "files_sha256": canonical_sha256(files),
    }


def _run_one(
    *,
    run_id: str,
    manual_root: Path,
    evidence_root: Path,
    manifest: dict[str, Any],
    evidence_name: str,
) -> dict[str, Any]:
    session_id = f"{run_id}-session"
    reset = reset_manual_root(manual_root, session_id=session_id)
    if reset["execution_identity_sha256"] != manifest[
        "manual_route_execution_identity_sha256"
    ]:
        raise RuntimeError("readiness run execution identity changed before start")
    started: dict[str, Any] | None = None
    stopped: dict[str, Any] | None = None
    calls: list[str] = []
    reviews: list[dict[str, Any]] = []
    try:
        started = start_manual_root(manual_root)
        health_status, health = _http_json("GET", "/health")
        model_status, models = _http_json("GET", "/v1/models")
        if (
            health_status != 200
            or model_status != 200
            or health.get("model") != CERA_CONTINUOUS_V3_MANUAL_MODEL
            or health.get("reasoner_session", {}).get("profile_id")
            != CONTINUOUS_V3_MANUAL_PROFILE_ID
            or models.get("data", [{}])[0].get("id")
            != CERA_CONTINUOUS_V3_MANUAL_MODEL
        ):
            raise RuntimeError("readiness run health or model identity changed")
        schedule_offsets = ((0, 3), (3, 6), (6, 10))
        for index, (prompt, offsets) in enumerate(
            zip(READINESS_PROMPTS, schedule_offsets, strict=True),
            start=1,
        ):
            status, reply = _http_json(
                "POST",
                "/v1/chat/completions",
                {
                    "model": CERA_CONTINUOUS_V3_MANUAL_MODEL,
                    "stream": False,
                    "cera_session_id": session_id,
                    "cera_profile_id": CONTINUOUS_V3_MANUAL_PROFILE_ID,
                    "cera_scene_change": index == 3,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=30,
            )
            if status != 200:
                raise RuntimeError(f"readiness turn {index} failed before review: {reply}")
            calls.extend(CONTINUOUS_V3_CALL_SCHEDULE[slice(*offsets)])
            review_id = reply.get("cera", {}).get("provisional_review_id")
            status, review = _http_json(
                "GET", f"/v1/cera/reviews/{review_id}", timeout=10
            )
            if status != 200:
                raise RuntimeError(f"readiness review {index} lookup failed")
            _validate_review(review)
            if review["execution_identity_sha256"] != manifest[
                "manual_route_execution_identity_sha256"
            ]:
                raise RuntimeError("readiness review execution identity changed")
            status, decision = _http_json(
                "POST",
                f"/v1/cera/reviews/{review_id}/decision",
                {"action": CreatorReviewAction.ACCEPT.value},
                timeout=20,
            )
            if (
                status != 200
                or decision.get("status") != "accepted"
                or decision.get("review_binding_sha256")
                != review["review_binding_sha256"]
            ):
                raise RuntimeError(
                    "readiness review "
                    f"{index} Accept changed: http_status={status}; "
                    f"decision_status={decision.get('status')}; "
                    "decision_binding="
                    f"{decision.get('review_binding_sha256')}; "
                    f"review_binding={review['review_binding_sha256']}"
                )
            reviews.append(
                {
                    "turn": index,
                    "review_id_sha256": canonical_sha256({"review_id": review_id}),
                    "review_binding_sha256": review["review_binding_sha256"],
                    "active_character_ids": review["active_character_ids"],
                    "validator_package_sha256": review[
                        "validator_package_sha256"
                    ],
                    "assessment_receipt_sha256": review["assessment"][
                        "assessment_receipt_sha256"
                    ],
                    "promotion_receipt_sha256": decision[
                        "promotion_receipt_sha256"
                    ],
                }
            )
        stopped = stop_manual_root(manual_root)
        state = ContinuousManualStateStore(
            manual_root / "state",
            identity=_state_identity(
                manifest["manual_route_manifest"], session_id=session_id
            ),
        )
        ledger = ContinuousProviderCallLedger(
            manual_root / "evidence" / "PROVIDER_CALL_LEDGER.jsonl",
            maximum_calls=10,
        )
        isolation = verify_isolation(manual_root)
        terminal_state = state.load()
        if (
            tuple(calls) != CONTINUOUS_V3_CALL_SCHEDULE
            or ledger.dispatched_call_count != 10
            or state.accepted_turn_ids != ("turn-001", "turn-002", "turn-003")
            or set(terminal_state["current_scene_character_ids"])
            != {"character:mia_hanezawa", "character:sakura_hanezawa"}
            or not isolation["passed"]
        ):
            raise RuntimeError("readiness run terminal state changed")
        result: dict[str, Any] = {
            "schema_version": "cera.continuous_manual_readiness_run.v1",
            "run_id": run_id,
            "status": "passed",
            "execution_identity_sha256": reset["execution_identity_sha256"],
            "process_instance_sha256": started["process_instance_sha256"],
            "terminal_record_sha256": stopped["terminal_record_sha256"],
            "process_stop_threads_sha256": stopped[
                "process_stop_threads_sha256"
            ],
            "accepted_turn_ids": state.accepted_turn_ids,
            "final_scene_character_ids": terminal_state[
                "current_scene_character_ids"
            ],
            "scene_count": 2,
            "calls": calls,
            "scripted_transport_invocations": 10,
            "external_provider_calls": 0,
            "reviews": reviews,
            "isolation": isolation,
        }
        result["run_result_sha256"] = canonical_sha256(result)
        _atomic_json(manual_root / "evidence" / "READINESS_RUN_RESULT.json", result)
        snapshot = _snapshot_tree(
            manual_root,
            evidence_root / "runs" / evidence_name,
        )
        final_reset = reset_manual_root(manual_root, session_id=session_id)
        clean_state = ContinuousManualStateStore(
            manual_root / "state",
            identity=_state_identity(
                manual_execution_manifest(), session_id=session_id
            ),
        )
        if (
            final_reset.get("generation") != 0
            or clean_state.accepted_turn_ids
            or clean_state.current_review_id is not None
            or clean_state.decision_intent is not None
            or clean_state.load()["current_scene_character_ids"]
            or manual_status(manual_root)["status"] != "stopped"
            or not verify_isolation(manual_root)["passed"]
        ):
            raise RuntimeError("readiness run did not leave generation-zero state")
        return {
            **result,
            "snapshot": snapshot,
            "clean_reset_execution_identity_sha256": final_reset[
                "execution_identity_sha256"
            ],
            "clean_generation": 0,
        }
    except BaseException:
        if started is not None and stopped is None:
            status = manual_status(manual_root, verify_execution=False)
            if status.get("status") == "running":
                stop_manual_root(manual_root)
        raise


def run_readiness(
    *, evidence_root: Path, manual_parent: Path, attempt_id: str
) -> dict[str, Any]:
    evidence_root = evidence_root.resolve()
    manual_parent = manual_parent.resolve()
    if evidence_root.exists():
        raise FileExistsError("refusing to overwrite readiness evidence")
    evidence_root.mkdir(parents=True)
    manifest = readiness_manifest(attempt_id=attempt_id)
    _atomic_json(evidence_root / "READINESS_MANIFEST.json", manifest)
    runs: list[dict[str, Any]] = []
    run_ids = readiness_run_ids(attempt_id)
    for index, run_id in enumerate(run_ids, start=1):
        current = manual_execution_manifest()
        if current != manifest["manual_route_manifest"]:
            raise RuntimeError("readiness execution bytes changed before a run")
        run = _run_one(
            run_id=run_id,
            manual_root=manual_parent / f"r{index}",
            evidence_root=evidence_root,
            manifest=manifest,
            evidence_name=f"r{index}.zip",
        )
        if runs and run["process_instance_sha256"] == runs[-1][
            "process_instance_sha256"
        ]:
            raise RuntimeError("readiness controlled process restart is absent")
        runs.append(run)
    result: dict[str, Any] = {
        "schema_version": "cera.continuous_manual_readiness_result.v1",
        "readiness_id": READINESS_ID,
        "attempt_id": attempt_id,
        "status": "passed",
        "readiness_manifest_sha256": manifest["readiness_manifest_sha256"],
        "manual_route_execution_identity_sha256": manifest[
            "manual_route_execution_identity_sha256"
        ],
        "runs": runs,
        "consecutive_passes": 2,
        "controlled_restart_between_passes": True,
        "scripted_transport_invocations": 20,
        "external_provider_calls": 0,
        "historical_codex_family_debit": 1,
        "historical_deepseek_debit": 0,
        "remaining_codex_family_ceiling": 799,
        "remaining_deepseek_ceiling": 800,
        "next_unused_live_v2_run_id": FUTURE_LIVE_V2_RUN_IDS[0],
        "live_dispatch_authorized": False,
    }
    result["readiness_result_sha256"] = canonical_sha256(result)
    _atomic_json(evidence_root / "READINESS_RESULT.json", result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument(
        "--manual-parent",
        type=Path,
        default=PROJECT_ROOT / "runtime" / "manual" / "pf-ready-v1",
        help="Use a short path under D:/AIChatBot/Cera/runtime/manual.",
    )
    args = parser.parse_args(argv)
    result = run_readiness(
        evidence_root=args.evidence_root,
        manual_parent=args.manual_parent,
        attempt_id=args.attempt_id,
    )
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
