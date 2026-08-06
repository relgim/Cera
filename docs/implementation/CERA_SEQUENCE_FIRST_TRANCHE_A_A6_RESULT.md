# CERA Sequence-First Tranche A A6 Result

Status: provider-free focused gate passed

Authority: Queue 0066 / Overnight Roadmap 0020

## Correction

- Reader issue and retry-feedback contracts use one closed truthful scope:
  `exact_quote`, `omitted_planner_item`, or `whole_candidate_quality`.
- Exact quotes must be verbatim substrings of the frozen Writer candidate.
- Omitted Planner item keys must be explicitly returned by the Reader and must
  belong to the current intended sequence. The provider schema and its OpenAI
  projection carry the exact enum.
- Whole-candidate feedback contains neither a quote nor an omitted-item claim.
- The fabricated fallback to the first intended item was removed.
- Reader prompt/schema/adapter/route identities advanced to
  `cera.sequence_first.reader_prompt.v3` and
  `cera.sequence_first.reader_verdict.v2`, with
  `cera.sequence_first.reader_adapter.v4` / route V4.
- One immediate-prior correction, three attempts maximum, frozen plan/brief,
  fresh complete replacement, no merge, and no fallback remain unchanged.

## Focused verification

Quoted severe repetition, quote-less whole-candidate quality, actual omitted
item, unknown item, non-verbatim quote, DTO/schema, and projected-schema gates
passed: 6 tests.

Provider calls: 0.

No story, database, accepted branch, route, service, installed SillyTavern,
deployment, remote, merge, or push effect occurred.
