# Genesis, Memory, and Retrieval

**Status:** controlling data and evidence architecture

## 1. Design principle

Runtime Codex must not depend on desktop Codex filesystem access or on Python guessing a perfect initial dossier. Python supplies a compact seed dossier and a bounded evidence service. Codex can search, inspect compact hits, and expand authoritative evidence before deciding.

The “master index” is therefore a generated evidence catalog, not a prose file Codex must trust.

## 2. Authority layers

| Layer | Purpose | Mutability |
|---|---|---|
| Genesis records | Creator-established identity, household, initial relationships, world facts, preferences | Immutable per Genesis version |
| Source ledger | Exact creator turn units and hashes | Append-only, branch/generation bound |
| Accepted story artifacts | Validated presentation-neutral prose | Immutable/content-addressed |
| Objective event/material/knowledge records | What was validated as happening, changing, or becoming known | Append-only plus typed supersession |
| Character memory | Owner-specific recollection tied to evidence | Branch-local; may develop/supersede |
| Relationship/thread/development overlays | Dynamic derived state | Branch-local, evidence-bound, supersedable |
| Summaries/indexes/views | Fast retrieval | Regenerable, never authority |
| Model proposals | Candidate interpretations or changes | Transient until validated |

Genesis may be changed only by an explicit creator migration producing a new version and provenance record.

## 3. Genesis organization

Authoritative JSON is modular and governed by one manifest:

```text
data/genesis/
  manifest.json
  characters/<character_id>.json
  relationships/<edge_id>.json
  world/<record_id>.json
  household/<record_id>.json
  creator_preferences/<record_id>.json
```

“Single authority” means this manifest-governed JSON record set is authoritative; it does not require one unmaintainable file. A build step validates IDs, references, privacy, unknown values, and hashes, then imports a versioned snapshot into the runtime store. Generated Markdown cards are read-only views.

The installed Hanezawa lineage contains immutable V1.1 and child revision V1.2.
V1.1 remains at `genesis/packages/hanezawa_core_v1_1/`; V1.2 is at
`genesis/packages/hanezawa_core_v1_2/`. The V1.2 compiler preserves the old
package byte-for-byte, assigns a distinct revision namespace, and explicitly
supersedes the complete active parent snapshot so cumulative revision reads do
not expose duplicate V1.1/V1.2 facts. Generated indexes remain disposable
views under the matching `genesis/generated/` revision directory.

V1.2 adds 79 events, 84 owner memories, seven owner-scoped embodied-identity
profiles, seven rhetorical signatures, 77 response-mode records, and 154
non-executable style examples. Profiles advertise bounded exact sections such
as `public_disposition`, `profile_sections`, `identity_invariants`, and
`activation_cues`. Response-mode records advertise `response_mode`,
`metaphor_domains`, `fidelity_rule`, and `style_examples`. Search results
remain references; runtime Codex must fetch the exact advertised sections
before citing them.

Hana's records separately store objective family/affair facts, her conscious faithful belief, her owner-private unease, and Sakura's and Enne's limited evidence knowledge. A later direct creator decision establishes the husband's biological paternity of Mia and Yuuni and excludes the conflicting V1.1 unknown from active truth. See `CREATOR_FACTS_AND_PREFERENCES.md` and D-035 through D-039.

V1.2 additionally stores Hana's aging-related self-blame as owner-private
belief pressure and stores the objective responsibility correction separately.
Pre-disclosure daughter support cannot reveal affair knowledge; post-disclosure
speech references require validated branch-local disclosure. Illustrative
speech never becomes an event, memory, Ted action, or scheduled future.

## 4. Evidence record

Every authoritative or derived evidence item carries:

```json
{
  "evidence_id": "evidence:...",
  "record_type": "genesis_fact|source_fact|event_fact|memory|relationship|thread|material|development",
  "claim": "bounded non-graphic proposition",
  "authority": "creator|accepted_source|validated_event|validated_derived",
  "branch_id": "branch:...|global",
  "owner_id": "character:...|shared|system",
  "visibility": "public|shared|owner_private|system_private",
  "knowledge_route": "direct|reported|inferred|creator_seed|not_applicable",
  "certainty": "established|believed|suspected|feared|unknown",
  "source_refs": ["source:...", "artifact:...", "event:..."],
  "valid_from": "story-time or generation",
  "valid_to": null,
  "supersedes": [],
  "tags": ["character:hana_hanezawa", "topic:husband", "theme:trust"]
}
```

