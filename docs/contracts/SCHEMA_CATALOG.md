# Schema Catalog

**Status:** controlling schemas-as-documentation
**Implementation note:** Python dataclass schemas are implemented through provider-free Phase 9 plus the separately authorized Hanezawa Genesis V1.1 and V1.2 compilations. Future JSON Schema or Pydantic projections must preserve these semantics and version every contract.

The `cera.genesis_record.v1` record-type registry now includes identity, family, world, character state/profile, voice profile, formative event, memory seed, household rule, visual canon, supersession ledger, directional relationship, adult eligibility, story-start placement, unresolved question, and creator preference. Epistemic layer, truth status, owner/knowledge scope, visibility, content class, and route flags remain orthogonal typed fields rather than being inferred from record type alone.

## 1. Common conventions

Every envelope contains:

```json
{
  "schema_version": "cera.<name>.v1",
  "record_id": "<typed stable ID>",
  "created_at": "<UTC ISO-8601>",
  "trace_id": "trace:...",
  "branch_id": "branch:...",
  "generation_id": "generation:...",
  "content_sha256": "<lowercase hex>"
}
```

Rules:

- IDs are opaque and never derived by string slicing.
- Hashes cover canonical serialization defined by the future schema implementation.
- Unknown, not applicable, false, and absent are different.
- Enumerations reject unknown values unless the version explicitly permits extensions.
- Story records include source/evidence references.
- Operational records are not story evidence unless a contract explicitly promotes them.

## 2. `TurnRequest`

```json
{
  "schema_version": "cera.turn_request.v1",
  "world_id": "world:...",
  "request_id": "request:...",
  "session_id": "session:...",
  "branch_id": "branch:...",
  "parent_artifact_id": "artifact:...",
  "generation_id": "generation:...",
  "snapshot_token": "snapshot:...",
  "genesis_revision_id": "genesis_revision:...",
  "protected_user_id": "character:ted",
  "raw_source_ref": "protected_source:...",
  "source_sha256": "...",
  "source_units": [
    {
      "source_unit_id": "source_unit:S01",
      "classification": "message|instruction|event|constraint|context",
      "text_ref": "protected_source_segment:...",
      "sha256": "..."
    }
  ],
  "requested_route_hints": [],
  "idempotency_key": "..."
}
```

Source text may be separately access-controlled. Downstream packets receive only permitted representations.

## 3. `EvidenceHit`

Phase 4 supersedes the runtime use of this early compact contract with a two-step interface:

- `cera.evidence_snapshot.v2` binds request, world, branch, generation, branch head, branch `authority_revision`, Genesis revision, perspective/access scope, visibility policy, and real/synthetic mode;
- `EvidenceReference` returns a safe abstract plus complete evidence metadata;
- `ExactEvidence` returns only explicitly authorized requested sections;
- `cera.evidence_lookup_receipt.v1` records operation, counts, bytes, truncation, cumulative turn budget, and zero authority writes.

`EvidenceHit` remains a Phase 3 compatibility view and is not the future runtime tool response.

```json
{
  "schema_version": "cera.evidence_hit.v1",
  "evidence_id": "evidence:...",
  "record_id": "record:...",
  "record_version": 1,
  "record_type": "genesis_fact|source_fact|event_fact|memory|relationship|thread|material|development",
  "truth_status": "objective|character_owned|derived|unknown",
  "claim": "bounded proposition",
  "authority": "creator|accepted_source|validated_event|validated_derived",
  "world_id": "world:...",
  "branch_origin_id": "branch:...|null",
  "generation": 0,
  "snapshot_token": "snapshot:...",
  "perspective_id": "character:...|null",
  "owner_id": "character:...|shared|system",
  "knowledge_owner_id": "character:...|null",
  "visibility": "public|shared|owner_private|system_private",
  "knowledge_route": "direct|reported|inferred|creator_seed|not_applicable",
  "certainty": "established|believed|suspected|feared|unknown",
  "content_class": "ordinary|protected_non_graphic|system",
  "genesis_revision_id": "genesis_revision:...",
  "valid_from": "story-time/generation|null",
  "valid_to": null,
  "branch_scope": ["branch:..."],
  "source_refs": ["..."],
  "supersession_status": "current|superseded|disputed",
  "tags": [],
  "expandable_sections": [],
  "retrieval_reason": "why this record matched the bounded request"
}
```

## 4. `SceneDecision` and `SequencePlan`

Phase 5 surrounds `SceneDecision` with:

- `cera.scene_reasoner_request.v2`, binding the prepared turn, safe source view, seed dossier, snapshot, hard boundaries, and typed scene-depth mode;
- `cera.reasoner_outcome.v3`, the Python-compiled authoritative result,
  distinguishing `decision_ready`, `insufficient_evidence`, and `blocked`
  without a prose field and optionally carrying beat-local
  `AdultCraftNeedV2` only for a consent-valid adult decision;
- versioned hard citations and participation/floor/intervention selections;
- `cera.scene_reasoner_receipt.v2`, binding request, source, snapshot, adapter
  evidence, outcome, safe provider/bridge/evidence receipts, tool usage, and
  external-call count.

The fake result is advisory contract evidence. It is not an accepted artifact or provider qualification.

`ReasonerSeedDossier.exact_seed_evidence` is a tuple of complete `ExactEvidence` records, not compact references. Each item is bound to the request snapshot and includes only explicitly selected sections. Compact search references may guide follow-up lookup but cannot support hard decisions until expanded.

```json
{
  "schema_version": "cera.scene_decision.v1",
  "decision_id": "decision:...",
  "route": "ordinary|consent_valid_adult|aftermath",
  "scene_intent": "non-graphic immediate purpose",
  "responding_npc_ids": ["character:..."],
  "floor_owner_id": "character:...|null",
  "character_moves": [
    {
      "character_id": "character:...",
      "perception": "what the character can perceive",
      "selected_intent": "what the character tries to accomplish",
      "action_direction": "non-graphic consequential direction",
      "evidence_ids": ["evidence:..."],
      "knowledge_constraints": []
    }
  ],
  "current_segment": {
    "segment_id": "segment:...",
    "ordered_beats": [
      {
        "beat_id": "beat:B01",
        "actor_id": "character:...",
        "state": "requested|attempted|ongoing|partial|interrupted|completed|stopped",
        "neutral_event": "bounded causal beat",
        "evidence_ids": ["evidence:..."]
      }
    ],
    "stop_before": "Ted's next unsupplied meaningful choice"
  },
  "future_segments": [
    {
      "segment_id": "segment:...",
      "status": "conditional_plan_only",
      "activation_conditions": [],
      "invalidation_conditions": [],
      "possible_consequences": [],
      "open_user_choice": "..."
    }
  ],
  "writer_must_preserve": [],
  "uncertainties": [],
  "prohibited_inferences": [],
  "advisory_state_candidates": []
}
```

Future segments are not event or memory authority.

## 5. `AdultMechanicsProposal` and adult-route bindings

Phase 7 replaces the ambiguous planning role with a non-authoritative mechanics contract surrounded by Python authority and synchronization records:

