import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const sourceRoot = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, value => value.slice(1)));

async function loadExtension() {
    const root = await mkdtemp(path.join(tmpdir(), 'cera-metadata-panel-'));
    const extension = path.join(
        root,
        'public',
        'scripts',
        'extensions',
        'third-party',
        'cera-creator-review',
    );
    await mkdir(extension, { recursive: true });
    await mkdir(path.join(root, 'public', 'scripts'), { recursive: true });
    await writeFile(path.join(root, 'package.json'), '{"type":"module"}\n');
    await writeFile(
        path.join(root, 'public', 'script.js'),
        `export const chat = [];
export const characters = [];
export const this_chid = 0;
export const event_types = {
  APP_READY: 'APP_READY', MESSAGE_RECEIVED: 'MESSAGE_RECEIVED',
  CHARACTER_MESSAGE_RENDERED: 'CHARACTER_MESSAGE_RENDERED',
  CHAT_CHANGED: 'CHAT_CHANGED', GENERATION_ENDED: 'GENERATION_ENDED'
};
export const eventSource = { on() {} };
export function addOneMessage() {}
export function activateSendButtons() {}
export function deactivateSendButtons() {}
export function getCurrentChatId() { return 'test-chat'; }
export function getMessageTimeStamp() { return 'test-time'; }
export function getRequestHeaders() { return {}; }
export async function saveChatConditional() {}
export function updateMessageBlock() {}
`,
    );
    await writeFile(
        path.join(root, 'public', 'scripts', 'openai.js'),
        `export const oai_settings = { custom_include_headers: '' };\n`,
    );
    for (const name of [
        'index.js',
        'completion-metadata.js',
        'creator-trace-panel.js',
        'review-actions.js',
    ]) {
        await writeFile(
            path.join(extension, name),
            await readFile(path.join(sourceRoot, name), 'utf8'),
        );
    }

    class TestCustomEvent extends Event {
        constructor(type, options = {}) {
            super(type);
            this.detail = options.detail;
        }
    }
    globalThis.CustomEvent = TestCustomEvent;
    globalThis.window = new EventTarget();
    globalThis.window.ceraCompletionMetadataQueue = [];
    globalThis.localStorage = { getItem() { return null; }, setItem() {} };
    globalThis.document = { querySelector() { return null; } };
    const module = await import(`${pathToFileURL(path.join(extension, 'index.js')).href}?v=${Date.now()}`);
    const metadataModule = await import(
        `${pathToFileURL(path.join(extension, 'completion-metadata.js')).href}?v=${Date.now()}`
    );
    return { module, metadataModule, root };
}

