import {
    activateSendButtons,
    chat,
    characters,
    deactivateSendButtons,
    eventSource,
    event_types,
    getRequestHeaders,
    saveChatConditional,
    this_chid,
    updateMessageBlock,
} from '../../../../script.js';
import { oai_settings } from '../../../../scripts/openai.js';
import {
    completionIdentity,
    normalizeCompletionMetadata,
    normalizeCreatorTrace,
    validReviewId,
} from './completion-metadata.js';
import { appendCreatorTrace, renderCompletionPanel } from './creator-trace-panel.js';
import {
    normalizeReprojectionRequired,
    provisionalAcceptanceCommitted,
    provisionalAcceptEnabled,
} from './review-actions.js';

const API_ROOT = '/api/plugins/cera-review';
const META_KEY = 'cera_creator_review';
const COMPLETION_EVENT = 'cera:completion-metadata';
const POLL_MS = 900;
const TERMINAL_REVIEW_STATES = Object.freeze([
    'review_ready',
    'error',
    'accepted',
    'rejected',
    'declined',
    'awaiting_feedback',
]);
const CONTROL_STORAGE_KEY = 'cera_creator_controls_v1';
const READABILITY_KEY = 'vera_cast_readability';
const DEFAULT_SPEAKER_COLORS = Object.freeze({
    hana: '#E7A6B2',
    sakura: '#9B59B6',
    mia: '#F4A460',
    enne: '#5DADE2',
    tomi: '#2ECC71',
    aoi: '#D4AC0D',
    yuuni: '#FF69B4',
});
const DEFAULT_CONTROLS = Object.freeze({
    scene_depth: 'auto',
    character_autonomy: 'both',
    adult_craft_mode: 'off',
    prompt_handling: 'adjustment',
    reasoning_effort: 'medium',
});

let completionMetadata = null;
const polling = new Map();

window.ceraCreatorControls = () => ({ ...readControls() });
window.ceraCaptureCompletionMetadata = value => captureCompletionMetadata(value);

eventSource.on(event_types.APP_READY, () => {
    installControlBar();
});

window.addEventListener(COMPLETION_EVENT, event => {
    const value = normalizeCompletionMetadata(event?.detail);
    if (value) completionMetadata = structuredClone(value);
});

eventSource.on(event_types.MESSAGE_RECEIVED, async messageId => {
    await attachPendingMetadata(messageId, { resume: false });
});

eventSource.on(event_types.CHARACTER_MESSAGE_RENDERED, async messageId => {
    await attachPendingMetadata(messageId, { resume: false });
    renderStoredSpeakerMarks(messageId);
    renderStoredCompletionMetadata(messageId);
    if (chat[messageId]?.extra?.[META_KEY]?.provisional) {
        await resumeReview(messageId);
    }
});

async function attachPendingMetadata(messageId, { resume = true } = {}) {
    const message = chat[messageId];
    if (!message || message.is_user || message.extra?.[META_KEY]) return false;
    const metadata = takeCompletionMetadata();
    if (!metadata) return false;
    const reviewId = validReviewId(metadata.provisional_review_id)
        ? metadata.provisional_review_id
        : null;
    message.extra ??= {};
    message.extra[META_KEY] = {
        review_id: reviewId,
        candidate_id: metadata.candidate_id,
        state: metadata.status,
        provisional: Boolean(metadata.provisional && reviewId),
        completion: structuredClone(metadata),
    };
    await saveChatConditional();
    renderStoredCompletionMetadata(messageId);
    if (resume && reviewId) await resumeReview(messageId);
    return true;
}

function captureCompletionMetadata(value) {
    const metadata = normalizeCompletionMetadata(value);
    if (!metadata) return false;
    const queue = Array.isArray(window.ceraCompletionMetadataQueue)
        ? window.ceraCompletionMetadataQueue
        : (window.ceraCompletionMetadataQueue = []);
    const snapshot = structuredClone(metadata);
    queue.push(snapshot);
    if (queue.length > 8) queue.splice(0, queue.length - 8);
    completionMetadata = structuredClone(snapshot);
    window.dispatchEvent(new CustomEvent(COMPLETION_EVENT, { detail: snapshot }));
    return true;
}

