import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { pathToFileURL } from 'node:url';

const sourceRoot = path.dirname(new URL(import.meta.url).pathname.replace(/^\/(?:[A-Za-z]:)/, value => value.slice(1)));

class TestElement {
    constructor(tagName) {
        this.tagName = String(tagName).toUpperCase();
        this.children = [];
        this.parentElement = null;
        this.id = '';
        this.className = '';
        this.textContent = '';
        this.value = '';
        this.disabled = false;
        this.listeners = new Map();
        this.classList = {
            add: (...names) => {
                const current = new Set(this.className.split(/\s+/).filter(Boolean));
                for (const name of names) current.add(name);
                this.className = [...current].join(' ');
            },
        };
    }

    get nextSibling() {
        if (!this.parentElement) return null;
        const index = this.parentElement.children.indexOf(this);
        return this.parentElement.children[index + 1] ?? null;
    }

    set innerHTML(value) {
        this.children = [];
        this.textContent = String(value);
    }

    append(...children) {
        for (const child of children) this.appendChild(child);
    }

    appendChild(child) {
        if (child.parentElement) child.remove();
        child.parentElement = this;
        this.children.push(child);
        return child;
    }

    insertBefore(child, reference) {
        if (child.parentElement) child.remove();
        child.parentElement = this;
        const index = reference ? this.children.indexOf(reference) : -1;
        if (index < 0) this.children.push(child);
        else this.children.splice(index, 0, child);
        return child;
    }

    remove() {
        if (!this.parentElement) return;
        const index = this.parentElement.children.indexOf(this);
        if (index >= 0) this.parentElement.children.splice(index, 1);
        this.parentElement = null;
    }

    addEventListener(type, handler) {
        const handlers = this.listeners.get(type) ?? [];
        handlers.push(handler);
        this.listeners.set(type, handlers);
    }

    focus() {}

    async click() {
        if (this.disabled) return;
        for (const handler of this.listeners.get('click') ?? []) await handler({ target: this });
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] ?? null;
    }

    querySelectorAll(selector) {
        const matches = [];
        for (const child of this.children) {
            if (matchesSimpleSelector(child, selector)) matches.push(child);
            matches.push(...child.querySelectorAll(selector));
        }
        return matches;
    }
}

class TestDocument {
    constructor(messageCount = 0) {
        this.body = new TestElement('body');
        const chat = new TestElement('section');
        chat.id = 'chat';
        for (let index = 0; index < messageCount; index += 1) {
            const message = new TestElement('article');
            message.className = 'mes';
            message.mesid = String(index);
            const block = new TestElement('div');
            block.className = 'mes_block';
            message.appendChild(block);
            chat.appendChild(message);
        }
        const wrapper = new TestElement('main');
        const sendForm = new TestElement('form');
        sendForm.id = 'send_form';
        wrapper.appendChild(sendForm);
        this.body.append(chat, wrapper);
    }

    createElement(tagName) { return new TestElement(tagName); }
    querySelector(selector) {
        const message = /^#chat \.mes\[mesid="(\d+)"\]$/.exec(selector);
        if (message) {
            return this.body.querySelectorAll('.mes')
                .find(element => element.mesid === message[1]) ?? null;
        }
        if (matchesSimpleSelector(this.body, selector)) return this.body;
        return this.body.querySelector(selector);
    }
}

function matchesSimpleSelector(element, selector) {
    if (selector.startsWith('#') && !selector.includes(' ')) return element.id === selector.slice(1);
    if (selector.startsWith('.') && !selector.includes(' ')) {
        return element.className.split(/\s+/).includes(selector.slice(1));
    }
    if (/^[a-z]+$/i.test(selector)) return element.tagName === selector.toUpperCase();
    return false;
}

function buttonByText(documentValue, text) {
    return documentValue.body.querySelectorAll('button')
        .find(button => button.textContent === text) ?? null;
}

function retryStore(storage) {
    return JSON.parse(storage.get('cera_transport_retry_receipts_v1') ?? '{}');
}

function providerFailureStore(storage) {
    return JSON.parse(storage.get('cera_provider_stage_failures_v1') ?? '{}');
}

function providerRetryStore(storage) {
    return JSON.parse(storage.get('cera_provider_stage_retry_status_v1') ?? '{}');
}

function elementText(element) {
    if (!element) return '';
    return [element.textContent, ...element.children.map(elementText)].join(' ');
}

async function loadExtension({
    initialStorage = {},
    initialChat = [],
    fetchImpl = undefined,
} = {}) {
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
    await mkdir(path.join(extension, 'generated'), { recursive: true });
    await mkdir(path.join(root, 'public', 'scripts'), { recursive: true });
    await writeFile(path.join(root, 'package.json'), '{"type":"module"}\n');
    await writeFile(
        path.join(root, 'public', 'script.js'),
        `export const chat = ${JSON.stringify(initialChat)};
export const characters = [{ name: 'Sakura' }];
export let this_chid = 0;
let currentChatId = 'test-chat';
const handlers = new Map();
let failedSavesRemaining = 0;
export const testState = {
  activateCount: 0, deactivateCount: 0, addCount: 0, saveCount: 0
};
export const event_types = {
  APP_READY: 'APP_READY', MESSAGE_RECEIVED: 'MESSAGE_RECEIVED',
  CHARACTER_MESSAGE_RENDERED: 'CHARACTER_MESSAGE_RENDERED',
  CHAT_CHANGED: 'CHAT_CHANGED', GENERATION_ENDED: 'GENERATION_ENDED'
};
export const eventSource = {
  on(type, handler) {
    const values = handlers.get(type) ?? [];
    values.push(handler);
    handlers.set(type, values);
  },
  async emit(type, ...args) {
    for (const handler of handlers.get(type) ?? []) await handler(...args);
  }
};
export function __setChatId(value) { currentChatId = value; }
export function __failNextSaves(count = 1) { failedSavesRemaining = count; }
export function addOneMessage() { testState.addCount += 1; }
export function activateSendButtons() { testState.activateCount += 1; }
export function deactivateSendButtons() { testState.deactivateCount += 1; }
export function getCurrentChatId() { return currentChatId; }
export function getMessageTimeStamp() { return 'test-time'; }
export function getRequestHeaders() { return {}; }
export async function saveChatConditional() {
  testState.saveCount += 1;
  if (failedSavesRemaining > 0) {
    failedSavesRemaining -= 1;
    throw new Error('simulated chat save interruption');
  }
}
export function updateMessageBlock() {}
`,
    );
    await writeFile(
        path.join(root, 'public', 'scripts', 'openai.js'),
        `export const oai_settings = { custom_include_headers: 'Authorization: Bearer ${'a'.repeat(43)}' };\n`,
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
    await writeFile(
        path.join(extension, 'generated', 'provider-stage-retry-contracts-v1.mjs'),
        await readFile(
            path.join(sourceRoot, 'generated', 'provider-stage-retry-contracts-v1.mjs'),
            'utf8',
        ),
    );

    class TestCustomEvent extends Event {
        constructor(type, options = {}) {
            super(type);
            this.detail = options.detail;
        }
    }
    globalThis.CustomEvent = TestCustomEvent;
    globalThis.window = new EventTarget();
    globalThis.window.ceraCompletionMetadataQueue = [];
    const storage = new Map(Object.entries(initialStorage));
    globalThis.localStorage = {
        getItem(key) { return storage.has(key) ? storage.get(key) : null; },
        setItem(key, value) { storage.set(key, String(value)); },
        removeItem(key) { storage.delete(key); },
    };
    const testDocument = new TestDocument(initialChat.length);
    globalThis.document = testDocument;
    if (fetchImpl) globalThis.fetch = fetchImpl;
    const module = await import(`${pathToFileURL(path.join(extension, 'index.js')).href}?v=${Date.now()}`);
    const scriptModule = await import(
        `${pathToFileURL(path.join(root, 'public', 'script.js')).href}`
    );
    const metadataModule = await import(
        `${pathToFileURL(path.join(extension, 'completion-metadata.js')).href}?v=${Date.now()}`
    );
    return {
        module,
        metadataModule,
        root,
        scriptModule,
        storage,
        testDocument,
    };
}

function eligibleTransportFailure({
    retryCharacter = 'b',
    requestCharacter = 'a',
    proofCharacter = 'c',
} = {}) {
    const retryId = `retry-${retryCharacter.repeat(64)}`;
    return {
        status: 'error',
        story_state_committed: false,
        error: {
            schema_version: 'cera.error.v1',
            error_code: 'CERA_PROVIDER_TRANSPORT_FAILED',
            request_id: `request-${requestCharacter.repeat(64)}`,
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
                effect_proof_sha256: proofCharacter.repeat(64),
            },
        },
    };
}

