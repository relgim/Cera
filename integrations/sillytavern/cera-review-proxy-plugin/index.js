import {
    normalizeProviderStageRetryActionV1,
    normalizeProviderStageRetryBlockedAmbiguousV1,
    normalizeProviderStageRetryExhaustedV1,
    normalizeProviderStageRetryStatusEnvelopeV1,
    normalizeProviderStageRetryStatusV1,
} from './generated/provider-stage-retry-contracts-v1.mjs';
import {
    normalizeOrdinaryReviewDecisionV2,
    normalizeOrdinaryReviewLifecycleV1,
    normalizeOrdinaryReviewV2,
} from './generated/ordinary-review-contracts-v2.mjs';
import {
    normalizeOrdinaryReviewDecisionV3,
    normalizeOrdinaryReviewLifecycleV2,
    normalizeOrdinaryReviewV3,
} from './generated/ordinary-review-contracts-v3.mjs';

const DEFAULT_CERA_LOOPBACK_ROOT = 'http://127.0.0.1:5101';
const MAX_UPSTREAM_BYTES = 2_000_000;
const GET_TIMEOUT_MS = 10_000;
export const FULL_PIPELINE_TIMEOUT_MS = 4_200_000;
const DECISION_TIMEOUT_MS = FULL_PIPELINE_TIMEOUT_MS;
const TRANSPORT_RETRY_TIMEOUT_MS = FULL_PIPELINE_TIMEOUT_MS;
const TRANSPORT_RETRY_STATUS_SCHEMA_V1 = 'cera.pi_scene.transport_retry_status.v1';
const TRANSPORT_RETRY_STATUS_SCHEMA_V2 = 'cera.pi_scene.transport_retry_status.v2';
const PROVIDER_STAGE_RETRY_EXHAUSTED_CODE = 'CERA_PROVIDER_STAGE_RETRY_EXHAUSTED';
const REVIEW_ID_PATTERN = /^(?:[a-z][a-z0-9_]{0,31}:[A-Za-z0-9._-]{1,160}|review-[a-f0-9]{28})$/;
const TRANSPORT_RETRY_ID_PATTERN = /^retry-[a-f0-9]{64}$/;
const PROVIDER_STAGE_RETRY_CHAIN_ID_PATTERN = /^stage-retry-[a-f0-9]{64}$/;
const PROVIDER_STAGE_RETRY_ACTION_ID_PATTERN = /^stage-action-[a-f0-9]{64}$/;
const REQUEST_ID_PATTERN = /^request-[a-f0-9]{64}$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const AUTHORIZATION_PATTERN = /^Bearer [A-Za-z0-9._~-]{24,512}$/;
const TRANSPORT_RETRY_PROGRESS_PHASES = new Set([
    'authorized',
    'owner_rotated',
    'dispatch_started',
]);
const TRANSPORT_RETRY_BLOCKED_REASONS = new Set([
    'effect_state_changed',
    'route_or_context_changed',
    'provider_ledger_changed',
    'owner_rotation_failed',
    'dispatch_state_ambiguous',
    'durable_request_progressed',
]);
const DECISION_ACTIONS = new Set([
    'accept',
    'accept_provisional',
    'decline',
    'regenerate',
    'replan',
    'repair_recording',
    'false_positive',
    'correction_adjustment',
    'deepseek_rewrite',
    'codex_replan',
]);

export const info = Object.freeze({
    id: 'cera-review',
    name: 'CERA Review Loopback Relay',
    description: 'Relays narrow authenticated review and provider-stage control requests to loopback-only CERA.',
});

export function normalizeLoopbackRoot(value) {
    const candidate = value === undefined || value === null || value === ''
        ? DEFAULT_CERA_LOOPBACK_ROOT
        : value;
    if (typeof candidate !== 'string' || candidate.length > 128) {
        throw new TypeError('CERA review loopback root is invalid');
    }
    let parsed;
    try {
        parsed = new URL(candidate);
    } catch {
        throw new TypeError('CERA review loopback root is invalid');
    }
    if (
        parsed.protocol !== 'http:'
        || parsed.hostname !== '127.0.0.1'
        || !parsed.port
        || parsed.username
        || parsed.password
        || (parsed.pathname !== '/' && parsed.pathname !== '')
        || parsed.search
        || parsed.hash
    ) {
        throw new TypeError('CERA review loopback root is invalid');
    }
    const port = Number(parsed.port);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
        throw new TypeError('CERA review loopback root is invalid');
    }
    return `http://127.0.0.1:${port}`;
}

const CERA_LOOPBACK_ROOT = normalizeLoopbackRoot(process.env.CERA_REVIEW_LOOPBACK_ROOT);

export function normalizeReviewId(value) {
    if (typeof value !== 'string' || !REVIEW_ID_PATTERN.test(value)) {
        throw new TypeError('CERA review ID is invalid');
    }
    return value;
}

export function normalizeTransportRetryId(value) {
    if (typeof value !== 'string' || !TRANSPORT_RETRY_ID_PATTERN.test(value)) {
        throw new TypeError('CERA transport retry ID is invalid');
    }
    return value;
}

export function normalizeProviderStageRetryChainId(value) {
    if (typeof value !== 'string' || !PROVIDER_STAGE_RETRY_CHAIN_ID_PATTERN.test(value)) {
        throw new TypeError('CERA provider stage retry chain ID is invalid');
    }
    return value;
}

export function normalizeProviderStageRetryActionId(value) {
    if (typeof value !== 'string' || !PROVIDER_STAGE_RETRY_ACTION_ID_PATTERN.test(value)) {
        throw new TypeError('CERA provider stage retry action ID is invalid');
    }
    return value;
}

export function normalizeAuthorization(value) {
    if (typeof value !== 'string' || !AUTHORIZATION_PATTERN.test(value)) {
        throw new TypeError('CERA review authorization is invalid');
    }
    return value;
}