- `cera.adult_authority_decision.v1` independently types participant adulthood, eligibility, consent, capacity, pressure, freedom, scenario kind, provider capability, and explicitly source-authored semantic assertions;
- `cera.adult_causal_ledger.v1` is the no-prose safe Reasoner representation;
- `cera.adult_route_context.v1` contains current-turn abstract craft families/references only and imports no actual examples;
- `cera.adult_dual_representation_receipt.v1` binds safe and exact representations by IDs, hashes, order, participants, progression, snapshot, and authority without storing exact text;
- `cera.adult_mechanics_request.v1`, `cera.adult_mechanics_proposal.v1`, and `cera.adult_mechanics_receipt.v1` bind the validated Reasoner decision to mechanics-only unit treatments and a scripted fake receipt;
- the Phase 6 `AdultComposerBinding` carries authority, synchronization, mechanics proposal/receipt, context, safe/exact view, and craft-reference bindings into the ordinary Composer acceptance path.

```json
{
  "schema_version": "cera.adult_mechanics_proposal.v1",
  "plan_id": "adult_plan:...",
  "decision_id": "decision:...",
  "authority_sha256": "...",
  "ledger_sha256": "...",
  "context_sha256": "...",
  "decision_sha256": "...",
  "sequence_plan_sha256": "...",
  "unit_treatments": [],
  "no_psychology_override": true,
  "contains_story_prose": false,
  "advisory_metadata": []
}
```

Required invariant: every participant is a confirmed adult and every protected unit is within current informed, freely given consent/capacity scope. The proposal cannot alter Scene Reasoner psychology or decisions and remains advisory after Python validation; it never commits itself.

## 6. `AcceptedStoryArtifact`

Phase 6 creates this artifact only in memory after structural validation. Its `transaction_id` is a reserved identity for future commit correlation; it does not prove persistence. Durable truth requires a later successful `CommitReceipt`.

```json
{
  "schema_version": "cera.accepted_story_artifact.v1",
  "artifact_id": "artifact:...",
  "branch_id": "branch:...",
  "generation_id": "generation:...",
  "parent_artifact_id": "artifact:...|null",
  "source_id": "source:...",
  "decision_id": "decision:...",
  "accepted_prose": "presentation-neutral story prose",
  "prose_sha256": "...",
  "responding_npc_ids": [],
  "realized_beat_ids": [],
  "validation_receipt_id": "validation:...",
  "transaction_id": "transaction:...",
  "status": "accepted"
}
```

No HTML, UI colors, diagnostics, or rejected text is allowed in `accepted_prose`.

## 6A. Phase 6 Composer records

`cera.scene_composer_request.v4` supersedes v3. It retains v3 publication topology and Adult ON/EX specificity binding, and additionally carries the same typed `SceneDepthMode` that Python accepted for the Reasoner. The provider DTO derives one matching `scene_development_contract`, so the Composer cannot treat Long or Epic as a hidden Reasoner-only preference. It is distinct from the Reasoner request and binds:

- the prepared request/snapshot references without raw source text;
- validated reasoner outcome and receipt;
- exact source/unit hashes and classification/order;
- safe reasoner-source-view hash;
- restricted protected-source-envelope hash when applicable;
- decision and SequencePlan hashes;
- selected cast, scene scope, response profile, continuity references, coverage mode, and hard boundaries.
- append or regeneration mode, replacement target, and the validated artifact parent used for immutable sibling regeneration.

`cera.composer_candidate.v1` contains raw presentation-neutral candidate text, its hash, and complete-Core/outline/partial/Detailer/presentation diagnostic flags. It cannot create an accepted artifact or commit receipt.

`cera.realization_manifest.v1` separately declares:

- candidate, decision, and SequencePlan bindings;
- floor, move-intent/action hashes, participants, and realized current beats;
- exact source-unit coverage order, spans, and event states;
- character ownership/source spans;
- declared semantic-inference edges;
- terminal-state, protected-user stop, and major-addition status.

`cera.scene_composer_receipt.v2` contains hashes and provider-neutral adapter evidence only. A fake binds fixture identity with zero external calls; DeepSeek binds one safe live-provider receipt. It never contains exact protected text. `cera.composer_validation_receipt.v1` records `structural_candidate_validation`, advisory quarantine, `semantic_quality_proven: false`, and `story_state_committed: false`.

`cera.deepseek_scene_composer_packet.v1` binds the Composer request, validated decision, SequencePlan, and bounded `ComposerRealizationContext`. `cera.deepseek_scene_composer_response.v1` is a closed provider declaration containing complete story prose, decision-copy fields, participant/beat declarations, exact quote anchors, semantic-inference declarations, and advisory notes. Python converts anchors to offsets and creates all candidate/manifest IDs and hashes before validation.

`cera.rendered_story.v1` is a pure downstream projection bound to an accepted-artifact hash and renderer profile/version. It is not story authority.

## 6B. Phase 16 transaction receipt evidence

`ReceiptRecord` is the storage envelope for an immutable receipt payload. It contains a typed validation/lookup/provider receipt ID, category, payload schema version, canonical JSON, and SHA-256. `TurnCommitBundle.receipt_records` must exactly cover its declared receipt-ID sets when records are present; the Phase 16 ordinary builder always requires complete coverage.

SQLite schema version 8 stores these records in append-only `turn_receipt_records` under the same transaction as the source, generation, accepted artifact, branch-head transition, and `CommitReceipt`. Provider-category envelopes retain normalized role receipts and optional sanitized transport/bridge receipts. They do not retain prompts, raw evidence, story prose, or secrets.

Phase 17 adds one direct `EvidenceDocument` `EVENT_FACT` to the ordinary bundle. Its `accepted_turn_event` payload binds validated realized current beats, source coverage, stop boundary, participant/knowledge-owner scope, and artifact/source hashes. It is system-private direct event authority and contains no derived memory or future conditional segment.

`cera.derived_consolidation_request.v2` adds retained lookup receipt payloads matching its lookup IDs. `cera.consolidation_bundle.v2` adds exact receipt-record coverage for lookup, provider-neutral consolidator, and validation receipts. SQLite schema version 9 stores them in append-only `consolidation_receipt_records` inside the derived-authority transaction.

## 6C. Phase 18 durable post-publication work

`PostPublicationWorkRequest` is an immutable canonical request for `render`, `consolidate`, or `derived_views`. It binds a typed work ID, branch, accepted artifact, request SHA-256, and optional same-bundle dependency. Kind-specific schemas require the renderer profile, direct-event/protected-user/world-mode consolidation identity, or consolidation-work dependency respectively.

`TurnCommitBundle` advances its hash domain to `cera.turn_commit_bundle.v2` and may schedule at most one request of each kind. Every request must preserve the bundle's accepted-artifact hash. Consolidation must name the direct event inserted by the same bundle; derived-view work must name and depend on that consolidation work.

SQLite schema version 10 adds `post_publication_work` and `post_publication_attempts`. Work states are `pending`, `running`, `completed`, `no_change`, and `failed`. Canonical result/error payloads are SHA-bound. Work identity/request columns and completed attempts are immutable; rows cannot be deleted. Attempt history records only the explicit authorization-reason SHA-256 and terminal hash, not the raw rationale, without making the journal story authority.

## 6D. Phase 19 operator contracts

`cera.post_publication_work_summary.v1` exposes only work/branch/artifact identity, work kind, state, attempt count, dependency identity/state, request/result/error hashes, and the retry-authorization flag.