function takeCompletionMetadata() {
    const queue = Array.isArray(window.ceraCompletionMetadataQueue)
        ? window.ceraCompletionMetadataQueue
        : [];
    const metadata = normalizeCompletionMetadata(completionMetadata ?? queue[0] ?? null);
    completionMetadata = null;
    if (!metadata) return null;
    const identity = completionIdentity(metadata);
    const queuedIndex = queue.findIndex(value => {
        const normalized = normalizeCompletionMetadata(value);
        return normalized && completionIdentity(normalized) === identity;
    });
    if (queuedIndex >= 0) queue.splice(queuedIndex, 1);
    return structuredClone(metadata);
}

eventSource.on(event_types.CHAT_CHANGED, () => {
    for (let index = 0; index < chat.length; index += 1) {
        renderStoredCompletionMetadata(index);
        renderStoredSpeakerMarks(index);
        if (chat[index]?.extra?.[META_KEY]?.provisional) {
            void resumeReview(index);
        }
    }
});

eventSource.on(event_types.GENERATION_ENDED, async () => {
    await attachPendingMetadata(chat.length - 1);
    if (hasPendingReview()) deactivateSendButtons();
});

async function resumeReview(messageId) {
    const metadata = chat[messageId]?.extra?.[META_KEY];
    if (!metadata?.review_id || polling.has(metadata.review_id)) return;
    deactivateSendButtons();
    renderPending(messageId);
    const task = poll(messageId, metadata.review_id).finally(() => {
        polling.delete(metadata.review_id);
    });
    polling.set(metadata.review_id, task);
    await task;
}

async function poll(messageId, reviewId) {
    while (chat[messageId]?.extra?.[META_KEY]?.provisional) {
        let review;
        try {
            review = await requestJson(`/v1/cera/reviews/${encodeURIComponent(reviewId)}`);
        } catch (error) {
            renderTransportError(messageId, reviewId, error);
            return;
        }
        updateStoredState(messageId, review);
        renderReview(messageId, review);
        if (TERMINAL_REVIEW_STATES.includes(review.state)) {
            await saveChatConditional();
            return;
        }
        await delay(POLL_MS);
    }
}

function renderPending(messageId) {
    const panel = panelFor(messageId);
    if (!panel) return;
    panel.innerHTML = `
        <div class="cera-review-badge">CERA - PROVISIONAL</div>
        <div class="cera-review-status">Preparing creator review...</div>`;
    appendCreatorTrace(panel, chat[messageId]?.extra?.[META_KEY]?.completion);
}

function renderStoredCompletionMetadata(messageId) {
    const stored = chat[messageId]?.extra?.[META_KEY];
    const completion = stored?.completion;
    if (!completion) return;
    const panel = panelFor(messageId);
    if (!panel) return;
    const reprojection = normalizeReprojectionRequired(stored.action_outcome);
    if (reprojection) {
        renderReprojectionRequired(messageId, reprojection);
        return;
    }
    if (stored.provisional && validReviewId(stored.review_id)) {
        if (!panel.querySelector('.cera-trace-details')) appendCreatorTrace(panel, completion);
        return;
    }
    renderCompletionPanel(
        panel,
        completion,
        stored.state,
        validReviewId(stored.review_id),
    );
}

function renderTransportError(messageId, reviewId, error) {
    const panel = panelFor(messageId);
    if (!panel) return;
    const diagnostic = describeReviewRequestError(error);
    panel.innerHTML = `
        <div class="cera-review-badge cera-review-error">CERA - PROVISIONAL</div>
        <div class="cera-review-status"></div>
        <div class="cera-review-reason"></div>
        <div class="cera-review-actions"></div>`;
    panel.querySelector('.cera-review-status').textContent = diagnostic.heading;
    panel.querySelector('.cera-review-reason').textContent = diagnostic.detail;
    panel.querySelector('.cera-review-actions').append(
        actionButton(
            'Check CERA status',
            false,
            () => refreshReviewStatus(messageId, reviewId),
        ),
    );
}