function transportRetryCompletion(failure) {
    return {
        id: 'chatcmpl-cera-retry',
        choices: [{ message: { content: 'Sakura answers once.' } }],
        cera: {
            profile_id: 'cera.pi_scene.lean.v1',
            request_id: failure.error.request_id,
            candidate_id: 'candidate:transport-retry-terminal',
            route_mode: 'ordinary',
            provisional: false,
            status: 'accepted',
            story_state_committed: true,
        },
    };
}

function transportRetryStatus(failure, state, extra = {}) {
    return {
        schema_version: 'cera.pi_scene.transport_retry_status.v1',
        retry_id: failure.error.transport_retry.retry_id,
        request_id: failure.error.request_id,
        state,
        effect_proof_sha256: failure.error.transport_retry.effect_proof_sha256,
        retry_transport_enabled: state === 'eligible' || state === 'superseded',
        ...extra,
    };
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
            message: 'CERA stopped after three failed attempts at one provider stage.',
            story_state_committed: critical.story_state_committed,
            retry_mode: 'exhausted',
            provider_operation_submitted: true,
            accepted_state_changed: critical.story_state_committed,
            fallback_used: false,
            next_action: 'report_critical_provider_failure',
            retry_transport_enabled: false,
            critical_provider_stage_failure: critical,
        },
    };
}

function exhaustedStatus(failure, critical = criticalProviderStageFailure()) {
    return {
        schema_version: 'cera.pi_scene.transport_retry_status.v2',
        retry_id: failure.error.transport_retry.retry_id,
        request_id: failure.error.request_id,
        state: 'attempts_exhausted',
        effect_proof_sha256: failure.error.transport_retry.effect_proof_sha256,
        retry_transport_enabled: false,
        critical_provider_stage_failure: critical,
    };
}