`cera.post_publication_work_error_envelope.v1` exposes a stable post-publication failure/interruption code, generic message, trace/work/branch/artifact identity, `story_state_retained: true`, `manual_after_review`, and no details.

`cera.post_publication_status_report.v1` contains one artifact's summaries, exact lifecycle counts, and error envelopes exactly covering failed work. `cera.post_publication_operation_receipt.v1` binds dispatch/retry/recovery operation, requested/affected IDs, optional rationale hash, and report hashes. All four are schema-registered and exclude prose, exact evidence, protected identity, configuration/request payloads, provider data, and raw operator rationale.

## 6E. Phase 20 ordinary application contracts

`cera.ordinary_application_request.v1` binds a prepared ordinary `SceneReasonerRequest`, ordinary `ComposerRequestPlan`, optional renderer profile, and consolidation-request flag. It rejects adult bindings and non-ordinary prepared routes.

`cera.ordinary_application_receipt.v1` binds application-request hash, request/branch/generation identity, accepted artifact and commit hashes/IDs, safe operational report hash/ID, adapter-call count, exact replay, downstream failure count, committed story state, and an explicit false adult-route flag. Exact replay requires zero adapter calls. Both schemas are registered; the result adds the accepted artifact, commit receipt, and safe report without exposing internal provider/evidence packets.

## 7. `BlockedTurnCheckpoint`

```json
{
  "schema_version": "cera.blocked_turn_checkpoint.v1",
  "checkpoint_id": "checkpoint:...",
  "world_id": "world:...",
  "genesis_revision_id": "genesis_revision:...",
  "protected_user_id": "character:ted",
  "request_id": "request:...",
  "source_sha256": "...",
  "branch_id": "branch:...",
  "generation_id": "generation:...",
  "starting_artifact_id": "artifact:...|null",
  "starting_artifact_sha256": "...|null",
  "classification": "blocked_nonconsensual_event",
  "boundary_source_unit_id": "source_unit:...",
  "facts_established_through_boundary": [],
  "actual_consent_capacity": {
    "consent": "refused|withdrawn|absent|unknown",
    "capacity": "clear|impaired|incapacitated|unknown",
    "pressure": "present|disqualifying|unknown",
    "freedom_to_stop": "limited|absent|unknown"
  },
  "story_state_committed": false,
  "checkpoint_sha256": "..."
}
```

This records detection, not occurrence of the blocked continuation. Starting-artifact ID and hash are both null only for a root branch with no accepted artifact. `checkpoint_sha256` is verified from canonical content excluding the self-hash field.

## 8. `RejectedTurnReceipt`

```json
{
  "schema_version": "cera.rejected_turn_receipt.v1",
  "rejection_id": "rejection:...",
  "request_id": "request:...",
  "checkpoint_id": "checkpoint:...",
  "error_code": "CERA_BLOCKED_NONCONSENSUAL_EVENT",
  "story_state_committed": false,
  "provider_calls_after_boundary": 0
}
```

It is operational audit data and is excluded from story retrieval.

## 9. `TemporaryAftermathProjection`

```json
{
  "schema_version": "cera.temporary_aftermath_projection.v1",
  "projection_id": "projection:...",
  "checkpoint_id": "checkpoint:...",
  "branch_id": "branch:...",
  "status": "noncanonical_hypothesis",
  "established_facts": [],
  "character_knowledge": [],
  "derived_interpretations": [
    {"owner_id": "character:...", "hypothesis": "...", "evidence_ids": [], "confidence": "low|medium|high"}
  ],
  "uncertainties": [],
  "prohibited_assumptions": [],
  "retrieval_visibility": "scratch_only",
  "expires_at": "...",
  "delete_after_reconciliation": true
}
```

## 10. `ExternalEventRequest`

```json
{
  "schema_version": "cera.external_event_request.v1",
  "external_request_id": "external_request:...",
  "world_id": "world:...",
  "genesis_revision_id": "genesis_revision:...",
  "protected_user_id": "character:ted",
  "request_id": "request:...",
  "source_sha256": "...",
  "branch_id": "branch:...",
  "generation_id": "generation:...",
  "starting_artifact_id": "artifact:...|null",
  "starting_artifact_sha256": "...|null",
  "checkpoint_id": "checkpoint:...",
  "checkpoint_sha256": "...",
  "required_receipt_schema": "cera.external_completion_receipt.v1",
  "allowed_event_registry_version": "cera.controlled_event_registry.v1",
  "expires_at": "...",
  "callback_correlation_token": "opaque single-use value"
}
```

This is a neutral integration envelope. It contains no prompt, generation instructions, or internal handler behavior. Phase 8 persists it as operational state so the opaque token can be recovered after restart; it is never story evidence or provider prompt context.

## 11. `ExternalCompletionReceipt`

```json
{
  "schema_version": "cera.external_completion_receipt.v1",
  "receipt_id": "external_receipt:...",
  "idempotency_key": "...",
  "external_request_id": "external_request:...",
  "allowed_event_registry_version": "cera.controlled_event_registry.v1",
  "world_id": "world:...",
  "genesis_revision_id": "genesis_revision:...",
  "protected_user_id": "character:ted",
  "request_id": "request:...",
  "source_sha256": "...",
  "branch_id": "branch:...",
  "generation_id": "generation:...",
  "starting_artifact_id": "artifact:...|null",
  "starting_artifact_sha256": "...|null",
  "checkpoint_id": "checkpoint:...",
  "checkpoint_sha256": "...",
  "ordered_events": [
    {
      "event_step_id": "external_step:E01",
      "classification": "controlled_non_graphic_enum",
      "actor_ids": [],
      "target_ids": [],
      "state": "attempted|ongoing|partial|interrupted|completed|stopped",
      "consent_status": "absent|refused|withdrawn|unknown",
      "resistance_or_freeze": [],
      "calls_for_help": [],
      "observed_response": "none_observed|response_observed|unknown",
      "defensive_actions": [],
      "material_changes": [],
      "injury_status": "none_established|possible|established|unknown",
      "reproductive_exposure": "not_applicable|none_established|possible|established|unknown",
      "conception_status": "not_applicable|not_established|established|unknown",
      "knowledge_owners": []
    }
  ],
  "event_end": {"state": "ended|interrupted|escaped|unknown", "current_safety": "safe|unsafe|uncertain"},
  "immediate_aftermath_facts": [],
  "receipt_sha256": "..."
}
```

The controlled event classification registry is versioned separately. Free-form graphic prose is prohibited. `receipt_sha256` is verified from canonical content excluding the self-hash field. The callback token travels in the submission envelope rather than the receipt and must match the request's single-use token.

## 12. `AftermathDecision`

```json
{
  "schema_version": "cera.aftermath_decision.v1",
  "receipt_id": "external_receipt:...",
  "receipt_sha256": "...",
  "checkpoint_id": "checkpoint:...",
  "checkpoint_sha256": "...",
  "projection_id": "projection:...|null",
  "projection_sha256": "...|null",
  "scene_decision": {"route": "aftermath"},
  "projection_reconciliation": [
    {"projection_item": "...", "status": "confirmed|contradicted|unknown|discarded", "receipt_step_ids": []}
  ],
  "authority_candidates": [
    {
      "record_id": "event:...|memory:...|relationship:...|material:...|development:...",
      "record_type": "objective_event|character_memory|relationship_evidence|material_state|development_overlay",
      "payload_json": "canonical cera.evidence_document.v1",
      "payload_sha256": "..."
    }
  ],
  "uncertainties": [],
  "composer_must_preserve": [],
  "contains_graphic_detail": false,
  "protected_user_action_authored": false
}
```

