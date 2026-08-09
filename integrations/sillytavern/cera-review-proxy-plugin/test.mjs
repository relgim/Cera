import assert from 'node:assert/strict';
import test from 'node:test';

import {
    info,
    init,
    normalizeAuthorization,
    normalizeDecisionBody,
    normalizeLoopbackRoot,
    normalizeReviewId,
    normalizeTransportRetryBody,
    normalizeTransportRetryId,
    projectTransportRetryPayload,
    projectTransportRetryStatusPayload,
    reviewUpstreamUrl,
    transportRetryUpstreamUrl,
} from './index.js';

test('plugin registers only the narrow CERA relay routes', async () => {
    const routes = [];
    const router = {
        get(path, handler) { routes.push(['GET', path, typeof handler]); },
        post(path, handler) { routes.push(['POST', path, typeof handler]); },
    };
    await init(router);
    assert.equal(info.id, 'cera-review');
    assert.deepEqual(routes, [
        ['GET', '/health', 'function'],
        ['GET', '/v1/cera/reviews/:reviewId', 'function'],
        ['POST', '/v1/cera/reviews/:reviewId/decision', 'function'],
        ['GET', '/v1/cera/transport-retries/:retryId', 'function'],
        ['POST', '/v1/cera/transport-retries/:retryId', 'function'],
    ]);
});

test('transport retry relay accepts only one stable ID and an exact empty body', () => {
    const retryId = `retry-${'a'.repeat(64)}`;
    assert.equal(normalizeTransportRetryId(retryId), retryId);
    assert.equal(
        transportRetryUpstreamUrl(retryId),
        `http://127.0.0.1:5101/v1/cera/transport-retries/${retryId}`,
    );
    assert.deepEqual(normalizeTransportRetryBody({}), {});
    assert.throws(() => normalizeTransportRetryId(`retry-${'a'.repeat(63)}`));
    assert.throws(() => normalizeTransportRetryId(`retry-${'A'.repeat(64)}`));
    assert.throws(() => normalizeTransportRetryId('../../chat/completions'));
    assert.throws(() => normalizeTransportRetryBody(null));
    assert.throws(() => normalizeTransportRetryBody([]));
    assert.throws(() => normalizeTransportRetryBody({ force: true }));
});

test('transport retry projection removes raw paths and rejects incomplete proofs', () => {
    const retryId = `retry-${'e'.repeat(64)}`;
    const eligible = {
        status: 'error',
        story_state_committed: false,
        error: {
            schema_version: 'cera.error.v1',
            error_code: 'CERA_PROVIDER_TRANSPORT_FAILED',
            request_id: `request-${'f'.repeat(64)}`,
            story_state_committed: false,
            retry_mode: 'manual_transport',
            provider_operation_submitted: true,
            accepted_state_changed: false,
            fallback_used: false,
            next_action: 'use_transport_retry',
            retry_transport_enabled: true,
            debug_log_path: 'D:\\private\\debug.md',
            trace_id: 'trace:private',
            transport_retry: {
                schema_version: 'cera.pi_scene.transport_retry.v1',
                retry_id: retryId,
                retry_url: `/v1/cera/transport-retries/${retryId}`,
                method: 'POST',
                eligible: true,
                automatic: false,
                effect_proof_sha256: 'a'.repeat(64),
            },
        },
    };
    const projected = projectTransportRetryPayload(eligible);
    assert.equal(projected.error.retry_transport_enabled, true);
    assert.equal(projected.error.provider_operation_submitted, true);
    assert.equal(JSON.stringify(projected).includes('private'), false);
    assert.equal(projectTransportRetryPayload({ choices: [] }).choices.length, 0);

    const ambiguous = projectTransportRetryPayload({
        ...eligible,
        error: { ...eligible.error, accepted_state_changed: true },
    });
    assert.equal(ambiguous.error.retry_transport_enabled, false);
    assert.equal('transport_retry' in ambiguous.error, false);
});

