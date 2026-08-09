import { validReviewId } from './completion-metadata.js';

const REPROJECTION_SCHEMA = 'cera.pi_scene.adult_provisional_acceptance_blocked.v1';
const REPROJECTION_DISPOSITION = 'reprojection_required';
const REPROJECTION_REASON = 'filter_rejection_has_no_promotable_projection';
const REPROJECTION_NEXT_ACTION = 'protected_reprojection_provider_operation_required';
const REQUIRED_ARTIFACTS = Object.freeze([
    'protected_full_record',
    'non_explicit_codex_projection',
    'route_transition',
]);
const TRANSPORT_RETRY_SCHEMA = 'cera.pi_scene.transport_retry.v1';
const TRANSPORT_RETRY_STATUS_SCHEMA = 'cera.pi_scene.transport_retry_status.v1';
const TRANSPORT_RETRY_STATE_SCHEMA = 'cera.sillytavern.transport_retry_state.v1';
const TRANSPORT_RETRY_COMPLETION_SCHEMA = 'cera.sillytavern.transport_retry_completion.v1';
const TRANSPORT_FAILURE_CODE = 'CERA_PROVIDER_TRANSPORT_FAILED';
const REQUEST_ID_PATTERN = /^request-[a-f0-9]{64}$/;
const TRANSPORT_RETRY_ID_PATTERN = /^retry-[a-f0-9]{64}$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const TRANSPORT_RETRY_PHASES = new Set([
    'eligible',
    'in_progress',
    'unknown',
    'completion_received',
]);
const BACKEND_PROGRESS_PHASES = new Set([
    'authorized',
    'owner_rotated',
    'dispatch_started',
]);
const BLOCKED_REASON_CODES = new Set([
    'effect_state_changed',
    'route_or_context_changed',
    'provider_ledger_changed',
    'owner_rotation_failed',
    'dispatch_state_ambiguous',
    'durable_request_progressed',
]);

/** A retry identity is backend-issued; the browser never derives one. */
export function validTransportRetryId(value) {
    return typeof value === 'string' && TRANSPORT_RETRY_ID_PATTERN.test(value);
}

/**
 * Project only a proven zero-effect provider transport failure.
 * Generic, pending, ambiguous, and post-accept errors deliberately return null.
 */
export function normalizeTransportRetryFailure(value) {
    if (!plainObject(value) || value.status !== 'error' || value.story_state_committed !== false) {
        return null;
    }
    const error = value.error;
    const retry = error?.transport_retry;
    if (
        !plainObject(error)
        || error.schema_version !== 'cera.error.v1'
        || error.error_code !== TRANSPORT_FAILURE_CODE
        || error.retry_mode !== 'manual_transport'
        || error.retry_transport_enabled !== true
        || error.story_state_committed !== false
        || typeof error.provider_operation_submitted !== 'boolean'
        || error.accepted_state_changed !== false
        || error.fallback_used !== false
        || error.next_action !== 'use_transport_retry'
        || typeof error.request_id !== 'string'
        || !REQUEST_ID_PATTERN.test(error.request_id)
        || !plainObject(retry)
        || retry.schema_version !== TRANSPORT_RETRY_SCHEMA
        || !validTransportRetryId(retry.retry_id)
        || retry.retry_url !== `/v1/cera/transport-retries/${retry.retry_id}`
        || retry.method !== 'POST'
        || retry.eligible !== true
        || retry.automatic !== false
        || typeof retry.effect_proof_sha256 !== 'string'
        || !SHA256_PATTERN.test(retry.effect_proof_sha256)
    ) return null;
    const retryKeys = Object.keys(retry).sort();
    if (retryKeys.join(',') !== [
        'automatic',
        'effect_proof_sha256',
        'eligible',
        'method',
        'retry_id',
        'retry_url',
        'schema_version',
    ].join(',')) return null;
    return {
        schema_version: TRANSPORT_RETRY_SCHEMA,
        request_id: error.request_id,
        provider_operation_submitted: error.provider_operation_submitted,
        retry_id: retry.retry_id,
        retry_url: retry.retry_url,
        method: 'POST',
        eligible: true,
        automatic: false,
        effect_proof_sha256: retry.effect_proof_sha256,
    };
}

/** Project the backend action object without retaining provider/error prose. */
export function normalizeTransportRetryAction(value) {
    if (!plainObject(value) || !exactKeys(value, [
        'automatic',
        'effect_proof_sha256',
        'eligible',
        'method',
        'retry_id',
        'retry_url',
        'schema_version',
    ])) return null;
    if (
        value.schema_version !== TRANSPORT_RETRY_SCHEMA
        || !validTransportRetryId(value.retry_id)
        || value.retry_url !== `/v1/cera/transport-retries/${value.retry_id}`
        || value.method !== 'POST'
        || value.eligible !== true
        || value.automatic !== false
        || !SHA256_PATTERN.test(value.effect_proof_sha256)
    ) return null;
    return structuredClone(value);
}

