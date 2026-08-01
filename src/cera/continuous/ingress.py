"""Trusted custody for continuous-turn source ownership classifications."""

from __future__ import annotations

from typing import Protocol

from cera.errors import ContractValidationError, StateConflictError
from cera.serialization import canonical_sha256, re_is_sha256, text_sha256

from .contracts import (
    ContinuousIngressAuthorityKind,
    ContinuousIngressReceiptV1,
    IngressSourceUnitV1,
)


class ContinuousIngressAuthorityPort(Protocol):
    def resolve(
        self, *, receipt_id: str, receipt_sha256: str
    ) -> ContinuousIngressReceiptV1: ...


class ContinuousIngressAuthorityStore:
    """Process-local authority store; requests carry only a receipt reference.

    The public issue methods validate exact source bytes before a receipt enters
    the store.  The runtime coordinator resolves the canonical stored receipt;
    it never trusts classifications supplied directly on a turn request.
    """

    def __init__(self) -> None:
        self._receipts: dict[str, ContinuousIngressReceiptV1] = {}

    def issue_frozen_fixture(
        self,
        *,
        fixture_id: str,
        world_id: str,
        branch_id: str,
        session_id: str,
        request_id: str,
        turn_id: str,
        idempotency_key: str,
        raw_source: str,
        protected_user_id: str,
        source_units: tuple[IngressSourceUnitV1, ...],
    ) -> ContinuousIngressReceiptV1:
        if not fixture_id.startswith("cera.fixture."):
            raise ContractValidationError("continuous fixture identity is not frozen")
        authority_identity = canonical_sha256(
            {
                "fixture_id": fixture_id,
                "world_id": world_id,
                "branch_id": branch_id,
                "session_id": session_id,
                "request_id": request_id,
                "turn_id": turn_id,
                "idempotency_key_sha256": text_sha256(idempotency_key),
                "raw_source_sha256": text_sha256(raw_source),
                "source_unit_sha256": tuple(
                    value.source_unit_sha256 for value in source_units
                ),
            }
        )
        return self._issue(
            authority_kind=ContinuousIngressAuthorityKind.FROZEN_PYTHON_FIXTURE,
            authority_identity_sha256=authority_identity,
            classification_adapter_id=fixture_id,
            world_id=world_id,
            branch_id=branch_id,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            idempotency_key=idempotency_key,
            raw_source=raw_source,
            protected_user_id=protected_user_id,
            source_units=source_units,
        )

    def issue_prepared_ingress(
        self,
        *,
        prepared_envelope_sha256: str,
        classification_receipt_sha256: str,
        classification_adapter_id: str,
        world_id: str,
        branch_id: str,
        session_id: str,
        request_id: str,
        turn_id: str,
        idempotency_key: str,
        raw_source: str,
        protected_user_id: str,
        source_units: tuple[IngressSourceUnitV1, ...],
    ) -> ContinuousIngressReceiptV1:
        if not re_is_sha256(prepared_envelope_sha256) or not re_is_sha256(
            classification_receipt_sha256
        ):
            raise ContractValidationError(
                "prepared continuous ingress lacks exact authority receipts"
            )
        if classification_adapter_id.startswith("cera.fixture."):
            raise ContractValidationError(
                "prepared continuous ingress cannot use a fixture adapter"
            )
        authority_identity = canonical_sha256(
            {
                "prepared_envelope_sha256": prepared_envelope_sha256,
                "classification_receipt_sha256": classification_receipt_sha256,
                "classification_adapter_id": classification_adapter_id,
            }
        )
        return self._issue(
            authority_kind=ContinuousIngressAuthorityKind.PREPARED_INGRESS,
            authority_identity_sha256=authority_identity,
            classification_adapter_id=classification_adapter_id,
            world_id=world_id,
            branch_id=branch_id,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            idempotency_key=idempotency_key,
            raw_source=raw_source,
            protected_user_id=protected_user_id,
            source_units=source_units,
        )

    def resolve(
        self, *, receipt_id: str, receipt_sha256: str
    ) -> ContinuousIngressReceiptV1:
        receipt = self._receipts.get(receipt_id)
        if receipt is None or receipt.receipt_sha256 != receipt_sha256:
            raise StateConflictError(
                "continuous ingress receipt is unknown or changed"
            )
        return receipt

    def _issue(
        self,
        *,
        authority_kind: ContinuousIngressAuthorityKind,
        authority_identity_sha256: str,
        classification_adapter_id: str,
        world_id: str,
        branch_id: str,
        session_id: str,
        request_id: str,
        turn_id: str,
        idempotency_key: str,
        raw_source: str,
        protected_user_id: str,
        source_units: tuple[IngressSourceUnitV1, ...],
    ) -> ContinuousIngressReceiptV1:
        if not idempotency_key.strip() or not raw_source:
            raise ContractValidationError(
                "continuous ingress issuance lacks source or idempotency identity"
            )
        cursor = 0
        for unit in source_units:
            if (
                unit.source_start != cursor
                or unit.source_end > len(raw_source)
                or raw_source[unit.source_start : unit.source_end] != unit.exact_text
            ):
                raise ContractValidationError(
                    "continuous ingress issuance requires gap-free exact source units"
                )
            cursor = unit.source_end
        if cursor != len(raw_source):
            raise ContractValidationError(
                "continuous ingress issuance does not cover the raw source"
            )
        receipt_seed = {
            "authority_kind": authority_kind.value,
            "authority_identity_sha256": authority_identity_sha256,
            "world_id": world_id,
            "branch_id": branch_id,
            "session_id": session_id,
            "request_id": request_id,
            "turn_id": turn_id,
            "idempotency_key_sha256": text_sha256(idempotency_key),
            "raw_source_sha256": text_sha256(raw_source),
            "protected_user_id": protected_user_id,
            "source_units": source_units,
        }
        receipt = ContinuousIngressReceiptV1(
            schema_version=ContinuousIngressReceiptV1.SCHEMA_VERSION,
            receipt_id="ingress_receipt:" + canonical_sha256(receipt_seed)[:24],
            world_id=world_id,
            branch_id=branch_id,
            session_id=session_id,
            request_id=request_id,
            turn_id=turn_id,
            idempotency_key_sha256=text_sha256(idempotency_key),
            raw_source_sha256=text_sha256(raw_source),
            protected_user_id=protected_user_id,
            authority_kind=authority_kind,
            authority_identity_sha256=authority_identity_sha256,
            classification_adapter_id=classification_adapter_id,
            source_units=source_units,
        )
        prior = self._receipts.get(receipt.receipt_id)
        if prior is not None and prior != receipt:
            raise StateConflictError("continuous ingress receipt identity collided")
        self._receipts[receipt.receipt_id] = receipt
        return receipt
