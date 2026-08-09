"""Typed active-lineage custody for immutable Pi Scene accepted objects.

Accepted turn directories are objects: once published, their bytes never
change.  ``ACTIVE_LINEAGE.json`` is the small atomic selector that decides
which receipt chain is current for one branch.  Replacing the selector never
deletes or rewrites an accepted object.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256, to_primitive

from .contracts import LeanAcceptedTurnReceiptV1


@dataclass(frozen=True, slots=True)
class LeanActiveLineageV1:
    """The current accepted head and the immediately previous selector."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.active_lineage.v1"

    schema_version: str
    world_id: str
    branch_id: str
    revision: int
    switch_kind: str
    selected_generation: int
    selected_turn_id: str | None
    selected_receipt_sha256: str | None
    previous_generation: int
    previous_turn_id: str | None
    previous_receipt_sha256: str | None

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("active-lineage schema changed")
        for name in ("world_id", "branch_id"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ContractValidationError(f"active-lineage {name} is empty")
        if type(self.revision) is not int or self.revision < 0:
            raise ContractValidationError("active-lineage revision is invalid")
        if self.switch_kind not in {"legacy", "append", "replacement", "fork"}:
            raise ContractValidationError("active-lineage switch kind is invalid")
        self._validate_pointer(
            generation=self.selected_generation,
            turn_id=self.selected_turn_id,
            receipt_sha256=self.selected_receipt_sha256,
            label="selected",
        )
        self._validate_pointer(
            generation=self.previous_generation,
            turn_id=self.previous_turn_id,
            receipt_sha256=self.previous_receipt_sha256,
            label="previous",
        )
        if self.revision == 0 and self.switch_kind != "legacy":
            raise ContractValidationError("active-lineage revision zero must be legacy")

    @staticmethod
    def _validate_pointer(
        *,
        generation: int,
        turn_id: str | None,
        receipt_sha256: str | None,
        label: str,
    ) -> None:
        if type(generation) is not int or generation < 0:
            raise ContractValidationError(f"active-lineage {label} generation is invalid")
        if generation == 0:
            if turn_id is not None or receipt_sha256 is not None:
                raise ContractValidationError(
                    f"active-lineage {label} root contains a turn"
                )
            return
        if (
            type(turn_id) is not str
            or not turn_id.strip()
            or type(receipt_sha256) is not str
            or not re_is_sha256(receipt_sha256)
        ):
            raise ContractValidationError(
                f"active-lineage {label} pointer is incomplete"
            )


@dataclass(frozen=True, slots=True)
class LeanAcceptedRegenerationBaseV1:
    """Frozen authority for replacing the selected head with a sibling."""

    world_id: str
    branch_id: str
    generation: int
    replaced_turn_id: str
    replaced_receipt_sha256: str
    parent_accepted_turn_id: str | None
    parent_accepted_head_sha256: str | None
    selected_prefix_receipt_sha256s: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("world_id", "branch_id", "replaced_turn_id"):
            value = getattr(self, name)
            if type(value) is not str or not value.strip():
                raise ContractValidationError(f"regeneration base {name} is empty")
        if type(self.generation) is not int or self.generation < 1:
            raise ContractValidationError("regeneration base generation is invalid")
        if not re_is_sha256(self.replaced_receipt_sha256):
            raise ContractValidationError("regeneration base receipt hash is invalid")
        if len(self.selected_prefix_receipt_sha256s) != self.generation - 1:
            raise ContractValidationError("regeneration base prefix length changed")
        if any(not re_is_sha256(value) for value in self.selected_prefix_receipt_sha256s):
            raise ContractValidationError("regeneration base prefix hash is invalid")
        if self.generation == 1:
            if (
                self.parent_accepted_turn_id is not None
                or self.parent_accepted_head_sha256 is not None
            ):
                raise ContractValidationError("root regeneration base contains a parent")
        elif (
            type(self.parent_accepted_turn_id) is not str
            or not self.parent_accepted_turn_id.strip()
            or type(self.parent_accepted_head_sha256) is not str
            or not re_is_sha256(self.parent_accepted_head_sha256)
            or self.selected_prefix_receipt_sha256s[-1]
            != self.parent_accepted_head_sha256
        ):
            raise ContractValidationError("regeneration base parent binding changed")

    @property
    def binding_sha256(self) -> str:
        return canonical_sha256(self)


def active_lineage_payload(value: LeanActiveLineageV1) -> dict[str, Any]:
    body = to_primitive(value)
    return {**body, "manifest_sha256": canonical_sha256(body)}


def active_lineage_from_payload(value: Any) -> LeanActiveLineageV1:
    fields = {
        "schema_version",
        "world_id",
        "branch_id",
        "revision",
        "switch_kind",
        "selected_generation",
        "selected_turn_id",
        "selected_receipt_sha256",
        "previous_generation",
        "previous_turn_id",
        "previous_receipt_sha256",
        "manifest_sha256",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise StateConflictError("active-lineage manifest fields changed")
    body = {key: value[key] for key in value if key != "manifest_sha256"}
    if value["manifest_sha256"] != canonical_sha256(body):
        raise StateConflictError("active-lineage manifest integrity changed")
    try:
        return LeanActiveLineageV1(**body)
    except (TypeError, ValueError, ContractValidationError) as exc:
        raise StateConflictError("active-lineage manifest values are invalid") from exc


def selected_receipt_chain(
    *,
    lineage: LeanActiveLineageV1,
    receipts_by_sha256: Mapping[str, LeanAcceptedTurnReceiptV1],
) -> tuple[LeanAcceptedTurnReceiptV1, ...]:
    """Resolve only the selected prefix; inactive siblings are never returned."""

    if lineage.selected_generation == 0:
        if receipts_by_sha256:
            # Orphan objects may exist after a pre-selector crash.  They remain
            # inspectable, but an explicit root selector still selects none.
            return ()
        return ()
    selected_hash = lineage.selected_receipt_sha256
    assert selected_hash is not None
    reversed_chain: list[LeanAcceptedTurnReceiptV1] = []
    seen: set[str] = set()
    while selected_hash is not None:
        if selected_hash in seen:
            raise StateConflictError("active-lineage receipt chain contains a cycle")
        seen.add(selected_hash)
        receipt = receipts_by_sha256.get(selected_hash)
        if receipt is None:
            raise StateConflictError("active-lineage selected receipt is missing")
        reversed_chain.append(receipt)
        selected_hash = receipt.parent_accepted_head_sha256
    chain = tuple(reversed(reversed_chain))
    if len(chain) != lineage.selected_generation:
        raise StateConflictError("active-lineage selected generation changed")
    if (
        chain[-1].accepted_turn_id != lineage.selected_turn_id
        or chain[-1].receipt_sha256 != lineage.selected_receipt_sha256
    ):
        raise StateConflictError("active-lineage selected head binding changed")
    return chain


def receipt_sha_index(
    receipts: Sequence[LeanAcceptedTurnReceiptV1],
) -> dict[str, LeanAcceptedTurnReceiptV1]:
    output: dict[str, LeanAcceptedTurnReceiptV1] = {}
    for receipt in receipts:
        if receipt.receipt_sha256 in output:
            raise StateConflictError("accepted receipt hash occurs more than once")
        output[receipt.receipt_sha256] = receipt
    return output


def accepted_object_directory_name(
    *,
    generation: int,
    accepted_turn_id: str,
    receipt_sha256: str,
) -> str:
    """Short, collision-checked Windows-safe name for one accepted object."""

    if type(generation) is not int or generation < 1:
        raise ContractValidationError("accepted object generation is invalid")
    if type(accepted_turn_id) is not str or not accepted_turn_id.strip():
        raise ContractValidationError("accepted object turn identity is invalid")
    if type(receipt_sha256) is not str or not re_is_sha256(receipt_sha256):
        raise ContractValidationError("accepted object receipt hash is invalid")
    return f"{generation:08d}-{receipt_sha256[:24]}"
