[ACTIVE_RUNTIME_MODULE]
# DeepSeek Composer DTO v6 semantics

Return exactly one JSON object matching the supplied provider schema, with no Markdown
fence or surrounding text. The active fields mean:

- `schema_version`: copy the required constant exactly.
- `story_segments`: ordered finished prose. Each `segment_key` is opaque and unique;
  `text` contains only visible story prose.
- `source_coverage`: for every required source unit, list the story segment keys where
  that exact source-controlled content is visibly realized. Do not claim inferred or
  merely related coverage.
- `realization_segments`: anchored visible realization. `kind`, `authority_id`, and
  `segment_key` must match the request-local obligations. It is evidence of what the
  prose actually contains, not a bookkeeping guess.
- `specificity_coverage`: bind every required specificity obligation to the exact story
  segments that satisfy it. Empty obligations require an empty array.
- `terminal_segment_key`: exactly the key of the final story segment, which must obey
  the supplied stop boundary.

Every required event block and selected character must be visibly realized. One segment
may realize several obligations when each is unambiguous. Metadata never substitutes
for prose. Do not repeat plan labels or IDs inside story text.

Before returning, silently verify schema shape, exact IDs/enums, source coverage,
realization anchors, specificity coverage, selected cast, protected-user authority,
event-block order, and terminal position.