Facts, character beliefs, fears, inferences, and unknowns cannot share the same certainty/type.

## 5. Evidence catalog

The generated catalog contains compact entries:

```json
{
  "evidence_id": "memory:hana:...",
  "title": "Hana private memory of event",
  "abstract": "non-graphic, owner-safe summary",
  "entity_ids": ["character:hana_hanezawa"],
  "tags": ["trauma", "trust", "boundary", "location:..."],
  "owner_id": "character:hana_hanezawa",
  "branch_id": "branch:...",
  "time_span": {},
  "authority_refs": ["event:..."],
  "expandable_sections": ["event_chain", "subjective_interpretation", "development_history"]
}
```

The catalog accelerates retrieval but does not replace expansion of evidence used in a consequential decision.

## 6. Bounded retrieval tools

Runtime Codex receives these conceptual operations:

```text
search_evidence(query, branch_id, owner_scope, entity_ids, tags, time_scope, limit)
expand_evidence(evidence_ids, sections, owner_scope)
get_entity_card(character_id, sections, branch_id)
get_relationship_edge(from_id, to_id, branch_id)
get_event_chain(event_id, branch_id)
get_memory_context(memory_id, owner_scope, branch_id)
get_thread(thread_id, branch_id)
get_material_state(entity_ids, branch_id)
```

The implemented Phase 4 interface exposes the provider-neutral equivalents `search_evidence`, `fetch_evidence`, `get_character_sections`, and `get_continuity`. Every operation carries one immutable request-bound snapshot token covering world, branch, generation, branch head, Genesis revision, access perspective, visibility-policy version, and fixture/real mode. Search returns compact references; exact text and sections require a separate authorized fetch. Every expansion rechecks access.

Phase 12 projects those same operations to runtime Codex through a per-request authenticated loopback MCP server. Structural Contract v4 exposes seven concrete tool names: `cera_get_turn_snapshot`, `cera_resolve_entities`, `cera_search_evidence`, `cera_search_query_plan`, `cera_fetch_evidence`, `cera_get_character_sections`, and `cera_get_continuity`. The query-plan operation accepts one bounded primary term set plus explicit paraphrase variants, filters, result cap, and ambiguity policy; Python executes it as one metered search and returns references only. The active MCP v4 provider projection expands one returned reference per `cera_fetch_evidence` call with singular `evidence_id` and an advertised `sections` array. Python retains its internal typed batch-fetch API. The server holds no independent authority: it delegates to one fresh `ReasonerEvidenceTools`, serializes typed results, and stops after the provider request. Codex cannot supply branch, perspective, database, or filesystem scope outside the bound snapshot. An ordered bridge trace and independent Codex SDK observations must agree before a safe bridge receipt can be emitted. Invalid bridge arguments, including framework rejection before dispatcher entry, retain only a safe tool/field path and issue class, never the supplied value.

The FTS5 search index is derived and rebuildable. It includes authoritative nested payload/section text so indirect cues can find atomized records, but returns candidate references only. Exact content still requires a separately authorized fetch. The index is hash-validated against authoritative records before search, cannot grant access or establish truth, and fails explicitly on mismatch instead of widening to an unfiltered scan.

Python enforces:

- exact branch/ancestor visibility;
- character knowledge and privacy scope;
- permitted record/section types;
- stable result limits and total packet bytes;
- provenance and supersession filtering;
- request/response trace IDs;
- no raw SQL, arbitrary file path, or unrestricted filesystem operation.

Codex owns semantic query intent and may supply a bounded
`EvidenceQueryPlan`: one primary term set, up to the declared number of
explicit alternate term sets, entity/tag/type filters, a result cap, and an
ambiguity policy. Python executes the complete plan as one metered search,
deduplicates records, and never treats a paraphrase variant as a new provider
call. Query expansion is explicit; the service does not silently change the
semantic target.

Real Genesis records project their semantic subtype into
`EvidenceRecordType`, expose normalized external aliases such as `H-M08` as
search tags, and compile typed links such as memory-to-source-event references.
Search titles/abstracts are privacy-safe descriptors and are generated only
after the record itself passes branch, visibility, owner, knowledge, content,
supersession, and world-mode filtering. Compact search results remain
references, never exact evidence.