/** Safe durable receipt. It contains identifiers/proof only, never prompt or response prose. */
export function normalizeTransportRetryReceipt(value) {
    if (!plainObject(value) || !exactKeys(value, [
        'automatic',
        'effect_proof_sha256',
        'eligible',
        'method',
        'request_id',
        'retry_id',
        'retry_url',
        'schema_version',
    ])) return null;
    if (!REQUEST_ID_PATTERN.test(value.request_id)) return null;
    const action = normalizeTransportRetryAction({
        schema_version: value.schema_version,
        retry_id: value.retry_id,
        retry_url: value.retry_url,
        method: value.method,
        eligible: value.eligible,
        automatic: value.automatic,
        effect_proof_sha256: value.effect_proof_sha256,
    });
    return action ? { request_id: value.request_id, ...action } : null;
}

export function transportRetryReceipt(value) {
    if (!plainObject(value)) return null;
    return normalizeTransportRetryReceipt({
        schema_version: value.schema_version,
        request_id: value.request_id,
        retry_id: value.retry_id,
        retry_url: value.retry_url,
        method: value.method,
        eligible: value.eligible,
        automatic: value.automatic,
        effect_proof_sha256: value.effect_proof_sha256,
    });
}

/** Closed browser-persistence record for one SillyTavern chat. */
export function normalizePersistedTransportRetryState(value) {
    if (!plainObject(value) || !exactKeys(value, [
        'chat_key',
        'completion_identity',
        'phase',
        'post_dispatched',
        'receipt',
        'schema_version',
    ])) return null;
    if (
        value.schema_version !== TRANSPORT_RETRY_STATE_SCHEMA
        || typeof value.chat_key !== 'string'
        || value.chat_key.length < 1
        || value.chat_key.length > 520
        || /[\u0000-\u001f\u007f]/.test(value.chat_key)
        || !TRANSPORT_RETRY_PHASES.has(value.phase)
        || typeof value.post_dispatched !== 'boolean'
        || (value.completion_identity !== null && !validCompletionIdentity(value.completion_identity))
    ) return null;
    const receipt = normalizeTransportRetryReceipt(value.receipt);
    if (!receipt) return null;
    if (value.phase === 'eligible' && value.post_dispatched) return null;
    if (value.phase !== 'eligible' && !value.post_dispatched) return null;
    if (value.phase === 'completion_received' && value.completion_identity === null) return null;
    if (value.phase !== 'completion_received' && value.completion_identity !== null) return null;
    return {
        schema_version: TRANSPORT_RETRY_STATE_SCHEMA,
        chat_key: value.chat_key,
        phase: value.phase,
        post_dispatched: value.post_dispatched,
        receipt,
        completion_identity: value.completion_identity,
    };
}

/** Stable marker stored beside one assistant message for append deduplication. */
export function normalizeTransportRetryCompletionMarker(value) {
    if (!plainObject(value) || !exactKeys(value, [
        'completion_identity',
        'request_id',
        'retry_id',
        'schema_version',
    ])) return null;
    if (
        value.schema_version !== TRANSPORT_RETRY_COMPLETION_SCHEMA
        || !validTransportRetryId(value.retry_id)
        || !REQUEST_ID_PATTERN.test(value.request_id)
        || !validCompletionIdentity(value.completion_identity)
    ) return null;
    return { ...value };
}

