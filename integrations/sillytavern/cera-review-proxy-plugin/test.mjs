import assert from 'node:assert/strict';
import test from 'node:test';

import {
    FULL_PIPELINE_TIMEOUT_MS,
    info,
    init,
    normalizeAuthorization,
    normalizeDecisionBody,
    normalizeLoopbackRoot,
    normalizeProviderStageRetryActionBody,
    normalizeProviderStageRetryActionId,
    normalizeProviderStageRetryChainId,
    normalizeReviewId,
    normalizeTransportRetryBody,
    normalizeTransportRetryId,
    providerStageRetryUpstreamUrl,
    projectProviderStageRetryAction,
    projectProviderStageRetryBlockedAmbiguous,
    projectProviderStageRetryExhausted,
    projectProviderStageRetryStatusEnvelope,
    projectTransportRetryPayload,
    projectTransportRetryStatusPayload,
    reviewUpstreamUrl,
    transportRetryUpstreamUrl,
} from './index.js';

function criticalProviderStageFailure({
    stage = 'planner',
    failureClass = 'transport_timeout',
    storyStateCommitted = stage === 'recorder',
} = {}) {
    const bindings = {
        planner: ['codex', 'sol'],
        semantic_validator: ['codex', 'luna'],
        writer: ['deepseek', 'deepseek_v4'],
        recorder: ['deepseek', 'deepseek_v4'],
        adult_scene: ['deepseek', 'deepseek_v4'],
        adult_filter: ['deepseek', 'deepseek_v4'],
    };
    const [provider, modelFamily] = bindings[stage];
    return {
        schema_version: 'cera.provider_stage_retry_exhausted.v1',
        severity: 'critical',
        provider,
        model_family: modelFamily,
        stage,
        maximum_attempts: 3,
        attempts_total: 3,
        retries_consumed: 2,
        story_state_committed: storyStateCommitted,
        failed_stage_effect_committed: false,
        provider_operations_observed_total: 2,
        provider_operations_conservative_total: 3,
        final_failure_class: failureClass,
        request_sha256: '1'.repeat(64),
        stage_input_sha256: '2'.repeat(64),
        attempt_chain_sha256: '3'.repeat(64),
        terminal_evidence_sha256: '4'.repeat(64),
    };
}

function exhaustedError(critical = criticalProviderStageFailure()) {
    return {
        status: 'error',
        story_state_committed: critical.story_state_committed,
        error: {
            schema_version: 'cera.error.v1',
            error_code: 'CERA_PROVIDER_STAGE_RETRY_EXHAUSTED',
            message: 'RAW PROVIDER FAILURE PROSE',
            trace_id: 'trace:private-local-id',
            request_id: `request-${'5'.repeat(64)}`,
            branch_id: null,
            generation_id: null,
            stage: 'pi_scene_http',
            story_state_committed: critical.story_state_committed,
            retry_mode: 'exhausted',
            details: ['PRIVATE PROVIDER OUTPUT'],
            fallback_used: false,
            provider_operation_submitted: true,
            accepted_state_changed: critical.story_state_committed,
            next_action: 'report_critical_provider_failure',
            debug_log_path: 'D:\\private\\provider-output.md',
            retry_transport_enabled: false,
            critical_provider_stage_failure: critical,
        },
    };
}

function providerStageRetryEnvelope(state, { chainCharacter = 'a' } = {}) {
    const config = {
        eligible: ['writer', 1, 0, 1, 1, 'transport_timeout', 'provider_retry'],
        in_progress: ['writer', 2, 1, 1, 1, null, null],
        succeeded: ['writer', 2, 1, 2, 2, null, null],
        blocked_ambiguous: ['adult_scene', 1, 0, 0, 1, 'dispatch_ambiguous', 'check_status'],
        attempts_exhausted: ['writer', 3, 2, 3, 3, 'provider_unavailable', null],
        recording_repair_required: [
            'recorder', 3, 2, 3, 3, 'provider_completion_incomplete', 'repair_recording',
        ],
        recovery_required: [
            'writer', 1, 0, 0, 0, 'provider_failure_not_retryable', null,
        ],
    }[state];
    const [stage, attempts, retries, observed, conservative, failure, actionKind] = config;
    const chainId = `stage-retry-${chainCharacter.repeat(64)}`;
    const chainSha256 = 'f'.repeat(64);
    const status = {
        schema_version: 'cera.provider_stage_retry_status.v1',
        chain_id: chainId,
        provider: 'deepseek',
        model_family: 'deepseek_v4',
        stage,
        state,
        maximum_attempts: 3,
        stage_attempts_total: attempts,
        retry_actions_accepted: retries,
        provider_operations_observed_total: observed,
        provider_operations_conservative_total: conservative,
        story_state_committed: stage === 'recorder',
        branch_preserved_at_last_accepted_head: true,
        failure_category: failure,
        available_actions: actionKind ? [actionKind] : [],
        technical_details: {
            schema_version: 'cera.provider_stage_retry_technical_details.v1',
            request_occurrence_sha256: 'b'.repeat(64),
            request_sha256: 'c'.repeat(64),
            stage_input_sha256: 'd'.repeat(64),
            accepted_state_sha256: 'e'.repeat(64),
            chain_sha256: chainSha256,
        },
    };
    const actions = actionKind ? [{
        schema_version: 'cera.provider_stage_retry_action.v1',
        action_id: `stage-action-${'9'.repeat(64)}`,
        chain_id: chainId,
        action_family: 'provider_stage_control',
        action_kind: actionKind,
        automatic: false,
        provider_dispatch_authorized: actionKind === 'provider_retry',
        consumes_retry_action: actionKind === 'provider_retry',
        retry_action_ordinal: actionKind === 'provider_retry' ? retries + 1 : null,
        whole_request_replay_authorized: false,
        provider_substitution_authorized: false,
        expected_chain_sha256: chainSha256,
    }] : [];
    return {
        schema_version: 'cera.provider_stage_retry_status_envelope.v1',
        status,
        actions,
    };
}