export function normalizeDecisionBody(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError('CERA review decision body must be an object');
    }
    const keys = Object.keys(value).sort();
    if (keys.some(key => !['action', 'feedback', 'force_rehydrate'].includes(key))) {
        throw new TypeError('CERA review decision body contains an unsupported field');
    }
    if (typeof value.action !== 'string' || !DECISION_ACTIONS.has(value.action)) {
        throw new TypeError('CERA review decision action is invalid');
    }
    if (
        value.feedback !== undefined
        && value.feedback !== null
        && (typeof value.feedback !== 'string' || value.feedback.length > 20_000)
    ) {
        throw new TypeError('CERA review feedback is invalid');
    }
    if (
        value.action === 'accept_provisional'
        && (
            typeof value.feedback !== 'string'
            || value.feedback.trim().length === 0
        )
    ) {
        throw new TypeError('CERA auditable override feedback is required');
    }
    if (
        value.force_rehydrate !== undefined
        && typeof value.force_rehydrate !== 'boolean'
    ) {
        throw new TypeError('CERA review rehydration flag is invalid');
    }
    const normalized = {
        action: value.action,
        feedback: value.feedback ?? null,
    };
    if (value.force_rehydrate !== undefined) {
        normalized.force_rehydrate = value.force_rehydrate;
    }
    return normalized;
}

export function normalizeTransportRetryBody(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError('CERA transport retry body must be an object');
    }
    if (Object.keys(value).length !== 0) {
        throw new TypeError('CERA transport retry body must be empty');
    }
    return {};
}

export function normalizeProviderStageRetryActionBody(value, { chainId, actionId }) {
    const normalized = projectProviderStageRetryAction(value);
    if (
        normalized.chain_id !== normalizeProviderStageRetryChainId(chainId)
        || normalized.action_id !== normalizeProviderStageRetryActionId(actionId)
        || normalized.action_kind === 'check_status'
    ) {
        throw new TypeError('CERA provider stage retry action body is invalid');
    }
    return normalized;
}

export function reviewUpstreamUrl(
    reviewId,
    {
        decision = false,
        terminalDecision = false,
        loopbackRoot = CERA_LOOPBACK_ROOT,
    } = {},
) {
    if (decision && terminalDecision) throw new TypeError('CERA review URL mode is invalid');
    const encoded = encodeURIComponent(normalizeReviewId(reviewId));
    const root = normalizeLoopbackRoot(loopbackRoot);
    const suffix = decision
        ? '/decision'
        : terminalDecision
            ? '/terminal-decision'
            : '';
    return `${root}/v1/cera/reviews/${encoded}${suffix}`;
}

export function transportRetryUpstreamUrl(
    retryId,
    { loopbackRoot = CERA_LOOPBACK_ROOT } = {},
) {
    const encoded = encodeURIComponent(normalizeTransportRetryId(retryId));
    const root = normalizeLoopbackRoot(loopbackRoot);
    return `${root}/v1/cera/transport-retries/${encoded}`;
}

export function providerStageRetryUpstreamUrl(
    chainId,
    { actionId = null, loopbackRoot = CERA_LOOPBACK_ROOT } = {},
) {
    const encodedChain = encodeURIComponent(normalizeProviderStageRetryChainId(chainId));
    const root = normalizeLoopbackRoot(loopbackRoot);
    if (actionId === null) return `${root}/v1/cera/provider-stage-retries/${encodedChain}`;
    const encodedAction = encodeURIComponent(normalizeProviderStageRetryActionId(actionId));
    return `${root}/v1/cera/provider-stage-retries/${encodedChain}/actions/${encodedAction}`;
}

/**
 * Project the terminal, provider-stage retry receipt.  The object is deliberately
 * hash/count only: upstream exception text, paths, prompts, and provider output
 * never cross the same-origin relay.
 */
export function projectProviderStageRetryExhausted(value) {
    const normalized = normalizeProviderStageRetryExhaustedV1(value);
    if (!normalized) throw new TypeError('CERA provider stage retry exhaustion is invalid');
    return normalized;
}

/** Privacy-safe canonical status projection for one stage-occurrence chain. */
export function projectProviderStageRetryStatus(value) {
    const normalized = normalizeProviderStageRetryStatusV1(value);
    if (!normalized) throw new TypeError('CERA provider stage retry status is invalid');
    return normalized;
}

/** Authoritative status plus backend-issued control identity and chain binding. */
export function projectProviderStageRetryStatusEnvelope(value) {
    const normalized = normalizeProviderStageRetryStatusEnvelopeV1(value);
    if (!normalized) {
        throw new TypeError('CERA provider stage retry status envelope is invalid');
    }
    return normalized;
}

/**
 * Authenticated stage continuation returns either another canonical status,
 * the original chat completion, or the original creator-decision DTO.  The
 * relay never wraps these results, so SillyTavern can resume its existing
 * completion/decision flow without inventing a fourth action family.
 */
