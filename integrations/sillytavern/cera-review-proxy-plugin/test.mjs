import assert from 'node:assert/strict';
import test from 'node:test';

import {
    info,
    init,
    normalizeAuthorization,
    normalizeDecisionBody,
    normalizeLoopbackRoot,
    normalizeReviewId,
    reviewUpstreamUrl,
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
    ]);
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