test('full-pipeline review decisions and transport retries share the outer safety ceiling', () => {
    assert.equal(FULL_PIPELINE_TIMEOUT_MS, 4_200_000);
});

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
        ['GET', '/v1/cera/provider-stage-retries/:chainId', 'function'],
        ['POST', '/v1/cera/provider-stage-retries/:chainId/actions/:actionId', 'function'],
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

test('terminal provider-stage projection is closed across all stages and failure classes', () => {
    const stages = [
        'planner',
        'semantic_validator',
        'writer',
        'recorder',
        'adult_scene',
        'adult_filter',
    ];
    const failureClasses = [
        'transport_timeout',
        'provider_unavailable',
        'provider_process_failed',
        'provider_stream_incomplete',
        'provider_completion_incomplete',
        'provider_output_invalid',
    ];
    for (const stage of stages) {
        for (const failureClass of failureClasses) {
            const value = criticalProviderStageFailure({ stage, failureClass });
            assert.deepEqual(projectProviderStageRetryExhausted(value), value);
        }
    }

    const base = criticalProviderStageFailure();
    for (const mutation of [
        { provider: 'deepseek' },
        { model_family: 'sol-medium' },
        { attempts_total: 2 },
        { retries_consumed: 3 },
        { failed_stage_effect_committed: true },
        { provider_operations_conservative_total: 1 },
        { final_failure_class: 'pretransport_failed' },
        { final_failure_class: 'dispatch_ambiguous' },
        { terminal_evidence_sha256: 'not-a-hash' },
        { raw_provider_output: 'private' },
    ]) {
        assert.throws(() => projectProviderStageRetryExhausted({ ...base, ...mutation }));
    }
    assert.throws(() => projectProviderStageRetryExhausted(
        criticalProviderStageFailure({ stage: 'recorder', storyStateCommitted: false }),
    ));
});

test('generated provider-stage envelope projects all seven states with exact action authority', () => {
    for (const state of [
        'eligible',
        'in_progress',
        'succeeded',
        'blocked_ambiguous',
        'attempts_exhausted',
        'recording_repair_required',
        'recovery_required',
    ]) {
        const envelope = providerStageRetryEnvelope(state);
        assert.deepEqual(projectProviderStageRetryStatusEnvelope(envelope), envelope);
        assert.deepEqual(projectTransportRetryStatusPayload(envelope), envelope);
        if (envelope.actions[0]) {
            assert.deepEqual(projectProviderStageRetryAction(envelope.actions[0]), envelope.actions[0]);
        }
    }
    const eligible = providerStageRetryEnvelope('eligible');
    assert.throws(() => projectProviderStageRetryStatusEnvelope({
        ...eligible,
        actions: [],
    }));
    assert.throws(() => projectProviderStageRetryStatusEnvelope({
        ...eligible,
        actions: [{
            ...eligible.actions[0],
            action_id: `stage-action-${'8'.repeat(64)}`,
            expected_chain_sha256: '7'.repeat(64),
        }],
    }));
    assert.throws(() => projectProviderStageRetryAction({
        ...eligible.actions[0],
        action_kind: 'replan',
    }));

    const blocked = providerStageRetryEnvelope('blocked_ambiguous').status;
    const evidence = {
        schema_version: 'cera.provider_stage_retry_blocked_ambiguous.v1',
        severity: 'critical',
        provider: blocked.provider,
        model_family: blocked.model_family,
        stage: blocked.stage,
        maximum_attempts: 3,
        attempts_total: blocked.stage_attempts_total,
        retries_consumed: blocked.retry_actions_accepted,
        story_state_committed: false,
        failed_stage_effect_committed: false,
        provider_operations_observed_total: blocked.provider_operations_observed_total,
        provider_operations_conservative_total: blocked.provider_operations_conservative_total,
        block_reason: 'dispatch_custody_ambiguous',
        request_sha256: blocked.technical_details.request_sha256,
        stage_input_sha256: blocked.technical_details.stage_input_sha256,
        attempt_chain_sha256: blocked.technical_details.chain_sha256,
        terminal_evidence_sha256: '6'.repeat(64),
    };
    assert.deepEqual(projectProviderStageRetryBlockedAmbiguous(evidence), evidence);
});