function providerStageRetryEnvelope(state, { chainCharacter = 'a' } = {}) {
    const stateValues = {
        eligible: {
            stage: 'writer', attempts: 1, retries: 0, observed: 1, conservative: 1,
            failure: 'transport_timeout', action: 'provider_retry',
        },
        in_progress: {
            stage: 'writer', attempts: 2, retries: 1, observed: 1, conservative: 1,
            failure: null, action: null,
        },
        succeeded: {
            stage: 'writer', attempts: 2, retries: 1, observed: 2, conservative: 2,
            failure: null, action: null,
        },
        blocked_ambiguous: {
            stage: 'adult_scene', attempts: 1, retries: 0, observed: 0, conservative: 1,
            failure: 'dispatch_ambiguous', action: 'check_status',
        },
        attempts_exhausted: {
            stage: 'writer', attempts: 3, retries: 2, observed: 3, conservative: 3,
            failure: 'provider_unavailable', action: null,
        },
        recording_repair_required: {
            stage: 'recorder', attempts: 3, retries: 2, observed: 3, conservative: 3,
            failure: 'provider_completion_incomplete', action: 'repair_recording',
        },
        recovery_required: {
            stage: 'writer', attempts: 1, retries: 0, observed: 0, conservative: 0,
            failure: 'provider_failure_not_retryable', action: null,
        },
    }[state];
    if (!stateValues) throw new TypeError(`unsupported test state ${state}`);
    const chainId = `stage-retry-${chainCharacter.repeat(64)}`;
    const chainSha256 = 'f'.repeat(64);
    const status = {
        schema_version: 'cera.provider_stage_retry_status.v1',
        chain_id: chainId,
        provider: 'deepseek',
        model_family: 'deepseek_v4',
        stage: stateValues.stage,
        state,
        maximum_attempts: 3,
        stage_attempts_total: stateValues.attempts,
        retry_actions_accepted: stateValues.retries,
        provider_operations_observed_total: stateValues.observed,
        provider_operations_conservative_total: stateValues.conservative,
        story_state_committed: stateValues.stage === 'recorder',
        branch_preserved_at_last_accepted_head: true,
        failure_category: stateValues.failure,
        available_actions: stateValues.action ? [stateValues.action] : [],
        technical_details: {
            schema_version: 'cera.provider_stage_retry_technical_details.v1',
            request_occurrence_sha256: 'b'.repeat(64),
            request_sha256: 'c'.repeat(64),
            stage_input_sha256: 'd'.repeat(64),
            accepted_state_sha256: 'e'.repeat(64),
            chain_sha256: chainSha256,
        },
    };
    const actions = stateValues.action ? [{
        schema_version: 'cera.provider_stage_retry_action.v1',
        action_id: `stage-action-${'9'.repeat(64)}`,
        chain_id: chainId,
        action_family: 'provider_stage_control',
        action_kind: stateValues.action,
        automatic: false,
        provider_dispatch_authorized: stateValues.action === 'provider_retry',
        consumes_retry_action: stateValues.action === 'provider_retry',
        retry_action_ordinal: stateValues.action === 'provider_retry'
            ? stateValues.retries + 1
            : null,
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

function providerStageCompletion(envelope, { content = 'Recovered provider-stage story.' } = {}) {
    return {
        id: 'chatcmpl-provider-stage-retry',
        object: 'chat.completion',
        choices: [{
            index: 0,
            message: { role: 'assistant', content },
            finish_reason: 'stop',
        }],
        cera: {
            profile_id: 'cera.pi_scene.lean.v1',
            request_id: `request-${'1'.repeat(64)}`,
            provider_stage_request_sha256: envelope.status.technical_details.request_sha256,
            candidate_id: 'candidate:provider-stage-terminal',
            route_mode: 'ordinary',
            provisional: false,
            status: 'accepted',
            story_state_committed: true,
        },
    };
}

function creatorReview(reviewId, action, { route = 'ordinary' } = {}) {
    return {
        schema_version: 'cera.pi_scene.review.v1',
        review_id: reviewId,
        state: 'review_ready',
        provisional: true,
        route,
        story_text: 'Original provisional story.',
        candidate_id: 'candidate:creator-action-test',
        candidate_sha256: '1'.repeat(64),
        primary_authority_kind: 'codex_cognition_plan',
        primary_authority_sha256: '2'.repeat(64),
        warnings: [],
        warnings_block_accept: false,
        recording_status: action === 'repair_recording' ? 'pending_repair' : null,
        story_state_committed: action === 'repair_recording',
        canon_status: action === 'repair_recording' ? 'accepted' : 'unaccepted',
        semantic_validation: null,
        request_controls: null,
        creator_guidance: null,
        accept_enabled: action === 'accept',
        provisional_accept_enabled: false,
        decline_enabled: true,
        regenerate_enabled: action === 'regenerate',
        replan_enabled: action === 'replan',
        repair_recording_enabled: action === 'repair_recording',
        provider_operations: { planner: 1, writer: 1, recorder: 0 },
    };
}

function creatorReviewMessage(reviewId, { accepted = false } = {}) {
    return {
        name: 'Sakura',
        is_user: false,
        mes: 'Original provisional story.',
        extra: {
            cera_creator_review: {
                review_id: reviewId,
                candidate_id: 'candidate:creator-action-test',
                state: accepted ? 'accepted' : 'review_ready',
                provisional: !accepted,
                completion: {
                    profile_id: 'cera.pi_scene.lean.v1',
                    request_id: `request-${'1'.repeat(64)}`,
                    candidate_id: 'candidate:creator-action-test',
                    route_mode: 'ordinary',
                    provisional: !accepted,
                    status: accepted ? 'accepted' : 'review_ready',
                    story_state_committed: accepted,
                },
            },
        },
    };
}

function creatorDecision(reviewId, action) {
    const committed = ['accept', 'repair_recording'].includes(action);
    const review = creatorReview(reviewId, action);
    review.state = action === 'accept' || action === 'repair_recording'
        ? 'accepted'
        : action === 'regenerate'
            ? 'regenerated'
            : 'replanned';
    review.provisional = false;
    review.story_state_committed = committed;
    review.canon_status = committed ? 'accepted' : 'unaccepted';
    review.recording_status = action === 'repair_recording' ? 'complete' : null;
    const result = {
        schema_version: 'cera.pi_scene.review_decision.v1',
        status: committed ? 'story_committed' : 'review_transitioned',
        creator_action: action,
        story_state_committed: committed,
        retry_mode: 'not_applicable',
        review,
        successor: null,
        operational_warnings: [],
    };
    if (committed) {
        result.accepted_turn_id = 'turn-0001-provider-stage-action';
        result.accepted_receipt_sha256 = '3'.repeat(64);
    }
    return result;
}

function jsonResponse(payload, status = 200) {
    return new Response(JSON.stringify(payload), {
        status,
        headers: { 'Content-Type': 'application/json' },
    });
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
    assert.equal(source.includes('window.ceraCaptureProviderStageRetryStatus'), true);
    assert.equal(source.includes("'Retry transport'"), true);
    assert.equal(source.includes("'Retry Provider Stage'"), true);
    assert.equal(source.includes("'Check Status'"), true);
    assert.equal(source.includes("'Repair Recording'"), true);
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

test('transport Retry receipt survives reload, blocks sends, and remains isolated per chat', async () => {
    const first = await loadExtension();
    let second = null;
    try {
        const sentinel = 'PROMPT-PROSE-MUST-NOT-BE-PERSISTED';
        const failure = eligibleTransportFailure();
        failure.error.prompt = sentinel;
        failure.error.debug_log_path = 'D:\\private\\debug.md';
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        assert.equal(first.scriptModule.testState.deactivateCount, 1);
        const stored = retryStore(first.storage);
        assert.equal(stored.schema_version, 'cera.sillytavern.transport_retry_store.v2');
        assert.equal(stored.entries.length, 1);
        assert.equal(
            stored.entries[0].schema_version,
            'cera.sillytavern.transport_retry_state.v2',
        );
        assert.equal(stored.entries[0].phase, 'eligible');
        assert.equal(stored.entries[0].retry_actions_dispatched, 0);
        assert.equal(JSON.stringify(stored).includes(sentinel), false);
        assert.equal(JSON.stringify(stored).includes('debug.md'), false);
        assert.ok(first.testDocument.querySelector('#cera_transport_retry_panel'));

        second = await loadExtension({ initialStorage: Object.fromEntries(first.storage) });
        await second.scriptModule.eventSource.emit(second.scriptModule.event_types.APP_READY);
        assert.ok(second.testDocument.querySelector('#cera_transport_retry_panel'));
        assert.ok(second.scriptModule.testState.deactivateCount >= 1);

        second.scriptModule.__setChatId('different-chat');
        await second.scriptModule.eventSource.emit(second.scriptModule.event_types.CHAT_CHANGED);
        assert.equal(second.testDocument.querySelector('#cera_transport_retry_panel'), null);
        assert.ok(second.scriptModule.testState.activateCount >= 1);
        assert.equal(retryStore(second.storage).entries.length, 1);

        second.scriptModule.__setChatId('test-chat');
        await second.scriptModule.eventSource.emit(second.scriptModule.event_types.CHAT_CHANGED);
        assert.ok(second.testDocument.querySelector('#cera_transport_retry_panel'));
        assert.ok(second.scriptModule.testState.deactivateCount >= 2);
    } finally {
        await rm(first.root, { recursive: true, force: true });
        if (second) await rm(second.root, { recursive: true, force: true });
    }
});

test('legacy v1 retry state migrates fail-closed and permits GET only', async () => {
    const failure = eligibleTransportFailure();
    const receipt = {
        request_id: failure.error.request_id,
        ...failure.error.transport_retry,
    };
    const legacyState = {
        schema_version: 'cera.sillytavern.transport_retry_state.v1',
        chat_key: JSON.stringify(['0', 'test-chat']),
        phase: 'eligible',
        post_dispatched: false,
        receipt,
        completion_identity: null,
    };
    const initialStorage = {
        cera_transport_retry_receipts_v1: JSON.stringify({
            schema_version: 'cera.sillytavern.transport_retry_store.v1',
            entries: [legacyState],
        }),
    };
    const methods = [];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        initialStorage,
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return jsonResponse(transportRetryStatus(failure, 'eligible', {
                transport_retry: failure.error.transport_retry,
            }));
        },
    });
    try {
        await loaded.scriptModule.eventSource.emit(loaded.scriptModule.event_types.APP_READY);
        for (
            let attempt = 0;
            attempt < 20 && !buttonByText(loaded.testDocument, 'Check retry status');
            attempt += 1
        ) {
            await new Promise(resolve => setTimeout(resolve, 0));
        }
        const stored = retryStore(loaded.storage);
        assert.equal(stored.schema_version, 'cera.sillytavern.transport_retry_store.v2');
        assert.equal(stored.entries[0].schema_version, 'cera.sillytavern.transport_retry_state.v2');
        assert.equal(stored.entries[0].retry_actions_dispatched, null);
        assert.deepEqual(methods, ['GET']);
        assert.equal(buttonByText(loaded.testDocument, 'Retry transport'), null);
        assert.ok(buttonByText(loaded.testDocument, 'Check retry status'));

        const actions = await import(
            `${pathToFileURL(path.join(
                loaded.root,
                'public/scripts/extensions/third-party/cera-creator-review/review-actions.js',
            )).href}?v=${Date.now()}`
        );
        const validV2 = {
            ...legacyState,
            schema_version: 'cera.sillytavern.transport_retry_state.v2',
            retry_actions_dispatched: 0,
        };
        assert.equal(actions.normalizePersistedTransportRetryState(validV2)
            .retry_actions_dispatched, 0);
        for (const retryActionsDispatched of [-1, 3, 0.5, '2']) {
            assert.equal(actions.normalizePersistedTransportRetryState({
                ...validV2,
                retry_actions_dispatched: retryActionsDispatched,
            }), null);
        }
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('two v1 successors consume the local budget and cannot expose a fourth attempt', async () => {
    const firstFailure = eligibleTransportFailure();
    const secondFailure = eligibleTransportFailure({
        retryCharacter: 'd',
        requestCharacter: 'a',
        proofCharacter: 'e',
    });
    const forbiddenThirdFailure = eligibleTransportFailure({
        retryCharacter: 'f',
        requestCharacter: 'a',
        proofCharacter: '1',
    });
    const methods = [];
    const responses = [
        jsonResponse(secondFailure, 500),
        jsonResponse(forbiddenThirdFailure, 500),
    ];
    const originalFetch = globalThis.fetch;
    const first = await loadExtension({
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return responses.shift();
        },
    });
    let second = null;
    try {
        assert.equal(window.ceraCaptureTransportFailure(firstFailure), true);
        await buttonByText(first.testDocument, 'Retry transport').click();
        let state = retryStore(first.storage).entries[0];
        assert.equal(state.receipt.retry_id, secondFailure.error.transport_retry.retry_id);
        assert.equal(state.retry_actions_dispatched, 1);
        const secondButton = buttonByText(first.testDocument, 'Retry transport');
        assert.ok(secondButton);
        await secondButton.click();

        state = retryStore(first.storage).entries[0];
        assert.equal(state.phase, 'limit_reached_unconfirmed');
        assert.equal(state.retry_actions_dispatched, 2);
        assert.equal(state.receipt.retry_id, secondFailure.error.transport_retry.retry_id);
        assert.deepEqual(methods, ['POST', 'POST']);
        assert.equal(buttonByText(first.testDocument, 'Retry transport'), null);
        assert.ok(buttonByText(first.testDocument, 'Check retry status'));
        assert.equal(providerFailureStore(first.storage).entries?.length ?? 0, 0);

        await secondButton.click();
        assert.deepEqual(methods, ['POST', 'POST']);

        const successorStatus = transportRetryStatus(secondFailure, 'superseded', {
            superseded_by_retry_id: forbiddenThirdFailure.error.transport_retry.retry_id,
            transport_retry: forbiddenThirdFailure.error.transport_retry,
        });
        second = await loadExtension({
            initialStorage: Object.fromEntries(first.storage),
            fetchImpl: async (url, options) => {
                methods.push(options.method);
                return jsonResponse(successorStatus);
            },
        });
        await second.scriptModule.eventSource.emit(second.scriptModule.event_types.APP_READY);
        for (
            let attempt = 0;
            attempt < 20 && !buttonByText(second.testDocument, 'Check retry status');
            attempt += 1
        ) {
            await new Promise(resolve => setTimeout(resolve, 0));
        }
        state = retryStore(second.storage).entries[0];
        assert.deepEqual(methods, ['POST', 'POST', 'GET']);
        assert.equal(state.phase, 'limit_reached_unconfirmed');
        assert.equal(state.retry_actions_dispatched, 2);
        assert.equal(state.receipt.retry_id, secondFailure.error.transport_retry.retry_id);
        assert.equal(buttonByText(second.testDocument, 'Retry transport'), null);
        assert.ok(buttonByText(second.testDocument, 'Check retry status'));
        assert.equal(providerFailureStore(second.storage).entries?.length ?? 0, 0);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(first.root, { recursive: true, force: true });
        if (second) await rm(second.root, { recursive: true, force: true });
    }
});

test('authoritative terminal evidence after the second Retry creates the critical state', async () => {
    const firstFailure = eligibleTransportFailure();
    const secondFailure = eligibleTransportFailure({
        retryCharacter: 'd',
        requestCharacter: 'a',
        proofCharacter: 'e',
    });
    const critical = criticalProviderStageFailure({
        stage: 'planner',
        failureClass: 'provider_unavailable',
    });
    const methods = [];
    const responses = [
        jsonResponse(secondFailure, 500),
        jsonResponse(exhaustedError(critical), 503),
    ];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return responses.shift();
        },
    });
    try {
        assert.equal(window.ceraCaptureTransportFailure(firstFailure), true);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.equal(retryStore(loaded.storage).entries[0].retry_actions_dispatched, 1);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.deepEqual(methods, ['POST', 'POST']);
        assert.equal(retryStore(loaded.storage).entries.length, 0);
        assert.equal(providerFailureStore(loaded.storage).entries.length, 1);
        assert.equal(buttonByText(loaded.testDocument, 'Retry transport'), null);
        assert.match(
            elementText(loaded.testDocument.querySelector('#cera_provider_stage_failure_panel')),
            /Codex Planner failed after three attempts/,
        );
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('retry status normalizer closes all five backend states and rejects extra authority', async () => {
    const { root } = await loadExtension();
    try {
        const actions = await import(
            `${pathToFileURL(path.join(
                root,
                'public/scripts/extensions/third-party/cera-creator-review/review-actions.js',
            )).href}?v=${Date.now()}`
        );
        const failure = eligibleTransportFailure();
        const action = failure.error.transport_retry;
        const completion = transportRetryCompletion(failure);
        const successorId = `retry-${'f'.repeat(64)}`;
        const statuses = [
            transportRetryStatus(failure, 'eligible', { transport_retry: action }),
            transportRetryStatus(failure, 'in_progress', { phase: 'authorized' }),
            transportRetryStatus(failure, 'succeeded', {
                completion,
                completion_sha256: '1'.repeat(64),
            }),
            transportRetryStatus(failure, 'superseded', {
                superseded_by_retry_id: successorId,
                transport_retry: {
                    ...action,
                    retry_id: successorId,
                    retry_url: `/v1/cera/transport-retries/${successorId}`,
                    effect_proof_sha256: '2'.repeat(64),
                },
            }),
            transportRetryStatus(failure, 'blocked', {
                blocked_reason_code: 'effect_state_changed',
            }),
        ];
        for (const status of statuses) {
            assert.deepEqual(actions.normalizeTransportRetryStatus(status), status);
        }
        assert.equal(actions.normalizeTransportRetryStatus({
            ...statuses[0],
            automatic_retry: true,
        }), null);
        assert.equal(actions.normalizeTransportRetryStatus({
            ...statuses[4],
            retry_transport_enabled: true,
        }), null);
        assert.equal(actions.normalizeTransportRetryStatus({
            ...statuses[2],
            completion: {
                ...completion,
                cera: {
                    ...completion.cera,
                    request_id: `request-${'9'.repeat(64)}`,
                },
            },
        }), null);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('generated provider-stage envelope validates all seven states and backend action bindings', async () => {
    const { root } = await loadExtension();
    try {
        const actions = await import(
            `${pathToFileURL(path.join(
                root,
                'public/scripts/extensions/third-party/cera-creator-review/review-actions.js',
            )).href}?v=${Date.now()}`
        );
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
            assert.deepEqual(actions.normalizeProviderStageRetryStatusEnvelope(envelope), envelope);
            assert.deepEqual(
                actions.normalizeProviderStageRetryStatus(envelope.status),
                envelope.status,
            );
            if (envelope.actions[0]) {
                assert.deepEqual(
                    actions.normalizeProviderStageRetryAction(envelope.actions[0]),
                    envelope.actions[0],
                );
            }
        }
        const eligible = providerStageRetryEnvelope('eligible');
        assert.equal(actions.normalizeProviderStageRetryStatusEnvelope({
            ...eligible,
            actions: [],
        }), null);
        assert.equal(actions.normalizeProviderStageRetryStatusEnvelope({
            ...eligible,
            actions: [{
                ...eligible.actions[0],
                expected_chain_sha256: '8'.repeat(64),
            }],
        }), null);
        assert.equal(actions.normalizeProviderStageRetryAction({
            ...eligible.actions[0],
            action_kind: 'regenerate',
        }), null);

        const blocked = providerStageRetryEnvelope('blocked_ambiguous').status;
        const blockedEvidence = {
            schema_version: 'cera.provider_stage_retry_blocked_ambiguous.v1',
            severity: 'critical',
            provider: blocked.provider,
            model_family: blocked.model_family,
            stage: blocked.stage,
            maximum_attempts: 3,
            attempts_total: blocked.stage_attempts_total,
            retries_consumed: blocked.retry_actions_accepted,
            story_state_committed: blocked.story_state_committed,
            failed_stage_effect_committed: false,
            provider_operations_observed_total: blocked.provider_operations_observed_total,
            provider_operations_conservative_total: blocked.provider_operations_conservative_total,
            block_reason: 'dispatch_custody_ambiguous',
            request_sha256: blocked.technical_details.request_sha256,
            stage_input_sha256: blocked.technical_details.stage_input_sha256,
            attempt_chain_sha256: blocked.technical_details.chain_sha256,
            terminal_evidence_sha256: '7'.repeat(64),
        };
        assert.deepEqual(
            actions.normalizeProviderStageRetryBlockedAmbiguous(blockedEvidence),
            blockedEvidence,
        );
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('eligible provider-stage status posts only the exact backend-issued Retry action', async () => {
    const calls = [];
    const originalFetch = globalThis.fetch;
    const eligible = providerStageRetryEnvelope('eligible');
    const inProgress = providerStageRetryEnvelope('in_progress');
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            return jsonResponse(inProgress);
        },
    });
    try {
        assert.equal(window.ceraCaptureProviderStageRetryStatus(eligible), true);
        const panel = loaded.testDocument.querySelector('#cera_provider_stage_retry_panel');
        assert.ok(panel);
        assert.match(elementText(panel), /Technical details/);
        assert.match(elementText(panel), /Browser-observed Retry actions \(advisory only\)/);
        assert.equal(
            panel.querySelector('.cera-review-status').textContent.includes('d'.repeat(64)),
            false,
        );
        assert.equal(buttonByText(loaded.testDocument, 'Check Status'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Repair Recording'), null);
        await buttonByText(loaded.testDocument, 'Retry Provider Stage').click();
        assert.equal(calls.length, 1);
        assert.equal(calls[0].options.method, 'POST');
        assert.match(calls[0].url, /provider-stage-retries\/stage-retry-/);
        assert.deepEqual(JSON.parse(calls[0].options.body), eligible.actions[0]);
        assert.equal(buttonByText(loaded.testDocument, 'Retry Provider Stage'), null);
        assert.match(
            loaded.testDocument.querySelector('.cera-review-status').textContent,
            /in progress/i,
        );
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('lost provider-stage POST persists its action and Continue replays without another Retry', async () => {
    const eligible = providerStageRetryEnvelope('eligible');
    const succeeded = providerStageRetryEnvelope('succeeded');
    const completion = providerStageCompletion(eligible);
    const calls = [];
    const originalFetch = globalThis.fetch;
    const replies = [
        new TypeError('simulated lost POST response'),
        jsonResponse(succeeded),
        jsonResponse(completion),
    ];
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            const reply = replies.shift();
            if (reply instanceof Error) throw reply;
            return reply;
        },
    });
    try {
        assert.equal(window.ceraCaptureProviderStageRetryStatus(eligible), true);
        await buttonByText(loaded.testDocument, 'Retry Provider Stage').click();

        assert.deepEqual(calls.map(call => call.options.method), ['POST', 'GET']);
        const stored = providerRetryStore(loaded.storage);
        assert.equal(
            stored.schema_version,
            'cera.sillytavern.provider_stage_retry_status_store.v2',
        );
        assert.deepEqual(stored.entries[0].last_submitted_action, eligible.actions[0]);
        assert.ok(buttonByText(loaded.testDocument, 'Continue'));
        assert.equal(buttonByText(loaded.testDocument, 'Retry Provider Stage'), null);

        await buttonByText(loaded.testDocument, 'Continue').click();
        assert.deepEqual(calls.map(call => call.options.method), ['POST', 'GET', 'POST']);
        assert.deepEqual(JSON.parse(calls[2].options.body), eligible.actions[0]);
        assert.equal(loaded.scriptModule.chat.length, 1);
        assert.equal(loaded.scriptModule.chat[0].mes, 'Recovered provider-stage story.');
        assert.equal(loaded.scriptModule.testState.addCount, 1);
        assert.equal(providerRetryStore(loaded.storage).entries.length, 0);
        assert.equal(buttonByText(loaded.testDocument, 'Continue'), null);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('prepared provider-stage action resumes manually from in-progress after reload', async () => {
    const eligible = providerStageRetryEnvelope('eligible');
    const inProgress = providerStageRetryEnvelope('in_progress');
    const completion = providerStageCompletion(eligible);
    const calls = [];
    const originalFetch = globalThis.fetch;
    const stored = {
        cera_provider_stage_retry_status_v1: JSON.stringify({
            schema_version: 'cera.sillytavern.provider_stage_retry_status_store.v2',
            entries: [{
                chat_key: JSON.stringify(['0', 'test-chat']),
                envelope: inProgress,
                last_submitted_action: eligible.actions[0],
                continuation: null,
            }],
        }),
    };
    const loaded = await loadExtension({
        initialStorage: stored,
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            if (options.method === 'GET') return jsonResponse(inProgress);
            return jsonResponse(completion);
        },
    });
    try {
        await loaded.scriptModule.eventSource.emit(loaded.scriptModule.event_types.APP_READY);
        for (let attempt = 0; attempt < 20; attempt += 1) {
            if (buttonByText(loaded.testDocument, 'Continue')) break;
            await new Promise(resolve => setTimeout(resolve, 0));
        }

        assert.deepEqual(calls.map(call => call.options.method), ['GET']);
        assert.equal(loaded.scriptModule.chat.length, 0);
        assert.ok(buttonByText(loaded.testDocument, 'Continue'));
        assert.equal(buttonByText(loaded.testDocument, 'Retry Provider Stage'), null);

        await buttonByText(loaded.testDocument, 'Continue').click();
        assert.deepEqual(calls.map(call => call.options.method), ['GET', 'POST']);
        assert.deepEqual(JSON.parse(calls[1].options.body), eligible.actions[0]);
        assert.equal(loaded.scriptModule.chat.length, 1);
        assert.equal(loaded.scriptModule.testState.addCount, 1);
        assert.equal(providerRetryStore(loaded.storage).entries.length, 0);
        assert.equal(buttonByText(loaded.testDocument, 'Continue'), null);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('creator decision Retry survives reload and returns its exact decision family', async () => {
    const cases = [
        { action: 'accept', button: 'Accept', stage: 'recorder' },
        { action: 'regenerate', button: 'Regenerate', stage: 'writer' },
        { action: 'replan', button: 'Replan', stage: 'planner' },
        { action: 'repair_recording', button: 'Repair Recording', stage: 'recorder' },
    ];
    const originalFetch = globalThis.fetch;
    try {
        for (const [caseIndex, scenario] of cases.entries()) {
        const reviewId = `review-${String(caseIndex + 1).repeat(28)}`;
        const pending = providerStageRetryEnvelope('eligible', {
            chainCharacter: String(caseIndex + 4),
        });
        pending.status.stage = scenario.stage;
        if (scenario.stage === 'planner') {
            pending.status.provider = 'codex';
            pending.status.model_family = 'sol';
        }
        if (scenario.stage === 'recorder') pending.status.story_state_committed = true;
        const review = creatorReview(reviewId, scenario.action);
        const initialMessage = creatorReviewMessage(reviewId, {
            accepted: scenario.action === 'repair_recording',
        });
        const firstCalls = [];
        const first = await loadExtension({
            initialChat: [initialMessage],
            fetchImpl: async (url, options) => {
                firstCalls.push({ url, options });
                if (options.method === 'GET' && url.includes(`/reviews/${reviewId}`)) {
                    return jsonResponse(review);
                }
                if (options.method === 'POST' && url.endsWith(`/reviews/${reviewId}/decision`)) {
                    return jsonResponse(pending, 409);
                }
                throw new Error(`unexpected first creator-action request: ${options.method} ${url}`);
            },
        });
        let second = null;
        try {
            if (scenario.action === 'repair_recording') {
                const repair = providerStageRetryEnvelope('recording_repair_required');
                assert.equal(window.ceraCaptureProviderStageRetryStatus(repair), true);
                await buttonByText(first.testDocument, scenario.button).click();
            } else {
                await first.scriptModule.eventSource.emit(
                    first.scriptModule.event_types.CHARACTER_MESSAGE_RENDERED,
                    0,
                );
                await buttonByText(first.testDocument, scenario.button).click();
                if (scenario.action === 'replan') {
                    await buttonByText(first.testDocument, 'Submit').click();
                }
            }

            const decisionPosts = firstCalls.filter(call => (
                call.options.method === 'POST' && call.url.endsWith('/decision')
            ));
            assert.equal(decisionPosts.length, 1, `${scenario.action} dispatched once`);
            assert.equal(firstCalls.some(call => call.url.includes('/actions/')), false);
            const retained = providerRetryStore(first.storage).entries[0];
            assert.deepEqual(retained.continuation, {
                review_id: reviewId,
                action: scenario.action,
            });
            assert.equal(retained.last_submitted_action, null);

            const secondCalls = [];
            const decision = creatorDecision(reviewId, scenario.action);
            second = await loadExtension({
                initialStorage: Object.fromEntries(first.storage),
                initialChat: structuredClone(first.scriptModule.chat),
                fetchImpl: async (url, options) => {
                    secondCalls.push({ url, options });
                    if (options.method === 'GET' && url.includes('/provider-stage-retries/')) {
                        return jsonResponse(pending);
                    }
                    if (options.method === 'POST' && url.includes('/actions/')) {
                        return jsonResponse(decision);
                    }
                    throw new Error(`unexpected continued creator-action request: ${options.method} ${url}`);
                },
            });
            await second.scriptModule.eventSource.emit(second.scriptModule.event_types.APP_READY);
            for (let attempt = 0; attempt < 20; attempt += 1) {
                if (buttonByText(second.testDocument, 'Retry Provider Stage')) break;
                await new Promise(resolve => setTimeout(resolve, 0));
            }
            await buttonByText(second.testDocument, 'Retry Provider Stage').click();

            assert.deepEqual(secondCalls.map(call => call.options.method), ['GET', 'POST']);
            assert.equal(
                secondCalls.filter(call => call.url.includes('/actions/')).length,
                1,
                `${scenario.action} Provider Retry dispatched once`,
            );
            assert.equal(second.scriptModule.chat.length, 1);
            assert.equal(second.scriptModule.testState.addCount, 0);
            assert.equal(providerRetryStore(second.storage).entries.length, 0);
            assert.equal(
                second.scriptModule.chat[0].extra.cera_creator_review.state,
                decision.review.state,
            );
        } finally {
            await rm(first.root, { recursive: true, force: true });
            if (second) await rm(second.root, { recursive: true, force: true });
        }
        }
    } finally {
        globalThis.fetch = originalFetch;
    }
});

test('creator decision continuation rejects review and action drift', async () => {
    const reviewId = `review-${'a'.repeat(28)}`;
    const pending = providerStageRetryEnvelope('eligible');
    const stored = {
        cera_provider_stage_retry_status_v1: JSON.stringify({
            schema_version: 'cera.sillytavern.provider_stage_retry_status_store.v2',
            entries: [{
                chat_key: JSON.stringify(['0', 'test-chat']),
                envelope: pending,
                last_submitted_action: null,
                continuation: { review_id: reviewId, action: 'regenerate' },
            }],
        }),
    };
    const originalFetch = globalThis.fetch;
    const calls = [];
    const loaded = await loadExtension({
        initialStorage: stored,
        initialChat: [creatorReviewMessage(reviewId)],
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            if (options.method === 'GET') return jsonResponse(pending);
            return jsonResponse(creatorDecision(reviewId, 'replan'));
        },
    });
    try {
        await loaded.scriptModule.eventSource.emit(loaded.scriptModule.event_types.APP_READY);
        for (let attempt = 0; attempt < 20; attempt += 1) {
            if (buttonByText(loaded.testDocument, 'Retry Provider Stage')) break;
            await new Promise(resolve => setTimeout(resolve, 0));
        }
        await buttonByText(loaded.testDocument, 'Retry Provider Stage').click();
        assert.deepEqual(calls.map(call => call.options.method), ['GET', 'POST', 'GET']);
        assert.equal(loaded.scriptModule.chat.length, 1);
        assert.equal(loaded.scriptModule.chat[0].extra.cera_creator_review.state, 'review_ready');
        assert.deepEqual(providerRetryStore(loaded.storage).entries[0].continuation, {
            review_id: reviewId,
            action: 'regenerate',
        });
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('provider-stage successor may change stage occurrence but not request hash', async () => {
    const eligible = providerStageRetryEnvelope('eligible', { chainCharacter: 'a' });
    const successor = providerStageRetryEnvelope('eligible', { chainCharacter: 'b' });
    successor.status.stage = 'semantic_validator';
    successor.status.provider = 'codex';
    successor.status.model_family = 'luna';
    successor.status.technical_details.request_occurrence_sha256 = '7'.repeat(64);
    successor.status.technical_details.stage_input_sha256 = '8'.repeat(64);
    successor.actions[0].chain_id = successor.status.chain_id;
    const originalFetch = globalThis.fetch;
    const calls = [];
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            return jsonResponse(successor);
        },
    });
    try {
        assert.equal(window.ceraCaptureProviderStageRetryStatus(eligible), true);
        await buttonByText(loaded.testDocument, 'Retry Provider Stage').click();
        assert.equal(calls.length, 1);
        assert.ok(buttonByText(loaded.testDocument, 'Retry Provider Stage'));
        assert.match(
            loaded.testDocument.querySelector('.cera-review-heading').textContent,
            /Codex Semantic Validator/i,
        );
        assert.equal(
            providerRetryStore(loaded.storage).entries[0].envelope.status.chain_id,
            successor.status.chain_id,
        );
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('provider-stage completion with a mismatched request hash is never appended', async () => {
    const eligible = providerStageRetryEnvelope('eligible');
    const mismatched = providerStageCompletion(eligible);
    mismatched.cera.provider_stage_request_sha256 = '0'.repeat(64);
    const succeeded = providerStageRetryEnvelope('succeeded');
    const methods = [];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return methods.length === 1 ? jsonResponse(mismatched) : jsonResponse(succeeded);
        },
    });
    try {
        assert.equal(window.ceraCaptureProviderStageRetryStatus(eligible), true);
        await buttonByText(loaded.testDocument, 'Retry Provider Stage').click();
        assert.deepEqual(methods, ['POST', 'GET']);
        assert.equal(loaded.scriptModule.chat.length, 0);
        assert.equal(loaded.scriptModule.testState.addCount, 0);
        assert.ok(buttonByText(loaded.testDocument, 'Continue'));
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('blocked ambiguity shows Check Status only and reconciliation performs GET only', async () => {
    const calls = [];
    const originalFetch = globalThis.fetch;
    const blocked = providerStageRetryEnvelope('blocked_ambiguous');
    const succeeded = structuredClone(blocked);
    succeeded.status.state = 'succeeded';
    succeeded.status.stage_attempts_total = 2;
    succeeded.status.retry_actions_accepted = 1;
    succeeded.status.provider_operations_observed_total = 1;
    succeeded.status.failure_category = null;
    succeeded.status.available_actions = [];
    succeeded.actions = [];
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            return jsonResponse(succeeded);
        },
    });
    try {
        assert.equal(window.ceraCaptureProviderStageRetryStatus(blocked), true);
        assert.ok(buttonByText(loaded.testDocument, 'Check Status'));
        assert.equal(buttonByText(loaded.testDocument, 'Retry Provider Stage'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Repair Recording'), null);
        await buttonByText(loaded.testDocument, 'Check Status').click();
        assert.deepEqual(calls.map(call => call.options.method), ['GET']);
        assert.equal(buttonByText(loaded.testDocument, 'Check Status'), null);
        assert.match(
            loaded.testDocument.querySelector('.cera-review-status').textContent,
            /succeeded/i,
        );
        assert.equal(buttonByText(loaded.testDocument, 'Continue'), null);
        assert.ok(loaded.scriptModule.testState.deactivateCount >= 1);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('attempt exhaustion is read-only and never exposes provider Retry', async () => {
    const loaded = await loadExtension();
    try {
        assert.equal(
            window.ceraCaptureProviderStageRetryStatus(
                providerStageRetryEnvelope('attempts_exhausted'),
            ),
            true,
        );
        assert.equal(buttonByText(loaded.testDocument, 'Explicit Recovery'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Retry Provider Stage'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Check Status'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Repair Recording'), null);
        assert.match(
            loaded.testDocument.querySelector('.cera-review-status').textContent,
            /last accepted head/,
        );
    } finally {
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('known non-Retry terminal is read-only', async () => {
    const loaded = await loadExtension();
    try {
        assert.equal(
            window.ceraCaptureProviderStageRetryStatus(
                providerStageRetryEnvelope('recovery_required'),
            ),
            true,
        );
        assert.equal(buttonByText(loaded.testDocument, 'Explicit Recovery'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Retry Provider Stage'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Check Status'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Repair Recording'), null);
        assert.match(
            loaded.testDocument.querySelector('.cera-review-status').textContent,
            /known non-Retry failure/,
        );
    } finally {
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('recording repair status exposes only the separate review repair authority', async () => {
    const reviewId = `review-${'a'.repeat(28)}`;
    const accepted = {
        name: 'Sakura',
        is_user: false,
        mes: 'Accepted story remains visible.',
        extra: {
            cera_creator_review: {
                review_id: reviewId,
                state: 'accepted',
                completion: {
                    profile_id: 'cera.pi_scene.lean.v1',
                    request_id: 'request:accepted-recording-repair',
                    candidate_id: 'candidate:accepted-recording-repair',
                    route_mode: 'ordinary',
                    provisional: false,
                    status: 'accepted',
                    story_state_committed: true,
                },
            },
        },
    };
    const repair = providerStageRetryEnvelope('recording_repair_required');
    const calls = [];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        initialChat: [accepted],
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            if (options.method === 'POST') {
                return jsonResponse({ state: 'accepted', recording_status: 'complete' });
            }
            throw new Error('recording repair must not fake generic Retry success via GET');
        },
    });
    try {
        assert.equal(window.ceraCaptureProviderStageRetryStatus(repair), true);
        assert.ok(buttonByText(loaded.testDocument, 'Repair Recording'));
        assert.equal(buttonByText(loaded.testDocument, 'Retry Provider Stage'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Check Status'), null);
        await buttonByText(loaded.testDocument, 'Repair Recording').click();
        assert.equal(calls[0].options.method, 'POST');
        assert.match(calls[0].url, /reviews\/review-/);
        assert.deepEqual(JSON.parse(calls[0].options.body), {
            action: 'repair_recording',
            feedback: null,
        });
        assert.equal(calls.length, 1);
        assert.equal(
            loaded.testDocument.querySelector('#cera_provider_stage_retry_panel'),
            null,
        );
        assert.equal(calls.some(call => call.url.includes('/actions/')), false);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('critical provider-stage normalizers close six stages and six retryable failure classes', async () => {
    const { root } = await loadExtension();
    try {
        const actions = await import(
            `${pathToFileURL(path.join(
                root,
                'public/scripts/extensions/third-party/cera-creator-review/review-actions.js',
            )).href}?v=${Date.now()}`
        );
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
                const critical = criticalProviderStageFailure({ stage, failureClass });
                assert.deepEqual(actions.normalizeProviderStageRetryExhausted(critical), critical);
                assert.deepEqual(
                    actions.normalizeProviderStageRetryExhaustedError(exhaustedError(critical)),
                    critical,
                );
            }
        }
        const failure = eligibleTransportFailure();
        const critical = criticalProviderStageFailure({ stage: 'semantic_validator' });
        const status = exhaustedStatus(failure, critical);
        assert.deepEqual(actions.normalizeTransportRetryStatus(status), status);
        assert.equal('transport_retry' in status, false);
        assert.equal('superseded_by_retry_id' in status, false);

        const base = criticalProviderStageFailure();
        for (const mutation of [
            { provider: 'deepseek' },
            { model_family: 'sol-medium' },
            { attempts_total: 2 },
            { retries_consumed: 1 },
            { failed_stage_effect_committed: true },
            { provider_operations_conservative_total: 1 },
            { final_failure_class: 'pretransport_failed' },
            { final_failure_class: 'dispatch_ambiguous' },
            { raw_provider_output: 'PRIVATE OUTPUT' },
        ]) {
            assert.equal(actions.normalizeProviderStageRetryExhausted({
                ...base,
                ...mutation,
            }), null);
        }
        assert.equal(actions.normalizeProviderStageRetryExhausted(
            criticalProviderStageFailure({ stage: 'recorder', storyStateCommitted: false }),
        ), null);
        assert.equal(actions.normalizeProviderStageRetryExhaustedError({
            ...exhaustedError(base),
            debug_log_path: 'D:\\private\\debug.md',
        }), null);
    } finally {
        await rm(root, { recursive: true, force: true });
    }
});

test('critical stage failure persists, blocks only its chat, and restores without Retry', async () => {
    const first = await loadExtension();
    let second = null;
    try {
        const failure = exhaustedError(criticalProviderStageFailure({
            stage: 'adult_filter',
            failureClass: 'provider_output_invalid',
        }));
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        const panel = first.testDocument.querySelector('#cera_provider_stage_failure_panel');
        assert.ok(panel);
        assert.match(elementText(panel), /CRITICAL CERA FAILURE - DeepSeek Adult Filter/);
        assert.match(elementText(panel), /failed after three attempts/);
        assert.match(elementText(panel), /Technical details/);
        assert.equal(buttonByText(first.testDocument, 'Retry transport'), null);
        assert.equal(buttonByText(first.testDocument, 'Check retry status'), null);
        assert.ok(first.scriptModule.testState.deactivateCount >= 1);
        const stored = providerFailureStore(first.storage);
        assert.equal(stored.schema_version, 'cera.sillytavern.provider_stage_failure_store.v1');
        assert.equal(stored.entries.length, 1);
        assert.equal(stored.entries[0].critical_provider_stage_failure.stage, 'adult_filter');
        assert.equal(JSON.stringify(stored).includes('provider output'), false);
        assert.equal(retryStore(first.storage).entries?.length ?? 0, 0);

        second = await loadExtension({ initialStorage: Object.fromEntries(first.storage) });
        await second.scriptModule.eventSource.emit(second.scriptModule.event_types.APP_READY);
        assert.ok(second.testDocument.querySelector('#cera_provider_stage_failure_panel'));
        assert.equal(buttonByText(second.testDocument, 'Retry transport'), null);

        second.scriptModule.__setChatId('independent-chat');
        await second.scriptModule.eventSource.emit(second.scriptModule.event_types.CHAT_CHANGED);
        assert.equal(second.testDocument.querySelector('#cera_provider_stage_failure_panel'), null);
        assert.ok(second.scriptModule.testState.activateCount >= 1);
        assert.equal(providerFailureStore(second.storage).entries.length, 1);

        second.scriptModule.__setChatId('test-chat');
        await second.scriptModule.eventSource.emit(second.scriptModule.event_types.CHAT_CHANGED);
        assert.ok(second.testDocument.querySelector('#cera_provider_stage_failure_panel'));
        assert.ok(second.scriptModule.testState.deactivateCount >= 2);
    } finally {
        await rm(first.root, { recursive: true, force: true });
        if (second) await rm(second.root, { recursive: true, force: true });
    }
});

test('v2 exhausted GET terminalizes one retry chain without another POST', async () => {
    const failure = eligibleTransportFailure();
    const methods = [];
    const responses = [
        jsonResponse({
            error: {
                message: 'SillyTavern could not reach the local CERA service.',
                type: 'cera_error',
                code: 'cera_loopback_unavailable',
                stage: 'sillytavern_cera_review_proxy',
                retryable: false,
                fallback_used: false,
            },
        }, 502),
        jsonResponse(exhaustedStatus(failure, criticalProviderStageFailure({
            stage: 'planner',
            failureClass: 'provider_unavailable',
        }))),
    ];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return responses.shift();
        },
    });
    try {
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.deepEqual(methods, ['POST', 'GET']);
        assert.equal(methods.filter(method => method === 'POST').length, 1);
        assert.equal(retryStore(loaded.storage).entries.length, 0);
        assert.equal(providerFailureStore(loaded.storage).entries.length, 1);
        assert.equal(buttonByText(loaded.testDocument, 'Retry transport'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Check retry status'), null);
        assert.match(
            elementText(loaded.testDocument.querySelector('#cera_provider_stage_failure_panel')),
            /Codex Planner failed after three attempts/,
        );
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('terminal HTTP 503 stops immediately without status polling or another Retry', async () => {
    const failure = eligibleTransportFailure();
    const methods = [];
    const critical = criticalProviderStageFailure({
        stage: 'writer',
        failureClass: 'provider_process_failed',
    });
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return jsonResponse(exhaustedError(critical), 503);
        },
    });
    try {
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.deepEqual(methods, ['POST']);
        assert.equal(retryStore(loaded.storage).entries.length, 0);
        assert.equal(providerFailureStore(loaded.storage).entries.length, 1);
        assert.equal(buttonByText(loaded.testDocument, 'Retry transport'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Check retry status'), null);
        assert.match(
            elementText(loaded.testDocument.querySelector('#cera_provider_stage_failure_panel')),
            /DeepSeek Writer failed after three attempts/,
        );
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('Recorder exhaustion attaches to the accepted assistant and preserves acceptance wording', async () => {
    const accepted = {
        name: 'Sakura',
        is_user: false,
        is_system: false,
        mes: 'Accepted story prose remains in the chat.',
        extra: {
            cera_creator_review: {
                state: 'accepted',
                completion: {
                    profile_id: 'cera.pi_scene.lean.v1',
                    request_id: 'request:accepted-recorder',
                    candidate_id: 'candidate:accepted-recorder',
                    route_mode: 'ordinary',
                    provisional: false,
                    status: 'accepted',
                    story_state_committed: true,
                },
            },
        },
    };
    const loaded = await loadExtension({ initialChat: [accepted] });
    try {
        const critical = criticalProviderStageFailure({
            stage: 'recorder',
            failureClass: 'provider_completion_incomplete',
        });
        assert.equal(window.ceraCaptureTransportFailure(exhaustedError(critical)), true);
        for (let attempt = 0; attempt < 20; attempt += 1) {
            if (loaded.scriptModule.chat[0].extra.cera_creator_review
                .provider_stage_retry_exhausted) break;
            await new Promise(resolve => setTimeout(resolve, 0));
        }
        assert.deepEqual(
            loaded.scriptModule.chat[0].extra.cera_creator_review
                .provider_stage_retry_exhausted,
            critical,
        );
        assert.equal(providerFailureStore(loaded.storage).entries[0].attached_message_index, 0);
        const panelText = elementText(
            loaded.testDocument.querySelector('#cera_provider_stage_failure_panel'),
        );
        assert.match(panelText, /DeepSeek Recorder failed after three attempts/);
        assert.match(panelText, /assistant story remains accepted/);
        assert.match(panelText, /recording is incomplete/);
        assert.equal(buttonByText(loaded.testDocument, 'Retry transport'), null);
        assert.ok(loaded.scriptModule.testState.saveCount >= 1);
    } finally {
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('proxy 502 after POST becomes unknown and only GET may reconcile it', async () => {
    const failure = eligibleTransportFailure();
    const calls = [];
    const responses = [
        jsonResponse({
            error: {
                message: 'SillyTavern could not reach the local CERA service.',
                type: 'cera_error',
                code: 'cera_loopback_unavailable',
                stage: 'sillytavern_cera_review_proxy',
                retryable: false,
                fallback_used: false,
            },
        }, 502),
        jsonResponse({
            error: {
                message: 'SillyTavern could not reach the local CERA service.',
                type: 'cera_error',
                code: 'cera_loopback_unavailable',
                stage: 'sillytavern_cera_review_proxy',
                retryable: false,
                fallback_used: false,
            },
        }, 502),
        jsonResponse(transportRetryStatus(failure, 'in_progress', {
            phase: 'dispatch_started',
        })),
    ];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            return responses.shift();
        },
    });
    try {
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.deepEqual(calls.map(call => call.options.method), ['POST', 'GET']);
        assert.equal(retryStore(loaded.storage).entries[0].phase, 'unknown');
        assert.equal(buttonByText(loaded.testDocument, 'Retry transport'), null);

        await buttonByText(loaded.testDocument, 'Check retry status').click();
        assert.deepEqual(calls.map(call => call.options.method), ['POST', 'GET', 'GET']);
        assert.equal(calls.filter(call => call.options.method === 'POST').length, 1);
        assert.equal(retryStore(loaded.storage).entries[0].phase, 'in_progress');
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('chat switch during POST cannot append or rewrite another chat receipt', async () => {
    const firstFailure = eligibleTransportFailure();
    const secondFailure = eligibleTransportFailure({
        retryCharacter: '4',
        requestCharacter: '5',
        proofCharacter: '6',
    });
    let resolvePost;
    const postResponse = new Promise(resolve => { resolvePost = resolve; });
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({ fetchImpl: async () => postResponse });
    try {
        assert.equal(window.ceraCaptureTransportFailure(firstFailure), true);
        const pendingClick = buttonByText(loaded.testDocument, 'Retry transport').click();
        loaded.scriptModule.__setChatId('different-chat');
        await loaded.scriptModule.eventSource.emit(loaded.scriptModule.event_types.CHAT_CHANGED);
        assert.equal(window.ceraCaptureTransportFailure(secondFailure), true);
        resolvePost(jsonResponse(transportRetryCompletion(firstFailure)));
        await pendingClick;

        assert.equal(loaded.scriptModule.chat.length, 0);
        const stored = retryStore(loaded.storage).entries;
        assert.equal(stored.length, 2);
        assert.equal(
            stored.find(entry => entry.chat_key.includes('different-chat')).receipt.retry_id,
            secondFailure.error.transport_retry.retry_id,
        );
        assert.equal(
            stored.find(entry => entry.chat_key.includes('test-chat')).phase,
            'in_progress',
        );
        assert.ok(buttonByText(loaded.testDocument, 'Retry transport'));
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('POST and GET completions with a mismatched CERA request identity never append', async () => {
    const failure = eligibleTransportFailure();
    const completion = transportRetryCompletion(failure);
    const mismatched = {
        ...completion,
        cera: {
            ...completion.cera,
            request_id: `request-${'9'.repeat(64)}`,
        },
    };
    const responses = [
        jsonResponse(mismatched),
        jsonResponse(transportRetryStatus(failure, 'succeeded', {
            completion: mismatched,
            completion_sha256: '0'.repeat(64),
        })),
    ];
    const methods = [];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return responses.shift();
        },
    });
    try {
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.deepEqual(methods, ['POST', 'GET']);
        assert.equal(loaded.scriptModule.chat.length, 0);
        assert.equal(loaded.scriptModule.testState.addCount, 0);
        assert.equal(retryStore(loaded.storage).entries[0].phase, 'unknown');
        assert.equal(buttonByText(loaded.testDocument, 'Retry transport'), null);
        assert.ok(buttonByText(loaded.testDocument, 'Check retry status'));
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('authenticated blocked status clears the action without another dispatch', async () => {
    const failure = eligibleTransportFailure();
    const responses = [
        jsonResponse({ status: 'error', error: { error_code: 'CERA_STATE_CONFLICT' } }, 409),
        jsonResponse(transportRetryStatus(failure, 'blocked', {
            blocked_reason_code: 'provider_ledger_changed',
        })),
    ];
    const methods = [];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return responses.shift();
        },
    });
    try {
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.deepEqual(methods, ['POST', 'GET']);
        assert.equal(retryStore(loaded.storage).entries.length, 0);
        assert.equal(buttonByText(loaded.testDocument, 'Retry transport'), null);
        assert.equal(buttonByText(loaded.testDocument, 'Check retry status'), null);
        assert.ok(loaded.scriptModule.testState.activateCount >= 1);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('superseded status exposes only the backend-issued successor manual action', async () => {
    const failure = eligibleTransportFailure();
    const successorId = `retry-${'7'.repeat(64)}`;
    const successorAction = {
        ...failure.error.transport_retry,
        retry_id: successorId,
        retry_url: `/v1/cera/transport-retries/${successorId}`,
        effect_proof_sha256: '8'.repeat(64),
    };
    const responses = [
        jsonResponse({
            error: {
                message: 'SillyTavern could not reach the local CERA service.',
                type: 'cera_error',
                code: 'cera_loopback_unavailable',
                stage: 'sillytavern_cera_review_proxy',
                retryable: false,
                fallback_used: false,
            },
        }, 502),
        jsonResponse(transportRetryStatus(failure, 'superseded', {
            superseded_by_retry_id: successorId,
            transport_retry: successorAction,
        })),
    ];
    const methods = [];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            methods.push(options.method);
            return responses.shift();
        },
    });
    try {
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.deepEqual(methods, ['POST', 'GET']);
        const state = retryStore(loaded.storage).entries[0];
        assert.equal(state.phase, 'eligible');
        assert.equal(state.receipt.retry_id, successorId);
        assert.ok(buttonByText(loaded.testDocument, 'Retry transport'));
        assert.equal(methods.filter(method => method === 'POST').length, 1);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('save interruption after push reconciles one terminal completion without duplication', async () => {
    const failure = eligibleTransportFailure();
    const completion = transportRetryCompletion(failure);
    const calls = [];
    const responses = [
        jsonResponse(completion),
        jsonResponse(transportRetryStatus(failure, 'succeeded', {
            completion,
            completion_sha256: 'd'.repeat(64),
        })),
    ];
    const originalFetch = globalThis.fetch;
    const loaded = await loadExtension({
        fetchImpl: async (url, options) => {
            calls.push({ url, options });
            return responses.shift();
        },
    });
    try {
        loaded.scriptModule.__failNextSaves(1);
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        await buttonByText(loaded.testDocument, 'Retry transport').click();
        assert.deepEqual(calls.map(call => call.options.method), ['POST', 'GET']);
        assert.equal(loaded.scriptModule.chat.length, 1);
        assert.equal(loaded.scriptModule.testState.addCount, 1);
        assert.equal(loaded.scriptModule.testState.saveCount, 2);
        assert.equal(
            loaded.scriptModule.chat[0].extra.cera_creator_review
                .transport_retry_completion.retry_id,
            failure.error.transport_retry.retry_id,
        );
        assert.equal(retryStore(loaded.storage).entries.length, 0);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(loaded.root, { recursive: true, force: true });
    }
});

test('reload plus succeeded status deduplicates a completion retained after save ambiguity', async () => {
    const failure = eligibleTransportFailure();
    const completion = transportRetryCompletion(failure);
    const unavailable = {
        error: {
            message: 'SillyTavern could not reach the local CERA service.',
            type: 'cera_error',
            code: 'cera_loopback_unavailable',
            stage: 'sillytavern_cera_review_proxy',
            retryable: false,
            fallback_used: false,
        },
    };
    const originalFetch = globalThis.fetch;
    const firstResponses = [jsonResponse(completion), jsonResponse(unavailable, 502)];
    const first = await loadExtension({
        fetchImpl: async () => firstResponses.shift(),
    });
    let second = null;
    try {
        first.scriptModule.__failNextSaves(1);
        assert.equal(window.ceraCaptureTransportFailure(failure), true);
        await buttonByText(first.testDocument, 'Retry transport').click();
        assert.equal(first.scriptModule.chat.length, 1);
        assert.equal(retryStore(first.storage).entries[0].phase, 'completion_received');

        const status = transportRetryStatus(failure, 'succeeded', {
            completion,
            completion_sha256: 'e'.repeat(64),
        });
        second = await loadExtension({
            initialStorage: Object.fromEntries(first.storage),
            initialChat: structuredClone(first.scriptModule.chat),
            fetchImpl: async () => jsonResponse(status),
        });
        await second.scriptModule.eventSource.emit(second.scriptModule.event_types.APP_READY);
        for (let attempt = 0; attempt < 20; attempt += 1) {
            if (retryStore(second.storage).entries?.length === 0) break;
            await new Promise(resolve => setTimeout(resolve, 0));
        }
        assert.equal(second.scriptModule.chat.length, 1);
        assert.equal(second.scriptModule.testState.addCount, 0);
        assert.equal(second.scriptModule.testState.saveCount, 1);
        assert.equal(retryStore(second.storage).entries.length, 0);
    } finally {
        globalThis.fetch = originalFetch;
        await rm(first.root, { recursive: true, force: true });
        if (second) await rm(second.root, { recursive: true, force: true });
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