async function refreshReviewStatus(messageId, reviewId) {
    disablePanel(messageId, true);
    statusText(messageId, 'Checking persisted CERA review status...');
    try {
        const review = await requestJson(
            `/v1/cera/reviews/${encodeURIComponent(reviewId)}`,
        );
        updateStoredState(messageId, review);
        renderReview(messageId, review);
        await saveChatConditional();
        if (!TERMINAL_REVIEW_STATES.includes(review.state)) {
            await resumeReview(messageId);
        }
    } catch (error) {
        renderTransportError(messageId, reviewId, error);
    }
}

function describeReviewRequestError(error) {
    if (error instanceof CeraReviewRequestError) {
        if (error.kind === 'transport') {
            return {
                heading: 'Error - Local CERA connection failed. Continuation is blocked.',
                detail: `${error.message} This does not establish that Codex failed.`,
            };
        }
        return {
            heading: 'Error - CERA review status could not be read. Continuation is blocked.',
            detail: error.message,
        };
    }
    return {
        heading: 'Error - CERA review status could not be read. Continuation is blocked.',
        detail: String(error),
    };
}

class CeraReviewRequestError extends Error {
    constructor(kind, message) {
        super(message);
        this.name = 'CeraReviewRequestError';
        this.kind = kind;
    }
}

function renderReview(messageId, review) {
    const panel = panelFor(messageId);
    if (!panel) return;
    if (review.state === 'accepted') {
        markCanonical(messageId, review, {
            canonStatus: review.canon_status === 'provisional' ? 'provisional' : 'accepted',
        });
        return;
    }
    if (['rejected', 'declined'].includes(review.state)) {
        panel.remove();
        removeProvisionalMessage(messageId);
        return;
    }
    if (review.schema_version === 'cera.pi_scene.review.v1') {
        renderPiSceneReview(messageId, review, panel);
        return;
    }
    renderLegacyReview(messageId, review, panel);
}

function renderPiSceneReview(messageId, review, panel) {
    panel.innerHTML = '';

    const badge = document.createElement('div');
    badge.className = 'cera-review-badge';
    badge.textContent = 'CERA - PROVISIONAL';
    panel.appendChild(badge);

    const heading = document.createElement('div');
    heading.className = 'cera-review-heading';
    heading.textContent = review.route === 'adult'
        ? 'ADULT SCENE READY FOR CREATOR REVIEW'
        : 'CODEX SEQUENCE REALIZATION READY FOR CREATOR REVIEW';
    panel.appendChild(heading);

    const result = document.createElement('div');
    result.className = 'cera-review-result cera-review-severity-good';
    result.textContent = 'Review the visible prose, then choose an action.';
    panel.appendChild(result);

    for (const warning of review.warnings ?? []) {
        const notice = document.createElement('div');
        notice.className = 'cera-review-reason';
        notice.textContent = `Advisory: ${warning.warning_code ?? 'review warning'}${
            review.route !== 'adult' && warning.excerpt ? ` - ${warning.excerpt}` : ''
        }`;
        panel.appendChild(notice);
    }

    appendCreatorTrace(panel, chat[messageId]?.extra?.[META_KEY]?.completion);

    const actions = document.createElement('div');
    actions.className = 'cera-review-actions';
    if (!validReviewId(review.review_id)) {
        const notice = document.createElement('div');
        notice.className = 'cera-review-reason cera-review-severity-error';
        notice.textContent = 'No durable action is available because CERA did not supply a valid review ID.';
        panel.appendChild(notice);
        return;
    }
    actions.append(actionButton(
        'Accept',
        !review.accept_enabled,
        () => decide(messageId, review, 'accept'),
    ));
    if (provisionalAcceptEnabled(review)) {
        actions.append(actionButton(
            'Accept as Provisional',
            false,
            () => decide(messageId, review, 'accept_provisional'),
        ));
    }
    if (review.regenerate_enabled) {
        actions.append(actionButton(
            'Regenerate',
            false,
            () => decide(messageId, review, 'regenerate'),
        ));
    }
    if (review.replan_enabled) {
        actions.append(actionButton(
            'Replan',
            false,
            () => feedbackDecision(messageId, review, 'replan'),
        ));
    }
    if (review.repair_recording_enabled) {
        actions.append(actionButton(
            'Repair Recording',
            false,
            () => decide(messageId, review, 'repair_recording'),
        ));
    }
    actions.append(actionButton(
        'Decline',
        !review.decline_enabled,
        () => decide(messageId, review, 'decline'),
    ));
    panel.appendChild(actions);
}

