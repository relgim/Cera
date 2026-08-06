# CERA Sequence-First Tranche A A4 Result

Status: provider-free focused gate passed

Authority: Queue 0066 / Overnight Roadmap 0020

## Correction

- The final composed Validator instructions now enforce exactly two Ted
  realization restrictions: no invented speech/dialogue and no invented
  thoughts/feelings.
- Compatible visible Ted movement, posture, placement, expression, physical
  action, response, and incidental environmental handling are allowed unless
  they contradict accepted state or strong character logic or create an
  unauthorized consequential E.
- The residual broad protected-user action/state prohibition and obsolete
  `noncanonical` framing were removed from the live appended guidance.
- Active Sequence-First hard-boundary fixtures now state the two V2
  restrictions and no longer use the superseded broad response ban.
- The Validator profile, prompt, adapter, and route identities advanced to
  `sequence_first_validator_v3`,
  `cera.sequence_first.validator_prompt.v11`, and
  `cera.sequence_first.validator_adapter.v12` / route V12.

## Focused verification

Command:

```text
PYTHONPATH=src .venv/Scripts/python.exe -m unittest \
  tests.test_sequence_first_runtime_v1.SequenceFirstPipelineTests.test_writer_and_validator_guidance_is_general_not_phrase_specific \
  tests.test_sequence_first_runtime_v1.SequenceFirstPipelineTests.test_compatible_presentation_fixture_matrix_is_allowed_secondary_realization \
  tests.test_sequence_first_runtime_v1.SequenceFirstPipelineTests.test_v2_ted_boundary_fake_candidate_matrix
```

Result: 3 passed.

Provider calls: 0.

No live story, accepted branch, database, route, service, installed
SillyTavern, deployment, remote, merge, or push effect occurred.
