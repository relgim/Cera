"""Filesystem custody for lean accepted turns and post-Accept records.

The accepted-turn directory is published atomically.  Its receipt never
changes.  Recorder attempts and their small status head are separate, so a
Recorder or reporting failure cannot erase or recommit visible canon.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Any, ClassVar
from uuid import uuid4

from cera.errors import ContractValidationError, StateConflictError
from cera.schema import from_mapping
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticVerdict,
)
from cera.serialization import (
    canonical_json,
    canonical_sha256,
    re_is_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import (
    AdultCodexProjectionV1,
    AdultCodexProjectionV2,
    AdultFullRecordV1,
    LeanAcceptedTurnReceiptV1,
    LeanCandidateV1,
    LeanRecordingAttemptV1,
    OrdinarySceneRecordV1,
    PiWriterReceiptV1,
    RecordingStatus,
    SceneRoute,
    validate_adult_records,
    validate_ordinary_record,
)
from .lineage import (
    LeanAcceptedRegenerationBaseV1,
    LeanActiveLineageV1,
    accepted_object_directory_name,
    active_lineage_from_payload,
    active_lineage_payload,
    receipt_sha_index,
    selected_receipt_chain,
)
from .ordinary_rejection_policy import (
    OrdinaryPolicyAcceptanceAuditV1,
    build_ordinary_policy_acceptance_audit,
)
from .review_lifecycle import (
    OrdinaryPythonQualificationV1,
    OrdinaryValidationInputBindingV1,
)

if TYPE_CHECKING:
    from cera.adult_pipeline.acceptance import AdultAcceptedTurnEnvelopeV1
    from cera.adult_pipeline.contracts import (
        AdultPromotionBundleV1,
        AdultRouteStateSnapshotV1,
        BoundAdultPromotionV1,
    )


@dataclass(frozen=True, slots=True)
class _RecordingHeadV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.recording_head.v1"

    schema_version: str
    accepted_turn_id: str
    status: RecordingStatus
    attempt_number: int
    attempt_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("recording-head schema changed")
        if type(self.accepted_turn_id) is not str or not self.accepted_turn_id.strip():
            raise ContractValidationError("recording-head accepted turn is invalid")
        if type(self.attempt_number) is not int or self.attempt_number < 0:
            raise ContractValidationError("recording-head attempt number is invalid")
        if type(self.status) is not RecordingStatus:
            raise ContractValidationError("recording-head status is invalid")
        if self.status is RecordingStatus.PROJECTION_PENDING:
            if self.attempt_number != 0 or self.attempt_sha256 is not None:
                raise ContractValidationError(
                    "projection-pending recording head must remain at attempt zero"
                )
        elif (
            self.attempt_number < 1
            or type(self.attempt_sha256) is not str
            or not re_is_sha256(self.attempt_sha256)
        ):
            raise ContractValidationError("recording-head attempt binding is invalid")


@dataclass(frozen=True, slots=True)
class LeanAcceptanceDecisionAuditV1:
    """Content-free decision identity published with one accepted object."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.acceptance_decision_audit.v1"

    schema_version: str
    candidate_sha256: str
    acceptance_action: str
    decision_request_sha256: str
    override_feedback_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("acceptance decision-audit schema changed")
        if self.acceptance_action not in {
            "accept",
            "automatic_accept",
            "provisional_accept",
        }:
            raise ContractValidationError("acceptance decision-audit action is invalid")
        if not re_is_sha256(self.candidate_sha256) or not re_is_sha256(
            self.decision_request_sha256
        ):
            raise ContractValidationError("acceptance decision-audit binding is invalid")
        if self.override_feedback_sha256 is not None and not re_is_sha256(
            self.override_feedback_sha256
        ):
            raise ContractValidationError("override feedback audit binding is invalid")
        if (self.acceptance_action == "provisional_accept") != (
            self.override_feedback_sha256 is not None
        ):
            raise ContractValidationError(
                "override feedback audit differs from the acceptance action"
            )


@dataclass(frozen=True, slots=True)
class _BranchHeadCacheV1:
    """Durable branch-head anchor used to detect coherent receipt rewrites."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.branch_head_cache.v1"

    schema_version: str
    generation: int
    accepted_turn_id: str | None
    accepted_head_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("branch-head cache schema changed")
        if type(self.generation) is not int or self.generation < 0:
            raise ContractValidationError("branch-head cache generation is invalid")
        if self.generation == 0:
            if self.accepted_turn_id is not None or self.accepted_head_sha256 is not None:
                raise ContractValidationError("root branch-head cache contains a turn")
        elif (
            type(self.accepted_turn_id) is not str
            or not self.accepted_turn_id.strip()
            or type(self.accepted_head_sha256) is not str
            or not re_is_sha256(self.accepted_head_sha256)
        ):
            raise ContractValidationError("branch-head cache binding is invalid")


@dataclass(frozen=True, slots=True)
class _RecordingBundleManifestV1:
    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.recording_bundle_manifest.v1"

    schema_version: str
    accepted_turn_id: str
    attempt_number: int
    attempt_sha256: str
    route: SceneRoute
    ordinary_record_sha256: str | None
    adult_full_record_sha256: str | None
    adult_projection_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("recording-bundle schema changed")
        if type(self.accepted_turn_id) is not str or not self.accepted_turn_id.strip():
            raise ContractValidationError("recording-bundle accepted turn is invalid")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ContractValidationError("recording-bundle attempt number is invalid")
        if type(self.route) is not SceneRoute:
            raise ContractValidationError("recording-bundle route is invalid")
        for field_name in (
            "attempt_sha256",
            "ordinary_record_sha256",
            "adult_full_record_sha256",
            "adult_projection_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not str or not re_is_sha256(value)):
                raise ContractValidationError(f"recording-bundle {field_name} is invalid")
        if type(self.attempt_sha256) is not str:
            raise ContractValidationError("recording-bundle attempt_sha256 is invalid")
        ordinary = self.ordinary_record_sha256 is not None
        adult = (
            self.adult_full_record_sha256 is not None and self.adult_projection_sha256 is not None
        )
        if ordinary == adult:
            raise ContractValidationError("recording bundle must contain one route shape")
        if ordinary != (self.route is SceneRoute.ORDINARY):
            raise ContractValidationError("recording bundle route shape changed")


@dataclass(frozen=True, slots=True)
class _RecordingBundleManifestV2:
    """Python-owned binding between one record bundle and accepted prose."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.recording_bundle_manifest.v2"

    schema_version: str
    accepted_turn_id: str
    accepted_receipt_sha256: str
    exact_accepted_prose_sha256: str
    primary_authority_sha256: str
    attempt_number: int
    attempt_sha256: str
    route: SceneRoute
    ordinary_record_sha256: str | None
    adult_full_record_sha256: str | None
    adult_projection_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("recording-bundle V2 schema changed")
        if type(self.accepted_turn_id) is not str or not self.accepted_turn_id.strip():
            raise ContractValidationError("recording-bundle accepted turn is invalid")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ContractValidationError("recording-bundle attempt number is invalid")
        if type(self.route) is not SceneRoute:
            raise ContractValidationError("recording-bundle route is invalid")
        for field_name in (
            "accepted_receipt_sha256",
            "exact_accepted_prose_sha256",
            "primary_authority_sha256",
            "attempt_sha256",
            "ordinary_record_sha256",
            "adult_full_record_sha256",
            "adult_projection_sha256",
        ):
            value = getattr(self, field_name)
            if value is not None and (type(value) is not str or not re_is_sha256(value)):
                raise ContractValidationError(f"recording-bundle {field_name} is invalid")
        ordinary = self.ordinary_record_sha256 is not None
        adult = (
            self.adult_full_record_sha256 is not None and self.adult_projection_sha256 is not None
        )
        if ordinary == adult:
            raise ContractValidationError("recording bundle must contain one route shape")
        if ordinary != (self.route is SceneRoute.ORDINARY):
            raise ContractValidationError("recording bundle route shape changed")


_RecordingBundleManifest = _RecordingBundleManifestV1 | _RecordingBundleManifestV2


@dataclass(frozen=True, slots=True)
class _AtomicAdultPromotionManifestV1:
    """Hash-only index for one pre-accept-filtered adult transaction.

    The protected envelope is stored beside this manifest but is never copied
    into ordinary context.  The separate projection file lets an ordinary
    reader consume only Filter-approved non-explicit bytes.
    """

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.atomic_adult_promotion.v1"

    schema_version: str
    accepted_turn_id: str
    accepted_receipt_sha256: str
    envelope_sha256: str
    promotion_bundle_sha256: str
    protected_full_record_sha256: str
    codex_projection_sha256: str
    promotion_receipt_sha256: str
    scene_session_binding_sha256: str
    current_logic_route: str
    return_to_codex: bool

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("atomic adult promotion schema changed")
        if type(self.accepted_turn_id) is not str or not self.accepted_turn_id.strip():
            raise ContractValidationError("atomic adult accepted turn is invalid")
        for field_name in (
            "accepted_receipt_sha256",
            "envelope_sha256",
            "promotion_bundle_sha256",
            "protected_full_record_sha256",
            "codex_projection_sha256",
            "promotion_receipt_sha256",
            "scene_session_binding_sha256",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not re_is_sha256(value):
                raise ContractValidationError(
                    f"atomic adult {field_name} is invalid"
                )
        if self.current_logic_route not in {"ordinary", "adult"}:
            raise ContractValidationError("atomic adult next route is invalid")
        if type(self.return_to_codex) is not bool:
            raise ContractValidationError("atomic adult return flag is invalid")
        if self.return_to_codex != (
            self.current_logic_route == "ordinary"
        ):
            raise ContractValidationError("atomic adult route and return flag disagree")


@dataclass(frozen=True, slots=True)
class _CompleteRecordingBundle:
    attempt: LeanRecordingAttemptV1
    ordinary_record: OrdinarySceneRecordV1 | None = None
    adult_full_record: AdultFullRecordV1 | None = None
    adult_projection: AdultCodexProjectionV1 | AdultCodexProjectionV2 | None = None


@dataclass(frozen=True, slots=True)
class LeanAcceptedHeadV1:
    world_id: str
    branch_id: str
    generation: int
    accepted_turn_id: str | None
    accepted_head_sha256: str | None
    receipt: LeanAcceptedTurnReceiptV1 | None
    recording_status: RecordingStatus | None


@dataclass(frozen=True, slots=True)
class AcceptedPiSessionV1:
    accepted_turn_id: str
    session_id: str
    session_path: str
    session_id_sha256: str
    accepted_receipt_sha256: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.accepted_turn_id) is not str
            or type(self.session_id) is not str
            or type(self.session_path) is not str
            or type(self.session_id_sha256) is not str
            or not self.accepted_turn_id.strip()
            or not self.session_id.strip()
            or not self.session_path.strip()
        ):
            raise ContractValidationError("accepted Pi session identity is incomplete")
        if text_sha256(self.session_id) != self.session_id_sha256:
            raise ContractValidationError("accepted Pi session binding changed")
        if self.accepted_receipt_sha256 is not None and not re_is_sha256(
            self.accepted_receipt_sha256
        ):
            raise ContractValidationError("accepted Pi session receipt binding is invalid")