function renderLegacyReview(messageId, review, panel) {
    const assessment = review.assessment;
    const title = assessment
        ? `${assessment.severity.toUpperCase()} - ${assessment.issue_owner}`
        : 'SOL REVIEWING...';
    panel.innerHTML = '';

    const badge = document.createElement('div');
    badge.className = `cera-review-badge ${review.state === 'error' ? 'cera-review-error' : ''}`;
    badge.textContent = 'CERA - PROVISIONAL';
    panel.appendChild(badge);

    const heading = document.createElement('div');
    heading.className = 'cera-review-heading';
    heading.textContent = 'CODEX SEQUENCE PLAN';
    panel.appendChild(heading);
    const list = document.createElement('ol');
    list.className = 'cera-review-sequence';
    for (const beat of review.sequence_plan ?? []) {
        const item = document.createElement('li');
        item.textContent = beat;
        list.appendChild(item);
    }
    panel.appendChild(list);

    const result = document.createElement('div');
    const severity = review.state === 'error'
        ? 'error'
        : String(assessment?.severity ?? 'pending').toLowerCase();
    result.className = `cera-review-result cera-review-severity-${severity}`;
    result.textContent = title;
    panel.appendChild(result);
    if (assessment?.creator_reason) {
        const reason = document.createElement('div');
        reason.className = 'cera-review-reason';
        reason.textContent = assessment.creator_reason;
        panel.appendChild(reason);
    }
    appendCreatorTrace(panel, chat[messageId]?.extra?.[META_KEY]?.completion);
    if (review.state === 'error') return;

    const actions = document.createElement('div');
    actions.className = 'cera-review-actions';
    if (!validReviewId(review.review_id)) {
        const notice = document.createElement('div');
        notice.className = 'cera-review-reason cera-review-severity-error';
        notice.textContent = 'No durable action is available because CERA did not supply a valid review ID.';
        panel.appendChild(notice);
        return;
    }
    actions.append(actionButton(
        'Accept',
        !review.accept_enabled,
        () => decide(messageId, review, 'accept'),
    ));
    if (
        review.accept_enabled
        && ['concern', 'critical'].includes(String(assessment?.severity ?? '').toLowerCase())
    ) {
        actions.append(actionButton(
            'False Positive',
            false,
            () => decide(messageId, review, 'false_positive'),
        ));
    }
    actions.append(
        actionButton('Adjustment', false, () => feedbackDecision(messageId, review, 'correction_adjustment')),
        actionButton('DeepSeek', false, () => feedbackDecision(messageId, review, 'deepseek_rewrite')),
        actionButton('Codex', false, () => feedbackDecision(messageId, review, 'codex_replan')),
        actionButton('Decline', false, () => decide(messageId, review, 'decline')),
    );
    panel.appendChild(actions);
}

async function feedbackDecision(messageId, review, action) {
    const labels = {
        correction_adjustment: 'Describe the small correction or adjustment.',
        deepseek_rewrite: 'Describe what the prose realization should change.',
        codex_replan: 'Describe what the causal SequencePlan got wrong.',
        replan: 'Describe what the causal sequence should change.',
    };
    renderFeedbackEditor(messageId, review, action, labels[action]);
}

function renderFeedbackEditor(messageId, review, action, label) {
    const panel = panelFor(messageId);
    if (!panel) return;
    panel.querySelector('.cera-review-feedback')?.remove();
    const editor = document.createElement('div');
    editor.className = 'cera-review-feedback';
    const prompt = document.createElement('label');
    prompt.textContent = label;
    const input = document.createElement('textarea');
    input.className = 'text_pole cera-review-feedback-input';
    input.rows = 3;
    input.placeholder = 'Creator control feedback (never story dialogue or canon)';
    const controls = document.createElement('div');
    controls.className = 'cera-review-feedback-actions';
    controls.append(
        actionButton('Submit', false, async () => {
            const feedback = input.value.trim();
            if (!feedback && action !== 'replan') {
                input.focus();
                return;
            }
            await decide(messageId, review, action, feedback);
        }),
        actionButton('Cancel', false, () => editor.remove()),
    );
    prompt.appendChild(input);
    editor.append(prompt, controls);
    panel.appendChild(editor);
    input.focus();
}