/** Validate the authenticated, read-only backend reconciliation response. */
export function normalizeTransportRetryStatus(value) {
    if (!plainObject(value)) return null;
    const commonKeys = [
        'effect_proof_sha256',
        'request_id',
        'retry_id',
        'retry_transport_enabled',
        'schema_version',
        'state',
    ];
    if (
        value.schema_version !== TRANSPORT_RETRY_STATUS_SCHEMA
        || !validTransportRetryId(value.retry_id)
        || !REQUEST_ID_PATTERN.test(value.request_id)
        || !SHA256_PATTERN.test(value.effect_proof_sha256)
        || typeof value.retry_transport_enabled !== 'boolean'
    ) return null;

    let normalized;
    if (value.state === 'eligible') {
        if (!exactKeys(value, [...commonKeys, 'transport_retry'])) return null;
        const action = normalizeTransportRetryAction(value.transport_retry);
        if (
            !action
            || value.retry_transport_enabled !== true
            || action.retry_id !== value.retry_id
            || action.effect_proof_sha256 !== value.effect_proof_sha256
        ) return null;
        normalized = { transport_retry: action };
    } else if (value.state === 'in_progress') {
        if (
            !exactKeys(value, [...commonKeys, 'phase'])
            || value.retry_transport_enabled !== false
            || !BACKEND_PROGRESS_PHASES.has(value.phase)
        ) return null;
        normalized = { phase: value.phase };
    } else if (value.state === 'succeeded') {
        if (
            !exactKeys(value, [...commonKeys, 'completion', 'completion_sha256'])
            || value.retry_transport_enabled !== false
            || !plainObject(value.completion)
            || !SHA256_PATTERN.test(value.completion_sha256)
        ) return null;
        normalized = {
            completion: structuredClone(value.completion),
            completion_sha256: value.completion_sha256,
        };
    } else if (value.state === 'superseded') {
        if (!exactKeys(value, [
            ...commonKeys,
            'superseded_by_retry_id',
            'transport_retry',
        ])) return null;
        const action = normalizeTransportRetryAction(value.transport_retry);
        if (
            value.retry_transport_enabled !== true
            || !validTransportRetryId(value.superseded_by_retry_id)
            || value.superseded_by_retry_id === value.retry_id
            || !action
            || action.retry_id !== value.superseded_by_retry_id
        ) return null;
        normalized = {
            superseded_by_retry_id: value.superseded_by_retry_id,
            transport_retry: action,
        };
    } else if (value.state === 'blocked') {
        if (
            !exactKeys(value, [...commonKeys, 'blocked_reason_code'])
            || value.retry_transport_enabled !== false
            || !BLOCKED_REASON_CODES.has(value.blocked_reason_code)
        ) return null;
        normalized = { blocked_reason_code: value.blocked_reason_code };
    } else {
        return null;
    }
    return {
        schema_version: TRANSPORT_RETRY_STATUS_SCHEMA,
        retry_id: value.retry_id,
        request_id: value.request_id,
        state: value.state,
        effect_proof_sha256: value.effect_proof_sha256,
        retry_transport_enabled: value.retry_transport_enabled,
        ...normalized,
    };
}

function plainObject(value) {
    return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function exactKeys(value, keys) {
    return Object.keys(value).sort().join(',') === [...keys].sort().join(',');
}

function validCompletionIdentity(value) {
    return typeof value === 'string'
        && value.length > 0
        && value.length <= 240
        && !/[\u0000-\u001f\u007f]/.test(value);
}

/** The backend, rather than the UI, owns provisional-accept availability. */
export function provisionalAcceptEnabled(review) {
    return review?.provisional_accept_enabled === true;
}

/**
 * Project only the exact fail-closed adult provisional-accept result.
 * Anything looser remains an ordinary unresolved review response.
 */
export function normalizeReprojectionRequired(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    if (
        value.schema_version !== REPROJECTION_SCHEMA
        || value.disposition !== REPROJECTION_DISPOSITION
        || value.reason_code !== REPROJECTION_REASON
        || value.next_action !== REPROJECTION_NEXT_ACTION
        || value.story_state_committed !== false
        || value.accepted_effect_created !== false
        || !validReviewId(value.review_id)
        || !Array.isArray(value.required_artifacts)
        || value.required_artifacts.length !== REQUIRED_ARTIFACTS.length
        || REQUIRED_ARTIFACTS.some((item, index) => value.required_artifacts[index] !== item)
    ) return null;
    return {
        schema_version: REPROJECTION_SCHEMA,
        review_id: value.review_id,
        disposition: REPROJECTION_DISPOSITION,
        reason_code: REPROJECTION_REASON,
        required_artifacts: [...REQUIRED_ARTIFACTS],
        story_state_committed: false,
        accepted_effect_created: false,
        next_action: REPROJECTION_NEXT_ACTION,
    };
}

/** Ordinary provisional acceptance is complete only after backend commitment. */
export function provisionalAcceptanceCommitted(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const committed = value.story_state_committed === true
        || value.review?.story_state_committed === true;
    const canonStatus = value.canon_status ?? value.review?.canon_status;
    return committed && canonStatus === 'provisional';
}

/** Validate one automatically accepted Regenerate successor before UI replacement. */
export function acceptedRegenerateSuccessor(value) {
    if (
        !value
        || value.schema_version !== 'cera.pi_scene.review_decision.v1'
        || value.creator_action !== 'regenerate'
        || value.story_state_committed !== true
        || value.successor?.cera?.status !== 'accepted'
        || value.successor?.cera?.story_state_committed !== true
    ) return null;
    const storyText = value.successor?.choices?.[0]?.message?.content;
    if (typeof storyText !== 'string' || !storyText.trim()) return null;
    return {
        story_text: storyText,
        completion: structuredClone(value.successor.cera),
        accepted_turn_id: value.accepted_turn_id ?? null,
        accepted_receipt_sha256: value.accepted_receipt_sha256 ?? null,
    };
}
