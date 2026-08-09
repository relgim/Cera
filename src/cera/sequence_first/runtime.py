"""Provider-neutral orchestration for one sequence-first candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cera.errors import ContractValidationError
from cera.continuous.operation_evidence import ProviderOperationEvidenceStoreV1
from cera.serialization import canonical_sha256, text_sha256
from cera.provider_dispatch_guard import (
    assert_provider_dispatch_allowed,
    is_external_provider_boundary,
)

from .contracts import (
    BoundSequenceV1,
    ControllerFailureType,
    ProviderReferenceScopeV1,
    PrimarySequenceStatus,
    ReaderStatus,
    RetryFeedbackScope,
    ReaderVerdictV1,
    SequenceCustodyEnvelopeV1,
    SequenceDraftV1,
    SequenceFirstCandidateV1,
    SequenceFirstReaderInputV1,
    SequenceFirstTurnRequestV1,
    SequenceFirstValidatorInputV1,
    SequenceFirstWriterBriefV1,
    SequenceRole,
    ValidatorDecisionV1,
    ValidatorVerdict,
    VoiceCueV1,
    WriterAttemptReceiptV1,
    WriterAttemptStatus,
    WriterRetryFeedbackV1,
    WriterResponseV1,
    writer_retry_eligible,
)


class PersistentPlannerPort(Protocol):
    """Branch-bound Planner whose provider session is retained outside a turn."""

    def plan(self, semantic_input) -> SequenceDraftV1: ...


class WriterPort(Protocol):
    def write(
        self,
        brief: SequenceFirstWriterBriefV1,
        attempt_number: int,
        retry_feedback: tuple[WriterRetryFeedbackV1, ...] = (),
    ) -> WriterResponseV1: ...


class CandidateValidatorSessionPort(Protocol):
    """One fresh, single-candidate Validator session."""

    def validate(
        self,
        request: SequenceFirstValidatorInputV1,
    ) -> ValidatorDecisionV1: ...

    def archive_and_prove_nonresumable(self) -> None: ...


class CandidateValidatorFactoryPort(Protocol):
    """No profile argument exists, so exhaustive fallback cannot be implicit."""

    def create_sequence_first_validator(self) -> CandidateValidatorSessionPort: ...


class ReaderPort(Protocol):
    def read(self, request: SequenceFirstReaderInputV1) -> ReaderVerdictV1: ...


class VoiceCueResolverPort(Protocol):
    """Fetch compact cues for Planner-selected owners without selecting them."""

    def resolve(
        self,
        *,
        responder_ids: tuple[str, ...],
        request: SequenceFirstTurnRequestV1,
    ) -> tuple[VoiceCueV1, ...]: ...


class SequenceFirstTransactionPort(Protocol):
    def commit(
        self,
        candidate: SequenceFirstCandidateV1,
        *,
        expected_parent_accepted_turn_id: str | None,
        creator_accepted: bool,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class SequenceFirstRunResultV1:
    intended_sequence: BoundSequenceV1
    candidate: SequenceFirstCandidateV1 | None
    attempt_receipts: tuple[WriterAttemptReceiptV1, ...]
    terminal_validator_decision: ValidatorDecisionV1 | None = None
    terminal_reader_verdict: ReaderVerdictV1 | None = None

    @property
    def accepted(self) -> bool:
        return self.candidate is not None

    @property
    def primary_sequence_status(self) -> PrimarySequenceStatus:
        return (
            PrimarySequenceStatus.REALIZED
            if self.candidate is not None
            else PrimarySequenceStatus.PLANNED
        )


class SequenceFirstCoordinator:
    """Generate a provisional result; never infer meaning or commit implicitly."""

    def __init__(
        self,
        *,
        planner: PersistentPlannerPort,
        writer: WriterPort,
        validator_factory: CandidateValidatorFactoryPort,
        reader: ReaderPort,
        voice_cue_resolver: VoiceCueResolverPort,
        maximum_writer_attempts: int = 3,
        operation_evidence: ProviderOperationEvidenceStoreV1 | None = None,
    ) -> None:
        if maximum_writer_attempts not in {1, 2, 3}:
            raise ContractValidationError("Writer attempt bound must be between one and three")
        self._planner = planner
        self._writer = writer
        self._validator_factory = validator_factory
        self._reader = reader
        self._voice_cue_resolver = voice_cue_resolver
        self._maximum_writer_attempts = maximum_writer_attempts
        self._operation_evidence = operation_evidence

    def generate(
        self,
        request: SequenceFirstTurnRequestV1,
    ) -> SequenceFirstRunResultV1:
        semantics = request.semantic_input
        assert_provider_dispatch_allowed(
            "sequence_first.coordinator.generate",
            external_provider_boundary=any(
                is_external_provider_boundary(owner)
                for owner in (
                    self._planner,
                    self._writer,
                    self._validator_factory,
                    self._reader,
                )
            ),
        )
        if self._operation_evidence is not None:
            self._operation_evidence.begin_turn(request.custody.turn_id)
        intended = self._planner.plan(semantics)
        semantics.validate_intended(intended)
        voice_cues = self._voice_cue_resolver.resolve(
            responder_ids=intended.responding_character_ids,
            request=request,
        )
        bound_intended = BoundSequenceV1(
            role=SequenceRole.INTENDED,
            semantic=intended,
            custody=request.custody,
        )
        brief = SequenceFirstWriterBriefV1(
            intended_sequence=intended,
            current_public_scene_state=semantics.current_public_scene_state,
            protected_source_claims=semantics.protected_source_claims,
            voice_cues=voice_cues,
            backgrounded_character_ids=(
                semantics.derived_backgrounded_character_ids(intended)
            ),
            hard_boundaries=semantics.hard_boundaries,
        )
        receipts: list[WriterAttemptReceiptV1] = []
        retry_feedback: tuple[WriterRetryFeedbackV1, ...] = ()

        for attempt_number in range(1, self._maximum_writer_attempts + 1):
            if self._operation_evidence is not None:
                self._operation_evidence.set_attempt(attempt_number)
            writer_response = self._writer.write(
                brief, attempt_number, retry_feedback=retry_feedback
            )
            validator_input = SequenceFirstValidatorInputV1(
                intended_sequence=intended,
                exact_writer_prose=writer_response.story_text,
                exact_current_source=semantics.exact_current_source,
                prior_realized_sequence=semantics.prior_realized_sequence,
                accepted_present_character_ids=(
                    semantics.accepted_present_character_ids
                ),
                current_public_scene_state=semantics.current_public_scene_state,
                protected_source_claims=semantics.protected_source_claims,
                hard_boundaries=semantics.hard_boundaries,
                reference_scope=ProviderReferenceScopeV1.from_turn(
                    semantics,
                    intended_sequence=intended,
                ),
                accepted_character_deltas=semantics.character_deltas,
                accepted_evidence_records=semantics.evidence_records,
            )
            validator = self._validator_factory.create_sequence_first_validator()
            try:
                decision = validator.validate(validator_input)
            except BaseException as primary:
                try:
                    validator.archive_and_prove_nonresumable()
                except BaseException as cleanup:
                    primary.add_note(
                        "Validator terminalization also failed: "
                        f"{type(cleanup).__name__}: {cleanup}"
                    )
                    raise primary from cleanup
                raise
            else:
                validator.archive_and_prove_nonresumable()

            if decision.verdict is ValidatorVerdict.REJECT:
                assert decision.conflict is not None
                conflict = decision.conflict
                if (
                    conflict.exact_quote is not None
                    and conflict.exact_quote not in writer_response.story_text
                ):
                    raise ContractValidationError(
                        "Validator rejection quote is absent from frozen Writer prose"
                    )
                if conflict.omitted_planner_item_key is not None and (
                    conflict.omitted_planner_item_key
                    not in {item.item_key for item in intended.items}
                ):
                    raise ContractValidationError(
                        "Validator rejection cites an unknown omitted Planner item"
                    )
                receipts.append(
                    WriterAttemptReceiptV1(
                        attempt_number=attempt_number,
                        status=WriterAttemptStatus.VALIDATOR_REJECTED,
                        writer_prose_sha256=text_sha256(writer_response.story_text),
                        concise_reason=conflict.concise_explanation,
                        retry_feedback_sha256=(
                            canonical_sha256(retry_feedback) if retry_feedback else None
                        ),
                    )
                )
                if not writer_retry_eligible(
                    failure_type=ControllerFailureType.SEMANTIC_WRITER_CONFLICT,
                    decision=decision,
                ) or attempt_number == self._maximum_writer_attempts:
                    return SequenceFirstRunResultV1(
                        intended_sequence=bound_intended,
                        candidate=None,
                        attempt_receipts=tuple(receipts),
                        terminal_validator_decision=decision,
                    )
                retry_feedback = (
                    WriterRetryFeedbackV1(
                        owner="validator",
                        feedback_type="semantic_writer_conflict",
                        issue_code=conflict.conflict_class.value,
                        concise_reason=conflict.concise_explanation,
                        required_correction=(
                            "Produce a fresh complete response that preserves the frozen "
                            "primary sequence and corrects this one material issue."
                        ),
                        exact_quote=conflict.exact_quote,
                        omitted_planner_item_key=conflict.omitted_planner_item_key,
                    ),
                )
                continue

            realized = decision.realized_sequence
            if realized is None:
                raise ContractValidationError("accepted Validator branch omitted realization")
            semantics.validate_realized(realized, intended=intended)
            reader_input = SequenceFirstReaderInputV1(
                exact_writer_prose=writer_response.story_text,
                intended_sequence=intended,
                realized_sequence=realized,
            )
            reader_verdict = self._reader.read(reader_input)
            intended_item_keys = {item.item_key for item in intended.items}
            for issue in reader_verdict.issues:
                if (
                    issue.feedback_scope is RetryFeedbackScope.EXACT_QUOTE
                    and issue.exact_quote not in writer_response.story_text
                ):
                    raise ContractValidationError(
                        "Reader rejection quote is absent from frozen Writer prose"
                    )
                if (
                    issue.feedback_scope is RetryFeedbackScope.OMITTED_PLANNER_ITEM
                    and issue.omitted_planner_item_key not in intended_item_keys
                ):
                    raise ContractValidationError(
                        "Reader rejection cites an unknown omitted Planner item"
                    )
            if reader_verdict.status is ReaderStatus.INCONCLUSIVE:
                # INCONCLUSIVE is a review/setup ambiguity, not evidence that the
                # frozen Writer candidate is defective.  It terminates this run
                # identity without Writer/Reader retry, fallback, or hidden repair.
                return SequenceFirstRunResultV1(
                    intended_sequence=bound_intended,
                    candidate=None,
                    attempt_receipts=tuple(receipts),
                    terminal_validator_decision=decision,
                    terminal_reader_verdict=reader_verdict,
                )
            if reader_verdict.status is ReaderStatus.REJECTED:
                # REJECTED is attributable to visible candidate quality and may
                # therefore open one fresh Writer attempt inside the fixed bound.
                receipts.append(
                    WriterAttemptReceiptV1(
                        attempt_number=attempt_number,
                        status=WriterAttemptStatus.READER_REJECTED,
                        writer_prose_sha256=text_sha256(writer_response.story_text),
                        concise_reason="; ".join(
                            issue.concise_explanation for issue in reader_verdict.issues
                        ),
                        retry_feedback_sha256=(
                            canonical_sha256(retry_feedback) if retry_feedback else None
                        ),
                    )
                )
                if not writer_retry_eligible(
                    failure_type=ControllerFailureType.READER_QUALITY_REJECTION,
                ) or attempt_number == self._maximum_writer_attempts:
                    return SequenceFirstRunResultV1(
                        intended_sequence=bound_intended,
                        candidate=None,
                        attempt_receipts=tuple(receipts),
                        terminal_validator_decision=decision,
                        terminal_reader_verdict=reader_verdict,
                    )
                issue = reader_verdict.issues[0]
                retry_feedback = (
                    WriterRetryFeedbackV1(
                        owner="reader",
                        feedback_type="reader_quality_rejection",
                        issue_code=issue.issue_code,
                        concise_reason=issue.concise_explanation,
                        required_correction=(
                            "Produce a fresh complete response that preserves the frozen "
                            "primary sequence and corrects this one severe reader-facing issue."
                        ),
                        exact_quote=issue.exact_quote,
                        omitted_planner_item_key=issue.omitted_planner_item_key,
                        feedback_scope=issue.feedback_scope,
                    ),
                )
                continue

            receipts.append(
                WriterAttemptReceiptV1(
                    attempt_number=attempt_number,
                    status=WriterAttemptStatus.ACCEPTED,
                    writer_prose_sha256=text_sha256(writer_response.story_text),
                )
            )
            candidate = SequenceFirstCandidateV1(
                intended_sequence=bound_intended,
                accepted_present_character_ids=(
                    semantics.accepted_present_character_ids
                ),
                writer_response=writer_response,
                realized_sequence=BoundSequenceV1(
                    role=SequenceRole.REALIZED,
                    semantic=realized,
                    custody=request.custody,
                ),
                reader_verdict=reader_verdict,
                accepted_attempt_number=attempt_number,
            )
            return SequenceFirstRunResultV1(
                intended_sequence=bound_intended,
                candidate=candidate,
                attempt_receipts=tuple(receipts),
                terminal_validator_decision=decision,
                terminal_reader_verdict=reader_verdict,
            )

        raise AssertionError("bounded Writer loop terminated unexpectedly")

    @staticmethod
    def commit(
        result: SequenceFirstRunResultV1,
        *,
        transaction: SequenceFirstTransactionPort,
        expected_parent_accepted_turn_id: str | None,
        creator_accepted: bool,
    ) -> str:
        if result.candidate is None:
            raise ContractValidationError("rejected generation cannot be committed")
        if not creator_accepted:
            raise ContractValidationError("creator acceptance is required before commit")
        if (
            result.candidate.custody.parent_accepted_turn_id
            != expected_parent_accepted_turn_id
        ):
            raise ContractValidationError("commit parent differs from candidate custody")
        return transaction.commit(
            result.candidate,
            expected_parent_accepted_turn_id=expected_parent_accepted_turn_id,
            creator_accepted=True,
        )