async function decide(messageId, review, action, feedback = null) {
    if (!validReviewId(review?.review_id)) {
        statusText(messageId, 'CERA did not supply a valid durable review ID. No action was sent.');
        return;
    }
    const acceptsCandidate = ['accept', 'false_positive'].includes(action);
    disablePanel(messageId, true);
    statusText(
        messageId,
        action === 'accept'
            ? 'Processing...'
            : action === 'accept_provisional'
                ? 'Processing provisional acceptance...'
            : action === 'false_positive'
                ? 'Recording false positive and accepting...'
                : 'Applying creator feedback...',
    );
    try {
        const result = await requestJson(
            `/v1/cera/reviews/${encodeURIComponent(review.review_id)}/decision`,
            { method: 'POST', body: { action, feedback } },
        );
        const resolvedReview = result?.review ?? result;
        if (acceptsCandidate) {
            markCanonical(messageId, result);
            return;
        }
        if (action === 'accept_provisional') {
            const reprojection = normalizeReprojectionRequired(result);
            if (reprojection) {
                storeReprojectionRequired(messageId, reprojection);
                renderReprojectionRequired(messageId, reprojection);
                await saveChatConditional();
                return;
            }
            if (provisionalAcceptanceCommitted(result)) {
                markCanonical(messageId, result, { canonStatus: 'provisional' });
                return;
            }
        }
        const successor = result?.successor;
        const successorReviewId = successor?.cera?.provisional_review_id;
        const successorText = successor?.choices?.[0]?.message?.content;
        if (validReviewId(successorReviewId) && successorText) {
            chat[messageId].mes = successorText;
            chat[messageId].extra[META_KEY] = {
                review_id: successorReviewId,
                candidate_id: successor.cera.candidate_id,
                state: 'review_ready',
                provisional: true,
            };
            updateMessageBlock(messageId, chat[messageId]);
            const nextReview = await requestJson(
                `/v1/cera/reviews/${encodeURIComponent(successorReviewId)}`,
            );
            updateStoredState(messageId, nextReview);
            renderReview(messageId, nextReview);
            await saveChatConditional();
            return;
        }
        updateStoredState(messageId, resolvedReview);
        renderReview(messageId, resolvedReview);
        await saveChatConditional();
    } catch (error) {
        if (await reconcileDecisionAfterError(messageId, review.review_id)) return;
        statusText(messageId, `Save failed - candidate remains provisional: ${String(error)}`);
        disablePanel(messageId, false);
    }
}

async function reconcileDecisionAfterError(messageId, reviewId) {
    try {
        const persisted = await requestJson(
            `/v1/cera/reviews/${encodeURIComponent(reviewId)}`,
        );
        updateStoredState(messageId, persisted);
        renderReview(messageId, persisted);
        await saveChatConditional();
        return ['accepted', 'declined', 'rejected'].includes(persisted.state);
    } catch {
        return false;
    }
}

function markCanonical(messageId, result = null, { canonStatus = 'accepted' } = {}) {
    const metadata = chat[messageId]?.extra?.[META_KEY];
    if (metadata) {
        metadata.provisional = false;
        metadata.state = 'accepted';
        metadata.canon_status = canonStatus;
        delete metadata.action_outcome;
        if (result?.artifact_id) metadata.artifact_id = result.artifact_id;
        if (result?.generation) metadata.generation = result.generation;
        if (result?.accepted_turn_id) metadata.accepted_turn_id = result.accepted_turn_id;
        if (result?.accepted_receipt_sha256) {
            metadata.accepted_receipt_sha256 = result.accepted_receipt_sha256;
        }
        if (metadata.completion) {
            metadata.completion.provisional = false;
            metadata.completion.status = 'accepted';
            metadata.completion.story_state_committed = true;
            metadata.completion.canon_status = canonStatus;
            if (result?.artifact_id) metadata.completion.artifact_id = result.artifact_id;
            if (result?.generation) metadata.completion.generation = result.generation;
            if (result?.accepted_turn_id) {
                metadata.completion.accepted_turn_id = result.accepted_turn_id;
            }
            if (result?.accepted_receipt_sha256) {
                metadata.completion.accepted_receipt_sha256 = result.accepted_receipt_sha256;
            }
        }
    }
    void saveChatConditional();
    panelFor(messageId)?.classList.add('cera-review-finished');
    renderStoredCompletionMetadata(messageId);
    activateSendButtons();
}