If evidence is insufficient within the route budget, Codex returns a typed uncertainty or `needs_evidence`; it does not invent. Packet budgets are configuration and evaluation parameters, not reasons to leak privacy or omit required authority.

## 7. Seed dossier

Python initially supplies:

- current source and authority classification;
- branch/generation and scene anchor;
- eligible cast and floor context;
- active-character compact realization cards;
- most relevant current events, memories, relationships, threads, and material state;
- actual adult/consent/capacity evidence when relevant;
- protected-user boundary;
- evidence catalog handles for expansion;
- explicit unknowns and prohibited inferences.

The dossier is a starting set, not a claim of completeness.

Structural Contract v2 makes assembly deterministic. The intent layer produces
typed `EvidenceObligation` records for required record, subject, record type,
or bounded query plan. `SeedDossierAssembler` reauthorizes every supplied
`ExactEvidence` byte-for-byte under the current immutable snapshot, resolves
each obligation through privacy-filtered search plus exact section expansion,
and emits `SeedDossierReceipt`. Ambiguous and unavailable obligations remain
typed unresolved evidence; Python never chooses one candidate arbitrarily.
Tampered, stale, foreign-branch, superseded, or unauthorized seed records are
rejected before Codex.

`exact_seed_evidence` contains complete, selectively expanded `ExactEvidence` records, including the authorized sections and subject/knowledge metadata. A metadata-only handle is not enough to support a hard decision. Seed evidence and follow-up tool evidence use the same snapshot, privacy, knowledge-owner, record-version, and citation checks.

Retrieval authorization is necessary but not sufficient for character use. Before a record may drive a character move, floor/participation decision, or beat, Python rechecks that the acting character is the private owner or an allowed `knowledge_owner_id`. A system-private record with no explicit knowledge owners may guide a subject character's behavior or system validation, but it cannot be transferred to an unrelated character or treated as character-known objective information without a typed knowledge route.

## 8. Character-owned trauma memory

A trauma-related memory uses:

1. compact index entry;
2. authoritative objective event references;
3. owner-private perceived sequence;
4. subjective meanings such as fear, betrayal, confusion, or self-blame;
5. explicit knowledge boundaries;
6. retrieval tags and triggers;
7. supersession/development history;
8. branch isolation.

Objective classification and subjective interpretation remain separate. Self-blame is a character belief, not objective responsibility. No memory automatically creates PTSD, a permanent trait, or a Genesis rewrite. Persistent development requires later validated evidence or explicit creator promotion.

## 9. Derived consolidation

Ordinary publication atomically creates one system-private direct accepted-turn event containing validated current-segment/source/artifact bindings. This event is the objective evidence root for later consolidation; the consolidator cannot invent or replace it. After publication, a `DerivedConsolidatorPort` may propose:

- memory salience and owner;
- relationship evidence;
- open/resolved threads;
- character-development observations;
- no change when the evidence does not justify durable state.

Python accepts only proposals with exact-expanded current event evidence, valid branch and owner scope, character knowledge, privacy, typed record sections, supersession continuity, and non-contradiction checks. The protected user cannot receive inferred private state. Directly established event/material/knowledge facts belong in the publication transaction; consolidation cannot edit accepted prose, create an objective event, rewrite Genesis, or diagnose a character. The direct event is system-private so a later owner-specific memory must still prove that owner may use the event evidence.

The durable consolidation transaction binds the current story artifact, generation, Genesis revision, evidence snapshot, and a separate branch-local `authority_revision`. A successful derived commit inserts its records atomically, records the receipt, and advances only `authority_revision`; it does not advance the accepted-story head or generation. A competing prepared transaction therefore fails stale even when the story head has not moved.

Fork visibility uses both artifact lineage and the fork creation cutoff. A child sees ancestor derived records that existed when it forked, but not later parent consolidation at the same artifact; parents and siblings never see child-local records.

Public, owner-private, and system-private summaries/indexes are deterministic `DerivedView` projections rebuilt from the current canonical record set. Their source-set hashes make staleness detectable. They remain replaceable and non-authoritative; retrieval decisions cite the underlying records rather than summary prose.

Phase 9 implements the port, strict validator, journaled SQLite commit/restart path, supersession, branch isolation, derived views, and scripted fake without provider or network calls. Real runtime Codex consolidation is not yet connected.
