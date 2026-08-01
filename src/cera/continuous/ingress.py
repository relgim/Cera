"""Durable trusted custody for continuous-turn source classifications."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, Protocol

from cera.contracts import SourceUnitClassification
from cera.errors import ContractValidationError, StateConflictError
from cera.ingress import IntentInterpretationReceipt, RawTurnEnvelope
from cera.ingress.facade import PreparedIngressTurn
from cera.serialization import (
    canonical_bytes,
    canonical_json,
    canonical_sha256,
    domain_sha256,
    text_sha256,
    to_primitive,
)

from .contracts import (
    ContinuousIngressAuthorityKind,
    ContinuousIngressClassificationReceiptV1,
    ContinuousIngressReceiptV2,
    FrozenContinuousIngressFixtureV1,
    IngressSourceUnitKind,
    IngressSourceUnitV1,
    PreparedIngressClassifierDescriptorV1,
)


_PREPARED_TURN_DOMAIN = "cera.prepared_continuous_ingress_turn.v1"
_AUTHORITY_RECORD_SCHEMA = "cera.continuous_ingress_authority_record.v2"


class ContinuousIngressAuthorityPort(Protocol):
    def resolve(
        self, *, receipt_id: str, receipt_sha256: str
    ) -> ContinuousIngressReceiptV2: ...


class PreparedIngressClassificationPort(Protocol):
    """Python-owned semantic source classifier behind the prepared bridge."""

    classification_adapter_id: str

    def classify(
        self, envelope: RawTurnEnvelope, prepared: PreparedIngressTurn
    ) -> tuple[IngressSourceUnitV1, ...]: ...


class RepositoryPreparedIngressClassifierV1:
    """Exact, non-owning projection for the repository shadow ingress path.

    The generic raw-turn seam does not yet carry actor/speaker semantics.  This
    adapter therefore preserves every exact byte while refusing to invent
    protected-user authority.  Frozen canary fixtures remain the separate path
    for exact action/dialogue claims.
    """

    classification_adapter_id = "cera.prepared_ingress_classifier.nonowning_exact.v1"

    def classify(
        self, envelope: RawTurnEnvelope, prepared: PreparedIngressTurn
    ) -> tuple[IngressSourceUnitV1, ...]:
        units: list[IngressSourceUnitV1] = []
        instruction_kinds = {
            SourceUnitClassification.INSTRUCTION,
            SourceUnitClassification.CONSTRAINT,
        }
        for index, span in enumerate(prepared.interpretation.source_spans):
            kind = (
                IngressSourceUnitKind.INSTRUCTION
                if span.classification in instruction_kinds
                else IngressSourceUnitKind.NARRATION
            )
            units.append(
                IngressSourceUnitV1(
                    schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
                    source_unit_key=f"source_{index:04d}",
                    kind=kind,
                    source_start=span.start,
                    source_end=span.end,
                    exact_text=envelope.raw_message[span.start : span.end],
                    actor_id=None,
                    speaker_id=None,
                    classification_basis=(
                        "explicit_ingress_instruction"
                        if kind is IngressSourceUnitKind.INSTRUCTION
                        else "explicit_ingress_narration"
                    ),
                )
            )
        return tuple(units)


def _classifier_descriptor(
    classifier: PreparedIngressClassificationPort,
) -> PreparedIngressClassifierDescriptorV1:
    implementation = type(classifier)
    try:
        source = inspect.getsource(implementation)
    except (OSError, TypeError) as error:
        raise ContractValidationError(
            "prepared classifier implementation source is unavailable"
        ) from error
    return PreparedIngressClassifierDescriptorV1(
        schema_version=PreparedIngressClassifierDescriptorV1.SCHEMA_VERSION,
        classification_adapter_id=classifier.classification_adapter_id,
        implementation_module=implementation.__module__,
        implementation_qualname=implementation.__qualname__,
        implementation_source_sha256=text_sha256(source),
        source_unit_schema_version=IngressSourceUnitV1.SCHEMA_VERSION,
        classification_receipt_schema_version=(
            ContinuousIngressClassificationReceiptV1.SCHEMA_VERSION
        ),
    )


class PreparedIngressClassifierRegistry:
    """Closed registry; adapter strings cannot substitute implementations."""

    SCHEMA_VERSION = "cera.prepared_ingress_classifier_registry.v1"
    _ALLOWED_TYPES = (RepositoryPreparedIngressClassifierV1,)

    def __init__(
        self,
        classifiers: tuple[PreparedIngressClassificationPort, ...],
    ) -> None:
        if not classifiers:
            raise ContractValidationError("prepared classifier registry is empty")
        self._entries: dict[
            str,
            tuple[
                PreparedIngressClassificationPort,
                PreparedIngressClassifierDescriptorV1,
            ],
        ] = {}
        for classifier in classifiers:
            if type(classifier) not in self._ALLOWED_TYPES:
                raise ContractValidationError(
                    "prepared classifier implementation is not repository controlled"
                )
            descriptor = _classifier_descriptor(classifier)
            if descriptor.classification_adapter_id in self._entries:
                raise ContractValidationError(
                    "prepared classifier registry contains duplicate adapter IDs"
                )
            self._entries[descriptor.classification_adapter_id] = (
                classifier,
                descriptor,
            )

    def resolve(
        self, classification_adapter_id: str
    ) -> tuple[
        PreparedIngressClassificationPort,
        PreparedIngressClassifierDescriptorV1,
    ]:
        entry = self._entries.get(classification_adapter_id)
        if entry is None:
            raise ContractValidationError(
                "prepared classifier identity is not in the closed registry"
            )
        classifier, frozen_descriptor = entry
        current_descriptor = _classifier_descriptor(classifier)
        if current_descriptor != frozen_descriptor:
            raise StateConflictError(
                "prepared classifier implementation identity changed"
            )
        return classifier, frozen_descriptor

    def assert_descriptor(
        self,
        *,
        classification_adapter_id: str,
        descriptor_sha256: str,
    ) -> PreparedIngressClassifierDescriptorV1:
        _, descriptor = self.resolve(classification_adapter_id)
        if descriptor.descriptor_sha256 != descriptor_sha256:
            raise StateConflictError(
                "prepared classifier descriptor is stale or substituted"
            )
        return descriptor

    @property
    def registry_sha256(self) -> str:
        return domain_sha256(
            self.SCHEMA_VERSION,
            tuple(
                self._entries[key][1]
                for key in sorted(self._entries)
            ),
        )


def build_default_prepared_classifier_registry() -> PreparedIngressClassifierRegistry:
    return PreparedIngressClassifierRegistry((RepositoryPreparedIngressClassifierV1(),))


class PreparedContinuousIngressBridge:
    """Verify actual ingress objects before issuing a continuous receipt."""

    def __init__(
        self,
        *,
        authority: "ContinuousIngressAuthorityStore",
        classifier_registry: PreparedIngressClassifierRegistry,
        classification_adapter_id: str = (
            RepositoryPreparedIngressClassifierV1.classification_adapter_id
        ),
    ) -> None:
        classifier, descriptor = classifier_registry.resolve(
            classification_adapter_id
        )
        self.authority = authority
        self.classifier = classifier
        self.classifier_registry = classifier_registry
        self.classifier_descriptor = descriptor

    def issue(
        self,
        *,
        envelope: RawTurnEnvelope,
        prepared: PreparedIngressTurn,
        turn_id: str,
    ) -> ContinuousIngressReceiptV2:
        envelope_sha256 = domain_sha256(RawTurnEnvelope.SCHEMA_VERSION, envelope)
        interpretation = prepared.interpretation
        interpretation_receipt = prepared.interpretation_receipt
        if interpretation_receipt.envelope_sha256 != envelope_sha256:
            raise StateConflictError(
                "prepared continuous ingress changed the raw-turn envelope"
            )
        if interpretation_receipt.interpretation_sha256 != interpretation.draft_sha256:
            raise StateConflictError(
                "prepared continuous ingress changed the interpretation"
            )
        request = prepared.reasoner_request.prepared_turn.request
        expected_ids = (
            (str(request.world_id), str(envelope.world_id), "world"),
            (str(request.branch_id), str(envelope.branch_id), "branch"),
            (str(request.session_id), str(envelope.session_id), "session"),
            (str(request.request_id), str(envelope.request_id), "request"),
            (
                str(request.protected_user_id),
                str(envelope.protected_user_id),
                "protected user",
            ),
        )
        for observed, expected, field in expected_ids:
            if observed != expected:
                raise StateConflictError(
                    f"prepared continuous ingress changed {field} identity"
                )
        expected_source_sha256 = _prepared_source_sha256(envelope, prepared)
        if (
            request.idempotency_key != envelope.idempotency_key
            or request.source_sha256 != expected_source_sha256
            or prepared.reasoner_request.source_view.source_sha256
            != expected_source_sha256
        ):
            raise StateConflictError(
                "prepared continuous ingress changed source or idempotency identity"
            )
        source_units = self.classifier.classify(envelope, prepared)
        prepared_turn_sha256 = domain_sha256(_PREPARED_TURN_DOMAIN, prepared)
        interpretation_receipt_sha256 = domain_sha256(
            IntentInterpretationReceipt.SCHEMA_VERSION, interpretation_receipt
        )
        classification = ContinuousIngressClassificationReceiptV1(
            schema_version=ContinuousIngressClassificationReceiptV1.SCHEMA_VERSION,
            envelope_sha256=envelope_sha256,
            prepared_turn_sha256=prepared_turn_sha256,
            interpretation_receipt_sha256=interpretation_receipt_sha256,
            classification_adapter_id=self.classifier.classification_adapter_id,
            classifier_descriptor_sha256=(
                self.classifier_descriptor.descriptor_sha256
            ),
            protected_user_id=str(envelope.protected_user_id),
            raw_source_sha256=text_sha256(envelope.raw_message),
            source_units=source_units,
        )
        return self.authority._issue_prepared_ingress(
            envelope=envelope,
            prepared=prepared,
            classification=classification,
            classifier_descriptor=self.classifier_descriptor,
            turn_id=turn_id,
        )


class ContinuousIngressAuthorityStore:
    """Immutable receipt store with a closed fixture registry and restart reads."""

    def __init__(
        self,
        root: Path,
        *,
        fixture_registry: tuple[FrozenContinuousIngressFixtureV1, ...] = (),
        prepared_classifier_registry: PreparedIngressClassifierRegistry | None = None,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.prepared_classifier_registry = prepared_classifier_registry
        self._fixtures: dict[str, FrozenContinuousIngressFixtureV1] = {}
        for fixture in fixture_registry:
            if fixture.fixture_id in self._fixtures:
                raise ContractValidationError(
                    "continuous fixture registry contains duplicate IDs"
                )
            self._fixtures[fixture.fixture_id] = fixture

    def issue_frozen_fixture(
        self,
        *,
        fixture_id: str,
        idempotency_key: str,
    ) -> ContinuousIngressReceiptV2:
        fixture = self._fixtures.get(fixture_id)
        if fixture is None:
            raise ContractValidationError(
                "continuous fixture identity is not in the frozen registry"
            )
        if text_sha256(idempotency_key) != fixture.idempotency_key_sha256:
            raise StateConflictError("continuous fixture idempotency identity changed")
        return self._issue(
            authority_kind=ContinuousIngressAuthorityKind.FROZEN_PYTHON_FIXTURE,
            authority_identity_sha256=fixture.fixture_sha256,
            classification_adapter_id=fixture.fixture_id,
            classifier_descriptor_sha256=None,
            world_id=fixture.world_id,
            branch_id=fixture.branch_id,
            session_id=fixture.session_id,
            request_id=fixture.request_id,
            turn_id=fixture.turn_id,
            idempotency_key=idempotency_key,
            raw_source=fixture.raw_source,
            protected_user_id=fixture.protected_user_id,
            source_units=fixture.source_units,
            prepared_envelope_sha256=None,
            prepared_turn_sha256=None,
            interpretation_receipt_sha256=None,
            classification_receipt_sha256=None,
            fixture_entry_sha256=fixture.fixture_sha256,
            authority_evidence={"fixture": to_primitive(fixture)},
        )

    def _issue_prepared_ingress(
        self,
        *,
        envelope: RawTurnEnvelope,
        prepared: PreparedIngressTurn,
        classification: ContinuousIngressClassificationReceiptV1,
        classifier_descriptor: PreparedIngressClassifierDescriptorV1,
        turn_id: str,
    ) -> ContinuousIngressReceiptV2:
        envelope_sha256 = domain_sha256(RawTurnEnvelope.SCHEMA_VERSION, envelope)
        prepared_turn_sha256 = domain_sha256(_PREPARED_TURN_DOMAIN, prepared)
        interpretation_receipt_sha256 = domain_sha256(
            IntentInterpretationReceipt.SCHEMA_VERSION,
            prepared.interpretation_receipt,
        )
        if (
            classification.envelope_sha256 != envelope_sha256
            or classification.prepared_turn_sha256 != prepared_turn_sha256
            or classification.interpretation_receipt_sha256
            != interpretation_receipt_sha256
            or classification.raw_source_sha256
            != text_sha256(envelope.raw_message)
            or classification.protected_user_id != str(envelope.protected_user_id)
            or classification.classifier_descriptor_sha256
            != classifier_descriptor.descriptor_sha256
            or classification.classification_adapter_id
            != classifier_descriptor.classification_adapter_id
        ):
            raise StateConflictError(
                "prepared continuous classification changed authoritative ingress"
            )
        request = prepared.reasoner_request.prepared_turn.request
        authority_identity = canonical_sha256(
            {
                "envelope_sha256": envelope_sha256,
                "prepared_turn_sha256": prepared_turn_sha256,
                "interpretation_receipt_sha256": interpretation_receipt_sha256,
                "classification_receipt_sha256": classification.receipt_sha256,
                "classification_adapter_id": classification.classification_adapter_id,
                "classifier_descriptor_sha256": (
                    classifier_descriptor.descriptor_sha256
                ),
            }
        )
        return self._issue(
            authority_kind=ContinuousIngressAuthorityKind.PREPARED_INGRESS,
            authority_identity_sha256=authority_identity,
            classification_adapter_id=classification.classification_adapter_id,
            classifier_descriptor_sha256=classifier_descriptor.descriptor_sha256,
            world_id=str(envelope.world_id),
            branch_id=str(envelope.branch_id),
            session_id=str(envelope.session_id),
            request_id=str(envelope.request_id),
            turn_id=turn_id,
            idempotency_key=request.idempotency_key,
            raw_source=envelope.raw_message,
            protected_user_id=str(envelope.protected_user_id),
            source_units=classification.source_units,
            prepared_envelope_sha256=envelope_sha256,
            prepared_turn_sha256=prepared_turn_sha256,
            interpretation_receipt_sha256=interpretation_receipt_sha256,
            classification_receipt_sha256=classification.receipt_sha256,
            fixture_entry_sha256=None,
            authority_evidence={
                "envelope": to_primitive(envelope),
                "prepared_turn": to_primitive(prepared),
                "interpretation_receipt": to_primitive(
                    prepared.interpretation_receipt
                ),
                "classification_receipt": to_primitive(classification),
                "classifier_descriptor": to_primitive(classifier_descriptor),
            },
        )

    def resolve(
        self, *, receipt_id: str, receipt_sha256: str
    ) -> ContinuousIngressReceiptV2:
        path = self.root / f"{receipt_sha256}.json"
        if not path.is_file():
            raise StateConflictError(
                "continuous ingress receipt is unknown or changed"
            )
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("schema_version") != _AUTHORITY_RECORD_SCHEMA:
                raise ValueError("record schema changed")
            receipt = _decode_receipt(record["receipt"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StateConflictError(
                "continuous ingress authority record is malformed"
            ) from error
        if receipt.receipt_id != receipt_id or receipt.receipt_sha256 != receipt_sha256:
            raise StateConflictError(
                "continuous ingress receipt is unknown or changed"
            )
        try:
            evidence = record["authority_evidence"]
            if not isinstance(evidence, dict):
                raise TypeError("authority evidence is not an object")
            self._validate_authority_evidence(receipt, evidence)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise StateConflictError(
                "continuous ingress authority evidence is malformed"
            ) from error
        return receipt

    def _issue(
        self,
        *,
        authority_kind: ContinuousIngressAuthorityKind,
        authority_identity_sha256: str,
        classification_adapter_id: str,
        classifier_descriptor_sha256: str | None,
        world_id: str,
        branch_id: str,
        session_id: str,
        request_id: str,
        turn_id: str,
        idempotency_key: str,
        raw_source: str,
        protected_user_id: str,
        source_units: tuple[IngressSourceUnitV1, ...],
        prepared_envelope_sha256: str | None,
        prepared_turn_sha256: str | None,
        interpretation_receipt_sha256: str | None,
        classification_receipt_sha256: str | None,
        fixture_entry_sha256: str | None,
        authority_evidence: dict[str, Any],
    ) -> ContinuousIngressReceiptV2:
        _validate_exact_units(raw_source, source_units)
        if not idempotency_key.strip():
            raise ContractValidationError(
                "continuous ingress issuance lacks idempotency identity"
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
        receipt = ContinuousIngressReceiptV2(
            schema_version=ContinuousIngressReceiptV2.SCHEMA_VERSION,
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
            classifier_descriptor_sha256=classifier_descriptor_sha256,
            prepared_envelope_sha256=prepared_envelope_sha256,
            prepared_turn_sha256=prepared_turn_sha256,
            interpretation_receipt_sha256=interpretation_receipt_sha256,
            classification_receipt_sha256=classification_receipt_sha256,
            fixture_entry_sha256=fixture_entry_sha256,
            source_units=source_units,
        )
        record = {
            "schema_version": _AUTHORITY_RECORD_SCHEMA,
            "receipt": to_primitive(receipt),
            "authority_evidence": authority_evidence,
        }
        path = self.root / f"{receipt.receipt_sha256}.json"
        data = canonical_bytes(record) + b"\n"
        if path.exists():
            if path.read_bytes() != data:
                raise StateConflictError(
                    "continuous ingress authority record identity collided"
                )
            return self.resolve(
                receipt_id=receipt.receipt_id,
                receipt_sha256=receipt.receipt_sha256,
            )
        temporary = path.with_suffix(".json.tmp")
        temporary.write_bytes(data)
        temporary.replace(path)
        return self.resolve(
            receipt_id=receipt.receipt_id,
            receipt_sha256=receipt.receipt_sha256,
        )

    def _validate_authority_evidence(
        self,
        receipt: ContinuousIngressReceiptV2,
        evidence: dict[str, Any],
    ) -> None:
        if receipt.authority_kind is ContinuousIngressAuthorityKind.FROZEN_PYTHON_FIXTURE:
            fixture = _decode_fixture(evidence["fixture"])
            if (
                fixture.fixture_sha256 != receipt.fixture_entry_sha256
                or fixture.fixture_id != receipt.classification_adapter_id
                or fixture.world_id != receipt.world_id
                or fixture.branch_id != receipt.branch_id
                or fixture.session_id != receipt.session_id
                or fixture.request_id != receipt.request_id
                or fixture.turn_id != receipt.turn_id
                or fixture.idempotency_key_sha256 != receipt.idempotency_key_sha256
                or text_sha256(fixture.raw_source) != receipt.raw_source_sha256
                or fixture.protected_user_id != receipt.protected_user_id
                or fixture.source_units != receipt.source_units
            ):
                raise StateConflictError(
                    "continuous fixture authority record changed"
                )
            return
        envelope = evidence["envelope"]
        prepared = evidence["prepared_turn"]
        interpretation = evidence["interpretation_receipt"]
        classification = _decode_classification(evidence["classification_receipt"])
        descriptor = PreparedIngressClassifierDescriptorV1(
            **evidence["classifier_descriptor"]
        )
        if self.prepared_classifier_registry is None:
            raise StateConflictError(
                "prepared continuous ingress lacks its closed classifier registry"
            )
        self.prepared_classifier_registry.assert_descriptor(
            classification_adapter_id=receipt.classification_adapter_id,
            descriptor_sha256=descriptor.descriptor_sha256,
        )
        if (
            domain_sha256(RawTurnEnvelope.SCHEMA_VERSION, envelope)
            != receipt.prepared_envelope_sha256
            or domain_sha256(_PREPARED_TURN_DOMAIN, prepared)
            != receipt.prepared_turn_sha256
            or domain_sha256(IntentInterpretationReceipt.SCHEMA_VERSION, interpretation)
            != receipt.interpretation_receipt_sha256
            or classification.receipt_sha256
            != receipt.classification_receipt_sha256
            or classification.envelope_sha256 != receipt.prepared_envelope_sha256
            or classification.prepared_turn_sha256 != receipt.prepared_turn_sha256
            or classification.interpretation_receipt_sha256
            != receipt.interpretation_receipt_sha256
            or classification.classification_adapter_id
            != receipt.classification_adapter_id
            or classification.classifier_descriptor_sha256
            != receipt.classifier_descriptor_sha256
            or descriptor.descriptor_sha256 != receipt.classifier_descriptor_sha256
            or descriptor.classification_adapter_id
            != receipt.classification_adapter_id
            or classification.source_units != receipt.source_units
            or classification.raw_source_sha256 != receipt.raw_source_sha256
            or classification.protected_user_id != receipt.protected_user_id
            or envelope.get("raw_message")
            != "".join(value.exact_text for value in receipt.source_units)
        ):
            raise StateConflictError(
                "prepared continuous ingress authority record changed"
            )


def _validate_exact_units(
    raw_source: str, source_units: tuple[IngressSourceUnitV1, ...]
) -> None:
    if not raw_source or not source_units:
        raise ContractValidationError(
            "continuous ingress issuance lacks exact source units"
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


def _prepared_source_sha256(
    envelope: RawTurnEnvelope, prepared: PreparedIngressTurn
) -> str:
    """Recompute the TurnKernel protected-source hash from durable ingress."""

    spans = prepared.interpretation.source_spans
    request_units = prepared.reasoner_request.prepared_turn.request.source_units
    if len(spans) != len(request_units):
        raise StateConflictError(
            "prepared continuous ingress changed source-unit cardinality"
        )
    cursor = 0
    payload_units: list[dict[str, str]] = []
    for span, request_unit in zip(spans, request_units):
        if span.start != cursor or span.end > len(envelope.raw_message):
            raise StateConflictError(
                "prepared continuous ingress changed ordered source spans"
            )
        exact = envelope.raw_message[span.start : span.end]
        if (
            request_unit.classification is not span.classification
            or request_unit.sha256 != text_sha256(exact)
        ):
            raise StateConflictError(
                "prepared continuous ingress changed source classification or bytes"
            )
        payload_units.append(
            {"classification": span.classification.value, "text": exact}
        )
        cursor = span.end
    if cursor != len(envelope.raw_message):
        raise StateConflictError(
            "prepared continuous ingress does not cover the raw source"
        )
    return text_sha256(canonical_json({"source_units": tuple(payload_units)}))


def _decode_unit(value: dict[str, Any]) -> IngressSourceUnitV1:
    return IngressSourceUnitV1(
        schema_version=value["schema_version"],
        source_unit_key=value["source_unit_key"],
        kind=IngressSourceUnitKind(value["kind"]),
        source_start=value["source_start"],
        source_end=value["source_end"],
        exact_text=value["exact_text"],
        actor_id=value.get("actor_id"),
        speaker_id=value.get("speaker_id"),
        classification_basis=value["classification_basis"],
    )


def _decode_fixture(value: dict[str, Any]) -> FrozenContinuousIngressFixtureV1:
    return FrozenContinuousIngressFixtureV1(
        **{
            **value,
            "source_units": tuple(_decode_unit(unit) for unit in value["source_units"]),
        }
    )


def _decode_classification(
    value: dict[str, Any],
) -> ContinuousIngressClassificationReceiptV1:
    return ContinuousIngressClassificationReceiptV1(
        **{
            **value,
            "source_units": tuple(_decode_unit(unit) for unit in value["source_units"]),
        }
    )


def _decode_receipt(value: dict[str, Any]) -> ContinuousIngressReceiptV2:
    return ContinuousIngressReceiptV2(
        **{
            **value,
            "authority_kind": ContinuousIngressAuthorityKind(value["authority_kind"]),
            "source_units": tuple(_decode_unit(unit) for unit in value["source_units"]),
        }
    )