function storeReprojectionRequired(messageId, outcome) {
    const metadata = chat[messageId]?.extra?.[META_KEY];
    if (!metadata) return;
    metadata.provisional = true;
    metadata.state = outcome.disposition;
    metadata.action_outcome = structuredClone(outcome);
    if (metadata.completion) {
        metadata.completion.provisional = true;
        metadata.completion.status = outcome.disposition;
        metadata.completion.story_state_committed = false;
    }
}

function renderReprojectionRequired(messageId, outcome) {
    const panel = panelFor(messageId);
    if (!panel) return;
    panel.innerHTML = '';

    const badge = document.createElement('div');
    badge.className = 'cera-review-badge';
    badge.textContent = 'CERA - PROVISIONAL';
    panel.appendChild(badge);

    const heading = document.createElement('div');
    heading.className = 'cera-review-heading';
    heading.textContent = 'PROVISIONAL ACCEPTANCE REQUIRES REPROJECTION';
    panel.appendChild(heading);

    const result = document.createElement('div');
    result.className = 'cera-review-result cera-review-severity-concern';
    result.textContent = 'No story state was accepted or committed.';
    panel.appendChild(result);

    const reason = document.createElement('div');
    reason.className = 'cera-review-reason';
    reason.textContent = 'CERA must create the protected record, non-explicit projection, and route transition before this candidate can become provisional canon.';
    panel.appendChild(reason);

    appendCreatorTrace(panel, chat[messageId]?.extra?.[META_KEY]?.completion);

    if (validReviewId(outcome.review_id)) {
        const actions = document.createElement('div');
        actions.className = 'cera-review-actions';
        actions.append(actionButton(
            'Check CERA status',
            false,
            () => refreshReviewStatus(messageId, outcome.review_id),
        ));
        panel.appendChild(actions);
    }
}

function removeProvisionalMessage(messageId) {
    if (!chat[messageId]?.extra?.[META_KEY]?.provisional) return;
    chat.splice(messageId, 1);
    document.querySelector(`#chat .mes[mesid="${messageId}"]`)?.remove();
    void saveChatConditional();
    if (!hasPendingReview()) activateSendButtons();
}

function updateStoredState(messageId, review) {
    const metadata = chat[messageId]?.extra?.[META_KEY];
    if (!metadata) return;
    metadata.state = review.state;
    metadata.prepared_package_id = review.prepared_package_id;
    if (metadata.completion) {
        metadata.completion.status = review.state;
        metadata.completion.recording_status = review.recording_status
            ?? metadata.completion.recording_status;
        if (review.semantic_validation) {
            metadata.completion.creator_trace = normalizeCreatorTrace(
                {
                    ...(metadata.completion.creator_trace ?? {}),
                    validation: review.semantic_validation,
                    provider_operations: review.provider_operations,
                },
                metadata.completion,
            );
        }
    }
    if (
        Array.isArray(review.speaker_marks)
        && (review.speaker_marks.length || !Array.isArray(metadata.speaker_marks))
    ) {
        metadata.speaker_marks = structuredClone(review.speaker_marks);
        renderStoredSpeakerMarks(messageId);
    }
}

function currentSpeakerPalette() {
    const configured = characters[this_chid]?.data?.extensions?.[READABILITY_KEY]?.speakers;
    const palette = new Map(Object.entries(DEFAULT_SPEAKER_COLORS));
    if (!Array.isArray(configured)) return palette;
    for (const entry of configured) {
        const key = String(entry?.key ?? '').trim().toLowerCase();
        const color = String(entry?.color ?? '').trim();
        if (key in DEFAULT_SPEAKER_COLORS && /^#[0-9a-f]{6}$/i.test(color)) {
            palette.set(key, color);
        }
    }
    return palette;
}