test('transport retry status projection accepts only the five closed lifecycle states', () => {
    const retryId = `retry-${'1'.repeat(64)}`;
    const successorId = `retry-${'2'.repeat(64)}`;
    const common = {
        schema_version: 'cera.pi_scene.transport_retry_status.v1',
        retry_id: retryId,
        request_id: `request-${'3'.repeat(64)}`,
        effect_proof_sha256: '4'.repeat(64),
    };
    const action = {
        schema_version: 'cera.pi_scene.transport_retry.v1',
        retry_id: retryId,
        retry_url: `/v1/cera/transport-retries/${retryId}`,
        method: 'POST',
        eligible: true,
        automatic: false,
        effect_proof_sha256: common.effect_proof_sha256,
    };
    const completion = {
        id: 'completion:test',
        choices: [{ message: { content: 'A terminal result.' } }],
        cera: { profile_id: 'cera.pi_scene.lean.v1', request_id: common.request_id },
    };
    const values = [
        {
            ...common,
            state: 'eligible',
            retry_transport_enabled: true,
            transport_retry: action,
        },
        {
            ...common,
            state: 'in_progress',
            retry_transport_enabled: false,
            phase: 'dispatch_started',
        },
        {
            ...common,
            state: 'succeeded',
            retry_transport_enabled: false,
            completion,
            completion_sha256: '5'.repeat(64),
        },
        {
            ...common,
            state: 'superseded',
            retry_transport_enabled: true,
            superseded_by_retry_id: successorId,
            transport_retry: {
                ...action,
                retry_id: successorId,
                retry_url: `/v1/cera/transport-retries/${successorId}`,
                effect_proof_sha256: '6'.repeat(64),
            },
        },
        {
            ...common,
            state: 'blocked',
            retry_transport_enabled: false,
            blocked_reason_code: 'dispatch_state_ambiguous',
        },
    ];
    for (const value of values) {
        assert.deepEqual(projectTransportRetryStatusPayload(value), value);
    }
    assert.throws(() => projectTransportRetryStatusPayload({
        ...values[0],
        hidden_path: 'D:\\private\\receipt.json',
    }));
    assert.throws(() => projectTransportRetryStatusPayload({
        ...values[1],
        retry_transport_enabled: true,
    }));
    assert.throws(() => projectTransportRetryStatusPayload({
        ...values[3],
        superseded_by_retry_id: retryId,
    }));
    assert.throws(() => projectTransportRetryStatusPayload({
        ...values[4],
        blocked_reason_code: 'generic_failure',
    }));
});

test('transport retry status projection sanitizes the exact authenticated not-found envelope', () => {
    const value = {
        status: 'error',
        story_state_committed: false,
        error: {
            schema_version: 'cera.error.v1',
            error_code: 'CERA_TRANSPORT_RETRY_NOT_FOUND',
            message: 'The transport Retry identity is unavailable.',
            trace_id: 'trace:private-local-id',
            request_id: null,
            branch_id: null,
            generation_id: null,
            stage: 'pi_scene_http',
            story_state_committed: false,
            retry_mode: 'not_applicable',
            details: [],
            fallback_used: false,
            provider_operation_submitted: false,
            accepted_state_changed: false,
            next_action: 'check_transport_retry_identity',
            debug_log_path: null,
            retry_transport_enabled: false,
        },
    };
    const projected = projectTransportRetryStatusPayload(value);
    assert.equal(projected.error.error_code, 'CERA_TRANSPORT_RETRY_NOT_FOUND');
    assert.equal(projected.error.retry_transport_enabled, false);
    assert.equal(JSON.stringify(projected).includes('private-local-id'), false);
    assert.equal('trace_id' in projected.error, false);
});

test('transport retry route forwards one authenticated empty POST and preserves status', async () => {
    const routes = new Map();
    const router = {
        get() {},
        post(path, handler) { routes.set(path, handler); },
    };
    await init(router);
    const retryId = `retry-${'d'.repeat(64)}`;
    const calls = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return new Response(JSON.stringify({ status: 'error', error: { code: 'test' } }), {
            status: 500,
            headers: { 'Content-Type': 'application/json' },
        });
    };
    let responseStatus = null;
    let responsePayload = null;
    const response = {
        headersSent: false,
        set() { return this; },
        status(value) { responseStatus = value; return this; },
        json(value) { responsePayload = value; return this; },
    };
    try {
        await routes.get('/v1/cera/transport-retries/:retryId')({
            params: { retryId },
            body: {},
            get(name) {
                assert.equal(name, 'X-Cera-Authorization');
                return `Bearer ${'a'.repeat(43)}`;
            },
        }, response);
    } finally {
        globalThis.fetch = originalFetch;
    }
    assert.equal(calls.length, 1);
    assert.equal(
        calls[0].url,
        `http://127.0.0.1:5101/v1/cera/transport-retries/${retryId}`,
    );
    assert.equal(calls[0].options.method, 'POST');
    assert.equal(calls[0].options.body, '{}');
    assert.equal(calls[0].options.headers.Authorization, `Bearer ${'a'.repeat(43)}`);
    assert.equal(responseStatus, 500);
    assert.equal(responsePayload.error.retry_transport_enabled, false);
    assert.equal(responsePayload.error.error_code, 'CERA_TRANSPORT_RETRY_NOT_AVAILABLE');
    assert.equal('transport_retry' in responsePayload.error, false);
});

test('transport retry status route forwards one authenticated GET without dispatch', async () => {
    const routes = new Map();
    const router = {
        get(path, handler) { routes.set(`GET ${path}`, handler); },
        post() {},
    };
    await init(router);
    const retryId = `retry-${'7'.repeat(64)}`;
    const status = {
        schema_version: 'cera.pi_scene.transport_retry_status.v1',
        retry_id: retryId,
        request_id: `request-${'8'.repeat(64)}`,
        state: 'in_progress',
        effect_proof_sha256: '9'.repeat(64),
        retry_transport_enabled: false,
        phase: 'owner_rotated',
    };
    const calls = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return new Response(JSON.stringify(status), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        });
    };
    let responseStatus = null;
    let responsePayload = null;
    const response = {
        headersSent: false,
        set() { return this; },
        status(value) { responseStatus = value; return this; },
        json(value) { responsePayload = value; return this; },
    };
    try {
        await routes.get('GET /v1/cera/transport-retries/:retryId')({
            params: { retryId },
            get() { return `Bearer ${'a'.repeat(43)}`; },
        }, response);
    } finally {
        globalThis.fetch = originalFetch;
    }
    assert.equal(calls.length, 1);
    assert.equal(calls[0].options.method, 'GET');
    assert.equal(calls[0].options.body, undefined);
    assert.equal(responseStatus, 200);
    assert.deepEqual(responsePayload, status);
});