test('provider-stage relay IDs, URLs, and action body are fixed and backend-issued', () => {
    const envelope = providerStageRetryEnvelope('eligible');
    const { chain_id: chainId, action_id: actionId } = envelope.actions[0];
    assert.equal(normalizeProviderStageRetryChainId(chainId), chainId);
    assert.equal(normalizeProviderStageRetryActionId(actionId), actionId);
    assert.equal(
        providerStageRetryUpstreamUrl(chainId),
        `http://127.0.0.1:5101/v1/cera/provider-stage-retries/${chainId}`,
    );
    assert.equal(
        providerStageRetryUpstreamUrl(chainId, { actionId }),
        `http://127.0.0.1:5101/v1/cera/provider-stage-retries/${chainId}/actions/${actionId}`,
    );
    assert.deepEqual(
        normalizeProviderStageRetryActionBody(envelope.actions[0], { chainId, actionId }),
        envelope.actions[0],
    );
    assert.throws(() => normalizeProviderStageRetryActionBody(
        providerStageRetryEnvelope('blocked_ambiguous').actions[0],
        {
            chainId: providerStageRetryEnvelope('blocked_ambiguous').status.chain_id,
            actionId: providerStageRetryEnvelope('blocked_ambiguous').actions[0].action_id,
        },
    ));
    assert.throws(() => normalizeProviderStageRetryActionBody(
        providerStageRetryEnvelope('recording_repair_required').actions[0],
        {
            chainId: providerStageRetryEnvelope('recording_repair_required').status.chain_id,
            actionId: providerStageRetryEnvelope('recording_repair_required').actions[0].action_id,
        },
    ));
    assert.throws(() => normalizeProviderStageRetryActionBody(envelope.actions[0], {
        chainId: `stage-retry-${'0'.repeat(64)}`,
        actionId,
    }));
    assert.throws(() => normalizeProviderStageRetryChainId('../../reviews'));
    assert.throws(() => normalizeProviderStageRetryActionId(`stage-action-${'A'.repeat(64)}`));
});

test('HTTP exhaustion projection strips paths and prose and never exposes Retry', () => {
    const raw = exhaustedError();
    const projected = projectTransportRetryPayload(raw);
    assert.equal(projected.error.error_code, 'CERA_PROVIDER_STAGE_RETRY_EXHAUSTED');
    assert.equal(projected.error.retry_mode, 'exhausted');
    assert.equal(projected.error.retry_transport_enabled, false);
    assert.equal(projected.error.next_action, 'report_critical_provider_failure');
    assert.equal('transport_retry' in projected.error, false);
    assert.equal(JSON.stringify(projected).includes('RAW PROVIDER FAILURE PROSE'), false);
    assert.equal(JSON.stringify(projected).includes('PRIVATE PROVIDER OUTPUT'), false);
    assert.equal(JSON.stringify(projected).includes('provider-output.md'), false);
    assert.equal(JSON.stringify(projected).includes('private-local-id'), false);
    assert.deepEqual(
        projected.error.critical_provider_stage_failure,
        raw.error.critical_provider_stage_failure,
    );

    const invalid = projectTransportRetryPayload({
        ...raw,
        error: {
            ...raw.error,
            critical_provider_stage_failure: {
                ...raw.error.critical_provider_stage_failure,
                hidden_prompt: 'must not project',
            },
        },
    });
    assert.equal(invalid.error.error_code, 'CERA_TRANSPORT_RETRY_NOT_AVAILABLE');
    assert.equal(JSON.stringify(invalid).includes('must not project'), false);
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
    assert.throws(() => projectTransportRetryStatusPayload({
        ...values[2],
        completion: {
            ...completion,
            cera: { ...completion.cera, request_id: `request-${'0'.repeat(64)}` },
        },
    }));
});

