"""Ordered SQLite schema migrations for the CERA authority store."""

from __future__ import annotations

import sqlite3

from cera.errors import TransactionError

CURRENT_SCHEMA_VERSION = 20


_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    migration_sha256 TEXT NOT NULL
);

CREATE TABLE worlds (
    world_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE branches (
    branch_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(world_id),
    parent_branch_id TEXT REFERENCES branches(branch_id),
    fork_artifact_id TEXT REFERENCES artifacts(artifact_id),
    head_artifact_id TEXT REFERENCES artifacts(artifact_id),
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0),
    fork_parent_generation INTEGER,
    status TEXT NOT NULL CHECK (status IN ('active', 'closed')),
    created_at TEXT NOT NULL
);

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    payload_json TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    transaction_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE generations (
    generation_id TEXT PRIMARY KEY,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    expected_generation INTEGER NOT NULL,
    resulting_generation INTEGER NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('append', 'regenerate')),
    parent_artifact_id TEXT REFERENCES artifacts(artifact_id),
    replaces_artifact_id TEXT REFERENCES artifacts(artifact_id),
    transaction_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE artifacts (
    artifact_id TEXT PRIMARY KEY,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    generation_id TEXT NOT NULL UNIQUE REFERENCES generations(generation_id),
    parent_artifact_id TEXT REFERENCES artifacts(artifact_id),
    source_id TEXT NOT NULL UNIQUE REFERENCES sources(source_id),
    decision_id TEXT NOT NULL,
    accepted_prose TEXT NOT NULL,
    prose_sha256 TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    responding_npc_ids_json TEXT NOT NULL,
    realized_beat_ids_json TEXT NOT NULL,
    validation_receipt_id TEXT NOT NULL,
    transaction_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status = 'accepted'),
    created_at TEXT NOT NULL
);