test('review authorization accepts only a bounded bearer credential', () => {
    const authorization = `Bearer ${'a'.repeat(43)}`;
    assert.equal(normalizeAuthorization(authorization), authorization);
    assert.throws(() => normalizeAuthorization(''));
    assert.throws(() => normalizeAuthorization('Basic abc'));
    assert.throws(() => normalizeAuthorization(`Bearer ${'a'.repeat(23)}`));
    assert.throws(() => normalizeAuthorization(`${authorization}\r\nX-Injected: true`));
});

test('review identities are validated and encoded without arbitrary proxying', () => {
    assert.equal(normalizeReviewId('review_packet:abc-123'), 'review_packet:abc-123');
    assert.equal(
        normalizeReviewId('review-0123456789abcdef0123456789ab'),
        'review-0123456789abcdef0123456789ab',
    );
    assert.equal(
        reviewUpstreamUrl('review_packet:abc-123'),
        'http://127.0.0.1:5101/v1/cera/reviews/review_packet%3Aabc-123',
    );
    assert.equal(
        reviewUpstreamUrl('review-0123456789abcdef0123456789ab'),
        'http://127.0.0.1:5101/v1/cera/reviews/review-0123456789abcdef0123456789ab',
    );
    assert.equal(normalizeLoopbackRoot('http://127.0.0.1:64321'), 'http://127.0.0.1:64321');
    assert.equal(
        reviewUpstreamUrl('review-0123456789abcdef0123456789ab', {
            decision: true,
            loopbackRoot: 'http://127.0.0.1:64321',
        }),
        'http://127.0.0.1:64321/v1/cera/reviews/review-0123456789abcdef0123456789ab/decision',
    );
    assert.throws(() => normalizeLoopbackRoot('https://127.0.0.1:5101'));
    assert.throws(() => normalizeLoopbackRoot('http://localhost:5101'));
    assert.throws(() => normalizeLoopbackRoot('http://127.0.0.1:5101/admin'));
    assert.throws(() => normalizeLoopbackRoot('http://127.0.0.1:5101?target=other'));
    assert.throws(() => normalizeLoopbackRoot('http://127.0.0.2:5101'));
    assert.throws(() => normalizeReviewId('../../admin'));
    assert.throws(() => normalizeReviewId('review_packet:abc/decision'));
    assert.throws(() => normalizeReviewId('review-0123456789abcdef0123456789a'));
    assert.throws(() => normalizeReviewId('review-0123456789abcdef0123456789aB'));
});

test('decision bodies retain only typed creator actions and bounded feedback', () => {
    assert.deepEqual(normalizeDecisionBody({ action: 'accept' }), {
        action: 'accept',
        feedback: null,
    });
    assert.deepEqual(normalizeDecisionBody({ action: 'accept_provisional' }), {
        action: 'accept_provisional',
        feedback: null,
    });
    assert.deepEqual(normalizeDecisionBody({ action: 'false_positive' }), {
        action: 'false_positive',
        feedback: null,
    });
    assert.deepEqual(
        normalizeDecisionBody({ action: 'codex_replan', feedback: 'Revise the causal plan.' }),
        { action: 'codex_replan', feedback: 'Revise the causal plan.' },
    );
    assert.deepEqual(normalizeDecisionBody({ action: 'decline' }), {
        action: 'decline',
        feedback: null,
    });
    assert.deepEqual(normalizeDecisionBody({ action: 'regenerate' }), {
        action: 'regenerate',
        feedback: null,
    });
    assert.deepEqual(
        normalizeDecisionBody({
            action: 'regenerate',
            feedback: 'Use a quieter realization.',
            force_rehydrate: true,
        }),
        {
            action: 'regenerate',
            feedback: 'Use a quieter realization.',
            force_rehydrate: true,
        },
    );
    assert.deepEqual(
        normalizeDecisionBody({ action: 'replan', feedback: 'Change the causal sequence.' }),
        { action: 'replan', feedback: 'Change the causal sequence.' },
    );
    assert.deepEqual(normalizeDecisionBody({ action: 'repair_recording' }), {
        action: 'repair_recording',
        feedback: null,
    });
    assert.throws(() => normalizeDecisionBody({ action: 'retry' }));
    assert.throws(() => normalizeDecisionBody({ action: 'accept', hidden: true }));
    assert.throws(() => normalizeDecisionBody({ action: 'regenerate', force_rehydrate: 'yes' }));
    assert.throws(() => normalizeDecisionBody({ action: 'accept', feedback: 'x'.repeat(20_001) }));
});