export function projectProviderStageRetryResult(value) {
    const envelope = normalizeProviderStageRetryStatusEnvelopeV1(value);
    if (envelope) return envelope;
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError('CERA provider stage retry result is invalid');
    }
    if (value.schema_version === 'cera.pi_scene.review_decision.v3') {
        const decision = normalizeOrdinaryReviewDecisionV3(value, {
            successorValidator: projectOrdinarySuccessorCompletion,
        });
        if (!decision) {
            throw new TypeError('CERA provider stage retry decision is invalid');
        }
        return decision;
    }
    if (value.schema_version === 'cera.pi_scene.review_decision.v2') {
        const decision = normalizeOrdinaryReviewDecisionV2(value, {
            successorValidator: projectOrdinarySuccessorCompletion,
        });
        if (!decision) throw new TypeError('CERA provider stage retry decision is invalid');
        return decision;
    }
    if (value.object === 'chat.completion') {
        return projectProviderStageCompletion(value);
    }
    const directReview = projectReviewPayloadOrNull(value);
    if (directReview) return directReview;
    const committed = value.story_state_committed === true;
    const review = projectReviewPayloadOrNull(value.review);
    const decisionKeys = [
        'schema_version',
        'status',
        'creator_action',
        'story_state_committed',
        'retry_mode',
        'review',
        'successor',
        'operational_warnings',
        ...(committed ? ['accepted_receipt_sha256', 'accepted_turn_id'] : []),
    ];
    if (
        exactKeys(value, decisionKeys)
        && value.schema_version === 'cera.pi_scene.review_decision.v1'
        && ['story_committed', 'review_transitioned'].includes(value.status)
        && value.status === (committed ? 'story_committed' : 'review_transitioned')
        && [
            'accept',
            'accept_provisional',
            'decline',
            'regenerate',
            'replan',
            'repair_recording',
        ].includes(value.creator_action)
        && typeof value.story_state_committed === 'boolean'
        && value.retry_mode === 'not_applicable'
        && review !== null
        && (value.successor === null || (
            value.successor
            && typeof value.successor === 'object'
            && !Array.isArray(value.successor)
        ))
        && Array.isArray(value.operational_warnings)
        && value.operational_warnings.every(warning => (
            typeof warning === 'string' && warning.length <= 240 && !warning.includes('\0')
        ))
        && (!committed || (
            SHA256_PATTERN.test(value.accepted_receipt_sha256)
            && typeof value.accepted_turn_id === 'string'
            && value.accepted_turn_id.length > 0
        ))
    ) {
        const successor = value.successor === null
            ? null
            : projectProviderStageRetryResult(value.successor);
        if (
            successor !== null
            && (
                successor.object !== 'chat.completion'
                || !['regenerate', 'replan'].includes(value.creator_action)
            )
        ) throw new TypeError('CERA provider stage retry result is invalid');
        return { ...structuredClone(value), review, successor };
    }
    throw new TypeError('CERA provider stage retry result is invalid');
}

function projectProviderStageCompletion(value) {
    return projectChatCompletion(value, { requireProviderStageBinding: true });
}

function projectOrdinarySuccessorCompletion(value) {
    const completion = projectChatCompletion(value, {
        requireProviderStageBinding: false,
    });
    const cera = completion.cera;
    const lifecycle = cera.review_lifecycle?.schema_version
        === 'cera.pi_scene.review_lifecycle.v2'
        ? normalizeOrdinaryReviewLifecycleV2(cera.review_lifecycle)
        : normalizeOrdinaryReviewLifecycleV1(cera.review_lifecycle);
    if (
        cera.route_mode !== 'ordinary'
        || cera.provisional !== true
        || cera.story_state_committed !== false
        || cera.status !== 'review_ready'
        || !REVIEW_ID_PATTERN.test(cera.provisional_review_id)
        || cera.review_url !== `/v1/cera/reviews/${cera.provisional_review_id}`
        || !/^candidate-[a-f0-9]{28}$/.test(cera.candidate_id)
        || !SHA256_PATTERN.test(cera.candidate_sha256)
        || !lifecycle
        || lifecycle.review_id !== cera.provisional_review_id
        || lifecycle.state !== 'checks_pending'
    ) {
        throw new TypeError('CERA ordinary review successor is invalid');
    }
    completion.cera.review_lifecycle = lifecycle;
    return completion;
}

function projectChatCompletion(value, { requireProviderStageBinding }) {
    const choice = value.choices?.[0];
    const cera = value.cera;
    const providerStageBinding = cera?.provider_stage_request_sha256;
    if (
        !exactKeys(value, ['id', 'object', 'created', 'model', 'choices', 'usage', 'cera'])
        || value.object !== 'chat.completion'
        || typeof value.id !== 'string'
        || !value.id.startsWith('chatcmpl-')
        || !Number.isSafeInteger(value.created)
        || value.created < 0
        || typeof value.model !== 'string'
        || !Array.isArray(value.choices)
        || value.choices.length !== 1
        || !choice
        || typeof choice !== 'object'
        || Array.isArray(choice)
        || !exactKeys(choice, ['index', 'message', 'finish_reason'])
        || choice.index !== 0
        || choice.finish_reason !== 'stop'
        || !exactKeys(choice.message, ['role', 'content'])
        || choice.message?.role !== 'assistant'
        || typeof choice.message?.content !== 'string'
        || !choice.message.content.trim()
        || !value.usage
        || typeof value.usage !== 'object'
        || Array.isArray(value.usage)
        || !exactKeys(value.usage, ['prompt_tokens', 'completion_tokens', 'total_tokens'])
        || Object.values(value.usage).some(count => (
            !Number.isSafeInteger(count) || count < 0
        ))
        || !cera
        || typeof cera !== 'object'
        || Array.isArray(cera)
        || typeof cera.profile_id !== 'string'
        || !cera.profile_id.startsWith('cera.pi_scene.')
        || (requireProviderStageBinding && !REQUEST_ID_PATTERN.test(cera.request_id))
        || (!requireProviderStageBinding
            && cera.request_id !== undefined
            && !REQUEST_ID_PATTERN.test(cera.request_id))
        || (requireProviderStageBinding && !SHA256_PATTERN.test(providerStageBinding))
        || (!requireProviderStageBinding
            && providerStageBinding !== undefined
            && !SHA256_PATTERN.test(providerStageBinding))
        || containsUnsafeContinuationKey(cera)
    ) {
        throw new TypeError('CERA provider stage retry completion is invalid');
    }
    return structuredClone(value);
}