test('full-model metadata is allowlisted and protected adult prose is never queued', async () => {
    const { metadataModule, root } = await loadExtension();
    try {
        const protectedSentinel = 'PROTECTED-ADULT-PROSE-MUST-NOT-DUPLICATE';
        const raw = {
            profile_id: 'cera.pi_scene.lean.v1',
            request_id: 'request:ui-test',
            candidate_id: 'candidate:ui-test',
            route_mode: 'adult',
            logic_owner: 'deepseek_adult_scene',
            provisional: false,
            status: 'accepted',
            story_state_committed: true,
            current_logic_route: 'ordinary',
            exact_story_prose: protectedSentinel,
            protected_full_record: { prose: protectedSentinel },
            adult_filter: {
                verdict: 'pass',
                exact_quote: protectedSentinel,
            },
            provider_operations: { planner: 0, adult_scene: 2, adult_filter: 1, recorder: 0 },
            creator_trace: {
                logic_owner: 'deepseek_adult_scene',
                decision_records: [{
                    decision_key: 'decision-1',
                    character_id: 'character:sakura',
                    concise_decision: 'Maintain the established boundary.',
                    evidence_refs: ['record:boundary'],
                    exact_quote: protectedSentinel,
                }],
                autonomy: { mode: 'both' },
                route_transition: {
                    from_route: 'adult',
                    to_route: 'ordinary',
                    return_to_codex: true,
                },
                validation: { owner: 'deepseek_adult_filter', status: 'pass' },
                recording: {
                    status: 'complete',
                    recorder_required: false,
                    projection_status: 'attached',
                    protected_record_status: 'sealed',
                },
                provider_operations: { adult_scene: 2, adult_filter: 1 },
                debug_log_path: 'D:\\Cera\\runtime\\debug\\readable\\LATEST.md',
                exact_story_prose: protectedSentinel,
            },
        };

        const normalized = metadataModule.normalizeCompletionMetadata(raw);
        assert.equal(normalized.creator_trace.decision_records[0].concise_decision,
            'Maintain the established boundary.');
        assert.equal(normalized.creator_trace.provider_operations.total, 3);
        assert.equal(normalized.current_logic_route, 'ordinary');
        assert.equal(JSON.stringify(normalized).includes(protectedSentinel), false);

        assert.equal(window.ceraCaptureCompletionMetadata(raw), true);
        assert.equal(window.ceraCompletionMetadataQueue.length, 1);
        assert.equal(
            JSON.stringify(window.ceraCompletionMetadataQueue[0]).includes(protectedSentinel),
            false,
        );
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('durable actions require an exact backend review identity', async () => {
    const { metadataModule, root } = await loadExtension();
    try {
        assert.equal(metadataModule.validReviewId('review-0123456789abcdef0123456789ab'), true);
        assert.equal(metadataModule.validReviewId('review-made-up'), false);
        const invalid = metadataModule.normalizeCompletionMetadata({
            profile_id: 'cera.pi_scene.lean.v1',
            candidate_id: 'candidate:invalid-review',
            provisional: true,
            provisional_review_id: 'review-made-up',
            status: 'validation_rejected',
        });
        assert.equal(invalid.provisional, true);
        assert.equal(invalid.provisional_review_id, null);

        const valid = metadataModule.normalizeCompletionMetadata({
            profile_id: 'cera.pi_scene.lean.v1',
            candidate_id: 'candidate:valid-review',
            provisional: true,
            provisional_review_id: 'review-0123456789abcdef0123456789ab',
            status: 'validation_rejected',
        });
        assert.equal(valid.provisional_review_id, 'review-0123456789abcdef0123456789ab');

        const adult = metadataModule.normalizeCompletionMetadata({
            profile_id: 'cera.pi_scene.lean.v1',
            candidate_id: 'candidate:adult-review',
            route_mode: 'adult',
            provisional: true,
            review_id: 'review-abcdef0123456789abcdef012345',
            status: 'validation_rejected',
        });
        assert.equal(adult.provisional_review_id, 'review-abcdef0123456789abcdef012345');

        const invalidAdult = metadataModule.normalizeCompletionMetadata({
            profile_id: 'cera.pi_scene.lean.v1',
            candidate_id: 'candidate:adult-invalid-review',
            route_mode: 'adult',
            provisional: true,
            review_id: 'adult-review:protected-internal-id',
            status: 'validation_rejected',
        });
        assert.equal(invalidAdult.provisional_review_id, null);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('historical flat creator fields remain readable without weakening projection', async () => {
    const { metadataModule, root } = await loadExtension();
    try {
        const normalized = metadataModule.normalizeCompletionMetadata({
            profile_id: 'cera.pi_scene.lean.v1',
            candidate_id: 'candidate:flat-trace',
            route_mode: 'ordinary',
            logic_owner: 'codex_cognition',
            status: 'accepted',
            story_state_committed: true,
            decision_records: [{
                decision_key: 'decision-flat',
                owner_id: 'character:hana',
                selected_intent: 'Answer the current conversational floor.',
                concise_decision_basis: 'Accepted state supports a direct response.',
                autonomy_application: {
                    mind_precedence_applied: true,
                    body_precedence_applied: true,
                    user_direction_disposition: 'proposed_outcome',
                    concise_effect: 'Character logic retained precedence.',
                },
                causal_trigger_refs: ['source:current'],
                decisive_factor_refs: ['record:accepted'],
            }],
            autonomy_application: [{
                decision_key: 'decision-flat',
                owner_id: 'character:hana',
                mind_precedence_applied: true,
                body_precedence_applied: true,
                user_direction_disposition: 'proposed_outcome',
                concise_effect: 'Character logic retained precedence.',
            }],
            route_transition: {
                from_route: 'ordinary',
                to_route: 'adult',
                reason: 'The accepted boundary changes the sole logic owner.',
            },
            semantic_validation: {
                verdict: 'pass',
                review_flags: [{
                    flag_code: 'minor_style_note',
                    concise_explanation: 'Visible for creator context.',
                }],
            },
            provisional_dependencies: [{
                provisional_record_id: 'provisional:1',
                assumed_value: 'true',
                concise_dependency: 'A provisional detail was used.',
            }],
            request_controls: { character_autonomy: 'both' },
            provider_operations: { planner: 1, writer: 1, luna: 1, recorder: 1 },
        });
        assert.equal(normalized.creator_trace.decision_records.length, 1);
        assert.equal(normalized.creator_trace.autonomy.mode, 'both');
        assert.equal(
            normalized.creator_trace.autonomy.applications[0].character_id,
            'character:hana',
        );
        assert.equal(normalized.creator_trace.route_transition.to_route, 'adult');
        assert.equal(
            normalized.creator_trace.route_transition.non_graphic_handoff_summary,
            'The accepted boundary changes the sole logic owner.',
        );
        assert.deepEqual(
            normalized.creator_trace.validation.review_flags,
            ['minor_style_note: Visible for creator context.'],
        );
        assert.equal(normalized.creator_trace.provisional_dependencies.length, 1);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('source presents collapsed trace and preserves creator actions', async () => {
    const source = await readFile(path.join(sourceRoot, 'index.js'), 'utf8');
    const panel = await readFile(path.join(sourceRoot, 'creator-trace-panel.js'), 'utf8');
    for (const marker of [
        "details.className = 'cera-trace-details'",
        'CERA decision and processing details',
        'Relevant decisions',
        'Autonomy application',
        'Route transition',
        'Provider operations',
        'Readable debug log',
    ]) assert.equal(panel.includes(marker), true, `missing panel marker: ${marker}`);
    for (const marker of ["'Accept as Provisional'", "'Regenerate'", "'Replan'", "'Decline'"]) {
        assert.equal(source.includes(marker), true, `missing action marker: ${marker}`);
    }
    assert.equal(source.includes("if (!feedback && action !== 'replan')"), true);
    assert.equal(panel.includes('CERA - PROVISIONAL CANON'), true);
    assert.equal(panel.includes('not settled final truth'), true);
    assert.equal(source.includes('window.ceraCaptureTransportFailure'), true);
    assert.equal(source.includes("'Retry transport'"), true);
    assert.equal(source.includes("body: {}"), true);
});

test('manual transport retry is gated by the complete zero-effect backend proof', async () => {
    const { root } = await loadExtension();
    try {
        const actions = await import(
            `${pathToFileURL(path.join(
                root,
                'public',
                'scripts',
                'extensions',
                'third-party',
                'cera-creator-review',
                'review-actions.js',
            )).href}?v=${Date.now()}`
        );
        const retryId = `retry-${'b'.repeat(64)}`;
        const eligible = {
            status: 'error',
            story_state_committed: false,
            error: {
                schema_version: 'cera.error.v1',
                error_code: 'CERA_PROVIDER_TRANSPORT_FAILED',
                request_id: `request-${'a'.repeat(64)}`,
                story_state_committed: false,
                retry_mode: 'manual_transport',
                provider_operation_submitted: true,
                accepted_state_changed: false,
                fallback_used: false,
                next_action: 'use_transport_retry',
                retry_transport_enabled: true,
                transport_retry: {
                    schema_version: 'cera.pi_scene.transport_retry.v1',
                    retry_id: retryId,
                    retry_url: `/v1/cera/transport-retries/${retryId}`,
                    method: 'POST',
                    eligible: true,
                    automatic: false,
                    effect_proof_sha256: 'c'.repeat(64),
                },
            },
        };
        assert.deepEqual(actions.normalizeTransportRetryFailure(eligible), {
            schema_version: 'cera.pi_scene.transport_retry.v1',
            request_id: `request-${'a'.repeat(64)}`,
            provider_operation_submitted: true,
            retry_id: retryId,
            retry_url: `/v1/cera/transport-retries/${retryId}`,
            method: 'POST',
            eligible: true,
            automatic: false,
            effect_proof_sha256: 'c'.repeat(64),
        });
        assert.equal(window.ceraCaptureTransportFailure(eligible), true);
        assert.equal(window.ceraCaptureTransportFailure({
            status: 'error',
            story_state_committed: false,
            error: { error_code: 'CERA_INTERNAL_ERROR' },
        }), false);
        for (const mutation of [
            { retry_transport_enabled: false },
            { retry_transport_enabled: 'true' },
            { retry_mode: 'manual_after_review' },
            { accepted_state_changed: true },
            { provider_operation_submitted: 'true' },
        ]) {
            assert.equal(actions.normalizeTransportRetryFailure({
                ...eligible,
                error: { ...eligible.error, ...mutation },
            }), null);
        }
        const preTransport = {
            ...eligible,
            error: { ...eligible.error, provider_operation_submitted: false },
        };
        assert.equal(
            actions.normalizeTransportRetryFailure(preTransport).provider_operation_submitted,
            false,
        );
        assert.equal(actions.normalizeTransportRetryFailure({
            ...eligible,
            error: {
                ...eligible.error,
                transport_retry: {
                    ...eligible.error.transport_retry,
                    hidden: true,
                },
            },
        }), null);
        assert.equal(actions.normalizeTransportRetryFailure({
            ...eligible,
            error: {
                ...eligible.error,
                transport_retry: {
                    ...eligible.error.transport_retry,
                    retry_url: '/v1/cera/reviews/not-the-retry',
                },
            },
        }), null);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('provisional acceptance is backend-gated and reprojection stays unaccepted', async () => {
    const { root } = await loadExtension();
    try {
        const actions = await import(
            `${pathToFileURL(path.join(
                root,
                'public',
                'scripts',
                'extensions',
                'third-party',
                'cera-creator-review',
                'review-actions.js',
            )).href}?v=${Date.now()}`
        );
        assert.equal(actions.provisionalAcceptEnabled({ provisional_accept_enabled: true }), true);
        assert.equal(actions.provisionalAcceptEnabled({ provisional_accept_enabled: false }), false);
        assert.equal(actions.provisionalAcceptEnabled({ provisional_accept_enabled: 'true' }), false);
        assert.equal(actions.provisionalAcceptEnabled({
            actions: { accept_provisional: true },
        }), false);

        const blocked = actions.normalizeReprojectionRequired({
            schema_version: 'cera.pi_scene.adult_provisional_acceptance_blocked.v1',
            review_id: 'review-0123456789abcdef0123456789ab',
            disposition: 'reprojection_required',
            reason_code: 'filter_rejection_has_no_promotable_projection',
            required_artifacts: [
                'protected_full_record',
                'non_explicit_codex_projection',
                'route_transition',
            ],
            story_state_committed: false,
            accepted_effect_created: false,
            accept_enabled: false,
            next_action: 'protected_reprojection_provider_operation_required',
            exact_story_prose: 'must not enter the UI projection',
        });
        assert.equal(blocked.disposition, 'reprojection_required');
        assert.equal(blocked.story_state_committed, false);
        assert.equal(blocked.accepted_effect_created, false);
        assert.equal('exact_story_prose' in blocked, false);
        assert.equal(actions.provisionalAcceptanceCommitted(blocked), false);
        assert.equal(actions.provisionalAcceptanceCommitted({
            story_state_committed: true,
            review: { canon_status: 'provisional' },
        }), true);
        assert.equal(actions.provisionalAcceptanceCommitted({
            story_state_committed: true,
            review: { canon_status: 'accepted' },
        }), false);
        assert.equal(actions.normalizeReprojectionRequired({
            ...blocked,
            story_state_committed: true,
        }), null);

        const acceptedRegenerate = actions.acceptedRegenerateSuccessor({
            schema_version: 'cera.pi_scene.review_decision.v1',
            creator_action: 'regenerate',
            story_state_committed: true,
            accepted_turn_id: 'turn-0001-test',
            accepted_receipt_sha256: 'a'.repeat(64),
            successor: {
                choices: [{ message: { content: 'A fresh accepted alternative.' } }],
                cera: {
                    status: 'accepted',
                    story_state_committed: true,
                    candidate_id: 'candidate:adult-regenerate:test',
                },
            },
        });
        assert.equal(acceptedRegenerate.story_text, 'A fresh accepted alternative.');
        assert.equal(acceptedRegenerate.completion.status, 'accepted');
        assert.equal(actions.acceptedRegenerateSuccessor({
            ...acceptedRegenerate,
            story_state_committed: false,
        }), null);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});