`cera.external_receipt_validation.v1` records the checked correlation fields while remaining noncanonical and pending. `cera.aftermath_reasoner_receipt.v1` binds the fake reasoner request, receipt, optional projection, fixture, and decision with zero external provider calls.

## 13. Canonical state records

### Event

`EventRecord` stores neutral ordered facts, objective classification, participants, time, branch, source/receipt/artifact evidence, consent/capacity, material results, and supersession status.

### `CharacterMemoryRecord`

```json
{
  "schema_version": "cera.character_memory.v1",
  "memory_id": "memory:...",
  "owner_id": "character:...",
  "branch_id": "branch:...",
  "privacy": "owner_private|shared|public",
  "title": "compact retrieval title",
  "abstract": "non-graphic owner-safe summary",
  "objective_event_refs": ["event:..."],
  "perceived_facts": [],
  "subjective_interpretations": [
    {"kind": "fear|betrayal|shame|anger|self_blame|other", "statement": "...", "certainty": "felt|believed|suspected"}
  ],
  "unknowns": [],
  "knowledge_route": "direct|reported|inferred",
  "retrieval_tags": [],
  "trigger_cues": [],
  "development_history_refs": [],
  "supersedes": [],
  "genesis_effect": "none"
}
```

Self-blame is always subjective. Objective responsibility comes only from event records.

### Development overlay

`CharacterDevelopmentOverlay` stores branch-local evidence-backed tendencies or current adaptations with strength, scope, first/last evidence, contradiction evidence, and supersession. It never edits Genesis.

### Relationship

`RelationshipEvidenceRecord` is directional (`from_id` -> `to_id`) and stores specific evidence such as trust, fear, obligation, resentment, secrecy, or reliance. It does not collapse these into one universal score.

### Transaction receipt

`cera.commit_receipt.v2` binds the exact source ID/hash, previous and new branch heads, generation transition, accepted-artifact hash, inserted/superseded deltas, validation receipts, lookup receipts, provider receipts, accepted external receipt IDs, transaction hash, and atomic outcome.

## 14. Phase 8 operational persistence

SQLite migration 6 adds `blocked_turns`, `temporary_aftermath_projections`, and `external_receipt_claims`. Checkpoint/request/receipt identity payloads are immutable. Lifecycle status, validated receipts, aftermath decisions, and staged commit bundles are restartable operational state. A successful resumption commit changes the receipt to `committed`, the checkpoint to `accepted`, advances the branch, inserts all story records, and deletes the temporary projection inside one SQLite transaction.

## 15. Phase 9 derived-consolidation records

Phase 9 registers:

- `cera.derived_consolidation_request.v2`;
- `cera.consolidation_proposal.v1`;
- `cera.consolidation_reasoner_receipt.v1`;
- `cera.consolidation_validation_receipt.v1`;
- `cera.consolidation_bundle.v2`;
- `cera.consolidation_commit_receipt.v1`;
- `cera.derived_view.v1`;
- `cera.derived_view_build_receipt.v1`.

The request binds one `cera.evidence_snapshot.v2`, exact expanded evidence, lookup receipts, eligible record types, a maximum output count, and hard boundaries. A proposal contains typed `EvidenceDocument` candidates or an explicit no-change reason. It cannot propose a Genesis rewrite, clinical diagnosis, accepted-story change, objective event, source fact, Genesis fact, or material fact.

Record-specific `sections_json` is closed and strict:

- memory requires owner-private objective-event refs, perceived facts, subjective interpretations, unknowns, retrieval tags/cues, `clinical_diagnosis: not_assessed`, and `genesis_effect: none`;
- relationship requires one directional `from_id` to `to_id` edge with named dimensions rather than a universal score;
- thread requires participants, open/resolved/dormant status, evidence, and status-consistent resolution data;
- development requires owner-private kind, bounded strength/scope, evidence history, contradiction evidence, and no permanent/global/identity rewrite. Strong development requires repeated evidence.

Supersession requires the exact current predecessor to have been expanded, the same record type/owner/subjects/typed identity, version increment by one, one predecessor, and a stated reason. Prior versions remain audit-addressable.

SQLite migration 7 adds `branches.authority_revision`, `consolidation_journal`, `consolidation_receipts`, and `derived_views`. The consolidation receipt binds the prior/new authority revision, unchanged story head/generation, inserted/superseded deltas, snapshot, proposal/validation/adapter hashes, and atomic outcome. Derived views are non-authoritative projections with source-set hashes and access scopes.

## 16. Phase 10 evaluation and readiness records

Phase 10 registers:

- `cera.evaluation_case.v1` and `cera.evaluation_suite_manifest.v1`;
- `cera.evaluation_run_report.v1`;
- `cera.telemetry_event.v1`;
- `cera.human_review_packet.v1` and `cera.human_review_ballot.v1`;
- `cera.promotion_policy.v1` and `cera.promotion_assessment.v1`;
- `cera.deployment_readiness_report.v1`.

An evaluation case binds one role, partition, input hash, expected-contract hash, risk tags, criteria, and provenance hashes. Holdout cases are hash-addressed and sealed; their content is not a Composer/Reasoner craft input. A suite lists known excluded craft hashes and rejects overlap. Evaluation findings derive from exact criterion observations and turn authority/privacy/identity/consent/cast/branch/protected-user failures into non-averagable hard defects.

`RouteIdentity` binds role, route kind, adapter, prompt/transport versions, configuration hash, and, for a live route only, concrete model/revision. An offline route cannot claim provider identity or live evidence. A run report is evidence, not authority or promotion, and records zero story writes.

Telemetry stores only bounded operational tokens, IDs/hashes, counters, stable error codes, and explicit protected-content absence flags. It contains no raw source, story prose, private evidence, prompts, or secrets.

Human-review packets use candidates A/B and keep route mappings in a separate `HumanReviewKey`. A promotion assessment can be `offline_evidence_only`, `pending_evidence`, `not_eligible`, or `eligible_for_creator_review`. It always requires creator approval and always records `route_promoted: false`. A deployment-readiness report similarly never deploys.

## 17. Phase 11 live-provider transport records

Phase 11 and D-169 register:

- `cera.live_provider_route.v1`;
- `cera.live_provider_call_receipt.v2`;
- `cera.provider_failure_call_receipt.v1`.

A live route binds exactly one runtime role, provider, adapter, concrete model/revision, prompt version, transport/version, auth mode, timeout, request/output/cost ceilings, and dated provider pricing where API billing applies. It rejects production enablement, automatic retry, fallback, legacy DeepSeek aliases, provider/role reassignment, embedded credentials, and unapproved endpoints.

A live call receipt records hashes and bounded counters only: route/request/output hashes, hashed provider request/fingerprint metadata, requested/returned model identity, duration, token usage, estimated API cost or ChatGPT quota status, one external call, zero retry, and zero story-authority writes. It contains no raw source, story prose, private evidence, prompt, or secret.