/** Closed public review projection shared by GET, decisions, and stage continuation. */
export function projectReviewPayload(value) {
    const normalized = projectReviewPayloadOrNull(value);
    if (!normalized) throw new TypeError('CERA review payload is invalid');
    return normalized;
}

/** Decision POST may return a generated stage status while its exact action is paused. */
export function projectReviewDecisionPayload(value) {
    return projectProviderStageRetryResult(value);
}

function projectReviewPayloadOrNull(value) {
    return projectReviewV2(value) ?? projectProviderStageRetryReview(value);
}

function projectReviewV2(value) {
    return value?.schema_version === 'cera.pi_scene.review.v3'
        ? normalizeOrdinaryReviewV3(value)
        : normalizeOrdinaryReviewV2(value);
}
function safeCountMap(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const allowed = new Set([
        'planner', 'writer', 'luna', 'validator', 'reader', 'adult_scene',
        'adult_filter', 'recorder', 'total',
    ]);
    const entries = Object.entries(value);
    return entries.length > 0 && entries.every(([key, count]) => (
        allowed.has(key) && Number.isSafeInteger(count) && count >= 0
    ));
}

function projectProviderStageRetryReview(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const baseKeys = [
        'schema_version',
        'review_id',
        'state',
        'provisional',
        'route',
        'story_text',
        'candidate_id',
        'candidate_sha256',
        'primary_authority_kind',
        'primary_authority_sha256',
        'warnings',
        'warnings_block_accept',
        'recording_status',
        'story_state_committed',
        'canon_status',
        'semantic_validation',
        'request_controls',
        'creator_guidance',
        'accept_enabled',
        'provisional_accept_enabled',
        'decline_enabled',
        'regenerate_enabled',
        'replan_enabled',
        'repair_recording_enabled',
        'provider_operations',
    ];
    const adultKeys = [
        'provisional_acceptance_status',
        'automatic_repair_limit',
        'operation_state',
    ];
    const attemptKeys = ['provider_attempts'];
    const booleanKeys = [
        'provisional',
        'warnings_block_accept',
        'story_state_committed',
        'accept_enabled',
        'provisional_accept_enabled',
        'decline_enabled',
        'regenerate_enabled',
        'replan_enabled',
        'repair_recording_enabled',
    ];
    const exactShape = exactKeys(value, baseKeys)
        || exactKeys(value, [...baseKeys, ...attemptKeys])
        || exactKeys(value, [...baseKeys, ...adultKeys])
        || exactKeys(value, [...baseKeys, ...adultKeys, ...attemptKeys]);
    if (
        !exactShape
        || value.schema_version !== 'cera.pi_scene.review.v1'
        || !/^review-[a-f0-9]{28}$/.test(value.review_id)
        || ![
            'review_ready',
            'accepted',
            'declined',
            'rejected',
            'regenerated',
            'replanned',
        ].includes(value.state)
        || !['ordinary', 'adult'].includes(value.route)
        || !(value.story_text === null || typeof value.story_text === 'string')
        || typeof value.candidate_id !== 'string'
        || !SHA256_PATTERN.test(value.candidate_sha256)
        || typeof value.primary_authority_kind !== 'string'
        || !SHA256_PATTERN.test(value.primary_authority_sha256)
        || !Array.isArray(value.warnings)
        || booleanKeys.some(key => typeof value[key] !== 'boolean')
        || !value.provider_operations
        || typeof value.provider_operations !== 'object'
        || Array.isArray(value.provider_operations)
        || Object.entries(value.provider_operations).some(([key, count]) => (
            typeof key !== 'string' || !Number.isSafeInteger(count) || count < 0
        ))
        || (
            value.provider_attempts !== undefined
            && (
                !Array.isArray(value.provider_attempts)
                || value.provider_attempts.length > 12
            )
        )
        || containsUnsafeContinuationKey(value)
    ) return null;
    return structuredClone(value);
}

function containsUnsafeContinuationKey(value) {
    if (Array.isArray(value)) return value.some(containsUnsafeContinuationKey);
    if (!value || typeof value !== 'object') return false;
    return Object.entries(value).some(([key, item]) => {
        const normalized = key.toLowerCase();
        return normalized === 'raw'
            || normalized.startsWith('raw_')
            || normalized.startsWith('exact_')
            || normalized.endsWith('_path')
            || [
                'feedback',
                'prompt',
                'provider_output',
                'protected_full_record',
            ].includes(normalized)
            || containsUnsafeContinuationKey(item);
    });
}

/** Closed provider-stage control action; semantic actions are rejected. */
export function projectProviderStageRetryAction(value) {
    const normalized = normalizeProviderStageRetryActionV1(value);
    if (!normalized) throw new TypeError('CERA provider stage retry action is invalid');
    return normalized;
}

/** Reconciliation-only blocked evidence that cannot authorize provider dispatch. */
export function projectProviderStageRetryBlockedAmbiguous(value) {
    const normalized = normalizeProviderStageRetryBlockedAmbiguousV1(value);
    if (!normalized) {
        throw new TypeError('CERA provider stage retry blocked ambiguity is invalid');
    }
    return normalized;
}