test('v2 status projects attempts_exhausted without an action or successor', () => {
    const value = {
        schema_version: 'cera.pi_scene.transport_retry_status.v2',
        retry_id: `retry-${'a'.repeat(64)}`,
        request_id: `request-${'b'.repeat(64)}`,
        state: 'attempts_exhausted',
        effect_proof_sha256: 'c'.repeat(64),
        retry_transport_enabled: false,
        critical_provider_stage_failure: criticalProviderStageFailure({
            stage: 'adult_filter',
            failureClass: 'provider_output_invalid',
        }),
    };
    assert.deepEqual(projectTransportRetryStatusPayload(value), value);
    assert.equal('transport_retry' in projectTransportRetryStatusPayload(value), false);
    assert.equal('superseded_by_retry_id' in projectTransportRetryStatusPayload(value), false);
    assert.throws(() => projectTransportRetryStatusPayload({
        ...value,
        retry_transport_enabled: true,
    }));
    assert.throws(() => projectTransportRetryStatusPayload({
        ...value,
        successor_retry_id: `retry-${'d'.repeat(64)}`,
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
            debug_log_path: 'D:\\private\\retry-status.md',
            retry_transport_enabled: false,
        },
    };
    const projected = projectTransportRetryStatusPayload(value);
    assert.equal(projected.error.error_code, 'CERA_TRANSPORT_RETRY_NOT_FOUND');
    assert.equal(projected.error.retry_transport_enabled, false);
    assert.equal(JSON.stringify(projected).includes('private-local-id'), false);
    assert.equal(JSON.stringify(projected).includes('retry-status.md'), false);
    assert.equal('trace_id' in projected.error, false);
    assert.equal('debug_log_path' in projected.error, false);
    assert.throws(() => projectTransportRetryStatusPayload({
        ...value,
        error: { ...value.error, debug_log_path: 'x'.repeat(2_001) },
    }));
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

test('transport retry route preserves a sanitized terminal HTTP 503', async () => {
    const routes = new Map();
    const router = {
        get() {},
        post(path, handler) { routes.set(path, handler); },
    };
    await init(router);
    const retryId = `retry-${'d'.repeat(64)}`;
    const raw = exhaustedError(criticalProviderStageFailure({
        stage: 'recorder',
        failureClass: 'provider_stream_incomplete',
    }));
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify(raw), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
    });
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
            get() { return `Bearer ${'a'.repeat(43)}`; },
        }, response);
    } finally {
        globalThis.fetch = originalFetch;
    }
    assert.equal(responseStatus, 503);
    assert.equal(responsePayload.story_state_committed, true);
    assert.equal(responsePayload.error.retry_transport_enabled, false);
    assert.equal(responsePayload.error.critical_provider_stage_failure.stage, 'recorder');
    assert.equal(JSON.stringify(responsePayload).includes('RAW PROVIDER FAILURE PROSE'), false);
    assert.equal(JSON.stringify(responsePayload).includes('provider-output.md'), false);
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

test('provider-stage routes forward read-only GET and one exact backend-issued action POST', async () => {
    const routes = new Map();
    const router = {
        get(path, handler) { routes.set(`GET ${path}`, handler); },
        post(path, handler) { routes.set(`POST ${path}`, handler); },
    };
    await init(router);
    const eligible = providerStageRetryEnvelope('eligible');
    const inProgress = providerStageRetryEnvelope('in_progress');
    const blocked = providerStageRetryEnvelope('blocked_ambiguous');
    const action = eligible.actions[0];
    const calls = [];
    const responses = [blocked, inProgress];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return new Response(JSON.stringify(responses.shift()), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        });
    };
    const payloads = [];
    const response = {
        headersSent: false,
        set() { return this; },
        status() { return this; },
        json(value) { payloads.push(value); return this; },
    };
    const authorization = `Bearer ${'a'.repeat(43)}`;
    try {
        await routes.get('GET /v1/cera/provider-stage-retries/:chainId')({
            params: { chainId: blocked.status.chain_id },
            get() { return authorization; },
        }, response);
        await routes.get('POST /v1/cera/provider-stage-retries/:chainId/actions/:actionId')({
            params: { chainId: action.chain_id, actionId: action.action_id },
            body: action,
            get() { return authorization; },
        }, response);
    } finally {
        globalThis.fetch = originalFetch;
    }
    assert.equal(calls[0].options.method, 'GET');
    assert.equal(calls[0].options.body, undefined);
    assert.equal(calls[1].options.method, 'POST');
    assert.deepEqual(JSON.parse(calls[1].options.body), action);
    assert.deepEqual(payloads, [blocked, inProgress]);
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