`ProviderFailureCallReceipt` wraps the same privacy-safe live call receipt when
the provider completed one response that CERA could not accept. It adds the
normalized provider finish reason, a controlled failure kind, and
`completion_accepted=false`. This keeps exact usage and terminal evidence
available after transport, typed-decoding, or semantic failure while retaining
only the response hash, never partial prose or provider reasoning.

DeepSeek returns the concrete model in its response, so its receipt marks model identity as provider-verified. Codex app-server currently confirms the OpenAI model provider and pinned CLI runtime but does not echo the concrete model slug through the high-level Python SDK result. Its receipt therefore binds the explicit model request and marks model identity as request-sourced rather than falsely provider-verified.

## 18. Phase 12 request-bound MCP bridge record

The active bridge receipt is `cera.mcp_evidence_bridge_receipt.v2` with typed ID kind `mcp_bridge_receipt`. Tool contract v4 retains bounded query-plan search, projects exact expansion as one singular `evidence_id` plus advertised sections per provider call, and records safe field-path/issue-class diagnostics even when FastMCP rejects arguments before dispatcher entry. Historical qualification evidence remains unchanged.

The receipt binds the provider receipt, reasoner request hash, immutable evidence snapshot and binding, public bridge binding, fixed server/transport/tool-contract identity, ordered tool-call hashes and stable error codes, exact evidence IDs, provider-observed call count, cumulative evidence bytes, and zero authority writes. Provider and bridge tool sequences must match exactly. The receipt contains no bearer token, URL, tool arguments/results, raw source, story prose, or private evidence.

`CodexMcpRuntimeBinding` is deliberately runtime-only rather than a registered durable schema. Its public descriptor contains the server name, tool allow-list, binding hash, required status, timeouts, and minimum/maximum call rules. The ephemeral loopback URL and bearer token cross only the parent-to-worker invocation boundary and are never serialized into a receipt.

`cera.codex_worker_progress.v1` is likewise a runtime-only privacy-safe
sidecar, not story or provider authority. The supervised parent creates it in
the otherwise isolated worker directory and the worker advances only an
allow-listed stage name. On timeout, the parent kills the complete process
tree and exports `transport:timeout` plus the last stage into typed failure
diagnostics. A timeout at or after `thread_run` counts as one conservatively
observed provider dispatch even when no provider receipt returns. The sidecar
contains no prompt, source, evidence text, raw response, story prose, or
credential.

## 19. Phase 13 typed Codex Reasoner adapter

Phase 13 evolves the registered Reasoner receipt to `cera.scene_reasoner_receipt.v2`. The former fake-only fixture fields become provider-neutral adapter role/version/evidence fields. The v2 receipt additionally binds provider-receipt and optional MCP-bridge-receipt hashes. Fake calls require zero external calls and no bridge. Codex calls require exactly one provider call; an investigation call requires one matched bridge receipt, while a Python-declared sufficient exact-seed call may omit MCP and therefore has no bridge receipt or tool calls.

`cera.codex_scene_reasoner_packet.v2` is a transient prompt packet rather than
durable authority. Structural Contract v2 replaces provider-authored
`ReasonerOutcome` with `cera.codex_reasoner_draft.v2`; Python compiles the
registered authoritative `ReasonerOutcome` v3. A closed JSON Schema constrains
provider syntax; strict dataclass decoding and Python compilation enforce
domain, evidence, and authority semantics.

Structural Contract v3 registers `cera.codex_reasoner_draft.v3` and advanced
the Python authority to `cera.reasoner_outcome.v4`. The v3 draft changes
only adult craft ownership: each channel separately declares semantic concepts
and a lexical subset. Historical v2/v3 records remain registered decoders.

Structural Contract v4 registers `cera.codex_reasoner_draft.v4` and advances
the active Python authority to `cera.reasoner_outcome.v5`. The provider may
identify exact protected-user action/dialogue quotes; Python requires a unique
occurrence and derives offsets and hashes. No quote means no protected-user
action/dialogue realization authority for that source unit.

## 20. Phase 14 typed DeepSeek Composer adapter

Phase 14 evolves the registered Composer receipt to `cera.scene_composer_receipt.v2`. Its provider-neutral adapter role/version/evidence fields bind either a fake fixture with zero external calls or one matching safe DeepSeek provider receipt. The receipt contains hashes and counters, never source or prose.

`ComposerRealizationContext` is a transient, hash-bound set of exact evidence and separately labeled creator-craft blocks. Exact blocks must be Reasoner-cited or continuity-selected and remain constrained by Genesis revision, visibility, knowledge ownership, selected-character applicability, content class, and byte budget. Every selected NPC requires identity or voice context before the DeepSeek adapter can call its provider.

`cera.deepseek_scene_composer_packet.v2` carries a minimal composition DTO,
selected context, hard boundaries, and closed output policy.
`cera.deepseek_composition_draft.v2` carries story prose, source-coverage
anchors, realization anchors, and one terminal-boundary anchor. Each quote must
be unique within `story_text`; the retained occurrence field is a zero
compatibility sentinel. Python rejects absence/ambiguity and derives effective
occurrence, offsets, hashes, participant/beat bookkeeping, and validation
records.

Structural Contract v3 registers `cera.deepseek_composition_draft.v3`. It
carries ordered local-keyed prose segments plus source,
realization, and terminal segment references. Python concatenates segments
with a fixed separator and derives text, spans, hashes, and manifest records;
providers no longer copy quote anchors from their own prose.

Structural Contract v4 registers active
`cera.deepseek_composition_draft.v4`. Source coverage becomes one-to-many
ordered segment-key mapping. Adult specificity coverage names only a
Python-owned obligation key; Python resolves its beat, channel, owner, and
exact ranges. General realization kinds remain distinct from adult craft
channels.

The continuous-route correction advances the active provider DTO to
`cera.deepseek_composition_draft.v5`. Each realization segment now contains
one mandatory `authority_id` whose typed kind is exactly `source_unit:` or
`beat:`. This replaces the v4 pair of nullable source/beat fields and makes the
provider schema express one exclusive authority.

The adaptive-depth correction advances the active provider DTO to
`cera.deepseek_composition_draft.v6`. The provider no longer repeats an
`owner_id` beside that authority. Python derives the character owner from the
validated request-local beat/source map, removes exact duplicate realization
references, then applies the existing domain obligations. Unknown authorities,
missing required beats or participants, and mismatched coverage still fail
closed. Historical v2-v5 payloads remain explicit compatibility decoders; they
are not accepted as active v6 output. Source, general realization, adult
specificity, and terminal coverage maps remain separate Python-validated
obligations.

## 21. Phase 15 context assembly and live-shaped turn records

`cera.composer_context_assembly_receipt.v1` binds the immutable snapshot, base and assembled Composer request hashes, realization-context hash, exact selected evidence IDs, selected craft-reference IDs, lookup receipt IDs/count/bytes, zero provider calls, and zero authority writes. Exact evidence content remains transient and is not stored in the receipt.