export function projectTransportRetryPayload(value) {
    const canonicalEnvelope = normalizeProviderStageRetryStatusEnvelopeV1(value);
    if (canonicalEnvelope) return canonicalEnvelope;
    const canonicalAction = normalizeProviderStageRetryActionV1(value);
    if (canonicalAction) return canonicalAction;
    const blockedAmbiguous = normalizeProviderStageRetryBlockedAmbiguousV1(value);
    if (blockedAmbiguous) return blockedAmbiguous;
    const exhausted = projectProviderStageRetryExhaustedError(value);
    if (exhausted) return exhausted;
    if (!value || typeof value !== 'object' || Array.isArray(value) || !value.error) {
        return value;
    }
    const error = value.error;
    const retry = error?.transport_retry;
    const eligible = (
        value.status === 'error'
        && value.story_state_committed === false
        && error?.schema_version === 'cera.error.v1'
        && error.error_code === 'CERA_PROVIDER_TRANSPORT_FAILED'
        && REQUEST_ID_PATTERN.test(error.request_id)
        && error.story_state_committed === false
        && error.retry_mode === 'manual_transport'
        && typeof error.provider_operation_submitted === 'boolean'
        && error.accepted_state_changed === false
        && error.fallback_used === false
        && error.next_action === 'use_transport_retry'
        && error.retry_transport_enabled === true
        && retry?.schema_version === 'cera.pi_scene.transport_retry.v1'
        && TRANSPORT_RETRY_ID_PATTERN.test(retry.retry_id)
        && retry.retry_url === `/v1/cera/transport-retries/${retry.retry_id}`
        && retry.method === 'POST'
        && retry.eligible === true
        && retry.automatic === false
        && SHA256_PATTERN.test(retry.effect_proof_sha256)
        && Object.keys(retry).sort().join(',')
            === 'automatic,effect_proof_sha256,eligible,method,retry_id,retry_url,schema_version'
    );
    if (!eligible) {
        const committed = value.story_state_committed === true
            || error?.story_state_committed === true
            || error?.accepted_state_changed === true;
        return {
            status: 'error',
            story_state_committed: committed,
            error: {
                schema_version: 'cera.error.v1',
                error_code: 'CERA_TRANSPORT_RETRY_NOT_AVAILABLE',
                message: 'CERA did not prove that another provider retry is safe.',
                story_state_committed: committed,
                retry_mode: 'not_applicable',
                provider_operation_submitted: false,
                accepted_state_changed: committed,
                fallback_used: false,
                next_action: 'check_current_state',
                retry_transport_enabled: false,
            },
        };
    }
    return {
        status: 'error',
        story_state_committed: false,
        error: {
            schema_version: 'cera.error.v1',
            error_code: 'CERA_PROVIDER_TRANSPORT_FAILED',
            message: 'CERA provider transport failed before any candidate or accepted effect.',
            request_id: error.request_id,
            story_state_committed: false,
            retry_mode: 'manual_transport',
            provider_operation_submitted: error.provider_operation_submitted,
            accepted_state_changed: false,
            fallback_used: false,
            next_action: 'use_transport_retry',
            retry_transport_enabled: true,
            transport_retry: {
                schema_version: retry.schema_version,
                retry_id: retry.retry_id,
                retry_url: retry.retry_url,
                method: 'POST',
                eligible: true,
                automatic: false,
                effect_proof_sha256: retry.effect_proof_sha256,
            },
        },
    };
}

function projectProviderStageRetryExhaustedError(value) {
    const error = value?.error;
    if (
        !exactKeys(value ?? {}, ['error', 'status', 'story_state_committed'])
        || value.status !== 'error'
        || typeof value.story_state_committed !== 'boolean'
        || !error
        || typeof error !== 'object'
        || Array.isArray(error)
        || !exactKeys(error, [
            'accepted_state_changed',
            'branch_id',
            'critical_provider_stage_failure',
            'debug_log_path',
            'details',
            'error_code',
            'fallback_used',
            'generation_id',
            'message',
            'next_action',
            'provider_operation_submitted',
            'request_id',
            'retry_mode',
            'retry_transport_enabled',
            'schema_version',
            'stage',
            'story_state_committed',
            'trace_id',
        ])
        || error.schema_version !== 'cera.error.v1'
        || error.error_code !== PROVIDER_STAGE_RETRY_EXHAUSTED_CODE
        || typeof error.message !== 'string'
        || error.message.length < 1
        || error.message.length > 500
        || typeof error.trace_id !== 'string'
        || error.trace_id.length < 1
        || error.trace_id.length > 240
        || (error.request_id !== null && !REQUEST_ID_PATTERN.test(error.request_id))
        || error.branch_id !== null
        || error.generation_id !== null
        || error.stage !== 'pi_scene_http'
        || error.story_state_committed !== value.story_state_committed
        || error.retry_mode !== 'exhausted'
        || !Array.isArray(error.details)
        || error.details.some(item => typeof item !== 'string' || item.length > 500)
        || error.fallback_used !== false
        || typeof error.provider_operation_submitted !== 'boolean'
        || error.accepted_state_changed !== value.story_state_committed
        || error.next_action !== 'report_critical_provider_failure'
        || (
            error.debug_log_path !== null
            && (
                typeof error.debug_log_path !== 'string'
                || error.debug_log_path.length > 2_000
                || /[\u0000-\u001f\u007f]/.test(error.debug_log_path)
            )
        )
        || error.retry_transport_enabled !== false
    ) return null;
    let critical;
    try {
        critical = projectProviderStageRetryExhausted(error.critical_provider_stage_failure);
    } catch {
        return null;
    }
    if (critical.story_state_committed !== value.story_state_committed) return null;
    return {
        status: 'error',
        story_state_committed: critical.story_state_committed,
        error: {
            schema_version: 'cera.error.v1',
            error_code: PROVIDER_STAGE_RETRY_EXHAUSTED_CODE,
            message: 'CERA stopped after three failed attempts at one provider stage.',
            story_state_committed: critical.story_state_committed,
            retry_mode: 'exhausted',
            provider_operation_submitted: error.provider_operation_submitted,
            accepted_state_changed: critical.story_state_committed,
            fallback_used: false,
            next_action: 'report_critical_provider_failure',
            retry_transport_enabled: false,
            critical_provider_stage_failure: critical,
        },
    };
}

