import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
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
    projectProviderStageRetryResult,
    projectProviderStageRetryStatusEnvelope,
    projectReviewDecisionPayload,
    projectReviewPayload,
    projectTransportRetryPayload,
    projectTransportRetryStatusPayload,
    reviewUpstreamUrl,
    transportRetryUpstreamUrl,
} from './index.js';

function bindDetachedDecisionHash(value) {
    const decision = structuredClone(value);
    const detached = structuredClone(decision);
    detached.review.terminal_decision = null;
    decision.review.terminal_decision.decision_sha256 = createHash('sha256')
        .update(canonicalJson(detached), 'utf8')
        .digest('hex');
    return decision;
}

function canonicalJson(value) {
    if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
    if (value && typeof value === 'object') {
        return `{${Object.keys(value).sort().map(key => (
            `${JSON.stringify(key)}:${canonicalJson(value[key])}`
        )).join(',')}}`;
    }
    return JSON.stringify(value);
}

function reviewCheck({
    role,
    required,
    status,
    verdictCharacter = '8',
    failures = [],
    retry = null,
    hasVerdict = ['pass', 'reject'].includes(status),
}) {
    return {
        role,
        required,
        status,
        verdict_sha256: hasVerdict ? verdictCharacter.repeat(64) : null,
        failures,
        provider_stage_retry_status: retry,
    };
}

function stageCheckEnvelope(stage, chainCharacter) {
    const envelope = providerStageRetryEnvelope('eligible', { chainCharacter });
    const owner = {
        semantic_validator: ['codex', 'luna'],
        reader: ['codex', 'sol'],
        adult_filter: ['deepseek', 'deepseek_v4'],
    }[stage];
    envelope.status.stage = stage;
    [envelope.status.provider, envelope.status.model_family] = owner;
    return envelope;
}

function reviewV2({
    mode = 'automatic',
    state = 'checks_pending',
    gate = 'pending',
    checks = null,
    actions = null,
    acceptance = null,
    creatorGuidance = null,
    recordingStatus = null,
} = {}) {
    const requiredChecks = checks ?? {
        luna: reviewCheck({
            role: 'luna_semantic_validator',
            required: true,
            status: 'pending',
        }),
        reader: reviewCheck({
            role: 'codex_reader_severe_quality',
            required: true,
            status: 'pending',
        }),
        adult_filter: reviewCheck({
            role: 'protected_adult_filter',
            required: false,
            status: 'not_applicable',
        }),
        python: reviewCheck({
            role: 'python_deterministic_custody_privacy',
            required: true,
            status: 'pending',
        }),
    };
    const noActions = {
        accept_enabled: false,
        auditable_override_action: null,
        auditable_override_enabled: false,
        decline_enabled: false,
        regenerate_enabled: false,
        repair_recording_enabled: false,
        replan_enabled: false,
    };
    const reviewId = `review-${'a'.repeat(28)}`;
    const resolved = ['accepted', 'declined', 'regenerated', 'replanned'].includes(state);
    const disposition = reviewAttemptDisposition(gate, requiredChecks);
    const attemptOperations = {
        planner: 1,
        writer: 1,
        validator: requiredChecks.luna.status === 'pending' ? 0 : 1,
        reader: requiredChecks.reader.status === 'pending' ? 0 : 1,
    };
    const recorderOperations = recordingStatus === 'pending_repair'
        ? 3
        : recordingStatus === 'complete'
            ? 1
            : 0;
    return {
        schema_version: 'cera.pi_scene.review.v2',
        review_id: reviewId,
        state,
        route: 'ordinary',
        story_text: 'Exact Writer prose.',
        candidate_id: `candidate-${'b'.repeat(28)}`,
        candidate_sha256: '1'.repeat(64),
        primary_authority_kind: 'codex_cognition_plan',
        primary_authority_sha256: '2'.repeat(64),
        warnings: [],
        recording_status: recordingStatus,
        terminal_decision: resolved ? {
            decision_sha256: '5'.repeat(64),
            url: `/v1/cera/reviews/${reviewId}/terminal-decision`,
        } : null,
        request_controls: requestControls(mode),
        creator_guidance: creatorGuidance,
        provider_attempts: [{
            attempt_number: 1,
            candidate_id: `candidate-${'b'.repeat(28)}`,
            disposition,
            provider_operations: attemptOperations,
        }],
        provider_operations: {
            ...attemptOperations, recorder: recorderOperations,
        },
        review_mode: mode,
        gate_status: gate,
        checks: { schema_version: 'cera.pi_scene.review_checks.v1', ...requiredChecks },
        acceptance,
        actions: actions ?? noActions,
    };
}

