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
export function activateSendButtons() {}
export function deactivateSendButtons() {}
export function getRequestHeaders() { return {}; }
export async function saveChatConditional() {}
export function updateMessageBlock() {}
`,
    );
    await writeFile(
        path.join(root, 'public', 'scripts', 'openai.js'),
        `export const oai_settings = { custom_include_headers: '' };\n`,
    );
    for (const name of ['index.js', 'completion-metadata.js', 'creator-trace-panel.js']) {
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
    for (const marker of ["'Regenerate'", "'Replan'", "'Decline'"]) {
        assert.equal(source.includes(marker), true, `missing action marker: ${marker}`);
    }
});