`cera.live_shaped_turn_receipt.v5` requires the independent
realization-verification ID/hash and the Python final-acceptance receipt
ID/hash. It binds
request/branch/generation/snapshot identity; Reasoner, context, Composer,
verifier, and accepted-artifact hashes; provider and lookup receipt IDs;
aggregate provider-call count; explicit zero-write/uncommitted state; and
optional Adult ON/EX selection, deterministic-specificity,
semantic-specificity, and beat-repair receipt bindings. It is orchestration
evidence, not a `CommitReceipt` and not story truth.

`ReasonerExecutionResult` and `LiveShapedTurnResult` are transient runtime envelopes rather than registered durable schemas. They may hold exact authorized evidence or accepted prose in memory and must not be serialized into telemetry or operational receipts.

## 22. Provider-free Adult ON/EX craft contracts

The Adult ON/EX gate registers:

- `cera.adult_craft_fragment.v1`;
- `cera.adult_craft_catalog_manifest.v1`;
- `cera.adult_craft_need.v1`;
- `cera.adult_craft_need.v2`;
- `cera.adult_craft_need.v3`;
- `cera.adult_craft_selection_receipt.v1`;
- `cera.specificity_contract.v1`;
- `cera.specificity_validation_receipt.v1`;
- `cera.semantic_specificity_request.v1`;
- `cera.semantic_specificity_result.v1`;
- `cera.semantic_specificity_receipt.v1`;
- `cera.beat_scoped_repair_request.v2`;
- `cera.beat_scoped_repair_receipt.v1`.

`AdultCraftFragment` is a CERA-owned, hash-bound craft section with source provenance, ON/EX eligibility, family/subfamily tags, teachable axes, optional compiled vocabulary groups, applicability requirements, and prohibited inferences. It is neither Genesis nor story authority. The catalog manifest binds all fragment and source-integrity hashes and prohibits external runtime paths.

`AdultCraftNeed` is semantic Reasoner output, not prose. It binds the validated request, decision, SequencePlan, a non-empty subset of current beats requiring adult-specific craft, content families, axes, distinct realization channels, character-card section requests, and separate climax/aftermath authority and commitment states. A beat need may never name a future or invented beat. The record is invalid for ordinary, insufficient, unresolved-authority, or blocked outcomes.

`AdultCraftNeedV3` makes word choice explicit. `semantic_concepts` select craft
technique and semantic verification without creating lexical quotas.
`lexical_concepts` must be a duplicate-free subset and alone create
deterministic vocabulary requirements. Python rejects attempts to use a
lexical concept that was not first declared semantically.

`AdultCraftSelectionReceipt` records deterministic coverage-within-budget selection and contains the resulting `SpecificityContract`. Each specificity requirement is bound to one current beat and one channel; direct/vulgar concepts require actual vocabulary alternatives rather than a catalog coverage declaration.

`SemanticSpecificityRequest` contains only locked beat-local text and hashes, the selected participant IDs, `SpecificityContract`, actor/action/object/material bindings, and required transitions. The typed result records bounded finding codes and makes initial failures repairable only once; post-repair failures are terminal. The privacy-safe receipt retains no candidate or beat text and binds exactly one invocation, no retry/fallback, adapter evidence, counters, and zero story writes.

`BeatScopedRepairRequest` v2 binds exactly one failed beat span and hash, the initial deterministic and semantic validation receipts, and all locked non-target content. Its receipt records one accepted-for-full-revalidation or rejected attempt. After splice, semantic verification runs again before the complete Composer validator. Neither schema authorizes a second repair, a provider change, an authority change, or a story commit.

## Structural Contract v2 records

The active provider and authority records are intentionally different:

| Record | Owner | Meaning |
|---|---|---|
| `cera.raw_turn_envelope.v2` | Python/application ingress | Exact raw message identity, cast, branch/generation, access scope, preflight authority, boundaries, and typed scene-depth mode |
| `cera.intent_interpretation_draft.v1` | `IntentInterpreterPort` | Advisory contiguous spans, responder/route hints, evidence obligations, anchors, unknowns, and prohibitions |
| `cera.intent_interpretation_receipt.v1` | Python | Hash-binds accepted interpretation with zero calls/writes in this fake-only phase |
| `cera.evidence_query_plan.v1` | retrieval caller | Bounded primary and alternate term sets plus filters, limit, variant bound, and ambiguity policy |
| `cera.evidence_obligation.v1` | Python ingress/seed contract | Exactly one required record, subject, record type, or query-plan selector plus exact required sections |
| `cera.seed_dossier_receipt.v1` | Python | Immutable-snapshot binding, reauthorized seed hashes, obligation resolutions, exact evidence IDs, and lookup receipts |
| `cera.codex_reasoner_draft.v4` | Scene Reasoner | Advisory semantic decision using local keys/evidence IDs, separate semantic/lexical craft concepts, and exact protected-user source-quote claims; no canonical bookkeeping |
| `cera.reasoner_outcome.v5` | Python | Authoritative validated compilation with deterministic IDs, versions, citations, route flags, hashes, and Python-anchored protected-user claim ranges |
| `cera.adult_craft_need.v3` | Python compilation of Reasoner semantics | Beat-local discriminated channel ownership plus an explicit lexical subset of semantic concepts |
| `cera.deepseek_composition_draft.v6` | Scene Composer | Ordered local-keyed prose segments plus mandatory typed source-unit-or-beat realization authority; Python-derived owner identity; one-to-many source coverage, adult specificity-obligation, and terminal references |
| `cera.scene_realization_verification_request.v7` | Python | Transient candidate prose/hash, semantic expected-beat descriptions and states, expected participants, protected-user exact claims, established accepted-scene continuity, bounded hashes-bound authoritative evidence context selected for the Composer, required semantic-boundary checks, and Composer anchors |
| `cera.codex_scene_realization_verifier_draft.v3` | Sol-medium verifier adapter | Advisory status, exact semantic coverage sets, atomic development verification, safe violation codes, and unique exact candidate quotes for anchored violations; no canonical offsets, hashes, IDs, prose revisions, or authority writes |
| `cera.scene_realization_verification_receipt.v5` | Python over verifier call/draft | Accepted/rejected/inconclusive status, exact verified IDs/boundaries/development atoms, Python-derived anchored finding hashes, adapter qualification evidence, optional safe provider-receipt binding, and zero prose retention |
| `cera.final_story_acceptance_receipt.v1` | Python | Final pre-publication binding across structural Composer validation, realization verification, and active adult lexical/semantic checks; this receipt alone may authorize construction of an accepted artifact |
| `cera.turn_failure_evidence_bundle.v2` | Python operational journal | Privacy-safe failure metadata, valid receipt handles, and allow-listed canonical safe receipt payloads; never story authority |
| `cera.turn_stage_audit_entry.v1` | Python operational journal | Append-only stage sequence and status; never automatic-resume authority |
| `cera.repository_source_inventory_receipt.v1` | Python build validation | Required package/source inventory and `.gitignore` binding |
| `cera.persistent_codex_qualification_evidence.v1` | Python transport activation | Typed projection of one exact hash-bound passed four-call probe; proves separate fixed-role process reuse with unique safe receipts and no MCP/retry/fallback/story writes |

