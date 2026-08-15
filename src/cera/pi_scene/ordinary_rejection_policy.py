"""Fail-closed standing policy for tolerable ordinary validation deviations.

The independent validators keep their original pass/reject authority.  This
module only decides whether an already-qualified ordinary rejection may enter
provisional continuity under the creator's versioned standing policy.  Adult
routes never call this policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.reader_validation import BoundReaderValidationV1, ReaderStatus
from cera.semantic_validation import (
    BoundSemanticValidationV1,
    SemanticConflictClass,
    SemanticVerdict,
)
from cera.sequence_first import RetryFeedbackScope
from cera.serialization import canonical_sha256, re_is_sha256, text_sha256, to_primitive

from .review_lifecycle import OrdinaryPythonQualificationV1

ORDINARY_STANDING_CREATOR_POLICY_TEXT = (
    "For ordinary scenes only, preserve a usable first Writer candidate as provisional "
    "continuity when Python custody passes and the candidate does not invent Ted dialogue, "
    "Ted thoughts, feelings, memories, or private state; contradict immutable identity, adult "
    "age, parent-child relation, or accepted-event existence; invent or override consent, "
    "withdrawal, or a major lasting protected-user choice; or become unusable, off-topic, or "
    "incoherent. Treat every other Luna conflict and every localized Reader exact-quote or "
    "omitted-planner-item issue as provisional continuity. Keep all validator evidence visible "
    "and do not Regenerate. Adult scenes remain governed by their separate consent, capacity, "
    "privacy, projection, and route-transition checks."
)
ORDINARY_STANDING_CREATOR_POLICY_ID = "ordinary_provisional_continuity"
ORDINARY_STANDING_CREATOR_POLICY_VERSION = 2
ORDINARY_STANDING_CREATOR_POLICY_TEXT_SHA256 = (
    "7a7366d626fb550674cee1cedbf11d0c8fc7ec4ee4391c7f5afb28e2a36bb336"
)
ORDINARY_STANDING_CREATOR_POLICY_SHA256 = (
    "628b72c8e5ced09f6af1b469a9825bad446eb3fd1560702854d8529b37e9dcc5"
)

_HISTORICAL_POLICY_V1_TEXT = (
    "For ordinary scenes only, preserve a usable first Writer candidate as provisional "
    "continuity when every validator rejection is limited to an omitted planned beat, "
    "presence or inactive-background staging, a stopping-boundary or aftermath drift, "
    "or an ordinary capability restriction. Keep all validator evidence visible and do "
    "not Regenerate. All other semantic conflicts, inconclusive Reader results, and "
    "whole-candidate quality failures remain hard stops. Adult scenes are excluded."
)
_HISTORICAL_POLICY_V1_TEXT_SHA256 = (
    "1fe7bf05034f1543040eac456818269768760e58dc625eecc03508bd64a804e2"
)
_HISTORICAL_POLICY_V1_SHA256 = "47729a4fc27046e8da768c8e0f1bc6670be48e576e606f193339de30b3bf3b23"


@dataclass(frozen=True, slots=True)
class OrdinaryStandingCreatorPolicyV1:
    """Immutable creator authority for provisional ordinary continuity."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.ordinary_standing_creator_policy.v1"

    schema_version: str
    authority_kind: str
    policy_id: str
    policy_version: int
    policy_text_sha256: str
    soft_semantic_conflict_classes: tuple[str, ...]
    soft_reader_feedback_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary standing-policy schema changed")
        if self.authority_kind != "standing_creator_policy":
            raise ContractValidationError("ordinary standing-policy authority changed")
        if self.policy_id != ORDINARY_STANDING_CREATOR_POLICY_ID:
            raise ContractValidationError("ordinary standing-policy identity changed")
        text, expected_text_sha256, _policy_sha256, semantic_classes = _policy_spec(
            self.policy_version
        )
        if (
            self.policy_text_sha256 != expected_text_sha256
            or self.policy_text_sha256 != text_sha256(text)
        ):
            raise ContractValidationError("ordinary standing-policy text binding changed")
        expected_semantic = tuple(sorted(value.value for value in semantic_classes))
        expected_reader = tuple(sorted(value.value for value in _SOFT_READER_SCOPES))
        if (
            self.soft_semantic_conflict_classes != expected_semantic
            or self.soft_reader_feedback_scopes != expected_reader
        ):
            raise ContractValidationError("ordinary standing-policy allowlist changed")

    @property
    def policy_sha256(self) -> str:
        return canonical_sha256(self)