function requestControls(reviewMode) {
    return {
        schema_version: 'cera.pi_scene.request_controls.v3',
        session_id: 'manual-review-test',
        scene_depth: 'auto',
        regeneration_key: null,
        character_autonomy: 'both',
        prompt_handling: 'adjustment',
        reasoning_effort: 'medium',
        scene_change: false,
        adult_craft_mode: 'off',
        review_mode: reviewMode,
    };
}

function reviewAttemptDisposition(gate, checks) {
    if (gate === 'blocked') return 'checks_blocked';
    if (gate === 'pass') return 'checks_passed';
    if (gate === 'reject') {
        const luna = checks.luna.status === 'reject';
        const reader = checks.reader.status === 'reject';
        if (luna && reader) return 'luna_reader_rejected';
        if (luna) return 'luna_rejected';
        if (reader) return 'reader_rejected';
    }
    return 'checks_pending';
}

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

function providerStageRetryEnvelope(state, { chainCharacter = 'a', prepared = false } = {}) {
    const config = {
        eligible: ['writer', 1, 0, 1, 1, 'transport_timeout', 'provider_retry'],
        in_progress: ['writer', 2, 1, 1, 1, null, prepared ? 'resume_prepared' : null],
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
        provider_dispatch_authorized: [
            'provider_retry', 'resume_prepared', 'repair_recording',
        ].includes(actionKind),
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

function providerStageReview(reviewId) {
    return {
        schema_version: 'cera.pi_scene.review.v1',
        review_id: reviewId,
        state: 'regenerated',
        provisional: false,
        route: 'ordinary',
        story_text: 'Visible reviewed story.',
        candidate_id: 'candidate:provider-stage-review',
        candidate_sha256: '1'.repeat(64),
        primary_authority_kind: 'codex_cognition_plan',
        primary_authority_sha256: '2'.repeat(64),
        warnings: [],
        warnings_block_accept: false,
        recording_status: null,
        story_state_committed: false,
        canon_status: 'unaccepted',
        semantic_validation: null,
        request_controls: null,
        creator_guidance: null,
        accept_enabled: false,
        provisional_accept_enabled: false,
        decline_enabled: false,
        regenerate_enabled: false,
        replan_enabled: false,
        repair_recording_enabled: false,
        provider_operations: { planner: 1, writer: 1, recorder: 0 },
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
        ['GET', '/v1/cera/reviews/:reviewId/terminal-decision', 'function'],
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
    const repair = providerStageRetryEnvelope('recording_repair_required');
    assert.deepEqual(
        normalizeProviderStageRetryActionBody(repair.actions[0], {
            chainId: repair.status.chain_id,
            actionId: repair.actions[0].action_id,
        }),
        repair.actions[0],
    );
    const prepared = providerStageRetryEnvelope('in_progress', { prepared: true });
    assert.deepEqual(
        normalizeProviderStageRetryActionBody(prepared.actions[0], {
            chainId: prepared.status.chain_id,
            actionId: prepared.actions[0].action_id,
        }),
        prepared.actions[0],
    );
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

test('provider-stage result union accepts bound completion and creator decision only', () => {
    const completion = {
        id: 'chatcmpl-stage-continuation',
        object: 'chat.completion',
        created: 1,
        model: 'cera-alpha',
        choices: [{
            index: 0,
            message: { role: 'assistant', content: 'Recovered story.' },
            finish_reason: 'stop',
        }],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        cera: {
            profile_id: 'cera.pi_scene.lean.v1',
            request_id: `request-${'1'.repeat(64)}`,
            provider_stage_request_sha256: '2'.repeat(64),
        },
    };
    const decision = {
        schema_version: 'cera.pi_scene.review_decision.v1',
        status: 'review_transitioned',
        creator_action: 'regenerate',
        story_state_committed: false,
        retry_mode: 'not_applicable',
        review: providerStageReview(`review-${'a'.repeat(28)}`),
        successor: null,
        operational_warnings: [],
    };
    assert.deepEqual(projectProviderStageRetryResult(completion), completion);
    assert.deepEqual(projectProviderStageRetryResult(decision), decision);
    assert.deepEqual(
        projectProviderStageRetryResult(providerStageRetryEnvelope('eligible')),
        providerStageRetryEnvelope('eligible'),
    );
    assert.throws(() => projectProviderStageRetryResult({
        ...completion,
        cera: { ...completion.cera, provider_stage_request_sha256: 'wrong' },
    }));
    assert.throws(() => projectProviderStageRetryResult({
        ...completion,
        cera: { ...completion.cera, raw_provider_output: 'private' },
    }));
    assert.throws(() => projectProviderStageRetryResult({
        ...decision,
        retry_mode: 'provider_retry',
    }));
    assert.throws(() => projectProviderStageRetryResult({
        ...decision,
        debug_log_path: 'D:\\private.txt',
    }));
    assert.throws(() => projectProviderStageRetryResult({
        ...decision,
        review: { ...decision.review, raw_provider_output: 'private' },
    }));
    assert.throws(() => projectProviderStageRetryResult({
        ...decision,
        review: { ...decision.review, internal_note: 'not a public review field' },
    }));
});

test('provider-stage GET and POST preserve durable terminal result shapes', async () => {
    const routes = new Map();
    const router = {
        get(path, handler) { routes.set(`GET ${path}`, handler); },
        post(path, handler) { routes.set(`POST ${path}`, handler); },
    };
    await init(router);
    const eligible = providerStageRetryEnvelope('eligible');
    const action = eligible.actions[0];
    const completion = {
        id: 'chatcmpl-stage-continuation',
        object: 'chat.completion',
        created: 1,
        model: 'cera-alpha',
        choices: [{
            index: 0,
            message: { role: 'assistant', content: 'Recovered story.' },
            finish_reason: 'stop',
        }],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        cera: {
            profile_id: 'cera.pi_scene.lean.v1',
            request_id: `request-${'1'.repeat(64)}`,
            provider_stage_request_sha256: eligible.status.technical_details.request_sha256,
        },
    };
    const decision = {
        schema_version: 'cera.pi_scene.review_decision.v1',
        status: 'review_transitioned',
        creator_action: 'regenerate',
        story_state_committed: false,
        retry_mode: 'not_applicable',
        review: providerStageReview(`review-${'a'.repeat(28)}`),
        successor: null,
        operational_warnings: [],
    };
    const replies = [completion, decision];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify(replies.shift()), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
    });
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
            params: { chainId: eligible.status.chain_id },
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
    assert.deepEqual(payloads, [completion, decision]);
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
    assert.equal(
        reviewUpstreamUrl('review-0123456789abcdef0123456789ab', {
            terminalDecision: true,
            loopbackRoot: 'http://127.0.0.1:64321',
        }),
        'http://127.0.0.1:64321/v1/cera/reviews/review-0123456789abcdef0123456789ab/terminal-decision',
    );
    assert.throws(() => reviewUpstreamUrl('review-0123456789abcdef0123456789ab', {
        decision: true,
        terminalDecision: true,
    }));
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
    assert.deepEqual(normalizeDecisionBody({
        action: 'accept_provisional',
        feedback: 'Creator audit reason.',
    }), {
        action: 'accept_provisional',
        feedback: 'Creator audit reason.',
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
    assert.throws(() => normalizeDecisionBody({ action: 'automatic_accept' }));
    assert.throws(() => normalizeDecisionBody({ action: 'accept_provisional' }));
    assert.throws(() => normalizeDecisionBody({
        action: 'accept_provisional', feedback: '   ',
    }));
    assert.throws(() => normalizeDecisionBody({ action: 'accept', hidden: true }));
    assert.throws(() => normalizeDecisionBody({ action: 'regenerate', force_rehydrate: 'yes' }));
    assert.throws(() => normalizeDecisionBody({ action: 'accept', feedback: 'x'.repeat(20_001) }));
});

test('review v2 keeps independent Luna and Reader retry authorities closed', () => {
    const luna = stageCheckEnvelope('semantic_validator', 'b');
    const reader = stageCheckEnvelope('reader', 'c');
    const payload = reviewV2({
        gate: 'blocked',
        checks: {
            luna: reviewCheck({
                role: 'luna_semantic_validator', required: true, status: 'inconclusive',
                retry: luna,
                failures: [{ code: 'provider_timeout', concise_explanation: 'Luna is blocked.' }],
            }),
            reader: reviewCheck({
                role: 'codex_reader_severe_quality', required: true, status: 'inconclusive',
                retry: reader,
                failures: [{ code: 'provider_timeout', concise_explanation: 'Reader is blocked.' }],
            }),
            adult_filter: reviewCheck({
                role: 'protected_adult_filter', required: false, status: 'not_applicable',
            }),
            python: reviewCheck({
                role: 'python_deterministic_custody_privacy', required: true, status: 'pass',
            }),
        },
    });
    const projected = projectReviewPayload(payload);
    assert.equal(projected.checks.luna.provider_stage_retry_status.status.chain_id, luna.status.chain_id);
    assert.equal(projected.checks.reader.provider_stage_retry_status.status.chain_id, reader.status.chain_id);
    assert.notEqual(
        projected.checks.luna.provider_stage_retry_status.status.chain_id,
        projected.checks.reader.provider_stage_retry_status.status.chain_id,
    );
    assert.throws(() => projectReviewPayload({ ...payload, raw_prompt: 'private' }));
});

test('technical lane failure without Retry projects blocked with no creator authority', () => {
    const payload = reviewV2({
        gate: 'blocked',
        checks: {
            luna: reviewCheck({
                role: 'luna_semantic_validator', required: true, status: 'inconclusive',
                failures: [{
                    code: 'validation_custody_unavailable',
                    concise_explanation: 'Luna validation custody needs manual recovery.',
                }],
            }),
            reader: reviewCheck({
                role: 'codex_reader_severe_quality', required: true, status: 'pending',
            }),
            adult_filter: reviewCheck({
                role: 'protected_adult_filter', required: false, status: 'not_applicable',
            }),
            python: reviewCheck({
                role: 'python_deterministic_custody_privacy', required: true, status: 'pending',
            }),
        },
    });
    const projected = projectReviewPayload(payload);
    assert.equal(projected.gate_status, 'blocked');
    assert.equal(projected.actions.auditable_override_enabled, false);
    assert.equal(projected.actions.regenerate_enabled, false);
    assert.equal(projected.checks.luna.provider_stage_retry_status, null);
});

test('known semantic rejection remains actionless until the peer checks join', () => {
    const payload = reviewV2({
        state: 'checks_pending',
        gate: 'reject',
        checks: {
            luna: reviewCheck({
                role: 'luna_semantic_validator', required: true, status: 'reject',
                failures: [{
                    code: 'semantic_conflict',
                    concise_explanation: 'Luna found a frozen continuity conflict.',
                }],
            }),
            reader: reviewCheck({
                role: 'codex_reader_severe_quality', required: true, status: 'pending',
            }),
            adult_filter: reviewCheck({
                role: 'protected_adult_filter', required: false, status: 'not_applicable',
            }),
            python: reviewCheck({
                role: 'python_deterministic_custody_privacy', required: true, status: 'pending',
            }),
        },
    });
    const projected = projectReviewPayload(payload);
    assert.equal(projected.state, 'checks_pending');
    assert.equal(projected.gate_status, 'reject');
    assert.equal(projected.checks.luna.status, 'reject');
    assert.equal(
        projected.checks.luna.failures[0].concise_explanation,
        'Luna found a frozen continuity conflict.',
    );
    assert.deepEqual(projected.actions, {
        accept_enabled: false,
        auditable_override_action: null,
        auditable_override_enabled: false,
        decline_enabled: false,
        regenerate_enabled: false,
        repair_recording_enabled: false,
        replan_enabled: false,
    });
});

test('review v2 carries only hash-bound creator guidance and rejects raw feedback', () => {
    const safe = reviewV2({
        creatorGuidance: {
            schema_version: 'cera.pi_scene.creator_guidance_projection.v1',
            action: 'replan',
            text_sha256: '4'.repeat(64),
        },
    });
    assert.deepEqual(projectReviewPayload(safe).creator_guidance, safe.creator_guidance);
    const sentinel = 'RAW-CREATOR-FEEDBACK-MUST-NOT-CROSS-REVIEW';
    assert.throws(() => projectReviewPayload({
        ...safe,
        creator_guidance: {
            ...safe.creator_guidance,
            text: sentinel,
        },
    }));
    assert.throws(() => projectReviewPayload({ ...safe, feedback: sentinel }));
});

test('manual pass exposes only backend Accept Regenerate and Decline authority', () => {
    const checks = {
        luna: reviewCheck({ role: 'luna_semantic_validator', required: true, status: 'pass' }),
        reader: reviewCheck({
            role: 'codex_reader_severe_quality', required: true, status: 'pass',
        }),
        adult_filter: reviewCheck({
            role: 'protected_adult_filter', required: false, status: 'not_applicable',
        }),
        python: reviewCheck({
            role: 'python_deterministic_custody_privacy', required: true, status: 'pass',
        }),
    };
    const payload = reviewV2({
        mode: 'manual',
        state: 'review_ready',
        gate: 'pass',
        checks,
        actions: {
            accept_enabled: true,
            auditable_override_action: null,
            auditable_override_enabled: false,
            decline_enabled: true,
            regenerate_enabled: true,
            repair_recording_enabled: false,
            replan_enabled: false,
        },
    });
    assert.equal(projectReviewPayload(payload).actions.regenerate_enabled, true);
    assert.throws(() => projectReviewPayload({
        ...payload,
        actions: { ...payload.actions, decline_enabled: false },
    }));
});

test('accepted recording repair is backend-authorized and never inferred from pending status', () => {
    const checks = {
        luna: reviewCheck({ role: 'luna_semantic_validator', required: true, status: 'pass' }),
        reader: reviewCheck({
            role: 'codex_reader_severe_quality', required: true, status: 'pass',
        }),
        adult_filter: reviewCheck({
            role: 'protected_adult_filter', required: false, status: 'not_applicable',
        }),
        python: reviewCheck({
            role: 'python_deterministic_custody_privacy', required: true, status: 'pass',
        }),
    };
    const acceptance = {
        mode: 'automatic',
        accepted_turn_id: 'turn-recorder-parent',
        accepted_receipt_sha256: '7'.repeat(64),
        canon_status: 'accepted',
    };
    const activeParent = reviewV2({
        state: 'accepted', gate: 'pass', checks, acceptance,
        recordingStatus: 'projection_pending',
    });
    assert.equal(projectReviewPayload(activeParent).actions.repair_recording_enabled, false);
    const repairRequired = {
        ...activeParent,
        recording_status: 'pending_repair',
        provider_operations: { ...activeParent.provider_operations, recorder: 3 },
        actions: { ...activeParent.actions, repair_recording_enabled: true },
    };
    assert.equal(projectReviewPayload(repairRequired).actions.repair_recording_enabled, true);
    assert.throws(() => projectReviewPayload({
        ...repairRequired,
        recording_status: 'complete',
    }));
});

test('adult remains on synchronous v1 projection and carries no Reader authority', () => {
    const payload = providerStageReview(`review-${'d'.repeat(28)}`);
    payload.route = 'adult';
    payload.story_text = null;
    payload.primary_authority_kind = 'adult_decision_plan';
    payload.provider_operations = { adult_scene: 1, adult_filter: 1, recorder: 0 };
    const projected = projectReviewPayload(payload);
    assert.equal(projected.schema_version, 'cera.pi_scene.review.v1');
    assert.equal(projected.story_text, null);
    assert.equal('checks' in projected, false);
    assert.equal(JSON.stringify(projected).includes('reader'), false);
    const attemptedAdultV2 = { ...reviewV2(), route: 'adult', story_text: null };
    assert.throws(() => projectReviewPayload(attemptedAdultV2));
});

test('auditable override preserves semantic rejection and requires Python pass', () => {
    const rejectedChecks = {
        luna: reviewCheck({
            role: 'luna_semantic_validator', required: true, status: 'reject',
            failures: [{ code: 'semantic_conflict', concise_explanation: 'Concise frozen conflict.' }],
        }),
        reader: reviewCheck({ role: 'codex_reader_severe_quality', required: true, status: 'pass' }),
        adult_filter: reviewCheck({
            role: 'protected_adult_filter', required: false, status: 'not_applicable',
        }),
        python: reviewCheck({
            role: 'python_deterministic_custody_privacy', required: true, status: 'pass',
        }),
    };
    const acceptance = {
        mode: 'auditable_override',
        accepted_turn_id: 'turn-override-1',
        accepted_receipt_sha256: '7'.repeat(64),
        canon_status: 'provisional',
    };
    const accepted = reviewV2({
        state: 'accepted', gate: 'reject', checks: rejectedChecks, acceptance,
    });
    assert.equal(projectReviewPayload(accepted).gate_status, 'reject');
    const pythonFailed = structuredClone(accepted);
    pythonFailed.checks.python = reviewCheck({
        role: 'python_deterministic_custody_privacy', required: true, status: 'reject',
        failures: [{ code: 'custody_failed', concise_explanation: 'Deterministic custody failed.' }],
    });
    assert.throws(() => projectReviewPayload(pythonFailed));
});

test('review decision v2 projects only backend-authoritative accepted evidence', () => {
    const checks = {
        luna: reviewCheck({ role: 'luna_semantic_validator', required: true, status: 'pass' }),
        reader: reviewCheck({ role: 'codex_reader_severe_quality', required: true, status: 'pass' }),
        adult_filter: reviewCheck({
            role: 'protected_adult_filter', required: false, status: 'not_applicable',
        }),
        python: reviewCheck({
            role: 'python_deterministic_custody_privacy', required: true, status: 'pass',
        }),
    };
    const review = reviewV2({
        state: 'accepted',
        gate: 'pass',
        checks,
        acceptance: {
            mode: 'automatic',
            accepted_turn_id: 'turn-auto-1',
            accepted_receipt_sha256: '6'.repeat(64),
            canon_status: 'accepted',
        },
    });
    const decision = bindDetachedDecisionHash({
        schema_version: 'cera.pi_scene.review_decision.v2',
        status: 'story_committed',
        creator_action: 'automatic_accept',
        story_state_committed: true,
        retry_mode: 'not_applicable',
        review,
        successor: null,
        operational_warnings: [],
        accepted_receipt_sha256: '6'.repeat(64),
        accepted_turn_id: 'turn-auto-1',
    });
    assert.equal(projectReviewDecisionPayload(decision).review.state, 'accepted');
    assert.throws(() => projectReviewDecisionPayload({
        ...decision,
        review: {
            ...review,
            acceptance: { ...review.acceptance, mode: 'manual' },
        },
    }));
    assert.throws(() => projectReviewDecisionPayload({ ...decision, debug_log_path: 'D:\\secret' }));
    assert.throws(() => projectReviewDecisionPayload({
        ...decision,
        feedback: 'RAW-TERMINAL-FEEDBACK-MUST-NOT-CROSS',
    }));
});

test('review decision v2 validates an exact provisional Regenerate successor', () => {
    const passedChecks = {
        luna: reviewCheck({ role: 'luna_semantic_validator', required: true, status: 'pass' }),
        reader: reviewCheck({
            role: 'codex_reader_severe_quality', required: true, status: 'pass',
        }),
        adult_filter: reviewCheck({
            role: 'protected_adult_filter', required: false, status: 'not_applicable',
        }),
        python: reviewCheck({
            role: 'python_deterministic_custody_privacy', required: true, status: 'pass',
        }),
    };
    const predecessor = reviewV2({
        mode: 'manual', state: 'regenerated', gate: 'pass', checks: passedChecks,
    });
    const successorReview = reviewV2({ mode: 'manual' });
    const lifecycle = {
        schema_version: 'cera.pi_scene.review_lifecycle.v1',
        review_id: successorReview.review_id,
        review_mode: successorReview.review_mode,
        state: successorReview.state,
        gate_status: successorReview.gate_status,
        checks: structuredClone(successorReview.checks),
        acceptance: null,
        actions: structuredClone(successorReview.actions),
        terminal_decision: null,
        review_url: `/v1/cera/reviews/${successorReview.review_id}`,
    };
    const successor = {
        id: 'chatcmpl-ordinary-successor',
        object: 'chat.completion',
        created: 1,
        model: 'cera-alpha',
        choices: [{
            index: 0,
            message: { role: 'assistant', content: 'Regenerated exact Writer prose.' },
            finish_reason: 'stop',
        }],
        usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
        cera: {
            profile_id: 'cera.pi_scene.lean.v1',
            request_id: `request-${'9'.repeat(64)}`,
            candidate_id: successorReview.candidate_id,
            candidate_sha256: successorReview.candidate_sha256,
            route_mode: 'ordinary',
            provisional: true,
            provisional_review_id: successorReview.review_id,
            status: 'checks_pending',
            story_state_committed: false,
            review_url: `/v1/cera/reviews/${successorReview.review_id}`,
            review_lifecycle: lifecycle,
        },
    };
    const decision = bindDetachedDecisionHash({
        schema_version: 'cera.pi_scene.review_decision.v2',
        status: 'review_transitioned',
        creator_action: 'regenerate',
        story_state_committed: false,
        retry_mode: 'not_applicable',
        review: predecessor,
        successor,
        operational_warnings: [],
    });
    const projected = projectReviewDecisionPayload(decision);
    assert.equal(projected.successor.cera.review_lifecycle.state, 'checks_pending');
    assert.equal(projected.successor.choices[0].message.content, 'Regenerated exact Writer prose.');

    const openSuccessor = structuredClone(decision);
    openSuccessor.successor.debug = 'not part of the exact completion';
    assert.throws(() => projectReviewDecisionPayload(bindDetachedDecisionHash(openSuccessor)));
    const mismatchedLifecycle = structuredClone(decision);
    mismatchedLifecycle.successor.cera.review_lifecycle.review_id = `review-${'f'.repeat(28)}`;
    assert.throws(() => projectReviewDecisionPayload(bindDetachedDecisionHash(mismatchedLifecycle)));
});

test('terminal decision relay recovers output-only automatic acceptance by fixed GET', async () => {
    const routes = new Map();
    const router = {
        get(path, handler) { routes.set(`GET ${path}`, handler); },
        post() {},
    };
    await init(router);
    const review = reviewV2({
        state: 'accepted',
        gate: 'pass',
        checks: {
            luna: reviewCheck({ role: 'luna_semantic_validator', required: true, status: 'pass' }),
            reader: reviewCheck({ role: 'codex_reader_severe_quality', required: true, status: 'pass' }),
            adult_filter: reviewCheck({
                role: 'protected_adult_filter', required: false, status: 'not_applicable',
            }),
            python: reviewCheck({
                role: 'python_deterministic_custody_privacy', required: true, status: 'pass',
            }),
        },
        acceptance: {
            mode: 'automatic',
            accepted_turn_id: 'turn-auto-terminal',
            accepted_receipt_sha256: '6'.repeat(64),
            canon_status: 'accepted',
        },
        recordingStatus: 'projection_pending',
    });
    const decision = bindDetachedDecisionHash({
        schema_version: 'cera.pi_scene.review_decision.v2',
        status: 'story_committed',
        creator_action: 'automatic_accept',
        story_state_committed: true,
        retry_mode: 'not_applicable',
        review,
        successor: null,
        operational_warnings: [],
        accepted_receipt_sha256: '6'.repeat(64),
        accepted_turn_id: 'turn-auto-terminal',
    });
    const calls = [];
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async (url, options) => {
        calls.push({ url, options });
        return new Response(JSON.stringify(decision), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
        });
    };
    let payload = null;
    const response = {
        headersSent: false,
        set() { return this; },
        status() { return this; },
        json(value) { payload = value; return this; },
    };
    try {
        await routes.get('GET /v1/cera/reviews/:reviewId/terminal-decision')({
            params: { reviewId: review.review_id },
            get() { return `Bearer ${'a'.repeat(43)}`; },
        }, response);
    } finally {
        globalThis.fetch = originalFetch;
    }
    assert.equal(calls.length, 1);
    assert.equal(calls[0].options.method, 'GET');
    assert.equal(payload.creator_action, 'automatic_accept');
    assert.equal(
        calls[0].url,
        `http://127.0.0.1:5101/v1/cera/reviews/${review.review_id}/terminal-decision`,
    );
    assert.equal(
        payload.review.terminal_decision.decision_sha256,
        decision.review.terminal_decision.decision_sha256,
    );
});

test('unresolved terminal decision returns a fixed safe 404 without projecting upstream prose', async () => {
    const routes = new Map();
    const router = {
        get(path, handler) { routes.set(`GET ${path}`, handler); },
        post() {},
    };
    await init(router);
    const reviewId = `review-${'f'.repeat(28)}`;
    const sentinel = 'RAW-UPSTREAM-NOT-FOUND-MUST-NOT-CROSS';
    const originalFetch = globalThis.fetch;
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: sentinel }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
    });
    let payload = null;
    let status = null;
    const response = {
        headersSent: false,
        set() { return this; },
        status(value) { status = value; return this; },
        json(value) { payload = value; return this; },
    };
    try {
        await routes.get('GET /v1/cera/reviews/:reviewId/terminal-decision')({
            params: { reviewId },
            get() { return `Bearer ${'a'.repeat(43)}`; },
        }, response);
    } finally {
        globalThis.fetch = originalFetch;
    }
    assert.equal(status, 404);
    assert.equal(payload.error.code, 'cera_terminal_decision_not_found');
    assert.equal(JSON.stringify(payload).includes(sentinel), false);
});