`SemanticInference`, `CharacterRealization` booleans, and the provider-authored
v1 `ReasonerOutcome` manifest remain historical compatibility contracts. They
are not active provider obligations. Provider schemas and Python dataclass
validation must accept and reject the same field ownership combinations;
unknown enums, nullable cross-field shortcuts, and provider-created
bookkeeping fail closed.

Persistent transport activation validates the immutable probe bytes before
creating a story evidence directory. Rehashing a changed summary is not enough:
Python revalidates terminal status, role order, one-process/two-request counts,
fresh evidence and provider identities, fixed model/role, safe retention flags,
and four exact Sol dispatches. This operational proof authorizes only the
transport mode; it cannot establish story semantics or bypass the full-route
call ledger.

Provider-facing semantic sets use schema descriptions and prompt rules because
the active OpenAI Structured Outputs subset does not support `uniqueItems`.
Python still rejects duplicates. Status-dependent Reasoner failures expose only
stable field-path codes. Protected-user semantic rejection findings are
validated against transient prose, while durable receipts retain only hashes.

The Sol-medium verifier draft uses the OpenAI strict-schema projection. Every
object is closed, every property is required, and nullable values are explicit.
The provider supplies semantic labels and a unique exact quote only; Python
rejects duplicate sets, absent or ambiguous quotes, invalid status-dependent
field combinations, incomplete beat/participant/boundary coverage, and any
attempt to cross authoritative domain validation. Quote occurrence, offsets,
hashes, canonical IDs, route flags, and acceptance remain Python-owned.

## Provider-schema compatibility profiles

Provider-facing schemas are deterministic projections of the provider-neutral
contracts, not alternate domain schemas. The active profile IDs are
`openai_structured_output_2026_07_v1` and
`deepseek_json_object_prompt_2026_07_v1`.

For OpenAI, CERA projects the domain schema into the documented strict-output
subset before request hashing and dispatch. The root remains a closed object,
all object properties remain required, nullability stays explicit, and the
AdultCraftNeed channel-owner `oneOf` becomes a disjoint `anyOf`. Recursive
validation rejects unsupported or unknown keywords, open objects, optional
properties, excessive nesting/property/enum/string budgets, and invalid
primitive declarations.

For DeepSeek JSON-object mode, CERA includes a closed portable projection in
the Composer packet as prompt guidance and marks it as not provider-enforced.
Strict dataclass decoding, enum/type checks, segment and violation-anchor conversion, participant and
beat verification, and every semantic/cross-field validator remain Python
owned after the response. The active inventory contains the full Codex
Reasoner draft, Codex transport probe, Codex MCP probe, and DeepSeek Composer
draft; an unlisted provider response schema is a build failure.

Rejected-candidate review artifacts are deliberately absent from this general
registry. An explicitly configured qualification-only port may retain bounded
creator-local excerpts under manual-deletion policy. Runtime bundles retain
only the review ID/hash and never the excerpt or candidate prose.

SQLite schema version 11 adds append-only `turn_failure_evidence` and
`turn_stage_journal` tables with foreign-key and immutability enforcement. A
failed stage may reference only an existing failure bundle. Neither table is a
story/evidence authority table, and neither can authorize replay, retry, or
resume.

## Hanezawa Genesis V1.2 expression records

Hanezawa V1.2 is a child Genesis revision of immutable V1.1. It introduces the
generated `character_expression.json` module while retaining the common
`cera.genesis_record.v1` envelope and the existing privacy, knowledge,
authority, supersession, and section-fetch semantics.

The module contains:

- seven owner-private embodied-identity profiles;
- seven rhetorical-signature records;
- seventy-seven response-mode records with two non-executable,
  noncopyable construction examples each;
- shared semantic separations, trust-state semantics, and
  non-predetermination rules;
- Hana's owner-private aging self-blame and a separate objective correction;
- pre-disclosure and post-disclosure owner-safe family-support records;
- one runtime-projection contract.

Each expression record exposes typed expandable sections and a stable
`source_ref` into the V1.2 creator artifact. Compact search metadata is not
exact evidence. The Reasoner must fetch and cite the exact relevant section
before a selected move or beat depends on it. Owner-private records remain
unavailable to an unauthorized speaker or observer.

The runtime does not add a provider-authored expression manifest. The active
Codex Reasoner draft continues to select semantic moves and evidence IDs;
Python derives and validates citations, scope, and bookkeeping; the Composer
context assembler projects only relevant cited expression records for selected
active speakers. DeepSeek receives those records as bounded
`character_expression` context and may realize fresh wording, but it does not
select participants, consent, trust promotion, durable truth, or protected-user
private state.

Illustrative examples have both `examples_are_non_executable: true` and
`examples_are_noncopyable: true`. They cannot establish a past event, a future
event, a protected-user belief, consent, or a canonical response string.

## Inactive modular Composer prompt contracts

These D-159 contracts are repository and fixture contracts only. They are
registered for canonical decoding but are not part of the active provider
route.

| Schema | Owner | Purpose |
|---|---|---|
| `cera.prompt_module_registry_manifest.v1` | Python/repository | Hash-bound allowlisted module declarations, layers, selector values, dependencies, conflicts, deterministic order, provenance, and disabled/fixture/shadow state |
| `cera.prompt_selection_request.v1` | Python with bounded Reasoner semantics | Exact Depth, Adult rendering, topology, scene function, tone, interiority, cast, eligibility, and selected evidence/craft inputs; arbitrary Reasoner module IDs are forbidden |
| `cera.prompt_compilation_receipt.v1` | Python | Manifest/compiler identity, ordered module contributions, evidence/craft identities, plan/cast hashes, size/count telemetry, and explicit zero provider/publication/retry/fallback/Detailer facts |

`AdultRenderingMode` is a transport enum rather than an authority decision. A
non-OFF value cannot establish route eligibility or alter a SequencePlan. The
compiler rejects mismatched craft modes, OFF-mode craft, unselected
owner-private evidence, and conditional future-event material.

## Branch-bound Reasoner session contracts

These D-172/D-179 schemas remain provider-neutral. D-179 activates their native
stored Codex adapter on the local human-test route only.

| Schema | Owner | Purpose |
|---|---|---|
| `cera.reasoner_session_compatibility.v1` | Python | Hashes world/branch/role, provider route, model/effort/tier, transport, adapter, prompt/schema/base instructions, tool contract, Genesis, authority/privacy policy, protected user, and autonomy profile |
| `cera.reasoner_session_ledger.v1` | Python | Names the active accepted checkpoint and provider-session root; status is active, rotated, or invalidated |
| `cera.reasoner_session_checkpoint.v1` | Python | Immutable accepted/candidate/rejected/invalidated provider-thread node with branch, request, review, generation, head, regeneration, and context-manifest bindings |
| `cera.context_authority_delta.v1` | Python | Reasserts current head, generation, authority revision, snapshot, evidence versions/hashes, revocations, creator constraints, source, and packet hash |
| `cera.session_reconstruction_bundle.v1` | Python | Complete authoritative context used for fresh creation or rotation |
| `cera.accepted_turn_session_receipt.v1` | Python | Injected after creator acceptance and atomic publication; binds artifact, generation, state/prose hashes, and commit with zero provider calls |
| `cera.rejected_candidate_session_receipt.v1` | Python | Keeps rejected checkpoint/candidate hashes, review, reasons, and constraint IDs; raw prose retention is false |
| `cera.creator_constraint_record.v1` | Creator control via Python | Exact explicit feedback, branch/global scope, owner, supersession, and fixed non-story/non-knowledge status |
| `cera.reasoner_session_usage_receipt.v2` | Python | Privacy-safe cache, token, byte, latency, age, and compaction telemetry with unsupported fields explicitly null/unknown |
| `cera.reasoner_session_prompt_compilation.v1` | Python | Byte-equivalent stable-instruction/variable-packet split of the current Reasoner prompt |
| `cera.provider_thread_custody_descriptor.v1` | Provider adapter projected by Python | Hashed stored-thread identity, parent hash, lifecycle role, raw-context retention, and fixed non-authority status |
| `cera.provider_thread_custody_event.v1` | Python | Append-only allocation, resume, acceptance, rejected/failed leaf archival, accepted-lineage archival, historical deletion, or missing-thread evidence for one checkpoint |

