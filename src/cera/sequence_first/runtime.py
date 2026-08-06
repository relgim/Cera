"""Provider-neutral orchestration for one sequence-first candidate."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from cera.errors import ContractValidationError
from cera.serialization import text_sha256

from .contracts import (
    BoundSequenceV1,
    ControllerFailureType,
    ReaderStatus,
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
    candidate: SequenceFirstCandidateV1 | None
    attempt_receipts: tuple[WriterAttemptReceiptV1, ...]
    terminal_validator_decision: ValidatorDecisionV1 | None = None
    terminal_reader_verdict: ReaderVerdictV1 | None = None

    @property
    def accepted(self) -> bool:
        return self.candidate is not None


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
    ) -> None:
        if maximum_writer_attempts not in {1, 2, 3}:
            raise ContractValidationError("Writer attempt bound must be between one and three")
        self._planner = planner
        self._writer = writer
        self._validator_factory = validator_factory
        self._reader = reader
        self._voice_cue_resolver = voice_cue_resolver
        self._maximum_writer_attempts = maximum_writer_attempts

    def generate(
        self,
        request: SequenceFirstTurnRequestV1,
    ) -> SequenceFirstRunResultV1:
        semantics = request.semantic_input
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
            hard_boundaries=semantics.hard_boundaries,
        )
        receipts: list[WriterAttemptReceiptV1] = []

        for attempt_number in range(1, self._maximum_writer_attempts + 1):
            writer_response = self._writer.write(brief, attempt_number)
            validator_input = SequenceFirstValidatorInputV1(
                intended_sequence=intended,
                exact_writer_prose=writer_response.story_text,
                prior_realized_sequence=semantics.prior_realized_sequence,
                accepted_present_character_ids=(
                    semantics.accepted_present_character_ids
                ),
                current_public_scene_state=semantics.current_public_scene_state,
                protected_source_claims=semantics.protected_source_claims,
                hard_boundaries=semantics.hard_boundaries,
            )
            validator = self._validator_factory.create_sequence_first_validator()
            try:
                decision = validator.validate(validator_input)
            finally:
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
                    )
                )
                if not writer_retry_eligible(
                    failure_type=ControllerFailureType.SEMANTIC_WRITER_CONFLICT,
                    decision=decision,
                ) or attempt_number == self._maximum_writer_attempts:
                    return SequenceFirstRunResultV1(
                        candidate=None,
                        attempt_receipts=tuple(receipts),
                        terminal_validator_decision=decision,
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
            if reader_verdict.status is ReaderStatus.INCONCLUSIVE:
                # INCONCLUSIVE is a review/setup ambiguity, not evidence that the
                # frozen Writer candidate is defective.  It terminates this run
                # identity without Writer/Reader retry, fallback, or hidden repair.
                return SequenceFirstRunResultV1(
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
                    )
                )
                if not writer_retry_eligible(
                    failure_type=ControllerFailureType.READER_QUALITY_REJECTION,
                ) or attempt_number == self._maximum_writer_attempts:
                    return SequenceFirstRunResultV1(
                        candidate=None,
                        attempt_receipts=tuple(receipts),
                        terminal_validator_decision=decision,
                        terminal_reader_verdict=reader_verdict,
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
