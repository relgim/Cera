# CERA qualification capture-root ownership V1 result

**Queue:** 0052

**Status:** provider-free correction in qualification

## Outcome

`QualificationRawProviderJsonCaptureRegistry` owns one explicit absolute
evidence root. Its stage, closed Validator/Reader roles, call counters, physical
session bindings, and inventory are local to that root. A nested historical
runner can no longer select the active campaign's capture location through an
unrebound module-global path.

The immutable `QualificationRawProviderJsonCapture` remains fail closed. The
registry does not overwrite, relocate, or reinterpret historical evidence.

## Trigger evidence

The final DeepSeek Pro Writer call returned a typed-accepted candidate. The
fresh Sol-medium Validator completed, but DTO decoding never occurred because
the historical capture registry targeted correction-008's existing
`attempt_001` artifact. Python correctly refused to overwrite it. This was an
evidence-custody path collision, not a Writer or semantic Validator rejection.

## Bounded recovery

The exact Planner output and exact typed Writer candidate remain immutable.
After provider-free tests and checkpointing, a fresh recovery identity may
replay both with zero Planner/Writer calls, run one fresh Sol-medium Validator,
and run one fresh Sol-medium Reader only if the Validator accepts. No new
DeepSeek call, candidate merge, fallback, route promotion, or production effect
is permitted.