function projectTransportRetryAction(value) {
    if (
        !value
        || typeof value !== 'object'
        || Array.isArray(value)
        || !exactKeys(value, [
            'automatic',
            'effect_proof_sha256',
            'eligible',
            'method',
            'retry_id',
            'retry_url',
            'schema_version',
        ])
        || value.schema_version !== 'cera.pi_scene.transport_retry.v1'
        || !TRANSPORT_RETRY_ID_PATTERN.test(value.retry_id)
        || value.retry_url !== `/v1/cera/transport-retries/${value.retry_id}`
        || value.method !== 'POST'
        || value.eligible !== true
        || value.automatic !== false
        || !SHA256_PATTERN.test(value.effect_proof_sha256)
    ) throw new TypeError('CERA transport retry action is invalid');
    return {
        schema_version: value.schema_version,
        retry_id: value.retry_id,
        retry_url: value.retry_url,
        method: 'POST',
        eligible: true,
        automatic: false,
        effect_proof_sha256: value.effect_proof_sha256,
    };
}

/** Closed projection of the authenticated read-only retry reconciliation API. */
export function projectTransportRetryStatusPayload(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new TypeError('CERA transport retry status is invalid');
    }
    const canonicalEnvelope = normalizeProviderStageRetryStatusEnvelopeV1(value);
    if (canonicalEnvelope) return canonicalEnvelope;
    const blockedAmbiguous = normalizeProviderStageRetryBlockedAmbiguousV1(value);
    if (blockedAmbiguous) return blockedAmbiguous;
    const notFound = projectTransportRetryNotFound(value);
    if (notFound) return notFound;
    if (value.schema_version === TRANSPORT_RETRY_STATUS_SCHEMA_V2) {
        return projectExhaustedTransportRetryStatus(value);
    }
    const commonKeys = [
        'effect_proof_sha256',
        'request_id',
        'retry_id',
        'retry_transport_enabled',
        'schema_version',
        'state',
    ];
    if (
        value.schema_version !== TRANSPORT_RETRY_STATUS_SCHEMA_V1
        || !TRANSPORT_RETRY_ID_PATTERN.test(value.retry_id)
        || !REQUEST_ID_PATTERN.test(value.request_id)
        || !SHA256_PATTERN.test(value.effect_proof_sha256)
        || typeof value.retry_transport_enabled !== 'boolean'
    ) throw new TypeError('CERA transport retry status is invalid');

    let stateFields;
    if (value.state === 'eligible') {
        if (!exactKeys(value, [...commonKeys, 'transport_retry'])) {
            throw new TypeError('CERA transport retry eligible status is invalid');
        }
        const action = projectTransportRetryAction(value.transport_retry);
        if (
            value.retry_transport_enabled !== true
            || action.retry_id !== value.retry_id
            || action.effect_proof_sha256 !== value.effect_proof_sha256
        ) throw new TypeError('CERA transport retry eligible status is invalid');
        stateFields = { transport_retry: action };
    } else if (value.state === 'in_progress') {
        if (
            !exactKeys(value, [...commonKeys, 'phase'])
            || value.retry_transport_enabled !== false
            || !TRANSPORT_RETRY_PROGRESS_PHASES.has(value.phase)
        ) throw new TypeError('CERA transport retry progress status is invalid');
        stateFields = { phase: value.phase };
    } else if (value.state === 'succeeded') {
        if (
            !exactKeys(value, [...commonKeys, 'completion', 'completion_sha256'])
            || value.retry_transport_enabled !== false
            || !value.completion
            || typeof value.completion !== 'object'
            || Array.isArray(value.completion)
            || !value.completion.cera
            || typeof value.completion.cera !== 'object'
            || Array.isArray(value.completion.cera)
            || value.completion.cera.request_id !== value.request_id
            || !SHA256_PATTERN.test(value.completion_sha256)
        ) throw new TypeError('CERA transport retry success status is invalid');
        stateFields = {
            completion: value.completion,
            completion_sha256: value.completion_sha256,
        };
    } else if (value.state === 'superseded') {
        if (!exactKeys(value, [
            ...commonKeys,
            'superseded_by_retry_id',
            'transport_retry',
        ])) throw new TypeError('CERA transport retry superseded status is invalid');
        const action = projectTransportRetryAction(value.transport_retry);
        if (
            value.retry_transport_enabled !== true
            || !TRANSPORT_RETRY_ID_PATTERN.test(value.superseded_by_retry_id)
            || value.superseded_by_retry_id === value.retry_id
            || action.retry_id !== value.superseded_by_retry_id
        ) throw new TypeError('CERA transport retry superseded status is invalid');
        stateFields = {
            superseded_by_retry_id: value.superseded_by_retry_id,
            transport_retry: action,
        };
    } else if (value.state === 'blocked') {
        if (
            !exactKeys(value, [...commonKeys, 'blocked_reason_code'])
            || value.retry_transport_enabled !== false
            || !TRANSPORT_RETRY_BLOCKED_REASONS.has(value.blocked_reason_code)
        ) throw new TypeError('CERA transport retry blocked status is invalid');
        stateFields = { blocked_reason_code: value.blocked_reason_code };
    } else {
        throw new TypeError('CERA transport retry status state is invalid');
    }
    return {
        schema_version: TRANSPORT_RETRY_STATUS_SCHEMA_V1,
        retry_id: value.retry_id,
        request_id: value.request_id,
        state: value.state,
        effect_proof_sha256: value.effect_proof_sha256,
        retry_transport_enabled: value.retry_transport_enabled,
        ...stateFields,
    };
}

