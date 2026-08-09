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
