const CERA_LOOPBACK_ROOT = 'http://127.0.0.1:5101';
const MAX_UPSTREAM_BYTES = 2_000_000;
const GET_TIMEOUT_MS = 10_000;
const DECISION_TIMEOUT_MS = 900_000;
const REVIEW_ID_PATTERN = /^(?:[a-z][a-z0-9_]{0,31}:[A-Za-z0-9._-]{1,160}|review-[a-f0-9]{28})$/;
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
    description: 'Relays narrow authenticated SillyTavern review requests to loopback-only CERA.',
});

export function normalizeReviewId(value) {
    if (typeof value !== 'string' || !REVIEW_ID_PATTERN.test(value)) {
        throw new TypeError('CERA review ID is invalid');
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

export function reviewUpstreamUrl(reviewId, { decision = false } = {}) {
    const encoded = encodeURIComponent(normalizeReviewId(reviewId));
    return `${CERA_LOOPBACK_ROOT}/v1/cera/reviews/${encoded}${decision ? '/decision' : ''}`;
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

async function forwardJson(response, url, { method = 'GET', body, timeoutMs, authorization }) {
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
        payload = JSON.parse(text);
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
}