function projectExhaustedTransportRetryStatus(value) {
    const keys = [
        'critical_provider_stage_failure',
        'effect_proof_sha256',
        'request_id',
        'retry_id',
        'retry_transport_enabled',
        'schema_version',
        'state',
    ];
    if (
        !exactKeys(value, keys)
        || value.schema_version !== TRANSPORT_RETRY_STATUS_SCHEMA_V2
        || !TRANSPORT_RETRY_ID_PATTERN.test(value.retry_id)
        || !REQUEST_ID_PATTERN.test(value.request_id)
        || !SHA256_PATTERN.test(value.effect_proof_sha256)
        || value.state !== 'attempts_exhausted'
        || value.retry_transport_enabled !== false
    ) throw new TypeError('CERA exhausted transport retry status is invalid');
    const critical = projectProviderStageRetryExhausted(
        value.critical_provider_stage_failure,
    );
    return {
        schema_version: TRANSPORT_RETRY_STATUS_SCHEMA_V2,
        retry_id: value.retry_id,
        request_id: value.request_id,
        state: 'attempts_exhausted',
        effect_proof_sha256: value.effect_proof_sha256,
        retry_transport_enabled: false,
        critical_provider_stage_failure: critical,
    };
}

function projectTransportRetryNotFound(value) {
    const error = value?.error;
    if (
        !exactKeys(value, ['error', 'status', 'story_state_committed'])
        || value.status !== 'error'
        || value.story_state_committed !== false
        || !error
        || typeof error !== 'object'
        || Array.isArray(error)
        || !exactKeys(error, [
            'accepted_state_changed',
            'branch_id',
            'debug_log_path',
            'details',
            'error_code',
            'fallback_used',
            'generation_id',
            'message',
            'next_action',
            'provider_operation_submitted',
            'request_id',
            'retry_mode',
            'retry_transport_enabled',
            'schema_version',
            'stage',
            'story_state_committed',
            'trace_id',
        ])
        || error.schema_version !== 'cera.error.v1'
        || error.error_code !== 'CERA_TRANSPORT_RETRY_NOT_FOUND'
        || error.message !== 'The transport Retry identity is unavailable.'
        || typeof error.trace_id !== 'string'
        || error.trace_id.length < 1
        || error.trace_id.length > 240
        || error.request_id !== null
        || error.branch_id !== null
        || error.generation_id !== null
        || error.stage !== 'pi_scene_http'
        || error.story_state_committed !== false
        || error.retry_mode !== 'not_applicable'
        || !Array.isArray(error.details)
        || error.details.length !== 0
        || error.fallback_used !== false
        || error.provider_operation_submitted !== false
        || error.accepted_state_changed !== false
        || error.next_action !== 'check_transport_retry_identity'
        || (
            error.debug_log_path !== null
            && (
                typeof error.debug_log_path !== 'string'
                || error.debug_log_path.length > 2_000
                || /[\u0000-\u001f\u007f]/.test(error.debug_log_path)
            )
        )
        || error.retry_transport_enabled !== false
    ) return null;
    return {
        status: 'error',
        story_state_committed: false,
        error: {
            schema_version: 'cera.error.v1',
            error_code: 'CERA_TRANSPORT_RETRY_NOT_FOUND',
            message: 'The transport Retry identity is unavailable.',
            stage: 'pi_scene_http',
            story_state_committed: false,
            retry_mode: 'not_applicable',
            fallback_used: false,
            provider_operation_submitted: false,
            accepted_state_changed: false,
            next_action: 'check_transport_retry_identity',
            retry_transport_enabled: false,
        },
    };
}

function exactKeys(value, keys) {
    return Object.keys(value).sort().join(',') === [...keys].sort().join(',');
}

function safeProxyError(response, status, code, message) {
    if (response.headersSent) return;
    response.status(status).json({
        error: {
            message,
            type: 'cera_error',
            code,
            stage: 'sillytavern_cera_review_proxy',
            retryable: false,
            fallback_used: false,
        },
    });
}

async function forwardJson(
    response,
    url,
    {
        method = 'GET',
        body,
        timeoutMs,
        authorization,
        projectPayload = value => value,
        notFoundPayload = null,
    },
) {
    const normalizedAuthorization = normalizeAuthorization(authorization);
    let upstream;
    try {
        upstream = await fetch(url, {
            method,
            headers: {
                Accept: 'application/json',
                Authorization: normalizedAuthorization,
                ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
            },
            body: body === undefined ? undefined : JSON.stringify(body),
            redirect: 'error',
            signal: AbortSignal.timeout(timeoutMs),
        });
    } catch {
        safeProxyError(
            response,
            502,
            'cera_loopback_unavailable',
            'SillyTavern could not reach the local CERA service. Continuation is blocked.',
        );
        return;
    }
    if (upstream.status === 404 && notFoundPayload !== null) {
        response.set('Cache-Control', 'no-store');
        response.status(404).json(structuredClone(notFoundPayload));
        return;
    }

    let payload;
    try {
        const declaredLength = Number(upstream.headers.get('content-length') ?? 0);
        if (declaredLength > MAX_UPSTREAM_BYTES) throw new RangeError('response too large');
        const text = await upstream.text();
        if (Buffer.byteLength(text, 'utf8') > MAX_UPSTREAM_BYTES) {
            throw new RangeError('response too large');
        }
        payload = projectPayload(JSON.parse(text));
    } catch {
        safeProxyError(
            response,
            502,
            'cera_loopback_invalid_response',
            'The local CERA service returned an invalid review response. Continuation is blocked.',
        );
        return;
    }
    response.set('Cache-Control', 'no-store');
    response.status(upstream.status).json(payload);
}