SQLite schema version 18 stores session ledgers, checkpoints, constraints,
accepted/rejected receipts, usage receipts, and provider-thread custody events.
Terminal rows and receipts are immutable; deletes are prohibited. One active
Reasoner session and one open candidate are permitted per branch/role. The
provider adapter archives a rejected/failed candidate leaf before recording its
terminal custody event; the leaf cannot be resumed or promoted. CERA retains
only its hashes and explicit creator constraint in authority records, never raw
rejected prose. Migration 18 adds `rejected_archived` and `failed_archived`;
the earlier deletion values remain decode-only compatibility records.

## Compact Reasoner v7 shadow contracts

D-185 adds these provider-facing or transient shadow contracts without changing
the active D-180 route:

| Schema | Owner | Purpose |
|---|---|---|
| `cera.scene_cast_scope.v1` | Python | Separates world-known, physically present, scene-reachable, currently active, exact-source, eligible, and selected cast sets; current-only continuation inherits the exact accepted branch head rather than a named fixture cast |
| `cera.codex_reasoner_packet.v15.compact_shadow` | Python | Supplies the scoped prepared turn, compact exact-evidence views, hard boundaries, and a discardable reading capsule while retaining the full authoritative evidence in Python |
| `cera.reasoner_reading_capsule.v1` | Python | Alias-based branch/generation/floor/continuity/material/thread reconstruction aid that is explicitly non-authoritative and discarded on rejection, regeneration, fork, stale snapshot, provider loss, or shutdown |
| `cera.codex_reasoner_draft.v7.compact_shadow` | Runtime Codex, advisory | Authors each responder and causal beat once; Python deterministically compiles it into the existing v6/domain path before unchanged validation and Composer construction |
| `cera.codex_operation_telemetry.v1` | Provider adapter plus Python | Content-free timestamps, per-step and cumulative token usage, hashed operation/thread/root/checkpoint identities, tool timings, actual attempt count, finish state, and transport error; unknown fields remain null and named unsupported rather than zero |

Compact evidence aliases retain the exact fetched section content together with
record type, subjects/owner, authority, truth, epistemic/knowledge boundaries,
visibility, record version, exact-evidence hash, and retrieval relevance. They
do not replace `ExactEvidence`, relax alias resolution, or become durable story
authority. The v7 compiler cannot add a responder, beat, source claim, Ted
allowance, or state change that runtime Codex did not supply. The active v6
packet/prompt/schema remain the default and v7 has no production selector.

`cera.sillytavern.request.v3` adds `cera_reasoning_effort` with exact values
`medium`, `high`, or `xhigh`. It controls only the Scene Reasoner. The value is
included in the Reasoner session compatibility hash, so changing it rotates and
reconstructs the session; it does not change the Sol-medium verifier.

## Continuous Planner/Validator V1 shadow contracts

D-186 registers the following additive schemas without changing active D-180
provider identities:

| Schema | Owner | Purpose |
|---|---|---|
| `cera.rich_planner_sequence.v1` | runtime Codex Planner, advisory | Material beats with actors, evidence perception, goal, pressures, tactic, causality, private/material continuity, result, realization space, protected-user allowance, and evidence bindings |
| `cera.character_summary_envelope.v2` | Python projection | Explicitly incomplete exact-field projection bound to character, source path, authority class, revision, content hash, JSON pointers, payload, and derivation receipt |
| `cera.accepted_final_sequence_envelope.v1` | Python after creator acceptance | Exact user message plus Validator final sequence, appended once and superseding the provisional Planner sequence |
| `cera.validator_finalization_package.v1` | runtime Codex Validator, advisory until Python validation and creator action | Closed final sequence, existing creator review, no-more-than-100 semantic edits, created-field log, event, or an explicit scene summary |
| `cera.continuous_session_snapshot.v1` | Python | Role-separated stored-thread handle, compatibility, context-event hashes, and accepted-turn index; persisted under `PLANNER_SESSION` or `VALIDATOR_SESSION` |
| `cera.continuous_world_promotion_receipt.v1` | Python | Revision-bound candidate-to-ACTIVE promotion or unchanged nonaccepting action |
| `cera.request_evidence_binding.v2` | Python | Request-local current-source, Python mechanical-connective, ACTIVE-authority, or DERIVED-navigation handle bound to exact scope, hash, visibility, owner, revision, and read operation |
| `cera.request_evidence_binding_registry.v2` | Python runtime ledger | Current request allocation and resolution set; provider strings outside it have no evidence authority and DERIVED evidence cannot alone satisfy a hard decision |
| `cera.continuous_provider_call_ledger_event.v2` | Python | Durable prepared/pretransport-failed/transport-invoked/completed/failed/post-validation/accepted accounting with privacy-safe receipts, telemetry, exact stored-thread hash, and tool bindings |
| `cera.scene_summary_derived_view.v2` | Python over Validator draft | Explicitly non-authoritative scene view with complete per-turn exact-pair authority provenance, optional event cross-check hashes, revision, and regeneration identity |
| `cera.continuous_acceptance_journal.v2` | Python operational journal | Complete local creator-acceptance transaction binding action, package, exact pair/event, prior/prepared ACTIVE trees, receipt, optional False Positive diagnostic, timeline, Planner ledger, and model-injection state |
| `cera.continuous_root_diagnostic.v1` | Python | Secret-safe owning stage, operation, contract name, and stack evidence for failures before per-turn diagnostics exist |
| `cera.sillytavern_chat_request.v4` | Python/browser ingress | Adds the one-shot boolean `cera_scene_change` flag; no automatic scene inference |

World operations are `add`, `replace`, `remove`, `append_unique`, `increment`,
or `create_file`. Mutable `create_file` values must be JSON objects and Python
adds `_cera_revision`; arrays/strings are not mutable V1 semantic records.
Stable relative JSON paths and expected internal revisions are
mandatory. Every `add` and `create_file` has an exact created-field/root log
with value type, value, reason, and source final-sequence item.

`cera.request_evidence_binding.v2`,
`cera.continuous_provider_call_ledger_event.v2`, and
`cera.scene_summary_derived_view.v2` are registered durable dataclass records.
The request-local registry, mutable acceptance journal, root diagnostic, and
world promotion journal are Python runtime-operational records with dedicated
writers, invariant checks, and recovery readers; they are intentionally not
decoded through the generic immutable schema registry.