function renderStoredSpeakerMarks(messageId) {
    const marks = chat[messageId]?.extra?.[META_KEY]?.speaker_marks;
    const container = document.querySelector(`#chat .mes[mesid="${messageId}"] .mes_text`);
    if (!container || !Array.isArray(marks) || !marks.length) return;
    const palette = currentSpeakerPalette();
    for (const mark of marks) {
        const speaker = String(mark?.speaker ?? '').trim().toLowerCase();
        const quote = String(mark?.quote ?? '');
        if (mark?.kind !== 'dialogue' || !palette.has(speaker) || !quote) continue;
        wrapFirstUnmarkedExactText(container, quote, speaker, palette.get(speaker));
    }
}

function wrapFirstUnmarkedExactText(container, target, speaker, color) {
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, {
        acceptNode(node) {
            const parent = node.parentElement;
            if (!parent || !node.nodeValue) return NodeFilter.FILTER_REJECT;
            if (parent.closest('.cera-speaker-dialogue, code, pre, textarea, script, style')) {
                return NodeFilter.FILTER_REJECT;
            }
            return NodeFilter.FILTER_ACCEPT;
        },
    });
    const entries = [];
    let text = '';
    while (walker.nextNode()) {
        const node = walker.currentNode;
        const start = text.length;
        text += node.nodeValue;
        entries.push({ node, start, end: text.length });
    }
    const index = text.indexOf(target);
    if (index < 0) return false;
    const start = rangePoint(entries, index);
    const end = rangePoint(entries, index + target.length);
    if (!start || !end) return false;
    const range = document.createRange();
    range.setStart(start.node, start.offset);
    range.setEnd(end.node, end.offset);
    const span = document.createElement('span');
    span.className = `cera-speaker-dialogue cera-speaker-${speaker}`;
    span.dataset.ceraSpeaker = speaker;
    span.style.setProperty('--cera-speaker-color', color);
    span.appendChild(range.extractContents());
    range.insertNode(span);
    return true;
}

function rangePoint(entries, index) {
    for (const entry of entries) {
        if (index >= entry.start && index <= entry.end) {
            return { node: entry.node, offset: index - entry.start };
        }
    }
    return null;
}

function panelFor(messageId) {
    const message = document.querySelector(`#chat .mes[mesid="${messageId}"]`);
    if (!message) return null;
    let panel = message.querySelector('.cera-creator-review');
    if (!panel) {
        panel = document.createElement('section');
        panel.className = 'cera-creator-review';
        (message.querySelector('.mes_block') ?? message).appendChild(panel);
    }
    return panel;
}

function actionButton(label, disabled, handler) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'menu_button cera-review-button';
    button.textContent = label;
    button.disabled = disabled;
    button.addEventListener('click', handler);
    return button;
}

function disablePanel(messageId, disabled) {
    panelFor(messageId)?.querySelectorAll('button').forEach(button => {
        button.disabled = disabled;
    });
}

function statusText(messageId, text) {
    const panel = panelFor(messageId);
    if (!panel) return;
    let status = panel.querySelector('.cera-review-status');
    if (!status) {
        status = document.createElement('div');
        status.className = 'cera-review-status';
        panel.appendChild(status);
    }
    status.textContent = text;
}

function hasPendingReview() {
    return chat.some(message => message?.extra?.[META_KEY]?.provisional);
}

async function requestJson(path, options = {}) {
    const headers = getRequestHeaders();
    headers['X-Cera-Authorization'] = ceraAuthorizationHeader();
    const request = {
        method: options.method ?? 'GET',
        headers,
    };
    if (options.body) request.body = JSON.stringify(options.body);
    let response;
    try {
        response = await fetch(`${API_ROOT}${path}`, request);
    } catch (error) {
        throw new CeraReviewRequestError(
            'transport',
            `The browser could not reach ${API_ROOT}: ${String(error)}`,
        );
    }
    let payload;
    try {
        payload = await response.json();
    } catch (error) {
        throw new CeraReviewRequestError(
            'invalid_response',
            `CERA returned an unreadable HTTP ${response.status} response: ${String(error)}`,
        );
    }
    if (!response.ok) {
        const failure = payload?.error ?? {};
        const labels = [failure.stage, failure.code].filter(Boolean).join(' / ');
        const prefix = labels ? `${labels}: ` : '';
        throw new CeraReviewRequestError(
            'server',
            `${prefix}${failure.message ?? `HTTP ${response.status}`}`,
        );
    }
    return payload;
}