@dataclass(frozen=True, slots=True)
class OrdinaryPolicyAcceptanceAuditV1:
    """Hash-only proof that one rejected candidate matched the standing policy."""

    SCHEMA_VERSION: ClassVar[str] = "cera.pi_scene.ordinary_policy_acceptance_audit.v1"

    schema_version: str
    authority_kind: str
    policy_id: str
    policy_version: int
    policy_sha256: str
    candidate_sha256: str
    semantic_validation_sha256: str
    reader_validation_sha256: str
    python_qualification_sha256: str
    tolerated_reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        policy = ordinary_standing_creator_policy(self.policy_version)
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("ordinary policy-acceptance audit schema changed")
        if (
            self.authority_kind != policy.authority_kind
            or self.policy_id != policy.policy_id
            or self.policy_version != policy.policy_version
            or self.policy_sha256 != policy.policy_sha256
        ):
            raise ContractValidationError("ordinary policy-acceptance authority changed")
        for name in (
            "candidate_sha256",
            "semantic_validation_sha256",
            "reader_validation_sha256",
            "python_qualification_sha256",
        ):
            if not re_is_sha256(getattr(self, name)):
                raise ContractValidationError(f"ordinary policy audit {name} is invalid")
        if (
            not self.tolerated_reason_codes
            or self.tolerated_reason_codes != tuple(sorted(set(self.tolerated_reason_codes)))
            or not set(self.tolerated_reason_codes).issubset(_soft_reason_codes_for_policy(policy))
        ):
            raise ContractValidationError("ordinary policy tolerated reasons are invalid")

    @property
    def audit_sha256(self) -> str:
        return canonical_sha256(self)


_HISTORICAL_SOFT_SEMANTIC_CONFLICTS_V1 = frozenset(
    {
        SemanticConflictClass.OMITTED_DECISION,
        SemanticConflictClass.PRESENCE_VIOLATION,
        SemanticConflictClass.STOPPING_BOUNDARY,
        SemanticConflictClass.CAPABILITY_RESTRICTION,
    }
)
_SOFT_SEMANTIC_CONFLICTS = frozenset(
    set(SemanticConflictClass)
    - {
        SemanticConflictClass.LOCKED_FACT_CONFLICT,
        SemanticConflictClass.PROTECTED_USER_DIALOGUE,
        SemanticConflictClass.PROTECTED_USER_PRIVATE_STATE,
        SemanticConflictClass.PROTECTED_BOUNDARY_CONFLICT,
        SemanticConflictClass.SEVERE_INCOMPLETENESS,
    }
)
_SOFT_READER_SCOPES = frozenset(
    {
        RetryFeedbackScope.EXACT_QUOTE,
        RetryFeedbackScope.OMITTED_PLANNER_ITEM,
    }
)


def _policy_spec(
    policy_version: int,
) -> tuple[str, str, str, frozenset[SemanticConflictClass]]:
    if policy_version == 1:
        return (
            _HISTORICAL_POLICY_V1_TEXT,
            _HISTORICAL_POLICY_V1_TEXT_SHA256,
            _HISTORICAL_POLICY_V1_SHA256,
            _HISTORICAL_SOFT_SEMANTIC_CONFLICTS_V1,
        )
    if policy_version == ORDINARY_STANDING_CREATOR_POLICY_VERSION:
        return (
            ORDINARY_STANDING_CREATOR_POLICY_TEXT,
            ORDINARY_STANDING_CREATOR_POLICY_TEXT_SHA256,
            ORDINARY_STANDING_CREATOR_POLICY_SHA256,
            _SOFT_SEMANTIC_CONFLICTS,
        )
    raise ContractValidationError("ordinary standing-policy version is unknown")


def _soft_reason_codes_for_policy(
    policy: OrdinaryStandingCreatorPolicyV1,
) -> frozenset[str]:
    return frozenset(
        {f"luna:{value}" for value in policy.soft_semantic_conflict_classes}
        | {f"reader:{value}" for value in policy.soft_reader_feedback_scopes}
    )


def ordinary_standing_creator_policy(
    policy_version: int = ORDINARY_STANDING_CREATOR_POLICY_VERSION,
) -> OrdinaryStandingCreatorPolicyV1:
    text, _text_digest, expected_policy_sha256, semantic_classes = _policy_spec(policy_version)
    policy = OrdinaryStandingCreatorPolicyV1(
        schema_version=OrdinaryStandingCreatorPolicyV1.SCHEMA_VERSION,
        authority_kind="standing_creator_policy",
        policy_id=ORDINARY_STANDING_CREATOR_POLICY_ID,
        policy_version=policy_version,
        policy_text_sha256=text_sha256(text),
        soft_semantic_conflict_classes=tuple(sorted(value.value for value in semantic_classes)),
        soft_reader_feedback_scopes=tuple(sorted(value.value for value in _SOFT_READER_SCOPES)),
    )
    if policy.policy_sha256 != expected_policy_sha256:
        raise ContractValidationError("ordinary standing-policy hash changed")
    return policy


