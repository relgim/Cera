const DEFAULT_CERA_LOOPBACK_ROOT = 'http://127.0.0.1:5101';
const MAX_UPSTREAM_BYTES = 2_000_000;
const GET_TIMEOUT_MS = 10_000;
const DECISION_TIMEOUT_MS = 900_000;
const TRANSPORT_RETRY_TIMEOUT_MS = 4_200_000;
const REVIEW_ID_PATTERN = /^(?:[a-z][a-z0-9_]{0,31}:[A-Za-z0-9._-]{1,160}|review-[a-f0-9]{28})$/;
const TRANSPORT_RETRY_ID_PATTERN = /^retry-[a-f0-9]{64}$/;
const REQUEST_ID_PATTERN = /^request-[a-f0-9]{64}$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;
const AUTHORIZATION_PATTERN = /^Bearer [A-Za-z0-9._~-]{24,512}$/;
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
    description: 'Relays narrow authenticated review and manual transport-retry requests to loopback-only CERA.',
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

export function reviewUpstreamUrl(
    reviewId,
    { decision = false, loopbackRoot = CERA_LOOPBACK_ROOT } = {},
) {
    const encoded = encodeURIComponent(normalizeReviewId(reviewId));
    const root = normalizeLoopbackRoot(loopbackRoot);
    return `${root}/v1/cera/reviews/${encoded}${decision ? '/decision' : ''}`;
}

export function transportRetryUpstreamUrl(
    retryId,
    { loopbackRoot = CERA_LOOPBACK_ROOT } = {},
) {
    const encoded = encodeURIComponent(normalizeTransportRetryId(retryId));
    const root = normalizeLoopbackRoot(loopbackRoot);
    return `${root}/v1/cera/transport-retries/${encoded}`;
}

export function projectTransportRetryPayload(value) {
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
    { method = 'GET', body, timeoutMs, authorization, projectPayload = value => value },
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