class LeanSceneStore:
    """Branch-scoped append-first store with restart reconciliation."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()

    def _branch_root(self, world_id: str, branch_id: str) -> Path:
        if (
            type(world_id) is not str
            or type(branch_id) is not str
            or not world_id.strip()
            or not branch_id.strip()
        ):
            raise ContractValidationError("world and branch identities are required")
        world_key = f"world-{text_sha256(world_id)[:24]}"
        branch_key = f"branch-{text_sha256(branch_id)[:24]}"
        root = (self.root / world_key / branch_key).resolve()
        if not root.is_relative_to(self.root):
            raise ContractValidationError("branch store escaped its configured root")
        root.mkdir(parents=True, exist_ok=True)
        identity = {"world_id": world_id, "branch_id": branch_id}
        identity_path = root / "BRANCH_IDENTITY.json"
        if identity_path.exists():
            if _read_json(identity_path) != identity:
                raise StateConflictError("branch hash collision changed identity")
        else:
            _atomic_write_json(identity_path, identity)
        (root / "accepted").mkdir(exist_ok=True)
        (root / "sessions").mkdir(exist_ok=True)
        cache_path = root / "BRANCH_HEAD_CACHE.json"
        if not cache_path.exists() and not any((root / "accepted").glob("*/ACCEPTED_RECEIPT.json")):
            _atomic_write_json(
                cache_path,
                to_primitive(
                    _BranchHeadCacheV1(
                        schema_version=_BranchHeadCacheV1.SCHEMA_VERSION,
                        generation=0,
                        accepted_turn_id=None,
                        accepted_head_sha256=None,
                    )
                ),
            )
        return root

    def load_head(self, *, world_id: str, branch_id: str) -> LeanAcceptedHeadV1:
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            _validate_receipt_chain(
                receipts,
                world_id=world_id,
                branch_id=branch_id,
            )
            self._verify_or_advance_branch_cache(branch_root, receipts)
            if not receipts:
                return LeanAcceptedHeadV1(
                    world_id=world_id,
                    branch_id=branch_id,
                    generation=0,
                    accepted_turn_id=None,
                    accepted_head_sha256=None,
                    receipt=None,
                    recording_status=None,
                )
            receipt = receipts[-1]
            return LeanAcceptedHeadV1(
                world_id=world_id,
                branch_id=branch_id,
                generation=receipt.generation,
                accepted_turn_id=receipt.accepted_turn_id,
                accepted_head_sha256=receipt.receipt_sha256,
                receipt=receipt,
                recording_status=self.recording_status(receipt),
            )

    def current_logic_route(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AdultRouteStateSnapshotV1:
        """Reconstruct the current logic owner from verified accepted state."""

        from cera.adult_pipeline.contracts import (
            AdultNextRoute,
            AdultRouteStateSnapshotV1,
        )

        with self._lock:
            head = self.load_head(world_id=world_id, branch_id=branch_id)
            if head.receipt is None:
                return AdultRouteStateSnapshotV1(
                    schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
                    world_id=world_id,
                    branch_id=branch_id,
                    accepted_head_sha256=None,
                    current_logic_route=AdultNextRoute.ORDINARY,
                    source_promotion_sha256=None,
                )
            branch_root = self._branch_root(world_id, branch_id)
            turn_dir = _locate_accepted_turn_dir(branch_root, head.receipt)
            atomic = _load_atomic_adult_promotion(turn_dir, accepted=head.receipt)
            if atomic is not None:
                _, manifest, _ = atomic
                route = AdultNextRoute(manifest.current_logic_route)
                source = manifest.promotion_bundle_sha256
            else:
                # Historical receipts have no explicit next-route artifact.
                # Their verified route is the safest non-invented continuation
                # state: ordinary stays ordinary; protected adult stays adult.
                route = (
                    AdultNextRoute.ADULT
                    if head.receipt.route is SceneRoute.ADULT
                    else AdultNextRoute.ORDINARY
                )
                source = head.receipt.receipt_sha256
            return AdultRouteStateSnapshotV1(
                schema_version=AdultRouteStateSnapshotV1.SCHEMA_VERSION,
                world_id=world_id,
                branch_id=branch_id,
                accepted_head_sha256=head.accepted_head_sha256,
                current_logic_route=route,
                source_promotion_sha256=source,
            )

    def adult_promotion_port(
        self,
        envelope: AdultAcceptedTurnEnvelopeV1,
    ) -> AdultEnvelopePromotionPort:
        """Bind the bare-bundle protocol to one complete Python envelope."""

        return AdultEnvelopePromotionPort(store=self, envelope=envelope)

    def load_active_lineage(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> LeanActiveLineageV1:
        """Return the selected head without exposing inactive sibling objects."""

        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            stored = self._load_active_lineage_manifest(branch_root)
            if stored is not None:
                return stored
            if not receipts:
                return LeanActiveLineageV1(
                    schema_version=LeanActiveLineageV1.SCHEMA_VERSION,
                    world_id=world_id,
                    branch_id=branch_id,
                    revision=0,
                    switch_kind="legacy",
                    selected_generation=0,
                    selected_turn_id=None,
                    selected_receipt_sha256=None,
                    previous_generation=0,
                    previous_turn_id=None,
                    previous_receipt_sha256=None,
                )
            head = receipts[-1]
            return LeanActiveLineageV1(
                schema_version=LeanActiveLineageV1.SCHEMA_VERSION,
                world_id=world_id,
                branch_id=branch_id,
                revision=0,
                switch_kind="legacy",
                selected_generation=head.generation,
                selected_turn_id=head.accepted_turn_id,
                selected_receipt_sha256=head.receipt_sha256,
                previous_generation=max(0, head.generation - 1),
                previous_turn_id=head.parent_accepted_turn_id,
                previous_receipt_sha256=head.parent_accepted_head_sha256,
            )

    def regeneration_base(
        self,
        receipt: LeanAcceptedTurnReceiptV1,
    ) -> LeanAcceptedRegenerationBaseV1:
        """Freeze the exact selected prefix used by an accepted Regenerate."""

        with self._lock:
            selected = self._selected_receipts_for_supplied_head(receipt)
            return LeanAcceptedRegenerationBaseV1(
                world_id=receipt.world_id,
                branch_id=receipt.branch_id,
                generation=receipt.generation,
                replaced_turn_id=receipt.accepted_turn_id,
                replaced_receipt_sha256=receipt.receipt_sha256,
                parent_accepted_turn_id=receipt.parent_accepted_turn_id,
                parent_accepted_head_sha256=receipt.parent_accepted_head_sha256,
                selected_prefix_receipt_sha256s=tuple(
                    value.receipt_sha256 for value in selected[:-1]
                ),
            )

    def regeneration_prefix_payloads(
        self,
        base: LeanAcceptedRegenerationBaseV1,
        *,
        adult_full: bool = False,
        allow_pending: bool = True,
    ) -> tuple[dict[str, Any], ...]:
        """Read only the exact pre-turn prefix bound by ``regeneration_base``."""

        if type(adult_full) is not bool or type(allow_pending) is not bool:
            raise ContractValidationError("regeneration prefix flags must be boolean")
        with self._lock:
            branch_root = self._branch_root(base.world_id, base.branch_id)
            selected = self._load_receipts(branch_root)
            if (
                not selected
                or selected[-1].receipt_sha256 != base.replaced_receipt_sha256
                or tuple(value.receipt_sha256 for value in selected[:-1])
                != base.selected_prefix_receipt_sha256s
            ):
                raise StateConflictError("regeneration base is no longer selected")
            return self._accepted_payloads(
                branch_root,
                selected[:-1],
                adult_full=adult_full,
                allow_pending=allow_pending,
            )

    def regeneration_prefix_ordinary_context_payloads(
        self,
        base: LeanAcceptedRegenerationBaseV1,
        *,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]:
        """Codex-safe context from the exact pre-Regenerate selected prefix."""

        with self._lock:
            branch_root, prefix = self._regeneration_prefix_receipts(base)
            raw = self._bounded_context_payloads(
                branch_root,
                prefix,
                limit=limit,
                adult_full=False,
            )
            output: list[dict[str, Any]] = []
            for value in raw:
                receipt = _payload_receipt(value)
                if receipt.route is SceneRoute.ADULT:
                    if "adult_projection" not in value:
                        raise StateConflictError(
                            "ordinary regeneration context requires the adult projection"
                        )
                    output.append(_reducer_payload(value))
                else:
                    output.append(value)
            return tuple(output)

    def regeneration_prefix_adult_context_payloads(
        self,
        base: LeanAcceptedRegenerationBaseV1,
        *,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]:
        """Protected adult context from the exact pre-Regenerate prefix."""

        with self._lock:
            branch_root, prefix = self._regeneration_prefix_receipts(base)
            return self._bounded_context_payloads(
                branch_root,
                prefix,
                limit=limit,
                adult_full=True,
            )

    def _regeneration_prefix_receipts(
        self,
        base: LeanAcceptedRegenerationBaseV1,
    ) -> tuple[Path, list[LeanAcceptedTurnReceiptV1]]:
        branch_root = self._branch_root(base.world_id, base.branch_id)
        selected = self._load_receipts(branch_root)
        if (
            not selected
            or selected[-1].receipt_sha256 != base.replaced_receipt_sha256
            or tuple(value.receipt_sha256 for value in selected[:-1])
            != base.selected_prefix_receipt_sha256s
        ):
            raise StateConflictError("regeneration base is no longer selected")
        return branch_root, selected[:-1]

    @staticmethod
    def _bounded_context_payloads(
        branch_root: Path,
        receipts: Sequence[LeanAcceptedTurnReceiptV1],
        *,
        limit: int,
        adult_full: bool,
    ) -> tuple[dict[str, Any], ...]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ContractValidationError("accepted context limit is invalid")
        all_payloads = LeanSceneStore._accepted_payloads(
            branch_root,
            receipts,
            adult_full=adult_full,
            allow_pending=True,
        )
        tail_start = max(0, len(all_payloads) - limit)
        return tuple(
            value
            for index, value in enumerate(all_payloads)
            if index >= tail_start
            or value.get("recording_status") != RecordingStatus.COMPLETE.value
        )

    def load_accepted_turn_by_receipt_sha256(
        self,
        *,
        world_id: str,
        branch_id: str,
        receipt_sha256: str,
    ) -> LeanAcceptedTurnReceiptV1:
        """Inspect one immutable accepted object, selected or inactive."""

        if type(receipt_sha256) is not str or not re_is_sha256(receipt_sha256):
            raise ContractValidationError("accepted receipt hash is invalid")
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            entries = self._load_all_receipt_entries(branch_root)
            matches = [receipt for receipt, _ in entries if receipt.receipt_sha256 == receipt_sha256]
            if len(matches) != 1:
                raise StateConflictError("accepted receipt hash is not uniquely stored")
            receipt = matches[0]
            if receipt.world_id != world_id or receipt.branch_id != branch_id:
                raise StateConflictError("accepted receipt escaped its branch identity")
            return receipt

    def load_acceptance_decision_audit(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
    ) -> LeanAcceptanceDecisionAuditV1 | None:
        """Load the immutable hash-only acceptance audit when this version wrote one."""

        with self._lock:
            branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
            turn_dir = _locate_accepted_turn_dir(branch_root, accepted)
            audit = _load_acceptance_decision_audit(turn_dir, accepted=accepted)
            if audit is not None and audit.candidate_sha256 != accepted.candidate_sha256:
                raise StateConflictError("acceptance decision audit changed candidate custody")
            return audit

    def load_policy_acceptance_audit(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
    ) -> OrdinaryPolicyAcceptanceAuditV1 | None:
        """Load the immutable standing-policy authority for one accepted object."""

        with self._lock:
            branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
            turn_dir = _locate_accepted_turn_dir(branch_root, accepted)
            return _load_policy_acceptance_audit(turn_dir, accepted=accepted)

    def promote_adult_acceptance_envelope(
        self,
        envelope: AdultAcceptedTurnEnvelopeV1,
    ) -> BoundAdultPromotionV1:
        """Atomically publish one fully filtered adult acceptance.

        Provider-visible bundle bytes are insufficient for this transaction;
        Python's full envelope supplies generation, parentage, exact source,
        provider/session custody, and creator action.  No post-Accept Recorder
        exists on this path.
        """

        from cera.adult_pipeline.acceptance import AdultAcceptedTurnEnvelopeV1
        from cera.adult_pipeline.contracts import (
            AdultAcceptedPromotionReceiptV1,
            BoundAdultPromotionV1,
        )

        if type(envelope) is not AdultAcceptedTurnEnvelopeV1:
            raise ContractValidationError(
                "adult atomic promotion requires the full acceptance envelope"
            )
        if envelope.creator_action == "provisional_accept":
            raise ContractValidationError(
                "adult atomic promotion does not create provisional canon"
            )
        with self._lock:
            branch_root = self._branch_root(envelope.world_id, envelope.branch_id)
            head = self.load_head(
                world_id=envelope.world_id,
                branch_id=envelope.branch_id,
            )
            existing_matches = [
                receipt
                for receipt, _ in self._load_all_receipt_entries(branch_root)
                if receipt.candidate_sha256 == envelope.envelope_sha256
            ]
            if len(existing_matches) > 1:
                raise StateConflictError("adult acceptance occurs more than once")
            if existing_matches:
                existing = existing_matches[0]
                turn_dir = _locate_accepted_turn_dir(branch_root, existing)
                loaded = _load_atomic_adult_promotion(turn_dir, accepted=existing)
                if loaded is None or loaded[0] != envelope:
                    raise StateConflictError("stored adult acceptance differs from replay")
                bound = loaded[2]
                if head.accepted_head_sha256 == existing.receipt_sha256:
                    if self._load_active_lineage_manifest(branch_root) is None:
                        previous = LeanAcceptedHeadV1(
                            world_id=existing.world_id,
                            branch_id=existing.branch_id,
                            generation=existing.generation - 1,
                            accepted_turn_id=existing.parent_accepted_turn_id,
                            accepted_head_sha256=(
                                existing.parent_accepted_head_sha256
                            ),
                            receipt=None,
                            recording_status=None,
                        )
                        self._select_receipt(
                            branch_root,
                            receipt=existing,
                            previous=previous,
                            switch_kind="append",
                        )
                    self._cache_adult_scene_session(branch_root, existing, envelope)
                    return bound
                if (
                    existing.generation != head.generation + 1
                    or existing.parent_accepted_turn_id != head.accepted_turn_id
                    or existing.parent_accepted_head_sha256
                    != head.accepted_head_sha256
                ):
                    raise StateConflictError(
                        "stored adult acceptance is outside the selected lineage"
                    )
                self._select_receipt(
                    branch_root,
                    receipt=existing,
                    previous=head,
                    switch_kind="append",
                )
                self._cache_adult_scene_session(branch_root, existing, envelope)
                return bound

            if envelope.generation != head.generation + 1:
                raise StateConflictError("adult acceptance generation changed")
            if (
                envelope.parent_accepted_turn_id != head.accepted_turn_id
                or envelope.parent_accepted_head_sha256 != head.accepted_head_sha256
                or envelope.promotion_bundle.accepted_head_before_sha256
                != head.accepted_head_sha256
            ):
                raise StateConflictError("adult acceptance parent head changed")
            receipt = _adult_receipt_from_envelope(envelope)
            promotion_receipt = AdultAcceptedPromotionReceiptV1(
                schema_version=AdultAcceptedPromotionReceiptV1.SCHEMA_VERSION,
                accepted_turn_id=receipt.accepted_turn_id,
                world_id=receipt.world_id,
                branch_id=receipt.branch_id,
                accepted_head_before_sha256=receipt.parent_accepted_head_sha256,
                accepted_head_after_sha256=receipt.receipt_sha256,
                promotion_bundle_sha256=canonical_sha256(envelope.promotion_bundle),
                current_logic_route=envelope.promotion_bundle.next_route,
                return_to_codex=envelope.promotion_bundle.return_to_codex,
            )
            bound = BoundAdultPromotionV1(
                bundle=envelope.promotion_bundle,
                receipt=promotion_receipt,
            )
            self._publish_atomic_adult_object(
                branch_root,
                envelope=envelope,
                accepted=receipt,
                bound=bound,
            )
            self._select_receipt(
                branch_root,
                receipt=receipt,
                previous=head,
                switch_kind="append",
            )
            self._cache_adult_scene_session(branch_root, receipt, envelope)
            return bound

    def promote_adult_replacement_envelope(
        self,
        envelope: AdultAcceptedTurnEnvelopeV1,
        *,
        base: LeanAcceptedRegenerationBaseV1,
    ) -> BoundAdultPromotionV1:
        """Atomically select a fully filtered adult sibling replacement.

        ``base`` freezes the exact selected turn being replaced and its
        pre-turn accepted prefix.  The new adult object is published in full
        before ``ACTIVE_LINEAGE.json`` switches; the replaced object and its
        provider/session evidence remain immutable and inspectable.
        """

        from cera.adult_pipeline.acceptance import AdultAcceptedTurnEnvelopeV1
        from cera.adult_pipeline.contracts import (
            AdultAcceptedPromotionReceiptV1,
            BoundAdultPromotionV1,
        )

        if type(envelope) is not AdultAcceptedTurnEnvelopeV1:
            raise ContractValidationError(
                "adult replacement requires the full acceptance envelope"
            )
        if type(base) is not LeanAcceptedRegenerationBaseV1:
            raise ContractValidationError(
                "adult replacement requires the frozen regeneration base"
            )
        if envelope.creator_action == "provisional_accept":
            raise ContractValidationError(
                "adult atomic replacement does not create provisional canon"
            )
        if (
            envelope.world_id != base.world_id
            or envelope.branch_id != base.branch_id
        ):
            raise StateConflictError("adult replacement escaped its frozen branch")
        if envelope.generation != base.generation:
            raise StateConflictError("adult replacement generation changed")
        if envelope.parent_accepted_turn_id != base.parent_accepted_turn_id:
            raise StateConflictError("adult replacement parent turn changed")
        if (
            envelope.parent_accepted_head_sha256
            != base.parent_accepted_head_sha256
            or envelope.promotion_bundle.accepted_head_before_sha256
            != base.parent_accepted_head_sha256
        ):
            raise StateConflictError("adult replacement parent head changed")

        with self._lock:
            branch_root = self._branch_root(base.world_id, base.branch_id)
            replaced = self.load_accepted_turn_by_receipt_sha256(
                world_id=base.world_id,
                branch_id=base.branch_id,
                receipt_sha256=base.replaced_receipt_sha256,
            )
            if (
                replaced.accepted_turn_id != base.replaced_turn_id
                or replaced.generation != base.generation
                or replaced.parent_accepted_turn_id
                != base.parent_accepted_turn_id
                or replaced.parent_accepted_head_sha256
                != base.parent_accepted_head_sha256
            ):
                raise StateConflictError(
                    "adult replacement target differs from frozen authority"
                )
            if replaced.route is not SceneRoute.ADULT:
                raise StateConflictError("adult replacement target is not adult")
            if envelope.accepted_turn_id != base.replaced_turn_id:
                raise StateConflictError("adult replacement turn identity changed")
            if (
                envelope.exact_current_source != replaced.exact_user_source
                or envelope.exact_current_source_sha256
                != replaced.exact_user_source_sha256
            ):
                raise StateConflictError("adult replacement source custody changed")
            if (
                envelope.primary_handoff_kind != replaced.primary_authority_kind
                or envelope.primary_handoff_json != replaced.primary_authority_json
                or envelope.primary_handoff_sha256
                != replaced.primary_authority_sha256
            ):
                raise StateConflictError("adult replacement settings custody changed")

            head = self.load_head(world_id=base.world_id, branch_id=base.branch_id)
            existing_matches = [
                receipt
                for receipt, _ in self._load_all_receipt_entries(branch_root)
                if receipt.candidate_sha256 == envelope.envelope_sha256
            ]
            if len(existing_matches) > 1:
                raise StateConflictError("adult replacement occurs more than once")
            existing = existing_matches[0] if existing_matches else None
            if existing is not None:
                turn_dir = _locate_accepted_turn_dir(branch_root, existing)
                loaded = _load_atomic_adult_promotion(turn_dir, accepted=existing)
                if loaded is None or loaded[0] != envelope:
                    raise StateConflictError(
                        "stored adult replacement differs from replay"
                    )
                bound = loaded[2]
                if head.accepted_head_sha256 == existing.receipt_sha256:
                    lineage = self.load_active_lineage(
                        world_id=base.world_id,
                        branch_id=base.branch_id,
                    )
                    if (
                        lineage.switch_kind != "replacement"
                        or lineage.previous_generation != base.generation
                        or lineage.previous_turn_id != base.replaced_turn_id
                        or lineage.previous_receipt_sha256
                        != base.replaced_receipt_sha256
                    ):
                        raise StateConflictError(
                            "adult replacement replay cites another selector switch"
                        )
                    self._cache_adult_scene_session(branch_root, existing, envelope)
                    return bound
                if head.accepted_head_sha256 != replaced.receipt_sha256:
                    raise StateConflictError(
                        "stored adult replacement is outside the selected lineage"
                    )
                self._regeneration_prefix_receipts(base)
                self._select_receipt(
                    branch_root,
                    receipt=existing,
                    previous=head,
                    switch_kind="replacement",
                )
                self._cache_adult_scene_session(branch_root, existing, envelope)
                return bound

            if head.accepted_head_sha256 != replaced.receipt_sha256:
                raise StateConflictError("adult replacement target is not selected")
            self._regeneration_prefix_receipts(base)
            receipt = _adult_receipt_from_envelope(envelope)
            promotion_receipt = AdultAcceptedPromotionReceiptV1(
                schema_version=AdultAcceptedPromotionReceiptV1.SCHEMA_VERSION,
                accepted_turn_id=receipt.accepted_turn_id,
                world_id=receipt.world_id,
                branch_id=receipt.branch_id,
                accepted_head_before_sha256=receipt.parent_accepted_head_sha256,
                accepted_head_after_sha256=receipt.receipt_sha256,
                promotion_bundle_sha256=canonical_sha256(
                    envelope.promotion_bundle
                ),
                current_logic_route=envelope.promotion_bundle.next_route,
                return_to_codex=envelope.promotion_bundle.return_to_codex,
            )
            bound = BoundAdultPromotionV1(
                bundle=envelope.promotion_bundle,
                receipt=promotion_receipt,
            )
            self._publish_atomic_adult_object(
                branch_root,
                envelope=envelope,
                accepted=receipt,
                bound=bound,
            )
            self._select_receipt(
                branch_root,
                receipt=receipt,
                previous=head,
                switch_kind="replacement",
            )
            self._cache_adult_scene_session(branch_root, receipt, envelope)
            return bound

    def load_promoted_adult_acceptance(
        self,
        *,
        world_id: str,
        branch_id: str,
        accepted_turn_id: str | None = None,
        promotion_bundle_sha256: str | None = None,
        request_id: str | None = None,
    ) -> tuple[AdultAcceptedTurnEnvelopeV1, BoundAdultPromotionV1]:
        """Recover one immutable adult envelope for journal/restart replay."""

        identities = (
            accepted_turn_id,
            promotion_bundle_sha256,
            request_id,
        )
        if sum(value is not None for value in identities) != 1:
            raise ContractValidationError(
                "adult promotion lookup requires exactly one identity"
            )
        if accepted_turn_id is not None and (
            type(accepted_turn_id) is not str or not accepted_turn_id.strip()
        ):
            raise ContractValidationError("adult promotion turn identity is invalid")
        if promotion_bundle_sha256 is not None and (
            type(promotion_bundle_sha256) is not str
            or not re_is_sha256(promotion_bundle_sha256)
        ):
            raise ContractValidationError("adult promotion hash is invalid")
        if request_id is not None and (
            type(request_id) is not str or not request_id.strip()
        ):
            raise ContractValidationError("adult promotion request identity is invalid")
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            matches: list[
                tuple[AdultAcceptedTurnEnvelopeV1, BoundAdultPromotionV1]
            ] = []
            for receipt, turn_dir in self._load_all_receipt_entries(branch_root):
                loaded = _load_atomic_adult_promotion(turn_dir, accepted=receipt)
                if loaded is None:
                    continue
                envelope, manifest, bound = loaded
                if (
                    accepted_turn_id is not None
                    and receipt.accepted_turn_id == accepted_turn_id
                ) or (
                    promotion_bundle_sha256 is not None
                    and manifest.promotion_bundle_sha256
                    == promotion_bundle_sha256
                ) or (
                    request_id is not None
                    and envelope.request_id == request_id
                ):
                    matches.append((envelope, bound))
            if len(matches) != 1:
                raise StateConflictError("adult promotion identity is not uniquely stored")
            return matches[0]

    @staticmethod
    def rebind_atomic_adult_fork_artifacts(
        turn_dir: Path,
        *,
        source_receipt_mapping: Mapping[str, Any],
        child_receipt_mapping: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Rebind Python custody for a copied adult object during a fork.

        Protected story/model bytes remain exact.  Only branch, parent-head,
        candidate/envelope hashes, promotion receipt, manifest, and recording
        head custody are rewritten in the unpublished child staging tree.
        """

        from cera.adult_pipeline.acceptance import AdultIntegratedExecutionV1
        from cera.adult_pipeline.contracts import (
            AdultAcceptedPromotionReceiptV1,
            AdultFilterCustodyV1,
            AdultFilterRequestV1,
            AdultPipelineResultV1,
            AdultSceneCustodyV1,
            AdultSceneRequestV1,
            BoundAdultFilterResultV1,
            BoundAdultPromotionV1,
            BoundAdultSceneCandidateV1,
        )

        source = _accepted_receipt_from_mapping(source_receipt_mapping)
        loaded = _load_atomic_adult_promotion(turn_dir, accepted=source)
        if loaded is None:
            return dict(child_receipt_mapping)
        envelope = loaded[0]
        child = dict(child_receipt_mapping)
        child_branch_id = child.get("branch_id")
        child_parent_sha256 = child.get("parent_accepted_head_sha256")
        if type(child_branch_id) is not str or not child_branch_id.strip():
            raise StateConflictError("forked adult branch identity is invalid")
        request = from_mapping(
            AdultSceneRequestV1,
            json.loads(envelope.primary_handoff_json),
        )
        scene_custody = AdultSceneCustodyV1(
            schema_version=AdultSceneCustodyV1.SCHEMA_VERSION,
            request_id=envelope.request_id,
            candidate_id=envelope.candidate_id,
            world_id=envelope.world_id,
            branch_id=child_branch_id,
            accepted_head_sha256=child_parent_sha256,
            exact_source_sha256=envelope.exact_current_source_sha256,
            scene_request_sha256=canonical_sha256(request),
        )
        scene = BoundAdultSceneCandidateV1(
            request=request,
            custody=scene_custody,
            invocation=envelope.scene_invocation,
        )
        filter_request = AdultFilterRequestV1(
            schema_version=AdultFilterRequestV1.SCHEMA_VERSION,
            scene_request=request,
            scene_output=envelope.scene_invocation.output,
        )
        filter_custody = AdultFilterCustodyV1(
            schema_version=AdultFilterCustodyV1.SCHEMA_VERSION,
            request_id=envelope.request_id,
            candidate_id=envelope.candidate_id,
            world_id=envelope.world_id,
            branch_id=child_branch_id,
            accepted_head_sha256=child_parent_sha256,
            scene_candidate_sha256=scene.candidate_sha256,
            filter_request_sha256=canonical_sha256(filter_request),
        )
        filtered = BoundAdultFilterResultV1(
            request=filter_request,
            custody=filter_custody,
            invocation=envelope.filter_invocation,
        )
        result = AdultPipelineResultV1(scene=scene, filtered=filtered)
        bundle = result.promotion_bundle()
        execution_sha256 = AdultIntegratedExecutionV1(
            result=result,
            scene_session=envelope.scene_session,
            filter_execution=envelope.filter_execution,
        ).execution_sha256
        child_envelope = replace(
            envelope,
            branch_id=child_branch_id,
            parent_accepted_head_sha256=child_parent_sha256,
            promotion_bundle=bundle,
            pipeline_execution_sha256=execution_sha256,
        )
        child["candidate_sha256"] = child_envelope.envelope_sha256
        child_receipt = _accepted_receipt_from_mapping(child)
        promotion_receipt = AdultAcceptedPromotionReceiptV1(
            schema_version=AdultAcceptedPromotionReceiptV1.SCHEMA_VERSION,
            accepted_turn_id=child_receipt.accepted_turn_id,
            world_id=child_receipt.world_id,
            branch_id=child_receipt.branch_id,
            accepted_head_before_sha256=child_receipt.parent_accepted_head_sha256,
            accepted_head_after_sha256=child_receipt.receipt_sha256,
            promotion_bundle_sha256=canonical_sha256(bundle),
            current_logic_route=bundle.next_route,
            return_to_codex=bundle.return_to_codex,
        )
        bound = BoundAdultPromotionV1(bundle=bundle, receipt=promotion_receipt)
        manifest = _atomic_adult_manifest(
            envelope=child_envelope,
            accepted=child_receipt,
            bound=bound,
        )
        _atomic_write_json(
            turn_dir / "ADULT_ACCEPTANCE_ENVELOPE.json",
            to_primitive(child_envelope),
        )
        _atomic_write_json(
            turn_dir / "ADULT_PROMOTION_RECEIPT.json",
            to_primitive(promotion_receipt),
        )
        _atomic_write_json(
            turn_dir / "ADULT_ATOMIC_PROMOTION.json",
            to_primitive(manifest),
        )
        _atomic_write_json(
            turn_dir / "RECORDING_HEAD.json",
            _recording_head_payload(
                accepted_turn_id=child_receipt.accepted_turn_id,
                status=RecordingStatus.COMPLETE,
                attempt_number=1,
                attempt_sha256=canonical_sha256(manifest),
            ),
        )
        return child

    def _publish_atomic_adult_object(
        self,
        branch_root: Path,
        *,
        envelope: AdultAcceptedTurnEnvelopeV1,
        accepted: LeanAcceptedTurnReceiptV1,
        bound: BoundAdultPromotionV1,
    ) -> None:
        final_dir = branch_root / "accepted" / _accepted_object_directory_name(accepted)
        if final_dir.exists():
            loaded = _load_atomic_adult_promotion(final_dir, accepted=accepted)
            if loaded is None or loaded[0] != envelope or loaded[2] != bound:
                raise StateConflictError("adult accepted object path is occupied")
            return
        manifest = _atomic_adult_manifest(
            envelope=envelope,
            accepted=accepted,
            bound=bound,
        )
        stage = branch_root / f".accept-adult-{uuid4().hex}"
        stage.mkdir(parents=False, exist_ok=False)
        try:
            _write_new_json(stage / "ACCEPTED_RECEIPT.json", to_primitive(accepted))
            _write_new_json(
                stage / "ADULT_ACCEPTANCE_ENVELOPE.json",
                to_primitive(envelope),
            )
            _write_new_json(
                stage / "ADULT_CODEX_PROJECTION.json",
                to_primitive(envelope.promotion_bundle.codex_projection),
            )
            _write_new_json(
                stage / "ADULT_PROMOTION_RECEIPT.json",
                to_primitive(bound.receipt),
            )
            _write_new_json(
                stage / "ADULT_ATOMIC_PROMOTION.json",
                to_primitive(manifest),
            )
            _write_new_json(
                stage / "RECORDING_HEAD.json",
                _recording_head_payload(
                    accepted_turn_id=accepted.accepted_turn_id,
                    status=RecordingStatus.COMPLETE,
                    attempt_number=1,
                    attempt_sha256=canonical_sha256(manifest),
                ),
            )
            os.replace(stage, final_dir)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    @staticmethod
    def _cache_adult_scene_session(
        branch_root: Path,
        accepted: LeanAcceptedTurnReceiptV1,
        envelope: AdultAcceptedTurnEnvelopeV1,
    ) -> None:
        session = envelope.scene_session
        payload = AcceptedPiSessionV1(
            accepted_turn_id=accepted.accepted_turn_id,
            session_id=session.session_id,
            session_path=session.session_path,
            session_id_sha256=session.session_id_sha256,
            accepted_receipt_sha256=accepted.receipt_sha256,
        )
        try:
            _atomic_write_json(
                branch_root / "sessions" / "ACCEPTED_SESSION.json",
                to_primitive(payload),
            )
        except Exception:
            # The immutable accepted object contains the authoritative binding;
            # this soft cache is reconstructed on its next verified load.
            pass

    def accept(
        self,
        candidate: LeanCandidateV1,
        *,
        semantic_validation: BoundSemanticValidationV1 | None = None,
        reader_validation: Any | None = None,
        python_qualification: OrdinaryPythonQualificationV1 | None = None,
        validation_input_binding: OrdinaryValidationInputBindingV1 | None = None,
        acceptance_action: str = "accept",
        acceptance_decision_request_sha256: str | None = None,
        override_feedback_sha256: str | None = None,
        policy_acceptance_audit: OrdinaryPolicyAcceptanceAuditV1 | None = None,
    ) -> LeanAcceptedTurnReceiptV1:
        """Publish phase one exactly once; identical recovery is read-only."""

        with self._lock:
            branch_root = self._branch_root(candidate.world_id, candidate.branch_id)
            existing = self._receipt_by_candidate(branch_root, candidate)
            if existing is not None:
                _require_acceptance_decision_replay(
                    branch_root,
                    candidate=candidate,
                    accepted=existing,
                    acceptance_action=acceptance_action,
                    decision_request_sha256=acceptance_decision_request_sha256,
                    override_feedback_sha256=override_feedback_sha256,
                    policy_acceptance_audit=policy_acceptance_audit,
                )
                head = self.load_head(
                    world_id=candidate.world_id,
                    branch_id=candidate.branch_id,
                )
                if head.accepted_head_sha256 == existing.receipt_sha256:
                    return existing
                if (
                    existing.generation != head.generation + 1
                    or existing.parent_accepted_turn_id != head.accepted_turn_id
                    or existing.parent_accepted_head_sha256 != head.accepted_head_sha256
                ):
                    raise StateConflictError(
                        "existing accepted object is not the next selected turn"
                    )
                self._select_receipt(
                    branch_root,
                    receipt=existing,
                    previous=head,
                    switch_kind="append",
                )
                return existing

            head = self.load_head(world_id=candidate.world_id, branch_id=candidate.branch_id)
            if candidate.generation != head.generation + 1:
                raise StateConflictError("candidate generation differs from accepted branch")
            if candidate.parent_accepted_turn_id != head.accepted_turn_id:
                raise StateConflictError("candidate parent differs from accepted branch")
            if candidate.accepted_head_before_sha256 != head.accepted_head_sha256:
                raise StateConflictError("candidate accepted-head binding is stale")

            _validate_candidate_qualification(
                candidate,
                semantic_validation,
                reader_validation,
                python_qualification,
                validation_input_binding=validation_input_binding,
                acceptance_action=acceptance_action,
                policy_acceptance_audit=policy_acceptance_audit,
            )
            receipt = _receipt_for_candidate(
                candidate,
                acceptance_action=acceptance_action,
            )
            self._publish_accepted_object(
                branch_root,
                candidate=candidate,
                receipt=receipt,
                semantic_validation=semantic_validation,
                reader_validation=reader_validation,
                python_qualification=python_qualification,
                validation_input_binding=validation_input_binding,
                acceptance_action=acceptance_action,
                acceptance_decision_request_sha256=(
                    acceptance_decision_request_sha256
                ),
                override_feedback_sha256=override_feedback_sha256,
                policy_acceptance_audit=policy_acceptance_audit,
            )
            self._select_receipt(
                branch_root,
                receipt=receipt,
                previous=head,
                switch_kind="append",
            )
            return receipt

    def accept_replacement(
        self,
        candidate: LeanCandidateV1,
        *,
        replaced_receipt: LeanAcceptedTurnReceiptV1,
        semantic_validation: BoundSemanticValidationV1 | None = None,
        reader_validation: Any | None = None,
        python_qualification: OrdinaryPythonQualificationV1 | None = None,
        validation_input_binding: OrdinaryValidationInputBindingV1 | None = None,
        acceptance_action: str = "automatic_accept",
        acceptance_decision_request_sha256: str | None = None,
        override_feedback_sha256: str | None = None,
        policy_acceptance_audit: OrdinaryPolicyAcceptanceAuditV1 | None = None,
    ) -> LeanAcceptedTurnReceiptV1:
        """Accept a same-generation sibling and atomically select it.

        The replaced receipt and every derived record beneath it remain
        immutable.  Only the active-lineage selector changes.
        """

        with self._lock:
            if (
                candidate.world_id != replaced_receipt.world_id
                or candidate.branch_id != replaced_receipt.branch_id
            ):
                raise StateConflictError("replacement candidate escaped its branch")
            branch_root = self._branch_root(candidate.world_id, candidate.branch_id)
            head = self.load_head(
                world_id=candidate.world_id,
                branch_id=candidate.branch_id,
            )
            existing = self._receipt_by_candidate(branch_root, candidate)
            if existing is not None:
                _require_acceptance_decision_replay(
                    branch_root,
                    candidate=candidate,
                    accepted=existing,
                    acceptance_action=acceptance_action,
                    decision_request_sha256=acceptance_decision_request_sha256,
                    override_feedback_sha256=override_feedback_sha256,
                    policy_acceptance_audit=policy_acceptance_audit,
                )
                if head.accepted_head_sha256 == existing.receipt_sha256:
                    return existing
            if (
                head.receipt is None
                or head.accepted_head_sha256 != replaced_receipt.receipt_sha256
                or head.accepted_turn_id != replaced_receipt.accepted_turn_id
            ):
                raise StateConflictError("replacement target is not the selected head")
            stored_replaced = self.load_accepted_turn_by_receipt_sha256(
                world_id=replaced_receipt.world_id,
                branch_id=replaced_receipt.branch_id,
                receipt_sha256=replaced_receipt.receipt_sha256,
            )
            if stored_replaced != replaced_receipt:
                raise StateConflictError("replacement target differs from stored bytes")
            if candidate.generation != replaced_receipt.generation:
                raise StateConflictError("replacement generation differs from selected turn")
            if candidate.parent_accepted_turn_id != replaced_receipt.parent_accepted_turn_id:
                raise StateConflictError("replacement parent turn changed")
            if (
                candidate.accepted_head_before_sha256
                != replaced_receipt.parent_accepted_head_sha256
            ):
                raise StateConflictError("replacement parent head changed")
            _validate_candidate_qualification(
                candidate,
                semantic_validation,
                reader_validation,
                python_qualification,
                validation_input_binding=validation_input_binding,
                acceptance_action=acceptance_action,
                policy_acceptance_audit=policy_acceptance_audit,
            )
            receipt = existing or _receipt_for_candidate(
                candidate,
                acceptance_action=acceptance_action,
            )
            if existing is None:
                self._publish_accepted_object(
                    branch_root,
                    candidate=candidate,
                    receipt=receipt,
                    semantic_validation=semantic_validation,
                    reader_validation=reader_validation,
                    python_qualification=python_qualification,
                    validation_input_binding=validation_input_binding,
                    acceptance_action=acceptance_action,
                    acceptance_decision_request_sha256=(
                        acceptance_decision_request_sha256
                    ),
                    override_feedback_sha256=override_feedback_sha256,
                    policy_acceptance_audit=policy_acceptance_audit,
                )
            self._select_receipt(
                branch_root,
                receipt=receipt,
                previous=head,
                switch_kind="replacement",
            )
            return receipt

    def _publish_accepted_object(
        self,
        branch_root: Path,
        *,
        candidate: LeanCandidateV1,
        receipt: LeanAcceptedTurnReceiptV1,
        semantic_validation: BoundSemanticValidationV1 | None,
        reader_validation: Any | None,
        python_qualification: OrdinaryPythonQualificationV1 | None,
        validation_input_binding: OrdinaryValidationInputBindingV1 | None,
        acceptance_action: str,
        acceptance_decision_request_sha256: str | None,
        override_feedback_sha256: str | None,
        policy_acceptance_audit: OrdinaryPolicyAcceptanceAuditV1 | None,
    ) -> None:
        decision_audit = _acceptance_decision_audit_for_candidate(
            candidate,
            acceptance_action=acceptance_action,
            decision_request_sha256=acceptance_decision_request_sha256,
            override_feedback_sha256=override_feedback_sha256,
        )
        if policy_acceptance_audit is not None and decision_audit is not None:
            raise ContractValidationError(
                "standing-policy acceptance cannot impersonate a creator override"
            )
        _validate_policy_acceptance_audit(
            candidate,
            semantic_validation=semantic_validation,
            reader_validation=reader_validation,
            python_qualification=python_qualification,
            acceptance_action=acceptance_action,
            audit=policy_acceptance_audit,
        )
        accepted_root = branch_root / "accepted"
        final_dir = accepted_root / _accepted_object_directory_name(receipt)
        if final_dir.exists():
            stored = _accepted_receipt_from_mapping(
                _read_json(final_dir / "ACCEPTED_RECEIPT.json")
            )
            if stored.receipt_sha256 != receipt.receipt_sha256:
                raise StateConflictError("accepted object path is occupied")
            if _load_acceptance_decision_audit(
                final_dir,
                accepted=stored,
            ) != decision_audit:
                raise StateConflictError("accepted decision audit differs from replay")
            if _load_policy_acceptance_audit(
                final_dir,
                accepted=stored,
            ) != policy_acceptance_audit:
                raise StateConflictError("accepted policy audit differs from replay")
            return
        stage = branch_root / f".accept-{uuid4().hex}"
        stage.mkdir(parents=False, exist_ok=False)
        try:
            _write_new_json(stage / "ACCEPTED_RECEIPT.json", to_primitive(receipt))
            if semantic_validation is not None:
                _write_new_json(
                    stage / "SEMANTIC_VALIDATION.json",
                    _semantic_validation_artifact(
                        candidate=candidate,
                        validation=semantic_validation,
                        acceptance_action=acceptance_action,
                    ),
                )
            if reader_validation is not None:
                _write_new_json(
                    stage / "READER_VALIDATION.json",
                    _reader_validation_artifact(
                        candidate=candidate,
                        validation=reader_validation,
                    ),
                )
            if python_qualification is not None:
                _write_new_json(
                    stage / "PYTHON_QUALIFICATION.json",
                    _python_qualification_artifact(
                        candidate=candidate,
                        qualification=python_qualification,
                    ),
                )
            if validation_input_binding is not None:
                _write_new_json(
                    stage / "VALIDATION_INPUT_BINDING.json",
                    _validation_input_binding_artifact(
                        candidate=candidate,
                        binding=validation_input_binding,
                    ),
                )
            if decision_audit is not None:
                _write_new_json(
                    stage / "ACCEPTANCE_DECISION_AUDIT.json",
                    to_primitive(decision_audit),
                )
            if policy_acceptance_audit is not None:
                _write_new_json(
                    stage / "POLICY_ACCEPTANCE_AUDIT.json",
                    {
                        **to_primitive(policy_acceptance_audit),
                        "audit_sha256": policy_acceptance_audit.audit_sha256,
                    },
                )
            if acceptance_action == "provisional_accept":
                if semantic_validation is None:  # guarded by qualification
                    raise AssertionError("provisional acceptance lost validation")
                _write_new_json(
                    stage / "PROVISIONAL_CANON.json",
                    _provisional_canon_artifact(
                        candidate=candidate,
                        validation=semantic_validation,
                        reader_validation=reader_validation,
                    ),
                )
            _write_new_json(
                stage / "RECORDING_HEAD.json",
                _recording_head_payload(
                    accepted_turn_id=receipt.accepted_turn_id,
                    status=RecordingStatus.PROJECTION_PENDING,
                    attempt_number=0,
                    attempt_sha256=None,
                ),
            )
            os.replace(stage, final_dir)
        except Exception:
            if stage.exists():
                shutil.rmtree(stage)
            raise

    def _select_receipt(
        self,
        branch_root: Path,
        *,
        receipt: LeanAcceptedTurnReceiptV1,
        previous: LeanAcceptedHeadV1,
        switch_kind: str,
    ) -> None:
        prior_manifest = self._load_active_lineage_manifest(branch_root)
        revision = 1 if prior_manifest is None else prior_manifest.revision + 1
        lineage = LeanActiveLineageV1(
            schema_version=LeanActiveLineageV1.SCHEMA_VERSION,
            world_id=receipt.world_id,
            branch_id=receipt.branch_id,
            revision=revision,
            switch_kind=switch_kind,
            selected_generation=receipt.generation,
            selected_turn_id=receipt.accepted_turn_id,
            selected_receipt_sha256=receipt.receipt_sha256,
            previous_generation=previous.generation,
            previous_turn_id=previous.accepted_turn_id,
            previous_receipt_sha256=previous.accepted_head_sha256,
        )
        # Accepted object publication precedes this one atomic selector write.
        # A crash before this replace leaves the previous lineage selected.
        _atomic_write_json(
            branch_root / "ACTIVE_LINEAGE.json",
            active_lineage_payload(lineage),
        )
        try:
            self._write_branch_cache(branch_root, receipt)
        except Exception:
            # ACTIVE_LINEAGE is authoritative.  The compatibility cache is
            # reconciled from lineage.previous_* on the next verified read.
            pass

    def _selected_receipts_for_supplied_head(
        self,
        receipt: LeanAcceptedTurnReceiptV1,
    ) -> list[LeanAcceptedTurnReceiptV1]:
        branch_root = self._branch_root(receipt.world_id, receipt.branch_id)
        selected = self._load_receipts(branch_root)
        if (
            not selected
            or selected[-1].accepted_turn_id != receipt.accepted_turn_id
            or selected[-1].receipt_sha256 != receipt.receipt_sha256
        ):
            raise StateConflictError("supplied accepted turn is not the selected head")
        return selected

    @staticmethod
    def _load_active_lineage_manifest(
        branch_root: Path,
    ) -> LeanActiveLineageV1 | None:
        path = branch_root / "ACTIVE_LINEAGE.json"
        if not path.exists():
            return None
        if not path.is_file() or path.is_symlink():
            raise StateConflictError("active-lineage manifest path is invalid")
        return active_lineage_from_payload(_read_json(path))

    def recording_status(self, accepted: LeanAcceptedTurnReceiptV1) -> RecordingStatus:
        with self._lock:
            turn_dir = self._accepted_turn_dir(accepted)
            head = _read_recording_head(turn_dir, accepted=accepted)
            atomic_adult = _load_atomic_adult_promotion(turn_dir, accepted=accepted)
            if atomic_adult is not None:
                manifest = atomic_adult[1]
                if (
                    head.status is not RecordingStatus.COMPLETE
                    or head.attempt_number != 1
                    or head.attempt_sha256 != canonical_sha256(manifest)
                ):
                    raise StateConflictError(
                        "atomic adult recording head differs from its promotion"
                    )
                return RecordingStatus.COMPLETE
            if head.status is not RecordingStatus.COMPLETE:
                recovered = _recover_atomic_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if recovered is not None:
                    return recovered.attempt.status
                orphan = _recover_orphan_recording_attempt(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if orphan is not None:
                    return orphan.status
            if head.status is RecordingStatus.COMPLETE:
                _load_complete_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
            elif head.status is RecordingStatus.PENDING_REPAIR:
                _load_attempt_from_head(turn_dir, accepted=accepted, head=head)
            return head.status

    def load_recording_attempt(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
    ) -> LeanRecordingAttemptV1:
        """Load and hash-verify the current immutable recording attempt."""

        with self._lock:
            turn_dir = self._accepted_turn_dir(accepted)
            head = _read_recording_head(turn_dir, accepted=accepted)
            if _load_atomic_adult_promotion(turn_dir, accepted=accepted) is not None:
                raise StateConflictError(
                    "atomic adult acceptance has no post-Accept Recorder attempt"
                )
            if head.status is not RecordingStatus.COMPLETE:
                recovered = _recover_atomic_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if recovered is not None:
                    return recovered.attempt
                orphan = _recover_orphan_recording_attempt(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if orphan is not None:
                    return orphan
            if head.status is RecordingStatus.PROJECTION_PENDING:
                return _phase_one_recording_state(accepted)
            if head.status is RecordingStatus.COMPLETE:
                return _load_complete_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                ).attempt
            return _load_attempt_from_head(turn_dir, accepted=accepted, head=head)

    def mark_recording_failure(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        *,
        recorder_request_sha256: str,
        provider_operations: int,
        failure_code: str,
        recorder_output_sha256: str | None = None,
    ) -> LeanRecordingAttemptV1:
        with self._lock:
            turn_dir = self._accepted_turn_dir(accepted)
            if _load_atomic_adult_promotion(turn_dir, accepted=accepted) is not None:
                raise StateConflictError(
                    "atomic adult acceptance cannot enter Recorder repair"
                )
            head = _read_recording_head(turn_dir, accepted=accepted)
            recovered = _recover_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if recovered is not None:
                return recovered.attempt
            orphan = _recover_orphan_recording_attempt(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if orphan is not None:
                if (
                    orphan.recorder_request_sha256 == recorder_request_sha256
                    and orphan.recorder_output_sha256 == recorder_output_sha256
                    and orphan.provider_operations == provider_operations
                    and orphan.failure_code == failure_code
                ):
                    return orphan
                head = _read_recording_head(turn_dir, accepted=accepted)
            if head.status is RecordingStatus.COMPLETE:
                raise StateConflictError("accepted turn recording is already complete")
            if head.status is RecordingStatus.PENDING_REPAIR:
                prior = _load_attempt_from_head(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                if (
                    prior.recorder_request_sha256 == recorder_request_sha256
                    and prior.recorder_output_sha256 == recorder_output_sha256
                    and prior.provider_operations == provider_operations
                    and prior.failure_code == failure_code
                ):
                    return prior
            attempt_number = self._next_recording_attempt(turn_dir)
            attempt = LeanRecordingAttemptV1(
                schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
                accepted_turn_id=accepted.accepted_turn_id,
                attempt_number=attempt_number,
                status=RecordingStatus.PENDING_REPAIR,
                recorder_request_sha256=recorder_request_sha256,
                recorder_output_sha256=recorder_output_sha256,
                provider_operations=provider_operations,
                failure_code=failure_code,
            )
            self._publish_recording_attempt(turn_dir, attempt)
            return attempt

    def attach_ordinary_record(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        record: OrdinarySceneRecordV1,
        *,
        recorder_request_sha256: str,
        recorder_output_sha256: str,
        provider_operations: int,
    ) -> LeanRecordingAttemptV1:
        with self._lock:
            validate_ordinary_record(record, accepted=accepted)
            turn_dir = self._accepted_turn_dir(accepted)
            if _load_atomic_adult_promotion(turn_dir, accepted=accepted) is not None:
                raise StateConflictError(
                    "atomic adult acceptance cannot enter ordinary Recorder"
                )
            record_sha256 = canonical_sha256(record)
            head = _read_recording_head(turn_dir, accepted=accepted)
            recovered = _recover_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if recovered is not None:
                _require_ordinary_bundle_hash(recovered, record_sha256)
                return recovered.attempt
            orphan = _recover_orphan_recording_attempt(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if orphan is not None:
                head = _read_recording_head(turn_dir, accepted=accepted)
            if head.status is RecordingStatus.COMPLETE:
                existing = _load_complete_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                _require_ordinary_bundle_hash(existing, record_sha256)
                return existing.attempt
            if head.status is RecordingStatus.PENDING_REPAIR:
                _load_attempt_from_head(turn_dir, accepted=accepted, head=head)
            attempt = LeanRecordingAttemptV1(
                schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
                accepted_turn_id=accepted.accepted_turn_id,
                attempt_number=self._next_recording_attempt(turn_dir),
                status=RecordingStatus.COMPLETE,
                recorder_request_sha256=recorder_request_sha256,
                recorder_output_sha256=recorder_output_sha256,
                provider_operations=provider_operations,
                ordinary_record_sha256=record_sha256,
            )
            bundle = _CompleteRecordingBundle(
                attempt=attempt,
                ordinary_record=record,
            )
            _publish_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                bundle=bundle,
            )
            _finalize_recording_bundle(
                turn_dir,
                accepted=accepted,
                bundle=bundle,
                prior_head=head,
            )
            return attempt

    def attach_adult_records(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        full: AdultFullRecordV1,
        projection: AdultCodexProjectionV1 | AdultCodexProjectionV2,
        *,
        recorder_request_sha256: str,
        recorder_output_sha256: str,
        provider_operations: int,
    ) -> LeanRecordingAttemptV1:
        with self._lock:
            validate_adult_records(full, projection, accepted=accepted)
            turn_dir = self._accepted_turn_dir(accepted)
            if _load_atomic_adult_promotion(turn_dir, accepted=accepted) is not None:
                raise StateConflictError(
                    "atomic adult acceptance has no post-Accept Recorder"
                )
            full_sha = canonical_sha256(full)
            projection_sha = canonical_sha256(projection)
            head = _read_recording_head(turn_dir, accepted=accepted)
            recovered = _recover_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if recovered is not None:
                _require_adult_bundle_hashes(recovered, full_sha, projection_sha)
                return recovered.attempt
            orphan = _recover_orphan_recording_attempt(
                turn_dir,
                accepted=accepted,
                head=head,
            )
            if orphan is not None:
                head = _read_recording_head(turn_dir, accepted=accepted)
            if head.status is RecordingStatus.COMPLETE:
                existing = _load_complete_recording_bundle(
                    turn_dir,
                    accepted=accepted,
                    head=head,
                )
                _require_adult_bundle_hashes(existing, full_sha, projection_sha)
                return existing.attempt
            if head.status is RecordingStatus.PENDING_REPAIR:
                _load_attempt_from_head(turn_dir, accepted=accepted, head=head)
            attempt = LeanRecordingAttemptV1(
                schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
                accepted_turn_id=accepted.accepted_turn_id,
                attempt_number=self._next_recording_attempt(turn_dir),
                status=RecordingStatus.COMPLETE,
                recorder_request_sha256=recorder_request_sha256,
                recorder_output_sha256=recorder_output_sha256,
                provider_operations=provider_operations,
                adult_full_record_sha256=full_sha,
                adult_projection_sha256=projection_sha,
            )
            bundle = _CompleteRecordingBundle(
                attempt=attempt,
                adult_full_record=full,
                adult_projection=projection,
            )
            _publish_atomic_recording_bundle(
                turn_dir,
                accepted=accepted,
                bundle=bundle,
            )
            _finalize_recording_bundle(
                turn_dir,
                accepted=accepted,
                bundle=bundle,
                prior_head=head,
            )
            return attempt

    def promote_pi_session(
        self,
        accepted: LeanAcceptedTurnReceiptV1,
        *,
        session_id: str,
        session_path: Path,
    ) -> AcceptedPiSessionV1 | None:
        """Promote only the current head's accepted Writer session.

        Pi continuity is a soft performance cache.  Recording repair for an
        older accepted turn must therefore never move the cache behind the
        authoritative accepted head.
        """

        with self._lock:
            if text_sha256(session_id) != accepted.writer_receipt.session_id_sha256:
                raise StateConflictError("accepted Pi session differs from Writer receipt")
            self._accepted_turn_dir(accepted)
            head = self.load_head(
                world_id=accepted.world_id,
                branch_id=accepted.branch_id,
            )
            branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
            path = branch_root / "sessions" / "ACCEPTED_SESSION.json"
            if head.accepted_turn_id != accepted.accepted_turn_id:
                return _load_current_pi_session(
                    path,
                    head=head,
                    allow_legacy_unbound=(
                        self._load_active_lineage_manifest(branch_root) is None
                    ),
                )
            payload = AcceptedPiSessionV1(
                accepted_turn_id=accepted.accepted_turn_id,
                session_id=session_id,
                session_path=str(session_path.resolve()),
                session_id_sha256=text_sha256(session_id),
                accepted_receipt_sha256=accepted.receipt_sha256,
            )
            if path.exists():
                existing = _decode_stored(
                    AcceptedPiSessionV1,
                    _read_json(path),
                    "accepted Pi session",
                )
                if (
                    existing.accepted_turn_id == accepted.accepted_turn_id
                    and existing.accepted_receipt_sha256 == accepted.receipt_sha256
                ):
                    if existing.session_id_sha256 != payload.session_id_sha256:
                        raise StateConflictError("accepted Pi session changed for the current head")
                    return existing
            _atomic_write_json(path, to_primitive(payload))
            return payload

    def load_accepted_pi_session(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AcceptedPiSessionV1 | None:
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            path = branch_root / "sessions" / "ACCEPTED_SESSION.json"
            head = self.load_head(world_id=world_id, branch_id=branch_id)
            current = _load_current_pi_session(
                path,
                head=head,
                allow_legacy_unbound=(
                    self._load_active_lineage_manifest(branch_root) is None
                ),
            )
            if current is not None or head.receipt is None:
                return current
            if (branch_root / "FORK_REBINDING.json").exists():
                # The stored binding remains immutable execution evidence from
                # the parent branch; a fork must rehydrate a fresh soft session
                # instead of reusing that provider conversation.
                return None
            turn_dir = _locate_accepted_turn_dir(branch_root, head.receipt)
            atomic = _load_atomic_adult_promotion(turn_dir, accepted=head.receipt)
            if atomic is None:
                return None
            envelope = atomic[0]
            session = AcceptedPiSessionV1(
                accepted_turn_id=head.receipt.accepted_turn_id,
                session_id=envelope.scene_session.session_id,
                session_path=envelope.scene_session.session_path,
                session_id_sha256=envelope.scene_session.session_id_sha256,
                accepted_receipt_sha256=head.receipt.receipt_sha256,
            )
            _atomic_write_json(path, to_primitive(session))
            return session

    def recent_accepted_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
        adult_full: bool = False,
        allow_pending: bool = False,
    ) -> tuple[dict[str, Any], ...]:
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ContractValidationError("accepted context limit is invalid")
        if type(adult_full) is not bool:
            raise ContractValidationError("accepted context adult_full flag is invalid")
        if type(allow_pending) is not bool:
            raise ContractValidationError("accepted context allow_pending flag is invalid")
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            _validate_receipt_chain(
                receipts,
                world_id=world_id,
                branch_id=branch_id,
            )
            self._verify_or_advance_branch_cache(branch_root, receipts)
            return self._accepted_payloads(
                branch_root,
                receipts[-limit:],
                adult_full=adult_full,
                allow_pending=allow_pending,
            )

    def accepted_branch_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> Sequence[Mapping[str, Any]]:
        """Return the whole verified branch in immutable receipt order.

        This reducer-facing view never exposes exact user source, exact accepted
        prose, protected adult authority, or the adult full record. A phase-one
        accepted receipt remains visible while its derived recording is
        pending, but no unfinished derived field is promoted into the payload.
        Consumers must keep the prior complete derived checkpoint.
        """

        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            _validate_receipt_chain(
                receipts,
                world_id=world_id,
                branch_id=branch_id,
            )
            self._verify_or_advance_branch_cache(branch_root, receipts)
            raw = self._accepted_payloads(
                branch_root,
                receipts,
                adult_full=False,
                allow_pending=True,
            )
            return tuple(_reducer_payload(value) for value in raw)

    def recent_ordinary_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]:
        """Return the ordinary route's exact bounded continuity view.

        Ordinary accepted prose remains available. Adult turns are represented
        only by their non-explicit projection; returning to Codex fails closed
        while that projection is pending.
        """

        raw = self._recent_context_payloads_with_all_pending(
            world_id=world_id,
            branch_id=branch_id,
            limit=limit,
            adult_full=False,
        )
        output: list[dict[str, Any]] = []
        for value in raw:
            receipt = _payload_receipt(value)
            if receipt.route is SceneRoute.ADULT:
                if "adult_projection" not in value:
                    raise StateConflictError(
                        "ordinary continuity requires the pending adult projection"
                    )
                output.append(_reducer_payload(value))
            else:
                output.append(value)
        return tuple(output)

    def recent_adult_context_payloads(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int = 6,
    ) -> tuple[dict[str, Any], ...]:
        """Return protected recent continuity for the DeepSeek adult owner.

        This is the only context API that may return exact accepted adult prose
        or a protected adult full record. It must never be passed to Codex.
        """

        return self._recent_context_payloads_with_all_pending(
            world_id=world_id,
            branch_id=branch_id,
            limit=limit,
            adult_full=True,
        )

    def _recent_context_payloads_with_all_pending(
        self,
        *,
        world_id: str,
        branch_id: str,
        limit: int,
        adult_full: bool,
    ) -> tuple[dict[str, Any], ...]:
        """Keep the presentation tail plus every unresolved accepted turn.

        A pending Recorder attachment is branch authority even after more than
        ``limit`` later turns have been accepted.  Omitting it here could let a
        route owner proceed without knowing that derived custody is incomplete.
        """

        if type(limit) is not int or not 1 <= limit <= 20:
            raise ContractValidationError("accepted context limit is invalid")
        with self._lock:
            branch_root = self._branch_root(world_id, branch_id)
            receipts = self._load_receipts(branch_root)
            _validate_receipt_chain(
                receipts,
                world_id=world_id,
                branch_id=branch_id,
            )
            self._verify_or_advance_branch_cache(branch_root, receipts)
            all_payloads = self._accepted_payloads(
                branch_root,
                receipts,
                adult_full=adult_full,
                allow_pending=True,
            )
            tail_start = max(0, len(all_payloads) - limit)
            selected = [
                value
                for index, value in enumerate(all_payloads)
                if index >= tail_start
                or value.get("recording_status") != RecordingStatus.COMPLETE.value
            ]
            return tuple(selected)

    @staticmethod
    def _accepted_payloads(
        branch_root: Path,
        receipts: Sequence[LeanAcceptedTurnReceiptV1],
        *,
        adult_full: bool,
        allow_pending: bool,
    ) -> tuple[dict[str, Any], ...]:
        output: list[dict[str, Any]] = []
        for receipt in receipts:
            turn_dir = _locate_accepted_turn_dir(branch_root, receipt)
            head = _read_recording_head(turn_dir, accepted=receipt)
            atomic_adult = _load_atomic_adult_promotion(turn_dir, accepted=receipt)
            if atomic_adult is not None:
                envelope, manifest, _ = atomic_adult
                if (
                    head.status is not RecordingStatus.COMPLETE
                    or head.attempt_number != 1
                    or head.attempt_sha256 != canonical_sha256(manifest)
                ):
                    raise StateConflictError(
                        "atomic adult recording head differs from its promotion"
                    )
                item: dict[str, Any] = {
                    "receipt": to_primitive(receipt),
                    "recording_status": RecordingStatus.COMPLETE.value,
                    "adult_projection": to_primitive(
                        envelope.promotion_bundle.codex_projection
                    ),
                }
                if adult_full:
                    item["adult_full_record"] = to_primitive(
                        envelope.promotion_bundle.protected_full_record
                    )
                output.append(item)
                continue
            if head.status is not RecordingStatus.COMPLETE:
                recovered = _recover_atomic_recording_bundle(
                    turn_dir,
                    accepted=receipt,
                    head=head,
                )
                if recovered is None:
                    orphan = _recover_orphan_recording_attempt(
                        turn_dir,
                        accepted=receipt,
                        head=head,
                    )
                    if orphan is not None:
                        head = _read_recording_head(turn_dir, accepted=receipt)
                    if not allow_pending:
                        raise StateConflictError(
                            "accepted turn recording is incomplete and cannot enter context"
                        )
                    if head.status is RecordingStatus.PENDING_REPAIR:
                        _load_attempt_from_head(
                            turn_dir,
                            accepted=receipt,
                            head=head,
                        )
                    item = {
                        "receipt": to_primitive(receipt),
                        "recording_status": head.status.value,
                    }
                    _attach_provisional_payload(item, turn_dir=turn_dir, receipt=receipt)
                    output.append(item)
                    continue
                bundle = recovered
            else:
                bundle = _load_complete_recording_bundle(
                    turn_dir,
                    accepted=receipt,
                    head=head,
                )
            item: dict[str, Any] = {
                "receipt": to_primitive(receipt),
                "recording_status": RecordingStatus.COMPLETE.value,
            }
            if bundle.ordinary_record is not None:
                item["ordinary_record"] = to_primitive(bundle.ordinary_record)
            if bundle.adult_projection is not None:
                item["adult_projection"] = to_primitive(bundle.adult_projection)
            if adult_full and bundle.adult_full_record is not None:
                item["adult_full_record"] = to_primitive(bundle.adult_full_record)
            _attach_provisional_payload(item, turn_dir=turn_dir, receipt=receipt)
            output.append(item)
        return tuple(output)

    def _load_all_receipt_entries(
        self,
        branch_root: Path,
    ) -> list[tuple[LeanAcceptedTurnReceiptV1, Path]]:
        entries: list[tuple[LeanAcceptedTurnReceiptV1, Path]] = []
        seen_hashes: set[str] = set()
        for path in sorted((branch_root / "accepted").glob("*/ACCEPTED_RECEIPT.json")):
            receipt = _accepted_receipt_from_mapping(_read_json(path))
            if path.parent.name not in {
                _turn_directory_name(receipt),
                _accepted_object_directory_name(receipt),
            }:
                raise StateConflictError(
                    "accepted branch head cache conflicts with receipt hash chain "
                    "or object directory binding"
                )
            reader_validation = _load_reader_validation_artifact(
                path.parent,
                receipt=receipt,
            )
            semantic_validation = _verify_semantic_validation_artifact(
                path.parent,
                receipt=receipt,
                reader_validation=reader_validation,
            )
            python_qualification = _verify_python_qualification_artifact(
                path.parent,
                receipt=receipt,
                semantic_validation=semantic_validation,
                reader_validation=reader_validation,
            )
            _load_validation_input_binding_artifact(
                path.parent,
                receipt=receipt,
                semantic_validation=semantic_validation,
                reader_validation=reader_validation,
                python_qualification=python_qualification,
            )
            decision_audit = _load_acceptance_decision_audit(path.parent, accepted=receipt)
            policy_audit = _load_policy_acceptance_audit(path.parent, accepted=receipt)
            if decision_audit is not None and policy_audit is not None:
                raise StateConflictError("accepted object has competing acceptance authorities")
            if policy_audit is not None:
                if (
                    semantic_validation is None
                    or reader_validation is None
                    or python_qualification is None
                    or build_ordinary_policy_acceptance_audit(
                        candidate_sha256=receipt.candidate_sha256,
                        semantic=semantic_validation,
                        reader=reader_validation,
                        python_qualification=python_qualification,
                    )
                    != policy_audit
                ):
                    raise StateConflictError("stored policy acceptance changed qualification")
            _load_atomic_adult_promotion(path.parent, accepted=receipt)
            if receipt.receipt_sha256 in seen_hashes:
                raise StateConflictError("accepted receipt object occurs more than once")
            seen_hashes.add(receipt.receipt_sha256)
            entries.append((receipt, path.parent))
        return entries

    def _load_receipts(self, branch_root: Path) -> list[LeanAcceptedTurnReceiptV1]:
        """Load only the selected receipt prefix, never inactive siblings."""

        entries = self._load_all_receipt_entries(branch_root)
        all_receipts = [receipt for receipt, _ in entries]
        identity = _read_json(branch_root / "BRANCH_IDENTITY.json")
        world_id = identity.get("world_id")
        branch_id = identity.get("branch_id")
        if not isinstance(world_id, str) or not isinstance(branch_id, str):
            raise StateConflictError("branch identity values are invalid")
        for receipt in all_receipts:
            if receipt.world_id != world_id or receipt.branch_id != branch_id:
                raise StateConflictError("accepted receipt escaped its branch identity")

        lineage = self._load_active_lineage_manifest(branch_root)
        if lineage is None:
            # Historical stores had exactly one receipt per generation.  Keep
            # that path readable and preserve its one-step crash recovery.  A
            # same-generation orphan can exist only before the first selector
            # switch; in that case the compatibility cache identifies the old
            # selected prefix and the orphan remains inactive.
            generations = [value.generation for value in all_receipts]
            if len(generations) == len(set(generations)):
                selected = sorted(all_receipts, key=lambda value: value.generation)
                _validate_receipt_chain(
                    selected,
                    world_id=world_id,
                    branch_id=branch_id,
                )
                self._verify_or_advance_branch_cache(branch_root, selected)
                return selected
            cache = _decode_stored(
                _BranchHeadCacheV1,
                _read_json(branch_root / "BRANCH_HEAD_CACHE.json"),
                "branch-head cache",
            )
            synthetic = LeanActiveLineageV1(
                schema_version=LeanActiveLineageV1.SCHEMA_VERSION,
                world_id=world_id,
                branch_id=branch_id,
                revision=0,
                switch_kind="legacy",
                selected_generation=cache.generation,
                selected_turn_id=cache.accepted_turn_id,
                selected_receipt_sha256=cache.accepted_head_sha256,
                previous_generation=0,
                previous_turn_id=None,
                previous_receipt_sha256=None,
            )
            selected = list(
                selected_receipt_chain(
                    lineage=synthetic,
                    receipts_by_sha256=receipt_sha_index(all_receipts),
                )
            )
            _validate_receipt_chain(
                selected,
                world_id=world_id,
                branch_id=branch_id,
            )
            return selected

        if lineage.world_id != world_id or lineage.branch_id != branch_id:
            raise StateConflictError("active-lineage manifest escaped its branch")
        selected = list(
            selected_receipt_chain(
                lineage=lineage,
                receipts_by_sha256=receipt_sha_index(all_receipts),
            )
        )
        _validate_receipt_chain(
            selected,
            world_id=world_id,
            branch_id=branch_id,
        )
        self._verify_or_advance_branch_cache(branch_root, selected)
        return selected

    def _receipt_by_candidate(
        self,
        branch_root: Path,
        candidate: LeanCandidateV1,
    ) -> LeanAcceptedTurnReceiptV1 | None:
        matches = [
            receipt
            for receipt, _ in self._load_all_receipt_entries(branch_root)
            if receipt.candidate_sha256 == candidate.candidate_sha256
        ]
        if len(matches) > 1:
            raise StateConflictError("accepted candidate occurs more than once")
        if matches and (
            matches[0].accepted_turn_id != candidate.turn_id
            or matches[0].world_id != candidate.world_id
            or matches[0].branch_id != candidate.branch_id
        ):
            raise StateConflictError("accepted candidate identity binding changed")
        return matches[0] if matches else None

    def _accepted_turn_dir(self, accepted: LeanAcceptedTurnReceiptV1) -> Path:
        branch_root = self._branch_root(accepted.world_id, accepted.branch_id)
        turn_dir = _locate_accepted_turn_dir(branch_root, accepted)
        stored = _accepted_receipt_from_mapping(_read_json(turn_dir / "ACCEPTED_RECEIPT.json"))
        if stored.receipt_sha256 != accepted.receipt_sha256:
            raise StateConflictError("accepted turn receipt differs from stored bytes")
        return turn_dir

    @staticmethod
    def _next_recording_attempt(turn_dir: Path) -> int:
        numbers: list[int] = []
        for path in turn_dir.glob("RECORDING_ATTEMPT_*.json"):
            match = re.fullmatch(r"RECORDING_ATTEMPT_(\d{4})\.json", path.name)
            if match:
                numbers.append(int(match.group(1)))
        for path in turn_dir.glob("RECORDING_BUNDLE_*"):
            match = re.fullmatch(r"RECORDING_BUNDLE_(\d{4})", path.name)
            if match and path.is_dir():
                numbers.append(int(match.group(1)))
        return (max(numbers) if numbers else 0) + 1

    @staticmethod
    def _publish_recording_attempt(
        turn_dir: Path,
        attempt: LeanRecordingAttemptV1,
    ) -> None:
        attempt_path = turn_dir / f"RECORDING_ATTEMPT_{attempt.attempt_number:04d}.json"
        _write_new_json(attempt_path, to_primitive(attempt))
        _atomic_write_json(
            turn_dir / "RECORDING_HEAD.json",
            _recording_head_payload(
                accepted_turn_id=attempt.accepted_turn_id,
                status=attempt.status,
                attempt_number=attempt.attempt_number,
                attempt_sha256=canonical_sha256(attempt),
            ),
        )

    @staticmethod
    def _write_branch_cache(branch_root: Path, receipt: LeanAcceptedTurnReceiptV1) -> None:
        _atomic_write_json(
            branch_root / "BRANCH_HEAD_CACHE.json",
            to_primitive(
                _BranchHeadCacheV1(
                    schema_version=_BranchHeadCacheV1.SCHEMA_VERSION,
                    generation=receipt.generation,
                    accepted_turn_id=receipt.accepted_turn_id,
                    accepted_head_sha256=receipt.receipt_sha256,
                )
            ),
        )

    @classmethod
    def _verify_or_advance_branch_cache(
        cls,
        branch_root: Path,
        receipts: Sequence[LeanAcceptedTurnReceiptV1],
    ) -> None:
        """Verify the closed anchor, advancing only from an exact ancestor.

        An exact ancestor is the only recoverable state: it represents a crash
        after atomic receipt publication and before the small cache update.
        Missing, forward, or same-generation-different-hash anchors fail closed.
        """

        path = branch_root / "BRANCH_HEAD_CACHE.json"
        if not path.exists():
            raise StateConflictError("accepted branch head cache is missing")
        cache = _decode_stored(
            _BranchHeadCacheV1,
            _read_json(path),
            "branch-head cache",
        )
        if not receipts:
            if cache.generation != 0:
                raise StateConflictError("empty accepted branch has a non-root head cache")
            return
        latest = receipts[-1]
        if (
            cache.generation == latest.generation
            and cache.accepted_turn_id == latest.accepted_turn_id
            and cache.accepted_head_sha256 == latest.receipt_sha256
        ):
            return
        lineage = cls._load_active_lineage_manifest(branch_root)
        if lineage is not None:
            previous_matches = (
                cache.generation == lineage.previous_generation
                and cache.accepted_turn_id == lineage.previous_turn_id
                and cache.accepted_head_sha256 == lineage.previous_receipt_sha256
            )
            if not previous_matches:
                raise StateConflictError(
                    "accepted branch head cache conflicts with active lineage"
                )
            cls._write_branch_cache(branch_root, latest)
            return
        anchor_matches = (cache.generation == 0 and len(receipts) == 1) or any(
            value.generation == cache.generation
            and value.accepted_turn_id == cache.accepted_turn_id
            and value.receipt_sha256 == cache.accepted_head_sha256
            for value in receipts
        )
        if not anchor_matches or latest.generation != cache.generation + 1:
            raise StateConflictError("accepted branch head cache conflicts with receipts")
        cls._write_branch_cache(branch_root, latest)


@dataclass(frozen=True, slots=True)
class AdultEnvelopePromotionPort:
    """One-envelope adapter for the existing bare-bundle promotion protocol."""

    store: LeanSceneStore
    envelope: AdultAcceptedTurnEnvelopeV1

    def current_logic_route(
        self,
        *,
        world_id: str,
        branch_id: str,
    ) -> AdultRouteStateSnapshotV1:
        return self.store.current_logic_route(world_id=world_id, branch_id=branch_id)

    def promote_adult_acceptance(
        self,
        bundle: AdultPromotionBundleV1,
    ) -> BoundAdultPromotionV1:
        if bundle != self.envelope.promotion_bundle:
            raise StateConflictError("adult promotion port received another bundle")
        return self.store.promote_adult_acceptance_envelope(self.envelope)


def _turn_directory_name(receipt: LeanAcceptedTurnReceiptV1) -> str:
    """Historical linear-store directory name (read compatibility only)."""

    return f"{receipt.generation:08d}-{text_sha256(receipt.accepted_turn_id)[:20]}"


def _accepted_object_directory_name(receipt: LeanAcceptedTurnReceiptV1) -> str:
    """Collision-free object name for same-generation accepted siblings."""

    return accepted_object_directory_name(
        generation=receipt.generation,
        accepted_turn_id=receipt.accepted_turn_id,
        receipt_sha256=receipt.receipt_sha256,
    )


def _adult_receipt_from_envelope(
    envelope: AdultAcceptedTurnEnvelopeV1,
) -> LeanAcceptedTurnReceiptV1:
    from cera.adult_pipeline.contracts import AdultProviderReceiptV2

    scene_receipt = envelope.scene_invocation.receipt
    parent_session_id_sha256 = (
        scene_receipt.parent_session_id_sha256
        if isinstance(scene_receipt, AdultProviderReceiptV2)
        else None
    )
    rehydrated = (
        scene_receipt.rehydrated
        if isinstance(scene_receipt, AdultProviderReceiptV2)
        else False
    )
    writer_receipt = PiWriterReceiptV1(
        schema_version=PiWriterReceiptV1.SCHEMA_VERSION,
        route=SceneRoute.ADULT,
        provider=scene_receipt.provider,
        model=scene_receipt.model,
        pi_version="adult-pipeline-v1",
        session_id_sha256=scene_receipt.session_id_sha256,
        parent_session_id_sha256=parent_session_id_sha256,
        request_sha256=scene_receipt.request_sha256,
        output_sha256=envelope.promotion_bundle.exact_story_prose_sha256,
        provider_operations=scene_receipt.provider_operations,
        tool_call_count=0,
        failed_tool_call_count=0,
        input_tokens=0,
        cached_input_tokens=0,
        output_tokens=0,
        reasoning_tokens=0,
        duration_ms=0,
        finish_status=scene_receipt.finish_status,
        rehydrated=rehydrated,
    )
    return LeanAcceptedTurnReceiptV1(
        schema_version=LeanAcceptedTurnReceiptV1.SCHEMA_VERSION,
        accepted_turn_id=envelope.accepted_turn_id,
        parent_accepted_turn_id=envelope.parent_accepted_turn_id,
        parent_accepted_head_sha256=envelope.parent_accepted_head_sha256,
        world_id=envelope.world_id,
        branch_id=envelope.branch_id,
        scene_id=envelope.scene_id,
        generation=envelope.generation,
        route=SceneRoute.ADULT,
        exact_user_source=envelope.exact_current_source,
        exact_user_source_sha256=envelope.exact_current_source_sha256,
        exact_accepted_prose=envelope.promotion_bundle.exact_story_prose,
        exact_accepted_prose_sha256=(
            envelope.promotion_bundle.exact_story_prose_sha256
        ),
        primary_authority_kind=envelope.primary_handoff_kind,
        primary_authority_json=envelope.primary_handoff_json,
        primary_authority_sha256=envelope.primary_handoff_sha256,
        writer_view_manifest_sha256=(
            envelope.scene_writer_view_manifest_sha256
        ),
        writer_receipt=writer_receipt,
        creator_action=envelope.creator_action,
        warnings=(),
        initial_recording_status=RecordingStatus.PROJECTION_PENDING,
        candidate_sha256=envelope.envelope_sha256,
    )


def _atomic_adult_manifest(
    *,
    envelope: AdultAcceptedTurnEnvelopeV1,
    accepted: LeanAcceptedTurnReceiptV1,
    bound: BoundAdultPromotionV1,
) -> _AtomicAdultPromotionManifestV1:
    bundle = envelope.promotion_bundle
    return _AtomicAdultPromotionManifestV1(
        schema_version=_AtomicAdultPromotionManifestV1.SCHEMA_VERSION,
        accepted_turn_id=accepted.accepted_turn_id,
        accepted_receipt_sha256=accepted.receipt_sha256,
        envelope_sha256=envelope.envelope_sha256,
        promotion_bundle_sha256=canonical_sha256(bundle),
        protected_full_record_sha256=canonical_sha256(bundle.protected_full_record),
        codex_projection_sha256=canonical_sha256(bundle.codex_projection),
        promotion_receipt_sha256=canonical_sha256(bound.receipt),
        scene_session_binding_sha256=canonical_sha256(envelope.scene_session),
        current_logic_route=bundle.next_route.value,
        return_to_codex=bundle.return_to_codex,
    )


def _load_atomic_adult_promotion(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> tuple[
    AdultAcceptedTurnEnvelopeV1,
    _AtomicAdultPromotionManifestV1,
    BoundAdultPromotionV1,
] | None:
    """Verify one atomic adult object without returning protected bytes broadly."""

    from cera.adult_pipeline.acceptance import AdultAcceptedTurnEnvelopeV1
    from cera.adult_pipeline.contracts import (
        AdultAcceptedPromotionReceiptV1,
        BoundAdultPromotionV1,
    )
    from cera.adult_pipeline.contracts import (
        AdultCodexProjectionV2 as PipelineAdultCodexProjectionV2,
    )

    paths = {
        "manifest": turn_dir / "ADULT_ATOMIC_PROMOTION.json",
        "envelope": turn_dir / "ADULT_ACCEPTANCE_ENVELOPE.json",
        "projection": turn_dir / "ADULT_CODEX_PROJECTION.json",
        "receipt": turn_dir / "ADULT_PROMOTION_RECEIPT.json",
    }
    present = {name for name, path in paths.items() if path.exists()}
    if "manifest" not in present:
        if present.intersection({"envelope", "receipt"}):
            raise StateConflictError("atomic adult promotion artifact set is incomplete")
        return None
    if present != set(paths) or any(
        not path.is_file() or path.is_symlink() for path in paths.values()
    ):
        raise StateConflictError("atomic adult promotion artifact set is incomplete")
    manifest = _decode_stored(
        _AtomicAdultPromotionManifestV1,
        _read_json(paths["manifest"]),
        "atomic adult promotion manifest",
    )
    envelope = _decode_stored(
        AdultAcceptedTurnEnvelopeV1,
        _read_json(paths["envelope"]),
        "adult accepted-turn envelope",
    )
    projection = _decode_stored(
        PipelineAdultCodexProjectionV2,
        _read_json(paths["projection"]),
        "adult Codex projection",
    )
    promotion_receipt = _decode_stored(
        AdultAcceptedPromotionReceiptV1,
        _read_json(paths["receipt"]),
        "adult accepted-promotion receipt",
    )
    bound = BoundAdultPromotionV1(
        bundle=envelope.promotion_bundle,
        receipt=promotion_receipt,
    )
    expected = _atomic_adult_manifest(
        envelope=envelope,
        accepted=accepted,
        bound=bound,
    )
    if manifest != expected:
        raise StateConflictError("atomic adult promotion manifest binding changed")
    if projection != envelope.promotion_bundle.codex_projection:
        raise StateConflictError("atomic adult safe projection changed")
    if (
        accepted.route is not SceneRoute.ADULT
        or accepted.accepted_turn_id != envelope.accepted_turn_id
        or accepted.parent_accepted_turn_id != envelope.parent_accepted_turn_id
        or accepted.parent_accepted_head_sha256
        != envelope.parent_accepted_head_sha256
        or accepted.world_id != envelope.world_id
        or accepted.branch_id != envelope.branch_id
        or accepted.scene_id != envelope.scene_id
        or accepted.generation != envelope.generation
        or accepted.exact_user_source != envelope.exact_current_source
        or accepted.exact_accepted_prose
        != envelope.promotion_bundle.exact_story_prose
        or accepted.primary_authority_json != envelope.primary_handoff_json
        or accepted.writer_view_manifest_sha256
        != envelope.scene_writer_view_manifest_sha256
        or accepted.writer_receipt.session_id_sha256
        != envelope.scene_session.session_id_sha256
        or accepted.candidate_sha256 != envelope.envelope_sha256
        or accepted.creator_action != envelope.creator_action
    ):
        raise StateConflictError("adult accepted receipt differs from its envelope")
    return envelope, manifest, bound


def _locate_accepted_turn_dir(
    branch_root: Path,
    receipt: LeanAcceptedTurnReceiptV1,
) -> Path:
    candidates = (
        branch_root / "accepted" / _accepted_object_directory_name(receipt),
        branch_root / "accepted" / _turn_directory_name(receipt),
    )
    matches: list[Path] = []
    for path in candidates:
        receipt_path = path / "ACCEPTED_RECEIPT.json"
        if not receipt_path.is_file() or receipt_path.is_symlink():
            continue
        stored = _accepted_receipt_from_mapping(_read_json(receipt_path))
        if stored.receipt_sha256 == receipt.receipt_sha256:
            matches.append(path)
    if len(matches) != 1:
        raise StateConflictError("accepted turn object is not uniquely stored")
    return matches[0]


def _receipt_for_candidate(
    candidate: LeanCandidateV1,
    *,
    acceptance_action: str,
) -> LeanAcceptedTurnReceiptV1:
    return LeanAcceptedTurnReceiptV1(
        schema_version=LeanAcceptedTurnReceiptV1.SCHEMA_VERSION,
        accepted_turn_id=candidate.turn_id,
        parent_accepted_turn_id=candidate.parent_accepted_turn_id,
        parent_accepted_head_sha256=candidate.accepted_head_before_sha256,
        world_id=candidate.world_id,
        branch_id=candidate.branch_id,
        scene_id=candidate.scene_id,
        generation=candidate.generation,
        route=candidate.route,
        exact_user_source=candidate.exact_user_source,
        exact_user_source_sha256=candidate.exact_user_source_sha256,
        exact_accepted_prose=candidate.story_text,
        exact_accepted_prose_sha256=candidate.story_text_sha256,
        primary_authority_kind=candidate.primary_authority_kind,
        primary_authority_json=candidate.primary_authority_json,
        primary_authority_sha256=candidate.primary_authority_sha256,
        writer_view_manifest_sha256=candidate.writer_view_manifest_sha256,
        writer_receipt=candidate.writer_receipt,
        creator_action=acceptance_action,
        warnings=candidate.warnings,
        initial_recording_status=RecordingStatus.PROJECTION_PENDING,
        candidate_sha256=candidate.candidate_sha256,
    )


def _acceptance_decision_audit_for_candidate(
    candidate: LeanCandidateV1,
    *,
    acceptance_action: str,
    decision_request_sha256: str | None,
    override_feedback_sha256: str | None,
) -> LeanAcceptanceDecisionAuditV1 | None:
    if decision_request_sha256 is None:
        if override_feedback_sha256 is not None:
            raise ContractValidationError(
                "override feedback audit lacks a decision-request binding"
            )
        return None
    return LeanAcceptanceDecisionAuditV1(
        schema_version=LeanAcceptanceDecisionAuditV1.SCHEMA_VERSION,
        candidate_sha256=candidate.candidate_sha256,
        acceptance_action=acceptance_action,
        decision_request_sha256=decision_request_sha256,
        override_feedback_sha256=override_feedback_sha256,
    )


def _load_acceptance_decision_audit(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> LeanAcceptanceDecisionAuditV1 | None:
    path = turn_dir / "ACCEPTANCE_DECISION_AUDIT.json"
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise StateConflictError("accepted decision audit path is unsafe")
    try:
        audit = from_mapping(LeanAcceptanceDecisionAuditV1, _read_json(path))
    except ContractValidationError as exc:
        raise StateConflictError("accepted decision audit is invalid") from exc
    if (
        audit.candidate_sha256 != accepted.candidate_sha256
        or audit.acceptance_action != accepted.creator_action
    ):
        raise StateConflictError("accepted decision audit changed accepted custody")
    return audit


def _load_policy_acceptance_audit(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> OrdinaryPolicyAcceptanceAuditV1 | None:
    path = turn_dir / "POLICY_ACCEPTANCE_AUDIT.json"
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise StateConflictError("accepted policy audit path is unsafe")
    payload = _read_json(path)
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "authority_kind",
        "policy_id",
        "policy_version",
        "policy_sha256",
        "candidate_sha256",
        "semantic_validation_sha256",
        "reader_validation_sha256",
        "python_qualification_sha256",
        "tolerated_reason_codes",
        "audit_sha256",
    }:
        raise StateConflictError("accepted policy audit fields changed")
    body = {key: payload[key] for key in payload if key != "audit_sha256"}
    try:
        audit = from_mapping(OrdinaryPolicyAcceptanceAuditV1, body)
    except ContractValidationError as exc:
        raise StateConflictError("accepted policy audit is invalid") from exc
    if (
        payload["audit_sha256"] != audit.audit_sha256
        or audit.candidate_sha256 != accepted.candidate_sha256
        or accepted.creator_action != "provisional_accept"
    ):
        raise StateConflictError("accepted policy audit changed accepted custody")
    return audit


def _require_acceptance_decision_replay(
    branch_root: Path,
    *,
    candidate: LeanCandidateV1,
    accepted: LeanAcceptedTurnReceiptV1,
    acceptance_action: str,
    decision_request_sha256: str | None,
    override_feedback_sha256: str | None,
    policy_acceptance_audit: OrdinaryPolicyAcceptanceAuditV1 | None,
) -> None:
    """Authenticate a recovery call against its immutable decision audit."""

    if accepted.creator_action != acceptance_action:
        raise StateConflictError("accepted action differs from replay")
    expected = _acceptance_decision_audit_for_candidate(
        candidate,
        acceptance_action=acceptance_action,
        decision_request_sha256=decision_request_sha256,
        override_feedback_sha256=override_feedback_sha256,
    )
    turn_dir = _locate_accepted_turn_dir(branch_root, accepted)
    if _load_acceptance_decision_audit(turn_dir, accepted=accepted) != expected:
        raise StateConflictError("accepted decision audit differs from replay")
    if (
        _load_policy_acceptance_audit(turn_dir, accepted=accepted)
        != policy_acceptance_audit
    ):
        raise StateConflictError("accepted policy audit differs from replay")


def _validate_policy_acceptance_audit(
    candidate: LeanCandidateV1,
    *,
    semantic_validation: BoundSemanticValidationV1 | None,
    reader_validation: Any | None,
    python_qualification: OrdinaryPythonQualificationV1 | None,
    acceptance_action: str,
    audit: OrdinaryPolicyAcceptanceAuditV1 | None,
) -> None:
    if audit is None:
        return
    if (
        acceptance_action != "provisional_accept"
        or semantic_validation is None
        or reader_validation is None
        or python_qualification is None
    ):
        raise ContractValidationError("ordinary policy acceptance lacks provisional custody")
    expected = build_ordinary_policy_acceptance_audit(
        candidate_sha256=candidate.candidate_sha256,
        semantic=semantic_validation,
        reader=reader_validation,
        python_qualification=python_qualification,
    )
    if expected is None or audit != expected:
        raise ContractValidationError("ordinary policy acceptance is not exactly eligible")


def _validate_candidate_qualification(
    candidate: LeanCandidateV1,
    validation: BoundSemanticValidationV1 | None,
    reader_validation: Any | None,
    python_qualification: OrdinaryPythonQualificationV1 | None,
    *,
    validation_input_binding: OrdinaryValidationInputBindingV1 | None = None,
    acceptance_action: str,
    policy_acceptance_audit: OrdinaryPolicyAcceptanceAuditV1 | None = None,
) -> None:
    if acceptance_action not in {
        "accept",
        "automatic_accept",
        "provisional_accept",
    }:
        raise ContractValidationError("candidate acceptance action is invalid")
    requires_validation = (
        candidate.route is SceneRoute.ORDINARY
        and candidate.primary_authority_kind == "codex_cognition_plan"
    )
    if not requires_validation:
        if acceptance_action == "provisional_accept" or policy_acceptance_audit is not None:
            raise ContractValidationError(
                "provisional acceptance requires a rejected semantic candidate"
            )
        if validation is not None:
            raise ContractValidationError("semantic validation cannot qualify this candidate route")
        if reader_validation is not None or python_qualification is not None:
            raise ContractValidationError("ordinary lifecycle checks cannot qualify this route")
        return
    if validation is None:
        raise ContractValidationError("cognition candidate requires a passing semantic validation")
    lifecycle = reader_validation is not None or python_qualification is not None
    reader_passed = bool(
        reader_validation is not None and getattr(reader_validation, "passed", False)
    )
    semantic_passed = validation.verdict.verdict is SemanticVerdict.PASS
    if lifecycle:
        if reader_validation is None:
            raise ContractValidationError("ordinary lifecycle acceptance lacks Reader custody")
        if acceptance_action == "provisional_accept":
            if python_qualification is None or (semantic_passed and reader_passed):
                raise ContractValidationError(
                    "creator override requires at least one semantic rejection"
                )
        elif not semantic_passed or not reader_passed or python_qualification is None:
            raise ContractValidationError(
                "ordinary lifecycle Accept requires Luna, Reader, and Python passage"
            )
        if (
            reader_validation.custody.candidate_id != candidate.candidate_id
            or reader_validation.custody.world_id != candidate.world_id
            or reader_validation.custody.branch_id != candidate.branch_id
            or reader_validation.custody.candidate_sha256 != candidate.candidate_sha256
            or reader_validation.custody.candidate_prose_sha256 != candidate.story_text_sha256
            or reader_validation.request.exact_candidate_prose != candidate.story_text
        ):
            raise ContractValidationError("Reader validation does not bind the exact candidate")
        if python_qualification is not None and (
            python_qualification.candidate_sha256 != candidate.candidate_sha256
            or python_qualification.semantic_validation_sha256 != canonical_sha256(validation)
            or python_qualification.reader_validation_sha256
            != reader_validation.binding_sha256
        ):
            raise ContractValidationError("Python qualification changed validator custody")
        if validation_input_binding is not None and (
            validation_input_binding.candidate_sha256 != candidate.candidate_sha256
            or validation_input_binding.semantic_request_sha256
            != canonical_sha256(validation.request)
            or validation_input_binding.semantic_custody_sha256
            != canonical_sha256(validation.custody)
            or validation_input_binding.reader_request_sha256
            != canonical_sha256(reader_validation.request)
            or validation_input_binding.reader_custody_sha256
            != canonical_sha256(reader_validation.custody)
            or python_qualification is None
            or python_qualification.validation_input_binding_sha256
            != validation_input_binding.binding_sha256
        ):
            raise ContractValidationError(
                "validation-input binding changed accepted validator custody"
            )
        _validate_policy_acceptance_audit(
            candidate,
            semantic_validation=validation,
            reader_validation=reader_validation,
            python_qualification=python_qualification,
            acceptance_action=acceptance_action,
            audit=policy_acceptance_audit,
        )
    else:
        if policy_acceptance_audit is not None:
            raise ContractValidationError("legacy acceptance cannot use standing policy")
        expected_verdict = (
            SemanticVerdict.REJECT
            if acceptance_action == "provisional_accept"
            else SemanticVerdict.PASS
        )
        if validation.verdict.verdict is not expected_verdict:
            if acceptance_action != "provisional_accept":
                raise ContractValidationError(
                    "rejected semantic validation cannot authorize Accept"
                )
            raise ContractValidationError(
                "semantic validation verdict does not authorize this acceptance action"
            )
    custody = validation.custody
    request = validation.request
    if (
        custody.candidate_id != candidate.candidate_id
        or custody.world_id != candidate.world_id
        or custody.branch_id != candidate.branch_id
        or custody.accepted_head_sha256 != candidate.accepted_head_before_sha256
        or request.exact_current_source != candidate.exact_user_source
        or request.exact_candidate_prose != candidate.story_text
        or canonical_json(request.cognition_plan) != candidate.primary_authority_json
    ):
        raise ContractValidationError(
            "semantic validation does not bind the exact cognition candidate"
        )


def _semantic_validation_artifact(
    *,
    candidate: LeanCandidateV1,
    validation: BoundSemanticValidationV1,
    acceptance_action: str,
) -> dict[str, Any]:
    del acceptance_action
    body = {
        "schema_version": "cera.pi_scene.semantic_acceptance.v1",
        "candidate_sha256": candidate.candidate_sha256,
        "validation": to_primitive(validation),
    }
    return {**body, "artifact_sha256": canonical_sha256(body)}


def _provisional_canon_artifact(
    *,
    candidate: LeanCandidateV1,
    validation: BoundSemanticValidationV1,
    reader_validation: Any | None = None,
) -> dict[str, Any]:
    semantic_rejected = validation.verdict.verdict is SemanticVerdict.REJECT
    reader_rejected = bool(
        reader_validation is not None and not getattr(reader_validation, "passed", False)
    )
    if not semantic_rejected and not reader_rejected:
        raise ContractValidationError("provisional canon requires a rejected candidate")
    body = {
        "schema_version": "cera.pi_scene.provisional_canon.v3",
        "provisional_canon_id": f"provisional:{candidate.candidate_sha256[:24]}",
        "candidate_id": candidate.candidate_id,
        "candidate_sha256": candidate.candidate_sha256,
        "validation_binding_sha256": validation.binding_sha256,
        "reader_validation_sha256": (
            None if reader_validation is None else reader_validation.binding_sha256
        ),
        "status": "unresolved",
        "resolution_policy": "dependent_cognition_plan_must_bind_true_or_false",
    }
    return {**body, "artifact_sha256": canonical_sha256(body)}


def _reader_validation_artifact(
    *,
    candidate: LeanCandidateV1,
    validation: Any,
) -> dict[str, Any]:
    if (
        validation.custody.candidate_sha256 != candidate.candidate_sha256
        or validation.custody.candidate_prose_sha256 != candidate.story_text_sha256
    ):
        raise ContractValidationError("Reader artifact changed candidate custody")
    body = {
        "schema_version": "cera.pi_scene.reader_acceptance.v1",
        "candidate_sha256": candidate.candidate_sha256,
        "validation": to_primitive(validation),
    }
    return {**body, "artifact_sha256": canonical_sha256(body)}


def _python_qualification_artifact(
    *,
    candidate: LeanCandidateV1,
    qualification: OrdinaryPythonQualificationV1,
) -> dict[str, Any]:
    if qualification.candidate_sha256 != candidate.candidate_sha256:
        raise ContractValidationError("Python qualification artifact changed candidate custody")
    body = {
        "schema_version": "cera.pi_scene.python_acceptance.v1",
        "candidate_sha256": candidate.candidate_sha256,
        "qualification": to_primitive(qualification),
    }
    return {**body, "artifact_sha256": canonical_sha256(body)}


def _validation_input_binding_artifact(
    *,
    candidate: LeanCandidateV1,
    binding: OrdinaryValidationInputBindingV1,
) -> dict[str, Any]:
    if binding.candidate_sha256 != candidate.candidate_sha256:
        raise ContractValidationError("validation-input artifact changed candidate custody")
    body = {
        "schema_version": "cera.pi_scene.validation_input_binding_acceptance.v1",
        "candidate_sha256": candidate.candidate_sha256,
        "binding": to_primitive(binding),
    }
    return {**body, "artifact_sha256": canonical_sha256(body)}


def _load_validation_input_binding_artifact(
    turn_dir: Path,
    *,
    receipt: LeanAcceptedTurnReceiptV1,
    semantic_validation: BoundSemanticValidationV1 | None,
    reader_validation: Any | None,
    python_qualification: OrdinaryPythonQualificationV1 | None,
) -> OrdinaryValidationInputBindingV1 | None:
    path = turn_dir / "VALIDATION_INPUT_BINDING.json"
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise StateConflictError("accepted validation-input custody is invalid")
    payload = _read_json(path)
    body = {key: payload[key] for key in payload if key != "artifact_sha256"}
    if (
        set(payload)
        != {
            "schema_version",
            "candidate_sha256",
            "binding",
            "artifact_sha256",
        }
        or payload["schema_version"] != "cera.pi_scene.validation_input_binding_acceptance.v1"
        or payload["candidate_sha256"] != receipt.candidate_sha256
        or payload["artifact_sha256"] != canonical_sha256(body)
        or not isinstance(payload["binding"], Mapping)
    ):
        raise StateConflictError("accepted validation-input artifact binding changed")
    binding = _decode_stored(
        OrdinaryValidationInputBindingV1,
        payload["binding"],
        "validation-input binding",
    )
    if (
        semantic_validation is None
        or reader_validation is None
        or python_qualification is None
        or binding.candidate_sha256 != receipt.candidate_sha256
        or binding.semantic_request_sha256 != canonical_sha256(semantic_validation.request)
        or binding.semantic_custody_sha256 != canonical_sha256(semantic_validation.custody)
        or binding.reader_request_sha256 != canonical_sha256(reader_validation.request)
        or binding.reader_custody_sha256 != canonical_sha256(reader_validation.custody)
        or python_qualification.validation_input_binding_sha256 != binding.binding_sha256
    ):
        raise StateConflictError("accepted validation-input custody changed")
    return binding


def _load_reader_validation_artifact(
    turn_dir: Path,
    *,
    receipt: LeanAcceptedTurnReceiptV1,
) -> Any | None:
    path = turn_dir / "READER_VALIDATION.json"
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise StateConflictError("accepted Reader-validation custody is invalid")
    payload = _read_json(path)
    expected = {
        "schema_version",
        "candidate_sha256",
        "validation",
        "artifact_sha256",
    }
    body = {key: payload[key] for key in payload if key != "artifact_sha256"}
    if (
        set(payload) != expected
        or payload["schema_version"] != "cera.pi_scene.reader_acceptance.v1"
        or payload["candidate_sha256"] != receipt.candidate_sha256
        or payload["artifact_sha256"] != canonical_sha256(body)
        or not isinstance(payload["validation"], Mapping)
    ):
        raise StateConflictError("Reader-validation artifact binding changed")
    from cera.reader_validation import BoundReaderValidationV1

    validation = _decode_stored(
        BoundReaderValidationV1,
        payload["validation"],
        "Reader validation",
    )
    if (
        validation.custody.world_id != receipt.world_id
        or validation.custody.branch_id != receipt.branch_id
        or validation.custody.candidate_sha256 != receipt.candidate_sha256
        or validation.custody.candidate_prose_sha256
        != receipt.exact_accepted_prose_sha256
        or validation.request.exact_candidate_prose != receipt.exact_accepted_prose
    ):
        raise StateConflictError("stored Reader validation changed accepted authority")
    return validation


def _verify_python_qualification_artifact(
    turn_dir: Path,
    *,
    receipt: LeanAcceptedTurnReceiptV1,
    semantic_validation: BoundSemanticValidationV1 | None,
    reader_validation: Any | None,
) -> OrdinaryPythonQualificationV1 | None:
    path = turn_dir / "PYTHON_QUALIFICATION.json"
    if reader_validation is None:
        if path.exists():
            raise StateConflictError("legacy accepted turn has Python qualification")
        return None
    if not path.is_file() or path.is_symlink():
        raise StateConflictError("qualified ordinary turn lacks Python custody")
    payload = _read_json(path)
    expected = {
        "schema_version",
        "candidate_sha256",
        "qualification",
        "artifact_sha256",
    }
    body = {key: payload[key] for key in payload if key != "artifact_sha256"}
    if (
        set(payload) != expected
        or payload["schema_version"] != "cera.pi_scene.python_acceptance.v1"
        or payload["candidate_sha256"] != receipt.candidate_sha256
        or payload["artifact_sha256"] != canonical_sha256(body)
        or not isinstance(payload["qualification"], Mapping)
    ):
        raise StateConflictError("Python-qualification artifact binding changed")
    qualification = _decode_stored(
        OrdinaryPythonQualificationV1,
        payload["qualification"],
        "Python qualification",
    )
    if (
        semantic_validation is None
        or qualification.candidate_sha256 != receipt.candidate_sha256
        or qualification.semantic_validation_sha256
        != canonical_sha256(semantic_validation)
        or qualification.reader_validation_sha256 != reader_validation.binding_sha256
        or (
            receipt.creator_action == "provisional_accept"
            and semantic_validation.verdict.verdict is SemanticVerdict.PASS
            and getattr(reader_validation, "passed", False)
        )
        or (
            receipt.creator_action != "provisional_accept"
            and (
                semantic_validation.verdict.verdict is not SemanticVerdict.PASS
                or not getattr(reader_validation, "passed", False)
            )
        )
    ):
        raise StateConflictError("stored Python qualification changed accepted authority")
    return qualification


def _verify_semantic_validation_artifact(
    turn_dir: Path,
    *,
    receipt: LeanAcceptedTurnReceiptV1,
    reader_validation: Any | None,
) -> BoundSemanticValidationV1 | None:
    path = turn_dir / "SEMANTIC_VALIDATION.json"
    requires_validation = (
        receipt.route is SceneRoute.ORDINARY
        and receipt.primary_authority_kind == "codex_cognition_plan"
    )
    if not requires_validation:
        if path.exists() or (turn_dir / "PROVISIONAL_CANON.json").exists():
            raise StateConflictError(
                "accepted turn has an unauthorized semantic-validation artifact"
            )
        return None
    if not path.is_file() or path.is_symlink():
        raise StateConflictError("accepted cognition turn lacks semantic-validation custody")
    payload = _read_json(path)
    expected = {
        "schema_version",
        "candidate_sha256",
        "validation",
        "artifact_sha256",
    }
    if set(payload) != expected:
        raise StateConflictError("semantic-validation artifact fields changed")
    body = {key: payload[key] for key in payload if key != "artifact_sha256"}
    if (
        payload["schema_version"] != "cera.pi_scene.semantic_acceptance.v1"
        or payload["candidate_sha256"] != receipt.candidate_sha256
        or payload["artifact_sha256"] != canonical_sha256(body)
        or not isinstance(payload["validation"], Mapping)
    ):
        raise StateConflictError("semantic-validation artifact binding changed")
    validation = _decode_stored(
        BoundSemanticValidationV1,
        payload["validation"],
        "semantic validation",
    )
    request = validation.request
    custody = validation.custody
    expected_verdict = SemanticVerdict.PASS
    if receipt.creator_action == "provisional_accept" and reader_validation is None:
        expected_verdict = SemanticVerdict.REJECT
    if (
        (
            receipt.creator_action != "provisional_accept"
            and validation.verdict.verdict is not expected_verdict
        )
        or (
            receipt.creator_action == "provisional_accept"
            and validation.verdict.verdict is SemanticVerdict.PASS
            and (
                reader_validation is None
                or getattr(reader_validation, "passed", False)
            )
        )
        or custody.world_id != receipt.world_id
        or custody.branch_id != receipt.branch_id
        or custody.accepted_head_sha256 != receipt.parent_accepted_head_sha256
        or request.exact_current_source != receipt.exact_user_source
        or request.exact_candidate_prose != receipt.exact_accepted_prose
        or canonical_json(request.cognition_plan) != receipt.primary_authority_json
    ):
        raise StateConflictError("stored semantic validation changed accepted authority")
    provisional_path = turn_dir / "PROVISIONAL_CANON.json"
    if receipt.creator_action == "provisional_accept":
        _verify_provisional_canon_artifact(
            provisional_path,
            receipt=receipt,
            validation=validation,
            reader_validation=reader_validation,
        )
    elif provisional_path.exists():
        raise StateConflictError("ordinary accepted turn has unauthorized provisional canon")
    return validation


def _verify_provisional_canon_artifact(
    path: Path,
    *,
    receipt: LeanAcceptedTurnReceiptV1,
    validation: BoundSemanticValidationV1,
    reader_validation: Any | None,
) -> None:
    if not path.is_file() or path.is_symlink():
        raise StateConflictError("provisional acceptance lacks its canon artifact")
    payload = _read_json(path)
    common = {
        "schema_version",
        "provisional_canon_id",
        "candidate_id",
        "candidate_sha256",
        "validation_binding_sha256",
        "status",
        "artifact_sha256",
    }
    schema_version = payload.get("schema_version")
    if schema_version == "cera.pi_scene.provisional_canon.v1":
        expected = common | {"working_assumption"}
    elif schema_version == "cera.pi_scene.provisional_canon.v3":
        expected = common | {"resolution_policy", "reader_validation_sha256"}
    else:
        expected = common | {"resolution_policy"}
    if set(payload) != expected:
        raise StateConflictError("provisional-canon artifact fields changed")
    body = {key: payload[key] for key in payload if key != "artifact_sha256"}
    if (
        schema_version
        not in {
            "cera.pi_scene.provisional_canon.v1",
            "cera.pi_scene.provisional_canon.v2",
            "cera.pi_scene.provisional_canon.v3",
        }
        or payload["candidate_id"] != validation.custody.candidate_id
        or payload["candidate_sha256"] != receipt.candidate_sha256
        or payload["validation_binding_sha256"] != validation.binding_sha256
        or payload["status"] != "unresolved"
        or payload["provisional_canon_id"]
        != f"provisional:{receipt.candidate_sha256[:24]}"
        or payload["artifact_sha256"] != canonical_sha256(body)
        or (
            schema_version == "cera.pi_scene.provisional_canon.v3"
            and payload["reader_validation_sha256"]
            != (
                None
                if reader_validation is None
                else reader_validation.binding_sha256
            )
        )
    ):
        raise StateConflictError("provisional-canon artifact binding changed")
    if schema_version == "cera.pi_scene.provisional_canon.v1":
        if (
            payload["working_assumption"]
            != "accepted_candidate_events_are_provisionally_true"
        ):
            raise StateConflictError("historical provisional assumption changed")
    elif (
        payload["resolution_policy"]
        != "dependent_cognition_plan_must_bind_true_or_false"
    ):
        raise StateConflictError("provisional resolution policy changed")


def _load_current_pi_session(
    path: Path,
    *,
    head: LeanAcceptedHeadV1,
    allow_legacy_unbound: bool,
) -> AcceptedPiSessionV1 | None:
    """Return a current soft cache entry; silently ignore a valid stale one."""

    if not path.exists():
        return None
    session = _decode_stored(
        AcceptedPiSessionV1,
        _read_json(path),
        "accepted Pi session",
    )
    if session.accepted_turn_id != head.accepted_turn_id:
        return None
    if session.accepted_receipt_sha256 is None:
        # Historical sessions predate receipt-bound sibling lineage.  They are
        # safe to reuse only while the branch itself still has no lineage
        # selector.  Once a selector exists, an unbound same-turn session may
        # belong to an inactive sibling and must be rehydrated instead.
        return session if allow_legacy_unbound else None
    if session.accepted_receipt_sha256 != head.accepted_head_sha256:
        return None
    return session


def _payload_receipt(value: Mapping[str, Any]) -> LeanAcceptedTurnReceiptV1:
    receipt = value.get("receipt")
    if not isinstance(receipt, Mapping):
        raise StateConflictError("accepted payload omitted its receipt")
    return _accepted_receipt_from_mapping(receipt)


def _reducer_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Remove exact source/prose and protected adult authority from state input."""

    receipt = _payload_receipt(value)
    primitive = to_primitive(receipt)
    common_fields = (
        "schema_version",
        "accepted_turn_id",
        "parent_accepted_turn_id",
        "parent_accepted_head_sha256",
        "world_id",
        "branch_id",
        "scene_id",
        "generation",
        "route",
        "exact_user_source_sha256",
        "exact_accepted_prose_sha256",
        "primary_authority_kind",
        "primary_authority_sha256",
        "writer_view_manifest_sha256",
        "creator_action",
        "initial_recording_status",
        "candidate_sha256",
    )
    projected_receipt = {name: primitive[name] for name in common_fields}
    if receipt.route is SceneRoute.ORDINARY:
        projected_receipt["primary_authority_json"] = primitive["primary_authority_json"]
    output: dict[str, Any] = {
        "receipt": projected_receipt,
        "accepted_receipt_sha256": receipt.receipt_sha256,
        "recording_status": value.get("recording_status"),
    }
    for name in ("ordinary_record", "adult_projection", "provisional_canon"):
        if name in value:
            output[name] = value[name]
    return output


def _attach_provisional_payload(
    item: dict[str, Any],
    *,
    turn_dir: Path,
    receipt: LeanAcceptedTurnReceiptV1,
) -> None:
    path = turn_dir / "PROVISIONAL_CANON.json"
    if receipt.creator_action == "provisional_accept":
        payload = _read_json(path)
        item["provisional_canon"] = {
            "provisional_canon_id": payload["provisional_canon_id"],
            "status": payload["status"],
            "authority_id": payload["candidate_sha256"],
        }
    elif path.exists():
        raise StateConflictError("non-provisional turn contains provisional canon")


def _validate_receipt_chain(
    receipts: Sequence[LeanAcceptedTurnReceiptV1],
    *,
    world_id: str,
    branch_id: str,
) -> None:
    parent: str | None = None
    parent_head_sha256: str | None = None
    for index, receipt in enumerate(receipts, start=1):
        if receipt.world_id != world_id or receipt.branch_id != branch_id:
            raise StateConflictError("accepted receipt escaped its branch identity")
        if receipt.generation != index:
            raise StateConflictError("accepted turn generations are not contiguous")
        if receipt.parent_accepted_turn_id != parent:
            raise StateConflictError("accepted turn parent chain is inconsistent")
        if receipt.parent_accepted_head_sha256 != parent_head_sha256:
            raise StateConflictError("accepted receipt hash chain is inconsistent")
        parent = receipt.accepted_turn_id
        parent_head_sha256 = receipt.receipt_sha256


def _recording_head_payload(
    *,
    accepted_turn_id: str,
    status: RecordingStatus,
    attempt_number: int,
    attempt_sha256: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": "cera.pi_scene.recording_head.v1",
        "accepted_turn_id": accepted_turn_id,
        "status": status.value,
        "attempt_number": attempt_number,
        "attempt_sha256": attempt_sha256,
    }


def _read_recording_head(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> _RecordingHeadV1:
    head = _decode_stored(
        _RecordingHeadV1,
        _read_json(turn_dir / "RECORDING_HEAD.json"),
        "recording head",
    )
    if head.accepted_turn_id != accepted.accepted_turn_id:
        raise StateConflictError("recording head changed accepted turn identity")
    return head


def _phase_one_recording_state(
    accepted: LeanAcceptedTurnReceiptV1,
) -> LeanRecordingAttemptV1:
    """Represent the immutable phase-one Accept head without inventing an attempt file."""

    return LeanRecordingAttemptV1(
        schema_version=LeanRecordingAttemptV1.SCHEMA_VERSION,
        accepted_turn_id=accepted.accepted_turn_id,
        attempt_number=0,
        status=RecordingStatus.PROJECTION_PENDING,
        recorder_request_sha256=canonical_sha256(
            {
                "schema_version": "cera.pi_scene.phase_one_recording_state.v1",
                "accepted_receipt_sha256": accepted.receipt_sha256,
            }
        ),
        recorder_output_sha256=None,
        provider_operations=0,
    )


def _load_attempt_from_head(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> LeanRecordingAttemptV1:
    if head.attempt_number < 1 or head.attempt_sha256 is None:
        raise StateConflictError("recording head does not identify an immutable attempt")
    attempt = _recording_attempt_from_mapping(
        _read_json(turn_dir / f"RECORDING_ATTEMPT_{head.attempt_number:04d}.json")
    )
    if attempt.accepted_turn_id != accepted.accepted_turn_id:
        raise StateConflictError("recording attempt changed accepted turn identity")
    if attempt.attempt_number != head.attempt_number:
        raise StateConflictError("recording attempt number differs from its head")
    if canonical_sha256(attempt) != head.attempt_sha256:
        raise StateConflictError("recording attempt differs from its head")
    if attempt.status is not head.status:
        raise StateConflictError("recording attempt status differs from its head")
    return attempt


def _recover_orphan_recording_attempt(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> LeanRecordingAttemptV1 | None:
    """Advance a head across one fully-written failed attempt after a crash.

    Complete attempts are recovered through their atomic bundle instead.  A
    gap, multiple candidates, or a different status is ambiguous and therefore
    fails closed rather than inventing publication order.
    """

    if head.status is RecordingStatus.COMPLETE:
        return None
    if head.status is RecordingStatus.PENDING_REPAIR:
        _load_attempt_from_head(turn_dir, accepted=accepted, head=head)

    numbered: list[tuple[int, Path]] = []
    for path in turn_dir.glob("RECORDING_ATTEMPT_*.json"):
        match = re.fullmatch(r"RECORDING_ATTEMPT_(\d{4})\.json", path.name)
        if match is not None:
            numbered.append((int(match.group(1)), path))
    candidates = sorted((number, path) for number, path in numbered if number > head.attempt_number)
    if not candidates:
        return None
    if len(candidates) != 1 or candidates[0][0] != head.attempt_number + 1:
        raise StateConflictError("recording attempts have ambiguous orphan publication")

    number, path = candidates[0]
    attempt = _recording_attempt_from_mapping(_read_json(path))
    if attempt.accepted_turn_id != accepted.accepted_turn_id or attempt.attempt_number != number:
        raise StateConflictError("orphan recording attempt changed its identity")
    if attempt.status is not RecordingStatus.PENDING_REPAIR:
        raise StateConflictError("orphan complete recording attempt is missing its atomic bundle")
    _atomic_write_json(
        turn_dir / "RECORDING_HEAD.json",
        _recording_head_payload(
            accepted_turn_id=accepted.accepted_turn_id,
            status=attempt.status,
            attempt_number=attempt.attempt_number,
            attempt_sha256=canonical_sha256(attempt),
        ),
    )
    recovered_head = _read_recording_head(turn_dir, accepted=accepted)
    recovered = _load_attempt_from_head(
        turn_dir,
        accepted=accepted,
        head=recovered_head,
    )
    if recovered != attempt:
        raise StateConflictError("orphan recording attempt changed during recovery")
    return recovered


def _recording_bundle_path(turn_dir: Path, attempt_number: int) -> Path:
    return turn_dir / f"RECORDING_BUNDLE_{attempt_number:04d}"


def _bundle_manifest(
    bundle: _CompleteRecordingBundle,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> _RecordingBundleManifestV2:
    attempt = bundle.attempt
    if attempt.status is not RecordingStatus.COMPLETE:
        raise ContractValidationError("atomic recording bundle requires a complete attempt")
    route = SceneRoute.ORDINARY if bundle.ordinary_record is not None else SceneRoute.ADULT
    manifest = _RecordingBundleManifestV2(
        schema_version=_RecordingBundleManifestV2.SCHEMA_VERSION,
        accepted_turn_id=attempt.accepted_turn_id,
        accepted_receipt_sha256=accepted.receipt_sha256,
        exact_accepted_prose_sha256=accepted.exact_accepted_prose_sha256,
        primary_authority_sha256=accepted.primary_authority_sha256,
        attempt_number=attempt.attempt_number,
        attempt_sha256=canonical_sha256(attempt),
        route=route,
        ordinary_record_sha256=(
            None if bundle.ordinary_record is None else canonical_sha256(bundle.ordinary_record)
        ),
        adult_full_record_sha256=(
            None if bundle.adult_full_record is None else canonical_sha256(bundle.adult_full_record)
        ),
        adult_projection_sha256=(
            None if bundle.adult_projection is None else canonical_sha256(bundle.adult_projection)
        ),
    )
    if (
        manifest.ordinary_record_sha256 != attempt.ordinary_record_sha256
        or manifest.adult_full_record_sha256 != attempt.adult_full_record_sha256
        or manifest.adult_projection_sha256 != attempt.adult_projection_sha256
    ):
        raise ContractValidationError("recording bundle differs from its attempt hashes")
    return manifest


def _publish_atomic_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    bundle: _CompleteRecordingBundle,
) -> None:
    """Atomically publish one immutable route-shaped record bundle.

    ``RECORDING_HEAD.json`` remains the commit marker. A crash before its update
    leaves either no bundle or one complete directory that restart reconciliation
    can verify and finish without repeating Recorder work.
    """

    manifest = _bundle_manifest(bundle, accepted=accepted)
    if manifest.accepted_turn_id != accepted.accepted_turn_id:
        raise StateConflictError("recording bundle changed accepted turn identity")
    if manifest.route is not accepted.route:
        raise StateConflictError("recording bundle route differs from accepted turn")
    final = _recording_bundle_path(turn_dir, bundle.attempt.attempt_number)
    if final.exists():
        existing = _load_recording_bundle_directory(final, accepted=accepted)
        if existing != bundle:
            raise StateConflictError("immutable recording bundle changed")
        return
    # Keep the staging component compact.  Repeating the full bundle name made
    # an otherwise valid qualification root hit Windows MAX_PATH while writing
    # the nested RECORDING_ATTEMPT.json.
    stage = turn_dir / f".rb.{uuid4().hex}.tmp"
    stage.mkdir(parents=False, exist_ok=False)
    try:
        _write_new_json(stage / "BUNDLE_MANIFEST.json", to_primitive(manifest))
        _write_new_json(
            stage / "RECORDING_ATTEMPT.json",
            to_primitive(bundle.attempt),
        )
        if bundle.ordinary_record is not None:
            _write_new_json(
                stage / "ORDINARY_RECORD.json",
                to_primitive(bundle.ordinary_record),
            )
        else:
            if bundle.adult_full_record is None or bundle.adult_projection is None:
                raise ContractValidationError("adult recording bundle is incomplete")
            _write_new_json(
                stage / "ADULT_FULL_RECORD.json",
                to_primitive(bundle.adult_full_record),
            )
            _write_new_json(
                stage / "ADULT_CODEX_PROJECTION.json",
                to_primitive(bundle.adult_projection),
            )
        os.replace(stage, final)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        if final.exists():
            existing = _load_recording_bundle_directory(final, accepted=accepted)
            if existing == bundle:
                return
        raise


def _recover_atomic_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> _CompleteRecordingBundle | None:
    if head.status is RecordingStatus.COMPLETE:
        return None
    paths = sorted(
        path
        for path in turn_dir.glob("RECORDING_BUNDLE_*")
        if path.is_dir() and re.fullmatch(r"RECORDING_BUNDLE_\d{4}", path.name) is not None
    )
    if not paths:
        return None
    if len(paths) != 1:
        raise StateConflictError("accepted turn has ambiguous recording bundles")
    bundle = _load_recording_bundle_directory(paths[0], accepted=accepted)
    _finalize_recording_bundle(
        turn_dir,
        accepted=accepted,
        bundle=bundle,
        prior_head=head,
    )
    return bundle


def _finalize_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    bundle: _CompleteRecordingBundle,
    prior_head: _RecordingHeadV1,
) -> None:
    canonical = _load_recording_bundle_directory(
        _recording_bundle_path(turn_dir, bundle.attempt.attempt_number),
        accepted=accepted,
    )
    if canonical != bundle:
        raise StateConflictError("recording bundle changed before finalization")
    current = _read_recording_head(turn_dir, accepted=accepted)
    if current.status is RecordingStatus.COMPLETE:
        existing = _load_complete_recording_bundle(
            turn_dir,
            accepted=accepted,
            head=current,
        )
        if existing != bundle:
            raise StateConflictError("completed recording differs from staged bundle")
        return
    if current != prior_head:
        raise StateConflictError("recording head changed during bundle publication")
    _materialize_recording_bundle_compatibility(
        turn_dir,
        accepted=accepted,
        bundle=bundle,
        allow_repair=True,
    )
    _atomic_write_json(
        turn_dir / "RECORDING_HEAD.json",
        _recording_head_payload(
            accepted_turn_id=accepted.accepted_turn_id,
            status=RecordingStatus.COMPLETE,
            attempt_number=bundle.attempt.attempt_number,
            attempt_sha256=canonical_sha256(bundle.attempt),
        ),
    )
    completed_head = _read_recording_head(turn_dir, accepted=accepted)
    _load_complete_recording_bundle(
        turn_dir,
        accepted=accepted,
        head=completed_head,
    )


def _materialize_recording_bundle_compatibility(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    bundle: _CompleteRecordingBundle,
    allow_repair: bool,
) -> None:
    expected: dict[str, Mapping[str, Any]] = {
        f"RECORDING_ATTEMPT_{bundle.attempt.attempt_number:04d}.json": to_primitive(bundle.attempt)
    }
    if bundle.ordinary_record is not None:
        if (turn_dir / "ADULT_FULL_RECORD.json").exists() or (
            turn_dir / "ADULT_CODEX_PROJECTION.json"
        ).exists():
            raise StateConflictError("ordinary turn contains adult recording artifacts")
        expected["ORDINARY_RECORD.json"] = to_primitive(bundle.ordinary_record)
    else:
        if bundle.adult_full_record is None or bundle.adult_projection is None:
            raise StateConflictError("adult recording bundle is incomplete")
        if (turn_dir / "ORDINARY_RECORD.json").exists():
            raise StateConflictError("adult turn contains an ordinary recording artifact")
        expected["ADULT_FULL_RECORD.json"] = to_primitive(bundle.adult_full_record)
        expected["ADULT_CODEX_PROJECTION.json"] = to_primitive(bundle.adult_projection)
    for name, payload in expected.items():
        path = turn_dir / name
        text = canonical_json(dict(payload))
        if not path.exists():
            if not allow_repair:
                raise StateConflictError(f"recording compatibility artifact is missing: {name}")
            _atomic_write_json(path, payload)
            continue
        try:
            existing = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            if not allow_repair:
                raise StateConflictError(
                    f"recording compatibility artifact is unreadable: {name}"
                ) from exc
            existing = ""
        if existing == text:
            continue
        if not allow_repair:
            raise StateConflictError(f"recording compatibility artifact changed: {name}")
        _atomic_write_json(path, payload)


def _load_complete_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> _CompleteRecordingBundle:
    if head.status is not RecordingStatus.COMPLETE:
        raise StateConflictError("attached record does not have complete status")
    bundle_path = _recording_bundle_path(turn_dir, head.attempt_number)
    if bundle_path.exists():
        bundle = _load_recording_bundle_directory(bundle_path, accepted=accepted)
        if (
            bundle.attempt.attempt_number != head.attempt_number
            or canonical_sha256(bundle.attempt) != head.attempt_sha256
        ):
            raise StateConflictError("recording bundle differs from its head")
        _materialize_recording_bundle_compatibility(
            turn_dir,
            accepted=accepted,
            bundle=bundle,
            allow_repair=False,
        )
        return bundle
    return _load_historical_complete_recording_bundle(
        turn_dir,
        accepted=accepted,
        head=head,
    )


def _load_recording_bundle_directory(
    path: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> _CompleteRecordingBundle:
    if not path.is_dir():
        raise StateConflictError("recording bundle directory is missing")
    names = {item.name for item in path.iterdir()}
    common = {"BUNDLE_MANIFEST.json", "RECORDING_ATTEMPT.json"}
    ordinary_names = common | {"ORDINARY_RECORD.json"}
    adult_names = common | {
        "ADULT_FULL_RECORD.json",
        "ADULT_CODEX_PROJECTION.json",
    }
    if names not in (ordinary_names, adult_names):
        raise StateConflictError("recording bundle file set changed")
    manifest_payload = _read_json(path / "BUNDLE_MANIFEST.json")
    manifest_version = manifest_payload.get("schema_version")
    if manifest_version == _RecordingBundleManifestV1.SCHEMA_VERSION:
        manifest: _RecordingBundleManifest = _decode_stored(
            _RecordingBundleManifestV1,
            manifest_payload,
            "recording bundle manifest",
        )
    elif manifest_version == _RecordingBundleManifestV2.SCHEMA_VERSION:
        manifest = _decode_stored(
            _RecordingBundleManifestV2,
            manifest_payload,
            "recording bundle manifest",
        )
    else:
        raise StateConflictError("recording bundle manifest version is unsupported")
    attempt = _recording_attempt_from_mapping(_read_json(path / "RECORDING_ATTEMPT.json"))
    if manifest.accepted_turn_id != accepted.accepted_turn_id:
        raise StateConflictError("recording bundle changed accepted turn identity")
    if manifest.route is not accepted.route:
        raise StateConflictError("recording bundle route differs from accepted turn")
    if isinstance(manifest, _RecordingBundleManifestV2) and (
        manifest.accepted_receipt_sha256 != accepted.receipt_sha256
        or manifest.exact_accepted_prose_sha256 != accepted.exact_accepted_prose_sha256
        or manifest.primary_authority_sha256 != accepted.primary_authority_sha256
    ):
        raise StateConflictError("recording bundle accepted custody changed")
    if (
        attempt.status is not RecordingStatus.COMPLETE
        or attempt.accepted_turn_id != accepted.accepted_turn_id
        or attempt.attempt_number != manifest.attempt_number
        or canonical_sha256(attempt) != manifest.attempt_sha256
        or attempt.ordinary_record_sha256 != manifest.ordinary_record_sha256
        or attempt.adult_full_record_sha256 != manifest.adult_full_record_sha256
        or attempt.adult_projection_sha256 != manifest.adult_projection_sha256
    ):
        raise StateConflictError("recording bundle attempt binding changed")
    if manifest.route is SceneRoute.ORDINARY:
        record = _decode_stored(
            OrdinarySceneRecordV1,
            _read_json(path / "ORDINARY_RECORD.json"),
            "ordinary scene record",
        )
        if canonical_sha256(record) != manifest.ordinary_record_sha256:
            raise StateConflictError("ordinary record differs from its bundle hash")
        _validate_stored_ordinary_record(record, accepted=accepted)
        return _CompleteRecordingBundle(attempt=attempt, ordinary_record=record)
    full = _decode_stored(
        AdultFullRecordV1,
        _read_json(path / "ADULT_FULL_RECORD.json"),
        "adult full record",
    )
    projection = _decode_adult_projection(
        _read_json(path / "ADULT_CODEX_PROJECTION.json"),
        label="adult Codex projection",
    )
    if canonical_sha256(full) != manifest.adult_full_record_sha256:
        raise StateConflictError("adult full record differs from its bundle hash")
    if canonical_sha256(projection) != manifest.adult_projection_sha256:
        raise StateConflictError("adult projection differs from its bundle hash")
    _validate_stored_adult_records(full, projection, accepted=accepted)
    return _CompleteRecordingBundle(
        attempt=attempt,
        adult_full_record=full,
        adult_projection=projection,
    )


def _load_historical_complete_recording_bundle(
    turn_dir: Path,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
    head: _RecordingHeadV1,
) -> _CompleteRecordingBundle:
    """Decode pre-bundle V1 storage without rewriting its immutable bytes."""

    attempt = _load_attempt_from_head(turn_dir, accepted=accepted, head=head)
    if attempt.status is not RecordingStatus.COMPLETE:
        raise StateConflictError("historical recording attempt is not complete")
    if attempt.ordinary_record_sha256 is not None:
        record = _decode_stored(
            OrdinarySceneRecordV1,
            _read_json(turn_dir / "ORDINARY_RECORD.json"),
            "historical ordinary scene record",
        )
        if canonical_sha256(record) != attempt.ordinary_record_sha256:
            raise StateConflictError("historical ordinary record hash changed")
        _validate_stored_ordinary_record(record, accepted=accepted)
        return _CompleteRecordingBundle(attempt=attempt, ordinary_record=record)
    full = _decode_stored(
        AdultFullRecordV1,
        _read_json(turn_dir / "ADULT_FULL_RECORD.json"),
        "historical adult full record",
    )
    projection = _decode_adult_projection(
        _read_json(turn_dir / "ADULT_CODEX_PROJECTION.json"),
        label="historical adult Codex projection",
    )
    if canonical_sha256(full) != attempt.adult_full_record_sha256:
        raise StateConflictError("historical adult full-record hash changed")
    if canonical_sha256(projection) != attempt.adult_projection_sha256:
        raise StateConflictError("historical adult projection hash changed")
    _validate_stored_adult_records(full, projection, accepted=accepted)
    return _CompleteRecordingBundle(
        attempt=attempt,
        adult_full_record=full,
        adult_projection=projection,
    )


def _validate_stored_ordinary_record(
    record: OrdinarySceneRecordV1,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> None:
    try:
        validate_ordinary_record(record, accepted=accepted)
    except ContractValidationError as exc:
        raise StateConflictError(f"stored ordinary record is invalid: {exc}") from exc


def _validate_stored_adult_records(
    full: AdultFullRecordV1,
    projection: AdultCodexProjectionV1 | AdultCodexProjectionV2,
    *,
    accepted: LeanAcceptedTurnReceiptV1,
) -> None:
    try:
        validate_adult_records(full, projection, accepted=accepted)
    except ContractValidationError as exc:
        raise StateConflictError(f"stored adult records are invalid: {exc}") from exc


def _require_ordinary_bundle_hash(
    bundle: _CompleteRecordingBundle,
    expected_sha256: str,
) -> None:
    if (
        bundle.ordinary_record is None
        or canonical_sha256(bundle.ordinary_record) != expected_sha256
    ):
        raise StateConflictError("ordinary record changed after attachment")


def _require_adult_bundle_hashes(
    bundle: _CompleteRecordingBundle,
    expected_full_sha256: str,
    expected_projection_sha256: str,
) -> None:
    if (
        bundle.adult_full_record is None
        or bundle.adult_projection is None
        or canonical_sha256(bundle.adult_full_record) != expected_full_sha256
        or canonical_sha256(bundle.adult_projection) != expected_projection_sha256
    ):
        raise StateConflictError("adult dual record changed after attachment")


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(canonical_json(dict(value)), encoding="utf-8")
    os.replace(temporary, path)


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = canonical_json(dict(value))
    try:
        with path.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
    except FileExistsError:
        if path.read_text(encoding="utf-8") != text:
            raise StateConflictError(f"immutable artifact changed: {path.name}") from None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StateConflictError(f"stored JSON is unreadable: {path.name}") from exc
    if not isinstance(value, dict):
        raise StateConflictError(f"stored JSON is not an object: {path.name}")
    return value


def _decode_stored[T](
    model_type: type[T],
    value: Mapping[str, Any],
    label: str,
) -> T:
    """Decode a closed durable DTO without primitive coercion."""

    try:
        return from_mapping(model_type, value)
    except (ContractValidationError, KeyError, TypeError, ValueError) as exc:
        raise StateConflictError(f"stored {label} is invalid: {exc}") from exc


def _accepted_receipt_from_mapping(value: Mapping[str, Any]) -> LeanAcceptedTurnReceiptV1:
    return _decode_stored(
        LeanAcceptedTurnReceiptV1,
        value,
        "accepted-turn receipt",
    )


def _recording_attempt_from_mapping(value: Mapping[str, Any]) -> LeanRecordingAttemptV1:
    return _decode_stored(
        LeanRecordingAttemptV1,
        value,
        "recording attempt",
    )


def ordinary_record_from_mapping(value: Mapping[str, Any]) -> OrdinarySceneRecordV1:
    return from_mapping(OrdinarySceneRecordV1, value)


def adult_full_record_from_mapping(value: Mapping[str, Any]) -> AdultFullRecordV1:
    return from_mapping(AdultFullRecordV1, value)


def adult_projection_from_mapping(
    value: Mapping[str, Any],
) -> AdultCodexProjectionV1 | AdultCodexProjectionV2:
    version = value.get("schema_version")
    if version == AdultCodexProjectionV1.SCHEMA_VERSION:
        return from_mapping(AdultCodexProjectionV1, value)
    if version == AdultCodexProjectionV2.SCHEMA_VERSION:
        return from_mapping(AdultCodexProjectionV2, value)
    raise ContractValidationError("adult Codex projection version is unsupported")


def _decode_adult_projection(
    value: Mapping[str, Any],
    *,
    label: str,
) -> AdultCodexProjectionV1 | AdultCodexProjectionV2:
    try:
        return adult_projection_from_mapping(value)
    except (ContractValidationError, KeyError, TypeError, ValueError) as exc:
        raise StateConflictError(f"stored {label} is invalid: {exc}") from exc
