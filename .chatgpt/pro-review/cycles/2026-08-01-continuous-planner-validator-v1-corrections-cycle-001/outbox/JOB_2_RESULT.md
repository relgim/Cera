# Progression 2 Result

task_id: `continuous-review-and-scene-summary-authority-alignment-v1`

status: completed

## Outcome

D-177 semantics are restored. Ordinary Accept requires Good plus
`accept_allowed`. False Positive is invalid for Good, available only for an
eligible Concern/Critical assessment, promotes the exact unchanged candidate,
and writes a Validator-owned non-story diagnostic outside ACTIVE. The
diagnostic never enters the world index or Planner constraints.

Scene Summary is no longer stored under accepted ACTIVE truth. It is written
under `DERIVED/Scenes` as `cera.scene_summary_derived_view.v1`, explicitly
classified non-authoritative and reproducible from exact accepted-turn IDs,
pair hashes, accepted-event hashes, revision, and regeneration identity. It may
be regenerated without changing accepted events, character state, or ACTIVE
tree bytes. Scene Change context labels the summary and carries its source and
regeneration metadata.

## Verification

- Good + Accept, Good + invalid False Positive, eligible Concern/Critical +
  False Positive, and ineligible/error behavior: passed;
- ordinary Accept and False Positive ACTIVE bytes are identical: passed;
- Validator diagnostic remains outside story authority: passed;
- derived-summary creation, source binding, regeneration, and unchanged ACTIVE:
  passed;
- new-scene prompt exclusion and exact tail behavior: passed;
- provider calls: 0.