export async function init(router) {
    router.get('/health', async (request, response) => {
        try {
            await forwardJson(response, `${CERA_LOOPBACK_ROOT}/v1/health`, {
                timeoutMs: GET_TIMEOUT_MS,
                authorization: request.get('X-Cera-Authorization'),
            });
        } catch (error) {
            safeProxyError(response, 401, 'cera_review_authorization_invalid', error.message);
        }
    });

    router.get('/v1/cera/reviews/:reviewId', async (request, response) => {
        try {
            await forwardJson(response, reviewUpstreamUrl(request.params.reviewId), {
                timeoutMs: GET_TIMEOUT_MS,
                authorization: request.get('X-Cera-Authorization'),
                projectPayload: projectReviewPayload,
            });
        } catch (error) {
            const authorizationFailure = error.message === 'CERA review authorization is invalid';
            safeProxyError(
                response,
                authorizationFailure ? 401 : 400,
                authorizationFailure ? 'cera_review_authorization_invalid' : 'cera_review_id_invalid',
                error.message,
            );
        }
    });

    router.get('/v1/cera/reviews/:reviewId/terminal-decision', async (request, response) => {
        try {
            await forwardJson(
                response,
                reviewUpstreamUrl(request.params.reviewId, { terminalDecision: true }),
                {
                    timeoutMs: GET_TIMEOUT_MS,
                    authorization: request.get('X-Cera-Authorization'),
                    projectPayload: projectReviewDecisionPayload,
                    notFoundPayload: {
                        error: {
                            message: 'No durable terminal creator decision is available.',
                            type: 'cera_error',
                            code: 'cera_terminal_decision_not_found',
                            stage: 'sillytavern_cera_review_proxy',
                            retryable: false,
                            fallback_used: false,
                        },
                    },
                },
            );
        } catch (error) {
            const authorizationFailure = error.message === 'CERA review authorization is invalid';
            safeProxyError(
                response,
                authorizationFailure ? 401 : 400,
                authorizationFailure
                    ? 'cera_review_authorization_invalid'
                    : 'cera_review_id_invalid',
                error.message,
            );
        }
    });

    router.post('/v1/cera/reviews/:reviewId/decision', async (request, response) => {
        try {
            const body = normalizeDecisionBody(request.body);
            await forwardJson(
                response,
                reviewUpstreamUrl(request.params.reviewId, { decision: true }),
                {
                    method: 'POST',
                    body,
                    timeoutMs: DECISION_TIMEOUT_MS,
                    authorization: request.get('X-Cera-Authorization'),
                    projectPayload: projectReviewDecisionPayload,
                },
            );
        } catch (error) {
            const authorizationFailure = error.message === 'CERA review authorization is invalid';
            safeProxyError(
                response,
                authorizationFailure ? 401 : 400,
                authorizationFailure ? 'cera_review_authorization_invalid' : 'cera_review_decision_invalid',
                error.message,
            );
        }
    });

    router.get('/v1/cera/provider-stage-retries/:chainId', async (request, response) => {
        try {
            await forwardJson(
                response,
                providerStageRetryUpstreamUrl(request.params.chainId),
                {
                    timeoutMs: GET_TIMEOUT_MS,
                    authorization: request.get('X-Cera-Authorization'),
                    projectPayload: projectProviderStageRetryResult,
                },
            );
        } catch (error) {
            const authorizationFailure = error.message === 'CERA review authorization is invalid';
            safeProxyError(
                response,
                authorizationFailure ? 401 : 400,
                authorizationFailure
                    ? 'cera_review_authorization_invalid'
                    : 'cera_provider_stage_retry_invalid',
                error.message,
            );
        }
    });

    router.post(
        '/v1/cera/provider-stage-retries/:chainId/actions/:actionId',
        async (request, response) => {
            try {
                const body = normalizeProviderStageRetryActionBody(request.body, {
                    chainId: request.params.chainId,
                    actionId: request.params.actionId,
                });
                await forwardJson(
                    response,
                    providerStageRetryUpstreamUrl(request.params.chainId, {
                        actionId: request.params.actionId,
                    }),
                    {
                        method: 'POST',
                        body,
                        timeoutMs: TRANSPORT_RETRY_TIMEOUT_MS,
                        authorization: request.get('X-Cera-Authorization'),
                        projectPayload: projectProviderStageRetryResult,
                    },
                );
            } catch (error) {
                const authorizationFailure = error.message === 'CERA review authorization is invalid';
                safeProxyError(
                    response,
                    authorizationFailure ? 401 : 400,
                    authorizationFailure
                        ? 'cera_review_authorization_invalid'
                        : 'cera_provider_stage_retry_invalid',
                    error.message,
                );
            }
        },
    );

    router.get('/v1/cera/transport-retries/:retryId', async (request, response) => {
        try {
            await forwardJson(
                response,
                transportRetryUpstreamUrl(request.params.retryId),
                {
                    timeoutMs: GET_TIMEOUT_MS,
                    authorization: request.get('X-Cera-Authorization'),
                    projectPayload: projectTransportRetryStatusPayload,
                },
            );
        } catch (error) {
            const authorizationFailure = error.message === 'CERA review authorization is invalid';
            safeProxyError(
                response,
                authorizationFailure ? 401 : 400,
                authorizationFailure
                    ? 'cera_review_authorization_invalid'
                    : 'cera_transport_retry_invalid',
                error.message,
            );
        }
    });

    router.post('/v1/cera/transport-retries/:retryId', async (request, response) => {
        try {
            const body = normalizeTransportRetryBody(request.body);
            await forwardJson(
                response,
                transportRetryUpstreamUrl(request.params.retryId),
                {
                    method: 'POST',
                    body,
                    timeoutMs: TRANSPORT_RETRY_TIMEOUT_MS,
                    authorization: request.get('X-Cera-Authorization'),
                    projectPayload: projectTransportRetryPayload,
                },
            );
        } catch (error) {
            const authorizationFailure = error.message === 'CERA review authorization is invalid';
            safeProxyError(
                response,
                authorizationFailure ? 401 : 400,
                authorizationFailure
                    ? 'cera_review_authorization_invalid'
                    : 'cera_transport_retry_invalid',
                error.message,
            );
        }
    });
}