def tolerated_ordinary_rejection_reasons(
    semantic: BoundSemanticValidationV1,
    reader: BoundReaderValidationV1,
) -> tuple[str, ...] | None:
    """Return exact soft reasons, or ``None`` when any hard signal is present."""

    if type(semantic) is not BoundSemanticValidationV1 or type(reader) is not (
        BoundReaderValidationV1
    ):
        raise ContractValidationError("ordinary standing policy requires typed validators")
    reasons: set[str] = set()
    if semantic.verdict.verdict is SemanticVerdict.REJECT:
        conflict = semantic.verdict.conflict
        if conflict is None or conflict.conflict_class not in _SOFT_SEMANTIC_CONFLICTS:
            return None
        reasons.add(f"luna:{conflict.conflict_class.value}")
    if reader.verdict.status is ReaderStatus.INCONCLUSIVE:
        return None
    if reader.verdict.status is ReaderStatus.REJECTED:
        scopes = {issue.feedback_scope for issue in reader.verdict.issues}
        if None in scopes or not scopes or not scopes.issubset(_SOFT_READER_SCOPES):
            return None
        reasons.update(f"reader:{scope.value}" for scope in scopes if scope is not None)
    if not reasons:
        return None
    return tuple(sorted(reasons))


def build_ordinary_policy_acceptance_audit(
    *,
    candidate_sha256: str,
    semantic: BoundSemanticValidationV1,
    reader: BoundReaderValidationV1,
    python_qualification: OrdinaryPythonQualificationV1,
) -> OrdinaryPolicyAcceptanceAuditV1 | None:
    reasons = tolerated_ordinary_rejection_reasons(semantic, reader)
    if reasons is None:
        return None
    return build_ordinary_policy_acceptance_audit_from_bindings(
        candidate_sha256=candidate_sha256,
        semantic_validation_sha256=semantic.binding_sha256,
        reader_validation_sha256=reader.binding_sha256,
        python_qualification_sha256=python_qualification.qualification_sha256,
        tolerated_reason_codes=reasons,
    )


def build_ordinary_policy_acceptance_audit_from_bindings(
    *,
    candidate_sha256: str,
    semantic_validation_sha256: str,
    reader_validation_sha256: str,
    python_qualification_sha256: str,
    tolerated_reason_codes: tuple[str, ...],
) -> OrdinaryPolicyAcceptanceAuditV1:
    """Rebuild a public audit from independent typed/hash-only evidence."""

    policy = ordinary_standing_creator_policy()
    return OrdinaryPolicyAcceptanceAuditV1(
        schema_version=OrdinaryPolicyAcceptanceAuditV1.SCHEMA_VERSION,
        authority_kind=policy.authority_kind,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_sha256=policy.policy_sha256,
        candidate_sha256=candidate_sha256,
        semantic_validation_sha256=semantic_validation_sha256,
        reader_validation_sha256=reader_validation_sha256,
        python_qualification_sha256=python_qualification_sha256,
        tolerated_reason_codes=tolerated_reason_codes,
    )


def ordinary_policy_acceptance_projection(
    audit: OrdinaryPolicyAcceptanceAuditV1,
) -> dict[str, object]:
    payload = to_primitive(audit)
    return {**payload, "audit_sha256": audit.audit_sha256}


__all__ = [
    "ORDINARY_STANDING_CREATOR_POLICY_TEXT",
    "ORDINARY_STANDING_CREATOR_POLICY_ID",
    "ORDINARY_STANDING_CREATOR_POLICY_SHA256",
    "ORDINARY_STANDING_CREATOR_POLICY_TEXT_SHA256",
    "ORDINARY_STANDING_CREATOR_POLICY_VERSION",
    "OrdinaryPolicyAcceptanceAuditV1",
    "OrdinaryStandingCreatorPolicyV1",
    "build_ordinary_policy_acceptance_audit",
    "build_ordinary_policy_acceptance_audit_from_bindings",
    "ordinary_policy_acceptance_projection",
    "ordinary_standing_creator_policy",
    "tolerated_ordinary_rejection_reasons",
]