CREATE TABLE authority_records (
    record_id TEXT PRIMARY KEY,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    record_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    supersedes_json TEXT NOT NULL,
    transaction_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE transaction_journal (
    transaction_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    expected_generation INTEGER NOT NULL,
    expected_head_artifact_id TEXT,
    bundle_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'committed', 'rolled_back')),
    receipt_id TEXT,
    rollback_reason TEXT,
    prepared_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE commit_receipts (
    commit_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL UNIQUE REFERENCES transaction_journal(transaction_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    receipt_json TEXT NOT NULL,
    transaction_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_artifacts_branch_parent ON artifacts(branch_id, parent_artifact_id);
CREATE INDEX idx_records_artifact ON authority_records(artifact_id);
CREATE INDEX idx_journal_status ON transaction_journal(status);

CREATE TRIGGER sources_no_update BEFORE UPDATE ON sources
BEGIN SELECT RAISE(ABORT, 'sources are append-only'); END;
CREATE TRIGGER sources_no_delete BEFORE DELETE ON sources
BEGIN SELECT RAISE(ABORT, 'sources are append-only'); END;
CREATE TRIGGER generations_no_update BEFORE UPDATE ON generations
BEGIN SELECT RAISE(ABORT, 'generations are append-only'); END;
CREATE TRIGGER generations_no_delete BEFORE DELETE ON generations
BEGIN SELECT RAISE(ABORT, 'generations are append-only'); END;
CREATE TRIGGER artifacts_no_update BEFORE UPDATE ON artifacts
BEGIN SELECT RAISE(ABORT, 'artifacts are append-only'); END;
CREATE TRIGGER artifacts_no_delete BEFORE DELETE ON artifacts
BEGIN SELECT RAISE(ABORT, 'artifacts are append-only'); END;
CREATE TRIGGER authority_records_no_update BEFORE UPDATE ON authority_records
BEGIN SELECT RAISE(ABORT, 'authority records are append-only'); END;
CREATE TRIGGER authority_records_no_delete BEFORE DELETE ON authority_records
BEGIN SELECT RAISE(ABORT, 'authority records are append-only'); END;
CREATE TRIGGER commit_receipts_no_update BEFORE UPDATE ON commit_receipts
BEGIN SELECT RAISE(ABORT, 'commit receipts are append-only'); END;
CREATE TRIGGER commit_receipts_no_delete BEFORE DELETE ON commit_receipts
BEGIN SELECT RAISE(ABORT, 'commit receipts are append-only'); END;
"""


_MIGRATION_2 = """
CREATE TABLE genesis_transaction_journal (
    transaction_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    revision_id TEXT NOT NULL UNIQUE,
    transaction_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'committed', 'rolled_back')),
    receipt_id TEXT,
    rollback_reason TEXT,
    prepared_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE genesis_revisions (
    revision_id TEXT PRIMARY KEY,
    revision_number INTEGER NOT NULL UNIQUE CHECK (revision_number > 0),
    parent_revision_id TEXT REFERENCES genesis_revisions(revision_id),
    schema_version TEXT NOT NULL,
    revision_label TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    authorization_id TEXT NOT NULL UNIQUE,
    transaction_id TEXT NOT NULL UNIQUE REFERENCES genesis_transaction_journal(transaction_id),
    created_at TEXT NOT NULL
);

CREATE TABLE genesis_sources (
    revision_id TEXT NOT NULL REFERENCES genesis_revisions(revision_id),
    source_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    PRIMARY KEY (revision_id, source_id),
    UNIQUE (revision_id, relative_path)
);

CREATE TABLE genesis_records (
    revision_id TEXT NOT NULL REFERENCES genesis_revisions(revision_id),
    record_id TEXT NOT NULL UNIQUE,
    record_version INTEGER NOT NULL CHECK (record_version > 0),
    record_type TEXT NOT NULL,
    epistemic_layer TEXT NOT NULL,
    truth_status TEXT NOT NULL,
    claim TEXT NOT NULL,
    owner_id TEXT,
    visibility TEXT NOT NULL,
    certainty TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    PRIMARY KEY (revision_id, record_id)
);

CREATE TABLE genesis_supersessions (
    revision_id TEXT NOT NULL REFERENCES genesis_revisions(revision_id),
    new_record_id TEXT NOT NULL REFERENCES genesis_records(record_id),
    old_record_id TEXT NOT NULL REFERENCES genesis_records(record_id),
    PRIMARY KEY (new_record_id, old_record_id)
);

CREATE TABLE genesis_revision_receipts (
    receipt_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL UNIQUE REFERENCES genesis_transaction_journal(transaction_id),
    revision_id TEXT NOT NULL UNIQUE REFERENCES genesis_revisions(revision_id),
    receipt_json TEXT NOT NULL,
    transaction_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_genesis_records_revision ON genesis_records(revision_id);
CREATE INDEX idx_genesis_records_subject ON genesis_records(record_type, visibility);
CREATE INDEX idx_genesis_journal_status ON genesis_transaction_journal(status);

CREATE TRIGGER genesis_revisions_no_update BEFORE UPDATE ON genesis_revisions
BEGIN SELECT RAISE(ABORT, 'Genesis revisions are append-only'); END;
CREATE TRIGGER genesis_revisions_no_delete BEFORE DELETE ON genesis_revisions
BEGIN SELECT RAISE(ABORT, 'Genesis revisions are append-only'); END;
CREATE TRIGGER genesis_sources_no_update BEFORE UPDATE ON genesis_sources
BEGIN SELECT RAISE(ABORT, 'Genesis sources are append-only'); END;
CREATE TRIGGER genesis_sources_no_delete BEFORE DELETE ON genesis_sources
BEGIN SELECT RAISE(ABORT, 'Genesis sources are append-only'); END;
CREATE TRIGGER genesis_records_no_update BEFORE UPDATE ON genesis_records
BEGIN SELECT RAISE(ABORT, 'Genesis records are append-only'); END;
CREATE TRIGGER genesis_records_no_delete BEFORE DELETE ON genesis_records
BEGIN SELECT RAISE(ABORT, 'Genesis records are append-only'); END;
CREATE TRIGGER genesis_supersessions_no_update BEFORE UPDATE ON genesis_supersessions
BEGIN SELECT RAISE(ABORT, 'Genesis supersessions are append-only'); END;
CREATE TRIGGER genesis_supersessions_no_delete BEFORE DELETE ON genesis_supersessions
BEGIN SELECT RAISE(ABORT, 'Genesis supersessions are append-only'); END;
CREATE TRIGGER genesis_receipts_no_update BEFORE UPDATE ON genesis_revision_receipts
BEGIN SELECT RAISE(ABORT, 'Genesis receipts are append-only'); END;
CREATE TRIGGER genesis_receipts_no_delete BEFORE DELETE ON genesis_revision_receipts
BEGIN SELECT RAISE(ABORT, 'Genesis receipts are append-only'); END;
"""


_MIGRATION_3 = """
CREATE TABLE world_genesis_bindings (
    world_id TEXT PRIMARY KEY REFERENCES worlds(world_id),
    revision_id TEXT NOT NULL REFERENCES genesis_revisions(revision_id),
    authorization_id TEXT NOT NULL,
    bound_at TEXT NOT NULL
);

CREATE TRIGGER world_genesis_bindings_no_update BEFORE UPDATE ON world_genesis_bindings
BEGIN SELECT RAISE(ABORT, 'world Genesis bindings are immutable'); END;
CREATE TRIGGER world_genesis_bindings_no_delete BEFORE DELETE ON world_genesis_bindings
BEGIN SELECT RAISE(ABORT, 'world Genesis bindings are immutable'); END;
"""


_MIGRATION_4 = """
ALTER TABLE genesis_revisions ADD COLUMN package_id TEXT;
ALTER TABLE genesis_revisions ADD COLUMN package_class TEXT NOT NULL DEFAULT 'creator_canon';
ALTER TABLE genesis_revisions ADD COLUMN compiler_contract_version TEXT;
ALTER TABLE genesis_revisions ADD COLUMN world_scope TEXT;
CREATE INDEX idx_genesis_revisions_package ON genesis_revisions(package_id, revision_number);
"""


_MIGRATION_5 = """
CREATE VIRTUAL TABLE evidence_search_fts USING fts5(
    record_key UNINDEXED,
    source_kind UNINDEXED,
    record_id UNINDEXED,
    record_sha256 UNINDEXED,
    searchable_text,
    tokenize = 'unicode61'
);
"""


_MIGRATION_6 = """
CREATE TABLE blocked_turns (
    checkpoint_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    generation_id TEXT NOT NULL UNIQUE,
    source_sha256 TEXT NOT NULL,
    starting_artifact_id TEXT REFERENCES artifacts(artifact_id),
    starting_artifact_sha256 TEXT,
    checkpoint_json TEXT NOT NULL,
    checkpoint_sha256 TEXT NOT NULL UNIQUE,
    rejection_json TEXT NOT NULL,
    external_request_id TEXT NOT NULL UNIQUE,
    external_request_json TEXT NOT NULL,
    external_request_sha256 TEXT NOT NULL,
    callback_token_sha256 TEXT NOT NULL,
    allowed_event_registry_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'awaiting_external_receipt',
        'receipt_received',
        'receipt_validated_pending',
        'aftermath_decided',
        'aftermath_validated',
        'accepted'
    )),
    accepted_receipt_id TEXT,
    transaction_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE temporary_aftermath_projections (
    projection_id TEXT PRIMARY KEY,
    checkpoint_id TEXT NOT NULL UNIQUE REFERENCES blocked_turns(checkpoint_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    projection_json TEXT NOT NULL,
    projection_sha256 TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE external_receipt_claims (
    receipt_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    external_request_id TEXT NOT NULL UNIQUE,
    checkpoint_id TEXT NOT NULL UNIQUE REFERENCES blocked_turns(checkpoint_id),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'receipt_received',
        'receipt_validated_pending',
        'aftermath_decided',
        'aftermath_validated',
        'committed'
    )),
    validation_receipt_json TEXT,
    aftermath_decision_json TEXT,
    aftermath_decision_sha256 TEXT,
    aftermath_reasoner_receipt_json TEXT,
    commit_bundle_json TEXT,
    commit_bundle_sha256 TEXT,
    transaction_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX idx_blocked_turns_status ON blocked_turns(status, updated_at);
CREATE INDEX idx_external_receipts_status ON external_receipt_claims(status, updated_at);

CREATE TRIGGER blocked_turns_immutable_payload BEFORE UPDATE OF
    request_id, branch_id, generation_id, source_sha256, starting_artifact_id,
    starting_artifact_sha256, checkpoint_json, checkpoint_sha256, rejection_json,
    external_request_id, external_request_json, external_request_sha256,
    callback_token_sha256, allowed_event_registry_version
ON blocked_turns
BEGIN SELECT RAISE(ABORT, 'blocked-turn identity and payload are immutable'); END;

CREATE TRIGGER blocked_turns_no_delete BEFORE DELETE ON blocked_turns
BEGIN SELECT RAISE(ABORT, 'blocked-turn audit records are append-only'); END;

CREATE TRIGGER external_receipts_immutable_claim BEFORE UPDATE OF
    idempotency_key, external_request_id, checkpoint_id, receipt_json, receipt_sha256
ON external_receipt_claims
BEGIN SELECT RAISE(ABORT, 'external receipt claims are immutable'); END;

CREATE TRIGGER external_receipts_no_delete BEFORE DELETE ON external_receipt_claims
BEGIN SELECT RAISE(ABORT, 'external receipt claims are append-only'); END;
"""


_MIGRATION_7 = """
ALTER TABLE branches ADD COLUMN authority_revision INTEGER NOT NULL DEFAULT 0
    CHECK (authority_revision >= 0);

CREATE TABLE consolidation_journal (
    transaction_id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL UNIQUE,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    expected_generation INTEGER NOT NULL CHECK (expected_generation >= 0),
    expected_head_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    expected_authority_revision INTEGER NOT NULL CHECK (expected_authority_revision >= 0),
    genesis_revision_id TEXT NOT NULL REFERENCES genesis_revisions(revision_id),
    snapshot_token TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    proposal_sha256 TEXT NOT NULL,
    bundle_json TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('prepared', 'committed', 'rolled_back')),
    receipt_id TEXT,
    rollback_reason TEXT,
    prepared_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE consolidation_receipts (
    commit_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL UNIQUE REFERENCES consolidation_journal(transaction_id),
    request_id TEXT NOT NULL UNIQUE,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    receipt_json TEXT NOT NULL,
    transaction_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE derived_views (
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    head_artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    authority_revision INTEGER NOT NULL CHECK (authority_revision >= 0),
    view_key TEXT NOT NULL,
    view_kind TEXT NOT NULL CHECK (
        view_kind IN ('public_summary', 'owner_summary', 'system_index')
    ),
    visibility TEXT NOT NULL CHECK (
        visibility IN ('public', 'owner_private', 'system_private')
    ),
    owner_id TEXT,
    payload_json TEXT NOT NULL,
    source_set_sha256 TEXT NOT NULL,
    view_sha256 TEXT NOT NULL,
    built_at TEXT NOT NULL,
    PRIMARY KEY (branch_id, head_artifact_id, authority_revision, view_key)
);

CREATE INDEX idx_consolidation_status
    ON consolidation_journal(status, branch_id, prepared_at);
CREATE INDEX idx_derived_views_branch
    ON derived_views(branch_id, head_artifact_id, authority_revision, view_kind);

CREATE TRIGGER consolidation_journal_immutable_payload BEFORE UPDATE OF
    idempotency_key, request_id, branch_id, expected_generation,
    expected_head_artifact_id, expected_authority_revision, genesis_revision_id,
    snapshot_token, request_sha256, proposal_sha256, bundle_json, bundle_sha256
ON consolidation_journal
BEGIN SELECT RAISE(ABORT, 'consolidation identity and payload are immutable'); END;

CREATE TRIGGER consolidation_journal_no_delete BEFORE DELETE ON consolidation_journal
BEGIN SELECT RAISE(ABORT, 'consolidation journal is append-only'); END;

CREATE TRIGGER consolidation_receipts_no_update BEFORE UPDATE ON consolidation_receipts
BEGIN SELECT RAISE(ABORT, 'consolidation receipts are append-only'); END;
CREATE TRIGGER consolidation_receipts_no_delete BEFORE DELETE ON consolidation_receipts
BEGIN SELECT RAISE(ABORT, 'consolidation receipts are append-only'); END;
"""


_MIGRATION_8 = """
CREATE TABLE turn_receipt_records (
    receipt_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL REFERENCES transaction_journal(transaction_id),
    category TEXT NOT NULL CHECK (category IN ('validation', 'lookup', 'provider')),
    schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_turn_receipts_transaction
ON turn_receipt_records(transaction_id, category, receipt_id);

CREATE TRIGGER turn_receipt_records_no_update BEFORE UPDATE ON turn_receipt_records
BEGIN SELECT RAISE(ABORT, 'turn receipt records are append-only'); END;
CREATE TRIGGER turn_receipt_records_no_delete BEFORE DELETE ON turn_receipt_records
BEGIN SELECT RAISE(ABORT, 'turn receipt records are append-only'); END;
"""


_MIGRATION_9 = """
CREATE TABLE consolidation_receipt_records (
    receipt_id TEXT PRIMARY KEY,
    transaction_id TEXT NOT NULL REFERENCES consolidation_journal(transaction_id),
    category TEXT NOT NULL CHECK (category IN ('validation', 'lookup', 'provider')),
    schema_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_consolidation_receipt_records_transaction
ON consolidation_receipt_records(transaction_id, category, receipt_id);

CREATE TRIGGER consolidation_receipt_records_no_update
BEFORE UPDATE ON consolidation_receipt_records
BEGIN SELECT RAISE(ABORT, 'consolidation receipt records are append-only'); END;
CREATE TRIGGER consolidation_receipt_records_no_delete
BEFORE DELETE ON consolidation_receipt_records
BEGIN SELECT RAISE(ABORT, 'consolidation receipt records are append-only'); END;
"""


_MIGRATION_10 = """
CREATE TABLE post_publication_work (
    work_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    work_kind TEXT NOT NULL CHECK (
        work_kind IN ('render', 'consolidate', 'derived_views')
    ),
    request_json TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    depends_on_work_id TEXT REFERENCES post_publication_work(work_id),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'running', 'completed', 'no_change', 'failed')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    result_json TEXT,
    result_sha256 TEXT,
    error_json TEXT,
    error_sha256 TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(artifact_id, work_kind, request_sha256),
    CHECK ((result_json IS NULL) = (result_sha256 IS NULL)),
    CHECK ((error_json IS NULL) = (error_sha256 IS NULL))
);

CREATE INDEX idx_post_publication_work_artifact
ON post_publication_work(artifact_id, work_kind, work_id);
CREATE INDEX idx_post_publication_work_status
ON post_publication_work(status, created_at, work_id);

CREATE TABLE post_publication_attempts (
    work_id TEXT NOT NULL REFERENCES post_publication_work(work_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
    authorization_reason_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'no_change', 'failed')),
    result_sha256 TEXT,
    error_sha256 TEXT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    PRIMARY KEY(work_id, attempt_number)
);

CREATE TRIGGER post_publication_work_identity_immutable
BEFORE UPDATE OF artifact_id, branch_id, work_kind, request_json, request_sha256,
                 depends_on_work_id, created_at
ON post_publication_work
BEGIN SELECT RAISE(ABORT, 'post-publication work identity and request are immutable'); END;

CREATE TRIGGER post_publication_work_no_delete
BEFORE DELETE ON post_publication_work
BEGIN SELECT RAISE(ABORT, 'post-publication work journal is append-only'); END;

CREATE TRIGGER post_publication_attempt_identity_immutable
BEFORE UPDATE OF work_id, attempt_number, authorization_reason_sha256, started_at
ON post_publication_attempts
BEGIN SELECT RAISE(ABORT, 'post-publication attempt identity is immutable'); END;
CREATE TRIGGER post_publication_attempt_terminal_immutable
BEFORE UPDATE ON post_publication_attempts
WHEN OLD.status != 'running'
BEGIN SELECT RAISE(ABORT, 'completed post-publication attempts are immutable'); END;
CREATE TRIGGER post_publication_attempts_no_delete
BEFORE DELETE ON post_publication_attempts
BEGIN SELECT RAISE(ABORT, 'post-publication attempts are append-only'); END;
"""


_MIGRATION_11 = """
CREATE TABLE turn_failure_evidence (
    failure_bundle_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    error_code TEXT NOT NULL,
    bundle_json TEXT NOT NULL,
    bundle_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE turn_stage_journal (
    journal_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    stage TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
    input_sha256 TEXT,
    output_sha256 TEXT,
    failure_bundle_id TEXT REFERENCES turn_failure_evidence(failure_bundle_id),
    external_provider_calls_observed INTEGER NOT NULL CHECK (
        external_provider_calls_observed >= 0
    ),
    entry_json TEXT NOT NULL,
    entry_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(request_id, sequence),
    CHECK (
        (status = 'failed' AND failure_bundle_id IS NOT NULL)
        OR (status != 'failed' AND failure_bundle_id IS NULL)
    )
);

CREATE INDEX idx_turn_stage_journal_request
ON turn_stage_journal(request_id, sequence);

CREATE TRIGGER turn_failure_evidence_no_update
BEFORE UPDATE ON turn_failure_evidence
BEGIN SELECT RAISE(ABORT, 'turn failure evidence is append-only'); END;
CREATE TRIGGER turn_failure_evidence_no_delete
BEFORE DELETE ON turn_failure_evidence
BEGIN SELECT RAISE(ABORT, 'turn failure evidence is append-only'); END;
CREATE TRIGGER turn_stage_journal_no_update
BEFORE UPDATE ON turn_stage_journal
BEGIN SELECT RAISE(ABORT, 'turn stage journal is append-only'); END;
CREATE TRIGGER turn_stage_journal_no_delete
BEFORE DELETE ON turn_stage_journal
BEGIN SELECT RAISE(ABORT, 'turn stage journal is append-only'); END;
"""


_MIGRATION_12 = """
CREATE TABLE creator_review_records (
    review_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(world_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    request_id TEXT NOT NULL,
    generation_id TEXT NOT NULL,
    expected_generation INTEGER NOT NULL CHECK (expected_generation >= 0),
    expected_head_artifact_id TEXT REFERENCES artifacts(artifact_id),
    candidate_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN (
        'provisional_visible',
        'verifying_and_preparing',
        'review_ready',
        'awaiting_feedback',
        'committing',
        'accepted',
        'rejected',
        'error'
    )),
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE UNIQUE INDEX idx_creator_review_one_unresolved_branch
ON creator_review_records(branch_id)
WHERE state IN (
    'provisional_visible',
    'verifying_and_preparing',
    'review_ready',
    'awaiting_feedback',
    'committing',
    'error'
);

CREATE INDEX idx_creator_review_request
ON creator_review_records(request_id, review_id);

CREATE INDEX idx_creator_review_state
ON creator_review_records(state, updated_at, review_id);

CREATE TRIGGER creator_review_identity_immutable
BEFORE UPDATE OF review_id, world_id, branch_id, request_id, generation_id,
                 expected_generation, expected_head_artifact_id,
                 candidate_sha256, created_at
ON creator_review_records
BEGIN SELECT RAISE(ABORT, 'creator-review identity and bindings are immutable'); END;

CREATE TRIGGER creator_review_terminal_immutable
BEFORE UPDATE ON creator_review_records
WHEN OLD.state IN ('accepted', 'rejected')
BEGIN SELECT RAISE(ABORT, 'resolved creator reviews are immutable'); END;

CREATE TRIGGER creator_review_no_delete
BEFORE DELETE ON creator_review_records
BEGIN SELECT RAISE(ABORT, 'creator-review audit rows cannot be deleted'); END;
"""


_MIGRATION_13 = """
DROP INDEX idx_creator_review_one_unresolved_branch;

CREATE UNIQUE INDEX idx_creator_review_one_active_candidate_branch
ON creator_review_records(branch_id)
WHERE state IN (
    'provisional_visible',
    'verifying_and_preparing',
    'review_ready',
    'committing',
    'error'
);
"""


_MIGRATION_14 = """
CREATE TABLE creator_correction_diagnostics (
    diagnostic_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL REFERENCES creator_review_records(review_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    issue_owner TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_creator_correction_diagnostic_branch
ON creator_correction_diagnostics(branch_id, created_at, diagnostic_id);

CREATE INDEX idx_creator_correction_diagnostic_owner
ON creator_correction_diagnostics(issue_owner, created_at, diagnostic_id);

CREATE TRIGGER creator_correction_diagnostic_no_update
BEFORE UPDATE ON creator_correction_diagnostics
BEGIN SELECT RAISE(ABORT, 'creator correction diagnostics are append-only'); END;

CREATE TRIGGER creator_correction_diagnostic_no_delete
BEFORE DELETE ON creator_correction_diagnostics
BEGIN SELECT RAISE(ABORT, 'creator correction diagnostics are append-only'); END;
"""


_MIGRATION_15 = """
CREATE TABLE creator_accept_timing_receipts (
    receipt_id TEXT PRIMARY KEY,
    review_id TEXT NOT NULL UNIQUE REFERENCES creator_review_records(review_id),
    package_id TEXT NOT NULL,
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    accept_to_commit_microseconds INTEGER NOT NULL CHECK (
        accept_to_commit_microseconds >= 0
    ),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    started_at TEXT NOT NULL,
    committed_at TEXT NOT NULL
);

CREATE INDEX idx_creator_accept_timing_branch
ON creator_accept_timing_receipts(branch_id, committed_at, receipt_id);

CREATE TRIGGER creator_accept_timing_no_update
BEFORE UPDATE ON creator_accept_timing_receipts
BEGIN SELECT RAISE(ABORT, 'creator accept timing receipts are append-only'); END;

CREATE TRIGGER creator_accept_timing_no_delete
BEFORE DELETE ON creator_accept_timing_receipts
BEGIN SELECT RAISE(ABORT, 'creator accept timing receipts are append-only'); END;
"""


_MIGRATION_16 = """
CREATE TABLE reasoner_sessions (
    session_id TEXT PRIMARY KEY,
    world_id TEXT NOT NULL REFERENCES worlds(world_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    role TEXT NOT NULL,
    compatibility_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'rotated', 'invalidated')),
    accepted_checkpoint_id TEXT NOT NULL,
    provider_session_id TEXT NOT NULL,
    provider_root_thread_id TEXT NOT NULL,
    accumulated_turns INTEGER NOT NULL CHECK (accumulated_turns >= 0),
    rotated_from_session_id TEXT REFERENCES reasoner_sessions(session_id),
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_reasoner_session_one_active_compatibility
ON reasoner_sessions(branch_id, role)
WHERE status = 'active';

CREATE INDEX idx_reasoner_session_branch_status
ON reasoner_sessions(branch_id, status, updated_at, session_id);

CREATE TABLE reasoner_session_checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES reasoner_sessions(session_id),
    parent_checkpoint_id TEXT REFERENCES reasoner_session_checkpoints(checkpoint_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    request_id TEXT,
    review_id TEXT REFERENCES creator_review_records(review_id),
    turn_mode TEXT CHECK (turn_mode IN ('append', 'regenerate')),
    replaces_artifact_id TEXT REFERENCES artifacts(artifact_id),
    status TEXT NOT NULL CHECK (status IN ('accepted', 'candidate', 'rejected', 'invalidated')),
    accepted_head_artifact_id TEXT REFERENCES artifacts(artifact_id),
    generation INTEGER NOT NULL CHECK (generation >= 0),
    authority_revision INTEGER NOT NULL CHECK (authority_revision >= 0),
    provider_session_id TEXT NOT NULL,
    provider_thread_id TEXT NOT NULL,
    context_manifest_sha256 TEXT NOT NULL,
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_reasoner_session_one_candidate
ON reasoner_session_checkpoints(session_id)
WHERE status = 'candidate';

CREATE INDEX idx_reasoner_checkpoint_parent
ON reasoner_session_checkpoints(parent_checkpoint_id, status, checkpoint_id);

CREATE INDEX idx_reasoner_checkpoint_request
ON reasoner_session_checkpoints(request_id, checkpoint_id);

CREATE TABLE creator_constraints (
    constraint_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('branch', 'global')),
    world_id TEXT NOT NULL REFERENCES worlds(world_id),
    branch_id TEXT REFERENCES branches(branch_id),
    source_review_id TEXT NOT NULL REFERENCES creator_review_records(review_id),
    source_diagnostic_id TEXT NOT NULL REFERENCES creator_correction_diagnostics(diagnostic_id),
    owner TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'superseded')),
    superseded_by_constraint_id TEXT REFERENCES creator_constraints(constraint_id),
    record_json TEXT NOT NULL,
    record_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((scope = 'branch' AND branch_id IS NOT NULL) OR
           (scope = 'global' AND branch_id IS NULL))
);

CREATE INDEX idx_creator_constraint_active_branch
ON creator_constraints(world_id, branch_id, owner, created_at, constraint_id)
WHERE status = 'active';

CREATE TABLE reasoner_accepted_turn_receipts (
    receipt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES reasoner_sessions(session_id),
    checkpoint_id TEXT NOT NULL UNIQUE REFERENCES reasoner_session_checkpoints(checkpoint_id),
    review_id TEXT NOT NULL REFERENCES creator_review_records(review_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE reasoner_rejected_candidate_receipts (
    receipt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES reasoner_sessions(session_id),
    checkpoint_id TEXT NOT NULL UNIQUE REFERENCES reasoner_session_checkpoints(checkpoint_id),
    review_id TEXT NOT NULL REFERENCES creator_review_records(review_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE reasoner_session_usage_receipts (
    receipt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES reasoner_sessions(session_id),
    checkpoint_id TEXT NOT NULL REFERENCES reasoner_session_checkpoints(checkpoint_id),
    branch_id TEXT NOT NULL REFERENCES branches(branch_id),
    receipt_json TEXT NOT NULL,
    receipt_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_reasoner_usage_session
ON reasoner_session_usage_receipts(session_id, created_at, receipt_id);

CREATE TRIGGER reasoner_session_identity_immutable
BEFORE UPDATE OF session_id, world_id, branch_id, role, compatibility_sha256,
                 provider_session_id, provider_root_thread_id,
                 rotated_from_session_id, created_at
ON reasoner_sessions
BEGIN SELECT RAISE(ABORT, 'reasoner session identity is immutable'); END;

CREATE TRIGGER reasoner_session_terminal_immutable
BEFORE UPDATE ON reasoner_sessions
WHEN OLD.status IN ('rotated', 'invalidated')
BEGIN SELECT RAISE(ABORT, 'terminal reasoner session is immutable'); END;

CREATE TRIGGER reasoner_session_no_delete
BEFORE DELETE ON reasoner_sessions
BEGIN SELECT RAISE(ABORT, 'reasoner sessions cannot be deleted'); END;

CREATE TRIGGER reasoner_checkpoint_identity_immutable
BEFORE UPDATE OF checkpoint_id, session_id, parent_checkpoint_id, branch_id,
                 request_id, turn_mode, replaces_artifact_id, created_at
ON reasoner_session_checkpoints
BEGIN SELECT RAISE(ABORT, 'reasoner checkpoint authority bindings are immutable'); END;

CREATE TRIGGER reasoner_checkpoint_terminal_immutable
BEFORE UPDATE ON reasoner_session_checkpoints
WHEN OLD.status IN ('accepted', 'rejected', 'invalidated')
BEGIN SELECT RAISE(ABORT, 'terminal reasoner checkpoint is immutable'); END;

CREATE TRIGGER reasoner_checkpoint_no_delete
BEFORE DELETE ON reasoner_session_checkpoints
BEGIN SELECT RAISE(ABORT, 'reasoner checkpoints cannot be deleted'); END;

CREATE TRIGGER creator_constraint_identity_immutable
BEFORE UPDATE OF constraint_id, scope, world_id, branch_id, source_review_id,
                 source_diagnostic_id, owner, created_at
ON creator_constraints
BEGIN SELECT RAISE(ABORT, 'creator constraint identity is immutable'); END;

CREATE TRIGGER creator_constraint_terminal_immutable
BEFORE UPDATE ON creator_constraints
WHEN OLD.status = 'superseded'
BEGIN SELECT RAISE(ABORT, 'superseded creator constraint is immutable'); END;

CREATE TRIGGER creator_constraint_no_delete
BEFORE DELETE ON creator_constraints
BEGIN SELECT RAISE(ABORT, 'creator constraints cannot be deleted'); END;

CREATE TRIGGER reasoner_accepted_receipt_no_update
BEFORE UPDATE ON reasoner_accepted_turn_receipts
BEGIN SELECT RAISE(ABORT, 'accepted turn session receipts are append-only'); END;
CREATE TRIGGER reasoner_accepted_receipt_no_delete
BEFORE DELETE ON reasoner_accepted_turn_receipts
BEGIN SELECT RAISE(ABORT, 'accepted turn session receipts are append-only'); END;
CREATE TRIGGER reasoner_rejected_receipt_no_update
BEFORE UPDATE ON reasoner_rejected_candidate_receipts
BEGIN SELECT RAISE(ABORT, 'rejected candidate receipts are append-only'); END;
CREATE TRIGGER reasoner_rejected_receipt_no_delete
BEFORE DELETE ON reasoner_rejected_candidate_receipts
BEGIN SELECT RAISE(ABORT, 'rejected candidate receipts are append-only'); END;
CREATE TRIGGER reasoner_usage_receipt_no_update
BEFORE UPDATE ON reasoner_session_usage_receipts
BEGIN SELECT RAISE(ABORT, 'reasoner usage receipts are append-only'); END;
CREATE TRIGGER reasoner_usage_receipt_no_delete
BEFORE DELETE ON reasoner_session_usage_receipts
BEGIN SELECT RAISE(ABORT, 'reasoner usage receipts are append-only'); END;
"""


_MIGRATION_17 = """
CREATE TABLE reasoner_provider_thread_custody_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES reasoner_sessions(session_id),
    checkpoint_id TEXT NOT NULL REFERENCES reasoner_session_checkpoints(checkpoint_id),
    event_kind TEXT NOT NULL CHECK (event_kind IN (
        'allocated', 'resumed', 'accepted', 'rejected_deleted',
        'failed_deleted', 'archived', 'missing'
    )),
    provider_thread_id_sha256 TEXT NOT NULL,
    parent_provider_thread_id_sha256 TEXT,
    storage_mode TEXT NOT NULL CHECK (storage_mode = 'stored_local'),
    raw_context_retained INTEGER NOT NULL CHECK (raw_context_retained IN (0, 1)),
    provider_context_is_story_authority INTEGER NOT NULL
        CHECK (provider_context_is_story_authority = 0),
    event_json TEXT NOT NULL,
    event_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_reasoner_thread_custody_checkpoint
ON reasoner_provider_thread_custody_events(checkpoint_id, created_at, event_id);

CREATE INDEX idx_reasoner_thread_custody_session
ON reasoner_provider_thread_custody_events(session_id, created_at, event_id);

CREATE TRIGGER reasoner_thread_custody_no_update
BEFORE UPDATE ON reasoner_provider_thread_custody_events
BEGIN SELECT RAISE(ABORT, 'provider thread custody events are append-only'); END;

CREATE TRIGGER reasoner_thread_custody_no_delete
BEFORE DELETE ON reasoner_provider_thread_custody_events
BEGIN SELECT RAISE(ABORT, 'provider thread custody events are append-only'); END;
"""


_MIGRATION_18 = """
ALTER TABLE reasoner_provider_thread_custody_events
RENAME TO reasoner_provider_thread_custody_events_v17;

DROP INDEX idx_reasoner_thread_custody_checkpoint;
DROP INDEX idx_reasoner_thread_custody_session;
DROP TRIGGER reasoner_thread_custody_no_update;
DROP TRIGGER reasoner_thread_custody_no_delete;

CREATE TABLE reasoner_provider_thread_custody_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES reasoner_sessions(session_id),
    checkpoint_id TEXT NOT NULL REFERENCES reasoner_session_checkpoints(checkpoint_id),
    event_kind TEXT NOT NULL CHECK (event_kind IN (
        'allocated', 'resumed', 'accepted', 'rejected_deleted',
        'failed_deleted', 'rejected_archived', 'failed_archived',
        'archived', 'missing'
    )),
    provider_thread_id_sha256 TEXT NOT NULL,
    parent_provider_thread_id_sha256 TEXT,
    storage_mode TEXT NOT NULL CHECK (storage_mode = 'stored_local'),
    raw_context_retained INTEGER NOT NULL CHECK (raw_context_retained IN (0, 1)),
    provider_context_is_story_authority INTEGER NOT NULL
        CHECK (provider_context_is_story_authority = 0),
    event_json TEXT NOT NULL,
    event_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO reasoner_provider_thread_custody_events
SELECT * FROM reasoner_provider_thread_custody_events_v17;

DROP TABLE reasoner_provider_thread_custody_events_v17;

CREATE INDEX idx_reasoner_thread_custody_checkpoint
ON reasoner_provider_thread_custody_events(checkpoint_id, created_at, event_id);

CREATE INDEX idx_reasoner_thread_custody_session
ON reasoner_provider_thread_custody_events(session_id, created_at, event_id);

CREATE TRIGGER reasoner_thread_custody_no_update
BEFORE UPDATE ON reasoner_provider_thread_custody_events
BEGIN SELECT RAISE(ABORT, 'provider thread custody events are append-only'); END;

CREATE TRIGGER reasoner_thread_custody_no_delete
BEFORE DELETE ON reasoner_provider_thread_custody_events
BEGIN SELECT RAISE(ABORT, 'provider thread custody events are append-only'); END;
"""


_MIGRATION_19 = """
CREATE TABLE provider_stage_retry_chains (
    chain_id TEXT PRIMARY KEY,
    logical_key_sha256 TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL CHECK (provider IN ('codex', 'deepseek')),
    model_family TEXT NOT NULL CHECK (model_family IN ('sol', 'luna', 'deepseek_v4')),
    stage TEXT NOT NULL CHECK (stage IN (
        'planner', 'semantic_validator', 'writer', 'recorder',
        'adult_scene', 'adult_filter'
    )),
    request_occurrence_sha256 TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    stage_input_sha256 TEXT NOT NULL,
    authority_sha256 TEXT NOT NULL,
    story_state_committed INTEGER NOT NULL CHECK (story_state_committed IN (0, 1)),
    identity_json TEXT NOT NULL,
    identity_sha256 TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN (
        'input_frozen', 'attempt_prepared', 'dispatch_started',
        'awaiting_owner_retirement', 'owner_retired', 'result_frozen',
        'downstream_intent_frozen', 'downstream_bound', 'succeeded',
        'exhausted', 'blocked_ambiguous', 'recording_repair_required',
        'recovery_required'
    )),
    input_checkpoint_sha256 TEXT NOT NULL,
    result_checkpoint_sha256 TEXT,
    downstream_intent_sha256 TEXT,
    downstream_evidence_sha256 TEXT,
    block_reason TEXT CHECK (block_reason IS NULL OR block_reason IN (
        'input_changed', 'authority_changed', 'ledger_prefix_changed',
        'owner_retirement_unproven', 'result_checkpoint_conflict',
        'dispatch_custody_ambiguous'
    )),
    block_evidence_sha256 TEXT,
    state_version INTEGER NOT NULL DEFAULT 1 CHECK (state_version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (stage = 'planner' AND provider = 'codex' AND model_family = 'sol') OR
        (stage = 'semantic_validator' AND provider = 'codex' AND model_family = 'luna') OR
        (stage IN ('writer', 'recorder', 'adult_scene', 'adult_filter')
            AND provider = 'deepseek' AND model_family = 'deepseek_v4')
    ),
    CHECK ((stage = 'recorder') = story_state_committed),
    CHECK ((block_reason IS NULL) = (block_evidence_sha256 IS NULL)),
    CHECK (
        (phase = 'blocked_ambiguous'
            AND block_reason = 'dispatch_custody_ambiguous') OR
        (phase = 'recovery_required'
            AND (block_reason IS NULL OR block_reason != 'dispatch_custody_ambiguous')) OR
        (phase NOT IN ('blocked_ambiguous', 'recovery_required')
            AND block_reason IS NULL)
    )
);

CREATE TABLE provider_stage_retry_actions (
    retry_action_sha256 TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL REFERENCES provider_stage_retry_chains(chain_id),
    action_kind TEXT NOT NULL CHECK (action_kind = 'provider_retry'),
    prior_attempt_number INTEGER NOT NULL CHECK (prior_attempt_number BETWEEN 1 AND 2),
    resulting_attempt_number INTEGER NOT NULL CHECK (resulting_attempt_number BETWEEN 2 AND 3),
    session_scope_sha256 TEXT NOT NULL,
    ledger_prefix_before_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(chain_id, resulting_attempt_number),
    CHECK (resulting_attempt_number = prior_attempt_number + 1)
);

CREATE TABLE provider_stage_retry_occurrence_scopes (
    chain_id TEXT PRIMARY KEY REFERENCES provider_stage_retry_chains(chain_id),
    scope_json TEXT NOT NULL,
    scope_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE provider_stage_retry_checkpoints (
    checkpoint_sha256 TEXT PRIMARY KEY,
    chain_id TEXT NOT NULL REFERENCES provider_stage_retry_chains(chain_id),
    checkpoint_kind TEXT NOT NULL CHECK (checkpoint_kind IN ('input', 'result')),
    attempt_number INTEGER CHECK (attempt_number BETWEEN 1 AND 3),
    blob_id_sha256 TEXT NOT NULL UNIQUE,
    blob_custody_sha256 TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK (size_bytes BETWEEN 1 AND 9007199254740991),
    evidence_sha256 TEXT NOT NULL,
    checkpoint_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (checkpoint_kind = 'input' AND attempt_number IS NULL) OR
        (checkpoint_kind = 'result' AND attempt_number IS NOT NULL)
    )
);

CREATE UNIQUE INDEX idx_provider_stage_retry_input_checkpoint
ON provider_stage_retry_checkpoints(chain_id)
WHERE checkpoint_kind = 'input';

CREATE UNIQUE INDEX idx_provider_stage_retry_result_checkpoint
ON provider_stage_retry_checkpoints(chain_id, attempt_number)
WHERE checkpoint_kind = 'result';

CREATE TABLE provider_stage_retry_attempts (
    chain_id TEXT NOT NULL REFERENCES provider_stage_retry_chains(chain_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number BETWEEN 1 AND 3),
    retry_action_sha256 TEXT REFERENCES provider_stage_retry_actions(retry_action_sha256),
    phase TEXT NOT NULL CHECK (phase IN (
        'prepared', 'dispatch_started', 'failed', 'owner_retired', 'result_frozen'
    )),
    session_scope_sha256 TEXT NOT NULL,
    ledger_prefix_before_sha256 TEXT NOT NULL,
    invocation_reserved INTEGER NOT NULL DEFAULT 0 CHECK (invocation_reserved IN (0, 1)),
    dispatch_evidence_sha256 TEXT,
    ledger_prefix_after_sha256 TEXT,
    provider_operations_observed INTEGER NOT NULL DEFAULT 0
        CHECK (provider_operations_observed BETWEEN 0 AND 9007199254740991),
    provider_operations_conservative INTEGER NOT NULL DEFAULT 0
        CHECK (provider_operations_conservative BETWEEN 0 AND 9007199254740991),
    duration_ms INTEGER CHECK (duration_ms BETWEEN 0 AND 9007199254740991),
    input_tokens INTEGER CHECK (input_tokens BETWEEN 0 AND 9007199254740991),
    cached_input_tokens INTEGER CHECK (cached_input_tokens BETWEEN 0 AND 9007199254740991),
    output_tokens INTEGER CHECK (output_tokens BETWEEN 0 AND 9007199254740991),
    reasoning_tokens INTEGER CHECK (reasoning_tokens BETWEEN 0 AND 9007199254740991),
    failure_class TEXT CHECK (failure_class IS NULL OR failure_class IN (
        'transport_timeout', 'provider_unavailable', 'provider_process_failed',
        'provider_stream_incomplete', 'provider_completion_incomplete',
        'provider_output_invalid', 'dispatch_ambiguous',
        'authentication_failed', 'invalid_request', 'context_size_exceeded',
        'unsupported_parameter', 'output_limit_truncated', 'custody_failed',
        'configuration_failed', 'budget_exhausted',
        'provider_failure_not_retryable'
    )),
    failure_evidence_sha256 TEXT,
    owner_retirement_evidence_sha256 TEXT,
    result_checkpoint_sha256 TEXT REFERENCES provider_stage_retry_checkpoints(checkpoint_sha256),
    attempt_json TEXT NOT NULL,
    attempt_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(chain_id, attempt_number),
    UNIQUE(retry_action_sha256),
    CHECK (
        (attempt_number = 1 AND retry_action_sha256 IS NULL) OR
        (attempt_number > 1 AND retry_action_sha256 IS NOT NULL)
    ),
    CHECK (provider_operations_conservative >= provider_operations_observed),
    CHECK (cached_input_tokens IS NULL OR (
        input_tokens IS NOT NULL AND cached_input_tokens <= input_tokens
    ))
);

CREATE UNIQUE INDEX idx_provider_stage_retry_one_live_attempt
ON provider_stage_retry_attempts(chain_id)
WHERE phase IN ('prepared', 'dispatch_started', 'failed');

CREATE TABLE provider_stage_retry_events (
    chain_id TEXT NOT NULL REFERENCES provider_stage_retry_chains(chain_id),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    event_kind TEXT NOT NULL,
    event_json TEXT NOT NULL,
    event_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(chain_id, sequence)
);

CREATE INDEX idx_provider_stage_retry_phase
ON provider_stage_retry_chains(phase, updated_at, chain_id);

CREATE TRIGGER provider_stage_retry_chain_identity_immutable
BEFORE UPDATE OF chain_id, logical_key_sha256, provider, model_family, stage,
    request_occurrence_sha256, request_sha256, stage_input_sha256,
    authority_sha256, story_state_committed, identity_json, identity_sha256,
    input_checkpoint_sha256, created_at
ON provider_stage_retry_chains
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry chain identity is immutable'); END;

CREATE TRIGGER provider_stage_retry_chain_terminal_immutable
BEFORE UPDATE ON provider_stage_retry_chains
WHEN OLD.phase IN (
    'succeeded', 'exhausted', 'recording_repair_required', 'recovery_required'
)
BEGIN SELECT RAISE(ABORT, 'terminal provider-stage Retry chain is immutable'); END;

CREATE TRIGGER provider_stage_retry_chains_no_delete
BEFORE DELETE ON provider_stage_retry_chains
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry chains cannot be deleted'); END;

CREATE TRIGGER provider_stage_retry_occurrence_scopes_no_update
BEFORE UPDATE ON provider_stage_retry_occurrence_scopes
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry occurrence scopes are immutable'); END;
CREATE TRIGGER provider_stage_retry_occurrence_scopes_no_delete
BEFORE DELETE ON provider_stage_retry_occurrence_scopes
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry occurrence scopes cannot be deleted'); END;

CREATE TRIGGER provider_stage_retry_actions_no_update
BEFORE UPDATE ON provider_stage_retry_actions
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry actions are append-only'); END;
CREATE TRIGGER provider_stage_retry_actions_no_delete
BEFORE DELETE ON provider_stage_retry_actions
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry actions are append-only'); END;

CREATE TRIGGER provider_stage_retry_checkpoints_no_update
BEFORE UPDATE ON provider_stage_retry_checkpoints
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry checkpoints are append-only'); END;
CREATE TRIGGER provider_stage_retry_checkpoints_no_delete
BEFORE DELETE ON provider_stage_retry_checkpoints
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry checkpoints are append-only'); END;

CREATE TRIGGER provider_stage_retry_attempt_identity_immutable
BEFORE UPDATE OF chain_id, attempt_number, retry_action_sha256,
    session_scope_sha256, ledger_prefix_before_sha256, created_at
ON provider_stage_retry_attempts
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry attempt identity is immutable'); END;

CREATE TRIGGER provider_stage_retry_attempt_terminal_immutable
BEFORE UPDATE ON provider_stage_retry_attempts
WHEN OLD.phase IN ('owner_retired', 'result_frozen')
    AND NOT (
        OLD.phase = 'owner_retired'
        AND OLD.failure_class = 'dispatch_ambiguous'
        AND NEW.phase IN ('owner_retired', 'result_frozen')
    )
BEGIN SELECT RAISE(ABORT, 'terminal provider-stage Retry attempt is immutable'); END;

CREATE TRIGGER provider_stage_retry_attempts_no_delete
BEFORE DELETE ON provider_stage_retry_attempts
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry attempts cannot be deleted'); END;

CREATE TRIGGER provider_stage_retry_events_no_update
BEFORE UPDATE ON provider_stage_retry_events
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry events are append-only'); END;
CREATE TRIGGER provider_stage_retry_events_no_delete
BEFORE DELETE ON provider_stage_retry_events
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry events are append-only'); END;
"""


_MIGRATION_20 = """
CREATE TABLE provider_stage_retry_chains_v20 (
    chain_id TEXT PRIMARY KEY,
    logical_key_sha256 TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL CHECK (provider IN ('codex', 'deepseek')),
    model_family TEXT NOT NULL CHECK (model_family IN ('sol', 'luna', 'deepseek_v4')),
    stage TEXT NOT NULL CHECK (stage IN (
        'planner', 'semantic_validator', 'reader', 'writer', 'recorder',
        'adult_scene', 'adult_filter'
    )),
    request_occurrence_sha256 TEXT NOT NULL,
    request_sha256 TEXT NOT NULL,
    stage_input_sha256 TEXT NOT NULL,
    authority_sha256 TEXT NOT NULL,
    story_state_committed INTEGER NOT NULL CHECK (story_state_committed IN (0, 1)),
    identity_json TEXT NOT NULL,
    identity_sha256 TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN (
        'input_frozen', 'attempt_prepared', 'dispatch_started',
        'awaiting_owner_retirement', 'owner_retired', 'result_frozen',
        'downstream_intent_frozen', 'downstream_bound', 'succeeded',
        'exhausted', 'blocked_ambiguous', 'recording_repair_required',
        'recovery_required'
    )),
    input_checkpoint_sha256 TEXT NOT NULL,
    result_checkpoint_sha256 TEXT,
    downstream_intent_sha256 TEXT,
    downstream_evidence_sha256 TEXT,
    block_reason TEXT CHECK (block_reason IS NULL OR block_reason IN (
        'input_changed', 'authority_changed', 'ledger_prefix_changed',
        'owner_retirement_unproven', 'result_checkpoint_conflict',
        'dispatch_custody_ambiguous'
    )),
    block_evidence_sha256 TEXT,
    state_version INTEGER NOT NULL DEFAULT 1 CHECK (state_version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (stage = 'planner' AND provider = 'codex' AND model_family = 'sol') OR
        (stage = 'semantic_validator' AND provider = 'codex' AND model_family = 'luna') OR
        (stage = 'reader' AND provider = 'codex' AND model_family = 'sol') OR
        (stage IN ('writer', 'recorder', 'adult_scene', 'adult_filter')
            AND provider = 'deepseek' AND model_family = 'deepseek_v4')
    ),
    CHECK ((stage = 'recorder') = story_state_committed),
    CHECK ((block_reason IS NULL) = (block_evidence_sha256 IS NULL)),
    CHECK (
        (phase = 'blocked_ambiguous'
            AND block_reason = 'dispatch_custody_ambiguous') OR
        (phase = 'recovery_required'
            AND (block_reason IS NULL OR block_reason != 'dispatch_custody_ambiguous')) OR
        (phase NOT IN ('blocked_ambiguous', 'recovery_required')
            AND block_reason IS NULL)
    )
);

INSERT INTO provider_stage_retry_chains_v20 (
    chain_id, logical_key_sha256, provider, model_family, stage,
    request_occurrence_sha256, request_sha256, stage_input_sha256,
    authority_sha256, story_state_committed, identity_json, identity_sha256,
    phase, input_checkpoint_sha256, result_checkpoint_sha256,
    downstream_intent_sha256, downstream_evidence_sha256, block_reason,
    block_evidence_sha256, state_version, created_at, updated_at
)
SELECT
    chain_id, logical_key_sha256, provider, model_family, stage,
    request_occurrence_sha256, request_sha256, stage_input_sha256,
    authority_sha256, story_state_committed, identity_json, identity_sha256,
    phase, input_checkpoint_sha256, result_checkpoint_sha256,
    downstream_intent_sha256, downstream_evidence_sha256, block_reason,
    block_evidence_sha256, state_version, created_at, updated_at
FROM provider_stage_retry_chains;

DROP TABLE provider_stage_retry_chains;
ALTER TABLE provider_stage_retry_chains_v20 RENAME TO provider_stage_retry_chains;

CREATE INDEX idx_provider_stage_retry_phase
ON provider_stage_retry_chains(phase, updated_at, chain_id);

CREATE TRIGGER provider_stage_retry_chain_identity_immutable
BEFORE UPDATE OF chain_id, logical_key_sha256, provider, model_family, stage,
    request_occurrence_sha256, request_sha256, stage_input_sha256,
    authority_sha256, story_state_committed, identity_json, identity_sha256,
    input_checkpoint_sha256, created_at
ON provider_stage_retry_chains
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry chain identity is immutable'); END;

CREATE TRIGGER provider_stage_retry_chain_terminal_immutable
BEFORE UPDATE ON provider_stage_retry_chains
WHEN OLD.phase IN (
    'succeeded', 'exhausted', 'recording_repair_required', 'recovery_required'
)
BEGIN SELECT RAISE(ABORT, 'terminal provider-stage Retry chain is immutable'); END;

CREATE TRIGGER provider_stage_retry_chains_no_delete
BEFORE DELETE ON provider_stage_retry_chains
BEGIN SELECT RAISE(ABORT, 'provider-stage Retry chains cannot be deleted'); END;
"""


MIGRATIONS: dict[int, str] = {
    1: _MIGRATION_1,
    2: _MIGRATION_2,
    3: _MIGRATION_3,
    4: _MIGRATION_4,
    5: _MIGRATION_5,
    6: _MIGRATION_6,
    7: _MIGRATION_7,
    8: _MIGRATION_8,
    9: _MIGRATION_9,
    10: _MIGRATION_10,
    11: _MIGRATION_11,
    12: _MIGRATION_12,
    13: _MIGRATION_13,
    14: _MIGRATION_14,
    15: _MIGRATION_15,
    16: _MIGRATION_16,
    17: _MIGRATION_17,
    18: _MIGRATION_18,
    19: _MIGRATION_19,
    20: _MIGRATION_20,
}


def apply_migrations(connection: sqlite3.Connection, now: str, hashes: dict[int, str]) -> None:
    version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    if version > CURRENT_SCHEMA_VERSION:
        raise TransactionError(
            f"database schema {version} is newer than supported {CURRENT_SCHEMA_VERSION}"
        )
    for next_version in range(version + 1, CURRENT_SCHEMA_VERSION + 1):
        rebuilds_referenced_table = next_version == 20
        if rebuilds_referenced_table:
            connection.execute("PRAGMA foreign_keys = OFF")
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + MIGRATIONS[next_version])
            if rebuilds_referenced_table:
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise TransactionError(
                        "provider-stage Reader migration broke foreign-key custody"
                    )
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at, migration_sha256) "
                "VALUES (?, ?, ?)",
                (next_version, now, hashes[next_version]),
            )
            connection.execute(f"PRAGMA user_version = {next_version}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            if rebuilds_referenced_table:
                connection.execute("PRAGMA foreign_keys = ON")