function ceraAuthorizationHeader() {
    const lines = String(oai_settings.custom_include_headers ?? '').split(/\r?\n/);
    const entry = lines.find(line => /^\s*Authorization\s*:/i.test(line));
    const value = entry?.slice(entry.indexOf(':') + 1).trim() ?? '';
    if (!/^Bearer [A-Za-z0-9._~-]{24,512}$/.test(value)) {
        throw new CeraReviewRequestError(
            'configuration',
            'The active CERA connection is missing its local review credential.',
        );
    }
    return value;
}

function delay(milliseconds) {
    return new Promise(resolve => setTimeout(resolve, milliseconds));
}

function readControls() {
    try {
        const value = JSON.parse(localStorage.getItem(CONTROL_STORAGE_KEY) ?? '{}');
        return {
            scene_depth: ['short', 'auto', 'medium', 'long', 'epic'].includes(value.scene_depth)
                ? value.scene_depth
                : DEFAULT_CONTROLS.scene_depth,
            character_autonomy: ['off', 'mind', 'body', 'both'].includes(value.character_autonomy)
                ? value.character_autonomy
                : DEFAULT_CONTROLS.character_autonomy,
            adult_craft_mode: ['off', 'on', 'ex'].includes(value.adult_craft_mode)
                ? value.adult_craft_mode
                : DEFAULT_CONTROLS.adult_craft_mode,
            prompt_handling: ['adjustment', 'modification'].includes(value.prompt_handling)
                ? value.prompt_handling
                : DEFAULT_CONTROLS.prompt_handling,
            reasoning_effort: ['medium', 'high', 'xhigh'].includes(value.reasoning_effort)
                ? value.reasoning_effort
                : DEFAULT_CONTROLS.reasoning_effort,
        };
    } catch {
        return { ...DEFAULT_CONTROLS };
    }
}

function installControlBar() {
    if (document.querySelector('#cera_creator_controls')) return;
    const sendForm = document.querySelector('#send_form');
    if (!sendForm) return;
    const controls = readControls();
    const bar = document.createElement('div');
    bar.id = 'cera_creator_controls';
    bar.className = 'cera-control-bar';
    bar.append(
        controlSelect('Depth', 'cera_depth_control', [
            ['short', 'Short'], ['auto', 'Auto'], ['medium', 'Medium'],
            ['long', 'Long'], ['epic', 'Epic'],
        ], controls.scene_depth),
        controlSelect('Autonomy', 'cera_autonomy_control', [
            ['off', 'Off'], ['mind', 'Mind'], ['body', 'Body'], ['both', 'Both'],
        ], controls.character_autonomy),
        controlSelect('Adult', 'cera_adult_craft_control', [
            ['off', 'Off'], ['on', 'On'], ['ex', 'Ex'],
        ], controls.adult_craft_mode),
        controlSelect('Prompt', 'cera_prompt_control', [
            ['adjustment', 'Adjustment'], ['modification', 'Modification'],
        ], controls.prompt_handling),
        controlSelect('Sol', 'cera_reasoning_effort_control', [
            ['medium', 'M'], ['high', 'H'], ['xhigh', 'Ex'],
        ], controls.reasoning_effort),
    );
    sendForm.parentElement?.insertBefore(bar, sendForm);
    bar.addEventListener('change', () => {
        localStorage.setItem(CONTROL_STORAGE_KEY, JSON.stringify({
            scene_depth: bar.querySelector('#cera_depth_control')?.value,
            character_autonomy: bar.querySelector('#cera_autonomy_control')?.value,
            adult_craft_mode: bar.querySelector('#cera_adult_craft_control')?.value,
            prompt_handling: bar.querySelector('#cera_prompt_control')?.value,
            reasoning_effort: bar.querySelector('#cera_reasoning_effort_control')?.value,
        }));
    });
}

function controlSelect(label, id, options, selected) {
    const wrapper = document.createElement('label');
    wrapper.className = 'cera-control';
    const text = document.createElement('span');
    text.textContent = label;
    const select = document.createElement('select');
    select.id = id;
    select.className = 'text_pole cera-control-select';
    for (const [value, display] of options) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = display;
        option.selected = value === selected;
        select.appendChild(option);
    }
    wrapper.append(text, select);
    return wrapper;
}
