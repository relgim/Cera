import {
    addOneMessage,
    activateSendButtons,
    chat,
    characters,
    deactivateSendButtons,
    eventSource,
    event_types,
    getCurrentChatId,
    getMessageTimeStamp,
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
    acceptedRegenerateSuccessor,
    normalizePersistedTransportRetryState,
    normalizeProviderStageFailureState,
    normalizeProviderStageRetryExhausted,
    normalizeProviderStageRetryExhaustedError,
    normalizeReprojectionRequired,
    normalizeTransportRetryCompletionMarker,
    normalizeTransportRetryFailure,
    normalizeTransportRetryStatus,
    provisionalAcceptanceCommitted,
    provisionalAcceptEnabled,
    transportRetryReceipt,
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
const TRANSPORT_RETRY_STORAGE_KEY = 'cera_transport_retry_receipts_v1';
const TRANSPORT_RETRY_STORE_SCHEMA_V1 = 'cera.sillytavern.transport_retry_store.v1';
const TRANSPORT_RETRY_STORE_SCHEMA_V2 = 'cera.sillytavern.transport_retry_store.v2';
const TRANSPORT_RETRY_STATE_SCHEMA_V2 = 'cera.sillytavern.transport_retry_state.v2';
const TRANSPORT_RETRY_COMPLETION_SCHEMA = 'cera.sillytavern.transport_retry_completion.v1';
const MAXIMUM_TRANSPORT_RETRY_ACTIONS = 2;
const PROVIDER_STAGE_FAILURE_STORAGE_KEY = 'cera_provider_stage_failures_v1';
const PROVIDER_STAGE_FAILURE_STORE_SCHEMA = 'cera.sillytavern.provider_stage_failure_store.v1';
const PROVIDER_STAGE_FAILURE_STATE_SCHEMA = 'cera.sillytavern.provider_stage_failure_state.v1';
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
let transportRetryState = null;
let transportRetryInFlight = false;
let transportRetryStatusInFlight = false;
let providerStageFailureState = null;

window.ceraCreatorControls = () => ({ ...readControls() });
window.ceraCaptureCompletionMetadata = value => captureCompletionMetadata(value);
window.ceraCaptureTransportFailure = value => captureTransportFailure(value);

eventSource.on(event_types.APP_READY, () => {
    installControlBar();
    restoreProviderStageFailureForCurrentChat();
    restoreTransportRetryForCurrentChat({ reconcile: true });
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
    renderProviderStageFailureAttachment(messageId);
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

function captureTransportFailure(value) {
    const exhausted = normalizeProviderStageRetryExhaustedError(value);
    if (exhausted) return captureProviderStageFailure(exhausted);
    if (providerStageFailureState?.chat_key === currentTransportRetryChatKey()) return false;
    const failure = normalizeTransportRetryFailure(value);
    const receipt = transportRetryReceipt(failure);
    const chatKey = currentTransportRetryChatKey();
    if (!failure || !receipt || !chatKey) return false;
    const existing = readTransportRetryEntries()
        .find(entry => entry.chat_key === chatKey);
    if (existing) {
        transportRetryState = existing;
        deactivateSendButtons();
        if (existing.receipt.retry_id !== receipt.retry_id) {
            renderTransportRetryControl({
                phase: 'unknown',
                detail: 'A prior transport Retry receipt is still unresolved for this chat. Its read-only status must be checked first.',
            });
            return false;
        }
        renderTransportRetryControl();
        if (!transportRetryActionAvailable(existing)) void reconcileTransportRetry();
        return true;
    }
    const nextState = makeTransportRetryState(
        chatKey,
        receipt,
        'eligible',
        null,
        { postDispatched: false, retryActionsDispatched: 0 },
    );
    if (!nextState) return false;
    transportRetryState = nextState;
    transportRetryInFlight = false;
    deactivateSendButtons();
    if (!persistTransportRetryState(nextState)) {
        renderTransportRetryControl({
            phase: 'terminal',
            detail: 'CERA could not store the safe retry receipt for this chat, so the provider Retry was not enabled.',
            allowAction: false,
        });
        return true;
    }
    renderTransportRetryControl();
    return true;
}

function captureProviderStageFailure(critical, chatKey = currentTransportRetryChatKey()) {
    const normalized = normalizeProviderStageRetryExhausted(critical);
    if (!normalized || !chatKey) return false;
    const existing = readProviderStageFailureEntries()
        .find(entry => entry.chat_key === chatKey);
    if (existing) {
        providerStageFailureState = existing;
        deactivateSendButtons();
        renderProviderStageFailureControl();
        if (
            existing.critical_provider_stage_failure.terminal_evidence_sha256
            !== normalized.terminal_evidence_sha256
        ) return false;
        if (normalized.stage === 'recorder') void attachProviderStageFailureToAcceptedAssistant();
        return true;
    }
    const attachedMessageIndex = normalized.stage === 'recorder'
        ? findAcceptedAssistantMessageIndex()
        : null;
    const nextState = normalizeProviderStageFailureState({
        schema_version: PROVIDER_STAGE_FAILURE_STATE_SCHEMA,
        chat_key: chatKey,
        critical_provider_stage_failure: normalized,
        attached_message_index: attachedMessageIndex,
    });
    if (!nextState || !persistProviderStageFailureState(nextState)) return false;
    providerStageFailureState = nextState;
    clearTransportRetryState(null, chatKey);
    deactivateSendButtons();
    renderProviderStageFailureControl();
    if (normalized.stage === 'recorder') void attachProviderStageFailureToAcceptedAssistant();
    return true;
}

function readProviderStageFailureEntries() {
    try {
        const value = JSON.parse(localStorage.getItem(PROVIDER_STAGE_FAILURE_STORAGE_KEY) ?? '{}');
        if (
            !value
            || typeof value !== 'object'
            || Array.isArray(value)
            || Object.keys(value).sort().join(',') !== 'entries,schema_version'
            || value.schema_version !== PROVIDER_STAGE_FAILURE_STORE_SCHEMA
            || !Array.isArray(value.entries)
        ) return [];
        return value.entries
            .map(normalizeProviderStageFailureState)
            .filter(Boolean);
    } catch {
        return [];
    }
}

function writeProviderStageFailureEntries(entries) {
    try {
        const normalized = entries
            .map(normalizeProviderStageFailureState)
            .filter(Boolean);
        localStorage.setItem(PROVIDER_STAGE_FAILURE_STORAGE_KEY, JSON.stringify({
            schema_version: PROVIDER_STAGE_FAILURE_STORE_SCHEMA,
            entries: normalized,
        }));
        return true;
    } catch {
        return false;
    }
}

function persistProviderStageFailureState(state) {
    const normalized = normalizeProviderStageFailureState(state);
    if (!normalized) return false;
    const entries = readProviderStageFailureEntries()
        .filter(entry => entry.chat_key !== normalized.chat_key);
    entries.push(normalized);
    const stored = writeProviderStageFailureEntries(entries);
    if (stored) providerStageFailureState = structuredClone(normalized);
    return stored;
}

function restoreProviderStageFailureForCurrentChat() {
    const chatKey = currentTransportRetryChatKey();
    providerStageFailureState = chatKey
        ? readProviderStageFailureEntries().find(entry => entry.chat_key === chatKey) ?? null
        : null;
    document.querySelector('#cera_provider_stage_failure_panel')?.remove();
    if (!providerStageFailureState) return;
    clearTransportRetryState(null, chatKey);
    deactivateSendButtons();
    renderProviderStageFailureControl();
    if (providerStageFailureState.critical_provider_stage_failure.stage === 'recorder') {
        void attachProviderStageFailureToAcceptedAssistant();
    }
}

function findAcceptedAssistantMessageIndex() {
    for (let index = chat.length - 1; index >= 0; index -= 1) {
        const message = chat[index];
        if (!message || message.is_user) continue;
        const stored = message.extra?.[META_KEY];
        const completion = normalizeCompletionMetadata(stored?.completion);
        if (completion?.story_state_committed === true || stored?.state === 'accepted') {
            return index;
        }
    }
    return null;
}

async function attachProviderStageFailureToAcceptedAssistant() {
    const state = providerStageFailureState;
    const critical = state?.critical_provider_stage_failure;
    if (!state || critical?.stage !== 'recorder') return;
    const messageIndex = state.attached_message_index ?? findAcceptedAssistantMessageIndex();
    const message = messageIndex === null ? null : chat[messageIndex];
    if (!message || message.is_user) return;
    message.extra ??= {};
    message.extra[META_KEY] ??= {};
    const existing = normalizeProviderStageRetryExhausted(
        message.extra[META_KEY].provider_stage_retry_exhausted,
    );
    if (
        existing
        && existing.terminal_evidence_sha256 !== critical.terminal_evidence_sha256
    ) return;
    message.extra[META_KEY].provider_stage_retry_exhausted = structuredClone(critical);
    if (state.attached_message_index !== messageIndex) {
        const updated = normalizeProviderStageFailureState({
            ...state,
            attached_message_index: messageIndex,
        });
        if (updated) persistProviderStageFailureState(updated);
    }
    try {
        await saveChatConditional();
    } catch {
        return;
    }
    renderProviderStageFailureAttachment(messageIndex);
}

function renderProviderStageFailureControl() {
    document.querySelector('#cera_provider_stage_failure_panel')?.remove();
    const critical = providerStageFailureState?.critical_provider_stage_failure;
    if (!critical) return;
    const controls = document.querySelector('#cera_creator_controls');
    const sendForm = document.querySelector('#send_form');
    const anchor = controls ?? sendForm;
    if (!anchor?.parentElement) return;
    const panel = document.createElement('section');
    panel.id = 'cera_provider_stage_failure_panel';
    panel.className = 'cera-provider-stage-critical';
    panel.role = 'alert';
    appendProviderStageFailureContent(panel, critical, { attached: false });
    anchor.parentElement.insertBefore(panel, anchor.nextSibling);
}

function renderProviderStageFailureAttachment(messageId) {
    const raw = chat[messageId]?.extra?.[META_KEY]?.provider_stage_retry_exhausted;
    const critical = normalizeProviderStageRetryExhausted(raw);
    if (!critical || critical.stage !== 'recorder') return;
    const message = document.querySelector(`#chat .mes[mesid="${messageId}"]`);
    if (!message) return;
    message.querySelector('.cera-provider-stage-critical-message')?.remove();
    const panel = document.createElement('section');
    panel.className = 'cera-provider-stage-critical cera-provider-stage-critical-message';
    panel.role = 'alert';
    appendProviderStageFailureContent(panel, critical, { attached: true });
    (message.querySelector('.mes_block') ?? message).appendChild(panel);
}

function appendProviderStageFailureContent(panel, critical, { attached }) {
    const providerLabel = critical.provider === 'codex' ? 'Codex' : 'DeepSeek';
    const stageLabels = {
        planner: 'Planner',
        semantic_validator: 'Semantic Validator',
        writer: 'Writer',
        recorder: 'Recorder',
        adult_scene: 'Adult Scene',
        adult_filter: 'Adult Filter',
    };
    const stageLabel = stageLabels[critical.stage];
    const heading = document.createElement('div');
    heading.className = 'cera-review-heading cera-provider-stage-critical-heading';
    heading.textContent = `CRITICAL CERA FAILURE - ${providerLabel} ${stageLabel}`;
    const status = document.createElement('div');
    status.className = 'cera-review-status';
    status.textContent = critical.stage === 'recorder'
        ? 'DeepSeek Recorder failed after three attempts. The assistant story remains accepted, but recording is incomplete. This branch is stopped pending repair.'
        : `${providerLabel} ${stageLabel} failed after three attempts. CERA stopped this branch; no further Retry is available.`;
    panel.append(heading, status);

    const details = document.createElement('details');
    details.className = 'cera-provider-stage-safe-details';
    const summary = document.createElement('summary');
    summary.textContent = 'Safe failure details';
    const facts = document.createElement('dl');
    facts.className = 'cera-trace-facts';
    const values = [
        ['Provider', critical.provider],
        ['Model family', critical.model_family],
        ['Stage', critical.stage],
        ['Attempts', `${critical.attempts_total} of ${critical.maximum_attempts}`],
        ['Retries consumed', String(critical.retries_consumed)],
        ['Observed operations', String(critical.provider_operations_observed_total)],
        ['Conservative operations', String(critical.provider_operations_conservative_total)],
        ['Failure class', critical.final_failure_class],
        ['Story accepted', critical.story_state_committed ? 'yes' : 'no'],
        ['Failed-stage effect committed', critical.failed_stage_effect_committed ? 'yes' : 'no'],
        ['Request SHA-256', critical.request_sha256],
        ['Stage input SHA-256', critical.stage_input_sha256],
        ['Attempt chain SHA-256', critical.attempt_chain_sha256],
        ['Terminal evidence SHA-256', critical.terminal_evidence_sha256],
    ];
    for (const [label, value] of values) {
        const term = document.createElement('dt');
        term.textContent = label;
        const description = document.createElement('dd');
        description.textContent = value;
        facts.append(term, description);
    }
    details.append(summary, facts);
    panel.appendChild(details);
    if (attached) panel.className += ' cera-provider-stage-attached';
}

function transportRetryContextIsCurrent() {
    return transportRetryOperationIsCurrent(
        transportRetryState?.chat_key,
        transportRetryState?.receipt,
    );
}

function transportRetryOperationIsCurrent(chatKey, receipt = null) {
    return Boolean(
        chatKey
        && transportRetryState?.chat_key === chatKey
        && currentTransportRetryChatKey() === chatKey
        && (
            !receipt
            || (
                transportRetryState.receipt.retry_id === receipt.retry_id
                && transportRetryState.receipt.request_id === receipt.request_id
            )
        ),
    );
}

function currentTransportRetryChatKey() {
    const chatId = String(getCurrentChatId() ?? '');
    const characterId = String(this_chid ?? 'group');
    if (
        !chatId
        || chatId.length > 480
        || characterId.length > 24
        || /[\u0000-\u001f\u007f]/.test(chatId)
        || /[\u0000-\u001f\u007f]/.test(characterId)
    ) return null;
    return JSON.stringify([characterId, chatId]);
}

function makeTransportRetryState(
    chatKey,
    receipt,
    phase,
    completionIdentityValue = null,
    { postDispatched, retryActionsDispatched },
) {
    return normalizePersistedTransportRetryState({
        schema_version: TRANSPORT_RETRY_STATE_SCHEMA_V2,
        chat_key: chatKey,
        phase,
        post_dispatched: postDispatched,
        retry_actions_dispatched: retryActionsDispatched,
        receipt: structuredClone(receipt),
        completion_identity: completionIdentityValue,
    });
}

function transportRetryActionAvailable(state = transportRetryState) {
    return Boolean(
        state
        && state.phase === 'eligible'
        && state.post_dispatched === false
        && Number.isSafeInteger(state.retry_actions_dispatched)
        && state.retry_actions_dispatched < MAXIMUM_TRANSPORT_RETRY_ACTIONS,
    );
}

function observedRetryActionCount(state) {
    if (!Number.isSafeInteger(state?.retry_actions_dispatched)) return null;
    return Math.min(
        MAXIMUM_TRANSPORT_RETRY_ACTIONS,
        state.retry_actions_dispatched + (state.post_dispatched ? 0 : 1),
    );
}

function readTransportRetryEntries() {
    try {
        const value = JSON.parse(localStorage.getItem(TRANSPORT_RETRY_STORAGE_KEY) ?? '{}');
        if (
            !value
            || typeof value !== 'object'
            || Array.isArray(value)
            || Object.keys(value).sort().join(',') !== 'entries,schema_version'
            || ![
                TRANSPORT_RETRY_STORE_SCHEMA_V1,
                TRANSPORT_RETRY_STORE_SCHEMA_V2,
            ].includes(value.schema_version)
            || !Array.isArray(value.entries)
        ) return [];
        const entries = value.entries
            .map(normalizePersistedTransportRetryState)
            .filter(Boolean);
        if (value.schema_version === TRANSPORT_RETRY_STORE_SCHEMA_V1) {
            writeTransportRetryEntries(entries);
        }
        return entries;
    } catch {
        return [];
    }
}

function writeTransportRetryEntries(entries) {
    try {
        const normalized = entries
            .map(normalizePersistedTransportRetryState)
            .filter(Boolean);
        localStorage.setItem(TRANSPORT_RETRY_STORAGE_KEY, JSON.stringify({
            schema_version: TRANSPORT_RETRY_STORE_SCHEMA_V2,
            entries: normalized,
        }));
        return true;
    } catch {
        return false;
    }
}

function persistTransportRetryState(state) {
    const normalized = normalizePersistedTransportRetryState(state);
    if (!normalized) return false;
    const entries = readTransportRetryEntries()
        .filter(entry => entry.chat_key !== normalized.chat_key);
    entries.push(normalized);
    const stored = writeTransportRetryEntries(entries);
    if (stored) transportRetryState = structuredClone(normalized);
    return stored;
}

function clearTransportRetryState(retryId = null, chatKey = transportRetryState?.chat_key) {
    const state = transportRetryState;
    if (!chatKey) return false;
    const entries = readTransportRetryEntries();
    const remaining = entries.filter(entry => !(
        entry.chat_key === chatKey
        && (!retryId || entry.receipt.retry_id === retryId)
    ));
    const removed = remaining.length !== entries.length;
    if (removed) writeTransportRetryEntries(remaining);
    if (
        state?.chat_key === chatKey
        && (!retryId || state.receipt.retry_id === retryId)
    ) {
        transportRetryState = null;
        document.querySelector('#cera_transport_retry_panel')?.remove();
    }
    return removed;
}

function restoreTransportRetryForCurrentChat({ reconcile = false } = {}) {
    const chatKey = currentTransportRetryChatKey();
    if (providerStageFailureState?.chat_key === chatKey) {
        transportRetryState = null;
        document.querySelector('#cera_transport_retry_panel')?.remove();
        deactivateSendButtons();
        return;
    }
    transportRetryState = chatKey
        ? readTransportRetryEntries().find(entry => entry.chat_key === chatKey) ?? null
        : null;
    document.querySelector('#cera_transport_retry_panel')?.remove();
    if (!transportRetryState) {
        syncSendButtons();
        return;
    }
    deactivateSendButtons();
    renderTransportRetryControl();
    if (reconcile && !transportRetryActionAvailable(transportRetryState)) {
        void reconcileTransportRetry();
    }
}

function renderTransportRetryControl({
    phase = transportRetryState?.phase ?? 'eligible',
    detail = null,
    allowAction = true,
} = {}) {
    document.querySelector('#cera_transport_retry_panel')?.remove();
    if (providerStageFailureState?.chat_key === currentTransportRetryChatKey()) return;
    if (!transportRetryState && phase !== 'terminal') return;
    const controls = document.querySelector('#cera_creator_controls');
    const sendForm = document.querySelector('#send_form');
    const anchor = controls ?? sendForm;
    if (!anchor?.parentElement) return;

    const panel = document.createElement('section');
    panel.id = 'cera_transport_retry_panel';
    panel.className = 'cera-transport-retry';

    const heading = document.createElement('div');
    heading.className = 'cera-review-heading';
    heading.textContent = ['in_progress', 'completion_received'].includes(phase)
        ? 'CERA TRANSPORT RETRY IN PROGRESS'
        : phase === 'unknown'
            ? 'CERA TRANSPORT RETRY RESULT UNKNOWN'
        : phase === 'limit_reached_unconfirmed'
            ? 'CERA TRANSPORT RETRY LIMIT REACHED'
        : phase === 'terminal'
            ? 'CERA TRANSPORT RETRY STOPPED'
            : 'CERA TRANSPORT RETRY AVAILABLE';

    const status = document.createElement('div');
    status.className = 'cera-review-status';
    status.textContent = detail ?? (
        phase === 'in_progress'
            ? 'The durable Retry was dispatched once. Status checks are read-only and cannot dispatch it again.'
            : phase === 'completion_received'
                ? 'The terminal completion was identified. CERA is waiting for the chat save to finish before clearing the receipt.'
                : phase === 'unknown'
                    ? 'The browser could not prove the POST result. Only a read-only status check is available; no second dispatch will occur.'
                    : phase === 'limit_reached_unconfirmed'
                        ? 'Two Retry actions were dispatched. No further provider request is allowed; only authoritative read-only status can establish the terminal provider-stage failure.'
                    : !transportRetryActionAvailable(transportRetryState)
                        ? 'This legacy receipt has no trustworthy local Retry count. Only a read-only status check is available.'
                    : 'The original zero-effect transport failure remains recorded. One manual Retry is available.'
    );
    panel.append(heading, status);

    if (
        allowAction
        && transportRetryState
        && transportRetryContextIsCurrent()
        && !transportRetryInFlight
        && !transportRetryStatusInFlight
    ) {
        const actions = document.createElement('div');
        actions.className = 'cera-review-actions';
        if (phase === 'eligible' && transportRetryActionAvailable(transportRetryState)) {
            actions.append(actionButton('Retry transport', false, () => retryTransport()));
        } else if ([
            'eligible',
            'in_progress',
            'unknown',
            'completion_received',
            'limit_reached_unconfirmed',
        ].includes(phase)) {
            actions.append(actionButton(
                'Check retry status',
                false,
                () => reconcileTransportRetry(),
            ));
        }
        panel.appendChild(actions);
    }
    anchor.parentElement.insertBefore(panel, anchor.nextSibling);
}

async function retryTransport() {
    if (
        transportRetryInFlight
        || providerStageFailureState?.chat_key === currentTransportRetryChatKey()
        || !transportRetryState
        || !transportRetryActionAvailable(transportRetryState)
        || !transportRetryContextIsCurrent()
    ) {
        renderTransportRetryControl({
            phase: transportRetryState?.phase ?? 'terminal',
            detail: 'This chat has no proven local Retry budget for another provider action, so nothing was sent.',
            allowAction: false,
        });
        return;
    }
    transportRetryInFlight = true;
    deactivateSendButtons();
    const attempted = structuredClone(transportRetryState.receipt);
    const attemptedChatKey = transportRetryState.chat_key;
    const retryActionsDispatched = transportRetryState.retry_actions_dispatched + 1;
    const dispatchedState = makeTransportRetryState(
        attemptedChatKey,
        attempted,
        'in_progress',
        null,
        { postDispatched: true, retryActionsDispatched },
    );
    if (!dispatchedState || !persistTransportRetryState(dispatchedState)) {
        transportRetryInFlight = false;
        renderTransportRetryControl({
            phase: 'terminal',
            detail: 'CERA could not durably mark this Retry as dispatched, so no provider request was sent.',
            allowAction: false,
        });
        return;
    }
    renderTransportRetryControl({ phase: 'in_progress', allowAction: false });
    try {
        const result = await requestJson(attempted.retry_url, {
            method: 'POST',
            body: {},
        });
        await appendTransportRetryCompletion(
            result,
            attempted,
            attemptedChatKey,
            retryActionsDispatched,
        );
    } catch (error) {
        const exhausted = normalizeProviderStageRetryExhaustedError(error?.payload);
        if (
            exhausted
            && transportRetryOperationIsCurrent(attemptedChatKey, attempted)
            && captureProviderStageFailure(exhausted, attemptedChatKey)
        ) return;
        const nextFailure = normalizeTransportRetryFailure(error?.payload);
        const nextReceipt = transportRetryReceipt(nextFailure);
        const successor = Boolean(
            nextReceipt
            && nextReceipt.request_id === attempted.request_id
            && nextReceipt.retry_id !== attempted.retry_id,
        );
        if (successor && transportRetryOperationIsCurrent(attemptedChatKey, attempted)) {
            const limitReached = retryActionsDispatched >= MAXIMUM_TRANSPORT_RETRY_ACTIONS;
            const nextState = makeTransportRetryState(
                attemptedChatKey,
                limitReached ? attempted : nextReceipt,
                limitReached ? 'limit_reached_unconfirmed' : 'eligible',
                null,
                {
                    postDispatched: limitReached,
                    retryActionsDispatched,
                },
            );
            if (nextState) persistTransportRetryState(nextState);
            renderTransportRetryControl({
                detail: limitReached
                    ? 'The second Retry ended in another zero-effect failure, but CERA did not provide authoritative terminal stage evidence. No further POST is allowed; status checks are read-only.'
                    : 'The retry also ended in a proven zero-effect transport failure. One final manual Retry is available.',
            });
        } else if (transportRetryOperationIsCurrent(attemptedChatKey, attempted)) {
            const current = transportRetryState;
            if (current.phase !== 'completion_received') {
                const limitReached = retryActionsDispatched >= MAXIMUM_TRANSPORT_RETRY_ACTIONS;
                const unknownState = makeTransportRetryState(
                    current.chat_key,
                    attempted,
                    limitReached ? 'limit_reached_unconfirmed' : 'unknown',
                    null,
                    { postDispatched: true, retryActionsDispatched },
                );
                if (unknownState) persistTransportRetryState(unknownState);
            }
            const proxyUnavailable = isProxyLoopbackUnavailable(error);
            renderTransportRetryControl({
                phase: transportRetryState?.phase ?? 'unknown',
                detail: proxyUnavailable
                    ? 'The same-origin relay could not read the POST result. CERA will reconcile this Retry ID with a read-only GET and will not dispatch it again.'
                    : 'The POST did not yield an authoritative terminal result. CERA will reconcile this Retry ID with a read-only GET and will not dispatch it again.',
                allowAction: false,
            });
            await reconcileTransportRetry();
        }
    } finally {
        transportRetryInFlight = false;
        if (transportRetryContextIsCurrent()) renderTransportRetryControl();
    }
}

async function reconcileTransportRetry() {
    if (
        transportRetryStatusInFlight
        || providerStageFailureState?.chat_key === currentTransportRetryChatKey()
        || !transportRetryState
        || !transportRetryContextIsCurrent()
    ) return;
    transportRetryStatusInFlight = true;
    deactivateSendButtons();
    const attemptedState = structuredClone(transportRetryState);
    const attempted = structuredClone(transportRetryState.receipt);
    const attemptedChatKey = transportRetryState.chat_key;
    renderTransportRetryControl({
        phase: transportRetryState.phase,
        detail: 'Checking the durable Retry status. This read-only request cannot contact the provider.',
        allowAction: false,
    });
    try {
        const rawStatus = await requestJson(attempted.retry_url);
        const status = normalizeTransportRetryStatus(rawStatus);
        if (
            !status
            || status.retry_id !== attempted.retry_id
            || status.request_id !== attempted.request_id
            || status.effect_proof_sha256 !== attempted.effect_proof_sha256
        ) {
            throw new CeraReviewRequestError(
                'invalid_response',
                'CERA returned a retry status that did not match the durable receipt.',
            );
        }
        if (!transportRetryOperationIsCurrent(attemptedChatKey, attempted)) return;
        if (status.state === 'attempts_exhausted') {
            if (!captureProviderStageFailure(
                status.critical_provider_stage_failure,
                attemptedChatKey,
            )) {
                throw new CeraReviewRequestError(
                    'storage',
                    'CERA could not persist the terminal provider-stage failure.',
                );
            }
        } else if (status.state === 'eligible') {
            const limitReached = attemptedState.retry_actions_dispatched
                === MAXIMUM_TRANSPORT_RETRY_ACTIONS;
            const phase = limitReached
                ? 'limit_reached_unconfirmed'
                : attemptedState.post_dispatched
                    ? 'unknown'
                    : 'eligible';
            const nextState = makeTransportRetryState(
                attemptedChatKey,
                attempted,
                phase,
                null,
                {
                    postDispatched: attemptedState.post_dispatched,
                    retryActionsDispatched: attemptedState.retry_actions_dispatched,
                },
            );
            if (!nextState || !persistTransportRetryState(nextState)) {
                throw new CeraReviewRequestError(
                    'storage',
                    'CERA could not persist the reconciled Retry receipt.',
                );
            }
        } else if (status.state === 'in_progress') {
            const retryActionsDispatched = observedRetryActionCount(attemptedState);
            const nextState = makeTransportRetryState(
                attemptedChatKey,
                attempted,
                'in_progress',
                null,
                { postDispatched: true, retryActionsDispatched },
            );
            if (nextState) persistTransportRetryState(nextState);
        } else if (status.state === 'succeeded') {
            await appendTransportRetryCompletion(
                status.completion,
                attempted,
                attemptedChatKey,
                observedRetryActionCount(attemptedState),
            );
        } else if (status.state === 'superseded') {
            const receipt = transportRetryReceipt({
                request_id: status.request_id,
                ...status.transport_retry,
            });
            const retryActionsDispatched = observedRetryActionCount(attemptedState);
            const limitReached = retryActionsDispatched === MAXIMUM_TRANSPORT_RETRY_ACTIONS;
            const nextState = makeTransportRetryState(
                attemptedChatKey,
                limitReached ? attempted : receipt,
                limitReached ? 'limit_reached_unconfirmed' : 'eligible',
                null,
                {
                    postDispatched: limitReached,
                    retryActionsDispatched,
                },
            );
            if (!nextState || !persistTransportRetryState(nextState)) {
                throw new CeraReviewRequestError(
                    'storage',
                    'CERA could not persist the successor Retry receipt.',
                );
            }
        } else if (status.state === 'blocked') {
            clearTransportRetryState(attempted.retry_id, attemptedChatKey);
            renderTransportRetryControl({
                phase: 'terminal',
                detail: `CERA blocked this Retry because its durable effect state changed (${status.blocked_reason_code}). No provider request was sent.`,
                allowAction: false,
            });
            syncSendButtons();
        }
    } catch (error) {
        if (isTransportRetryNotFound(error)) {
            clearTransportRetryState(attempted.retry_id, attemptedChatKey);
            if (currentTransportRetryChatKey() === attemptedChatKey) {
                renderTransportRetryControl({
                    phase: 'terminal',
                    detail: 'CERA could not resolve this durable Retry identity. No provider request was sent.',
                    allowAction: false,
                });
                syncSendButtons();
            }
        } else if (transportRetryOperationIsCurrent(attemptedChatKey, attempted)) {
            const current = transportRetryState;
            const phase = current.phase === 'completion_received'
                ? 'completion_received'
                : current.retry_actions_dispatched === MAXIMUM_TRANSPORT_RETRY_ACTIONS
                    ? 'limit_reached_unconfirmed'
                    : current.post_dispatched
                        ? 'unknown'
                        : current.phase;
            const nextState = makeTransportRetryState(
                current.chat_key,
                current.receipt,
                phase,
                phase === 'completion_received' ? current.completion_identity : null,
                {
                    postDispatched: current.post_dispatched,
                    retryActionsDispatched: current.retry_actions_dispatched,
                },
            );
            if (nextState) persistTransportRetryState(nextState);
            renderTransportRetryControl({
                phase,
                detail: 'The Retry status is still unavailable. The receipt remains stored; another read-only status check is available.',
                allowAction: false,
            });
        }
    } finally {
        transportRetryStatusInFlight = false;
        if (transportRetryContextIsCurrent()) renderTransportRetryControl();
    }
}

async function appendTransportRetryCompletion(
    result,
    receipt,
    chatKey,
    retryActionsDispatched,
) {
    const storyText = result?.choices?.[0]?.message?.content;
    const completion = normalizeCompletionMetadata(result?.cera);
    if (
        typeof storyText !== 'string'
        || !storyText.trim()
        || !completion
        || completion.request_id !== receipt.request_id
    ) {
        throw new CeraReviewRequestError(
            'invalid_response',
            'CERA returned a transport retry completion that did not match the durable request.',
        );
    }
    const identity = completionIdentity(completion);
    if (!identity || !transportRetryOperationIsCurrent(chatKey, receipt)) {
        throw new CeraReviewRequestError(
            'context_changed',
            'The active chat changed while CERA was retrying.',
        );
    }
    const completionState = makeTransportRetryState(
        chatKey,
        receipt,
        'completion_received',
        identity,
        { postDispatched: true, retryActionsDispatched },
    );
    if (!completionState || !persistTransportRetryState(completionState)) {
        throw new CeraReviewRequestError(
            'storage',
            'CERA could not retain the Retry receipt before saving the completion.',
        );
    }
    const reviewId = validReviewId(completion.provisional_review_id)
        ? completion.provisional_review_id
        : null;
    const marker = normalizeTransportRetryCompletionMarker({
        schema_version: TRANSPORT_RETRY_COMPLETION_SCHEMA,
        retry_id: receipt.retry_id,
        request_id: receipt.request_id,
        completion_identity: identity,
    });
    if (!marker) {
        throw new CeraReviewRequestError(
            'invalid_response',
            'CERA returned an invalid completion identity for this Retry.',
        );
    }
    let messageId = findTransportRetryCompletion(marker);
    let inserted = false;
    if (messageId === -1) {
        const message = {
            name: characters[this_chid]?.name ?? 'CERA',
            is_user: false,
            is_system: false,
            send_date: getMessageTimeStamp(),
            mes: storyText,
            extra: {
                [META_KEY]: {
                    review_id: reviewId,
                    candidate_id: completion.candidate_id,
                    state: completion.status,
                    provisional: Boolean(completion.provisional && reviewId),
                    completion: structuredClone(completion),
                    transport_retry_completion: marker,
                },
            },
        };
        chat.push(message);
        messageId = chat.length - 1;
        inserted = true;
        addOneMessage(message);
    } else {
        chat[messageId].extra ??= {};
        chat[messageId].extra[META_KEY] ??= {};
        chat[messageId].extra[META_KEY].transport_retry_completion = marker;
    }
    await saveChatConditional();
    clearTransportRetryState(receipt.retry_id, chatKey);
    if (inserted && currentTransportRetryChatKey() === chatKey) {
        await eventSource.emit(event_types.MESSAGE_RECEIVED, messageId, 'cera_transport_retry');
        await eventSource.emit(
            event_types.CHARACTER_MESSAGE_RENDERED,
            messageId,
            'cera_transport_retry',
        );
    }
    if (currentTransportRetryChatKey() !== chatKey) return;
    renderStoredCompletionMetadata(messageId);
    if (chat[messageId]?.extra?.[META_KEY]?.provisional) {
        await resumeReview(messageId);
    } else {
        syncSendButtons();
    }
}

function findTransportRetryCompletion(marker) {
    for (let index = 0; index < chat.length; index += 1) {
        const stored = chat[index]?.extra?.[META_KEY];
        const rawMarker = stored?.transport_retry_completion;
        const normalizedMarker = normalizeTransportRetryCompletionMarker(rawMarker);
        if (rawMarker?.retry_id === marker.retry_id && !normalizedMarker) {
            throw new CeraReviewRequestError(
                'identity_conflict',
                'The chat contains a malformed completion marker for this Retry.',
            );
        }
        if (
            normalizedMarker?.retry_id === marker.retry_id
            || (
                normalizedMarker?.request_id === marker.request_id
                && normalizedMarker?.completion_identity === marker.completion_identity
            )
        ) {
            if (
                normalizedMarker.request_id !== marker.request_id
                || normalizedMarker.completion_identity !== marker.completion_identity
            ) {
                throw new CeraReviewRequestError(
                    'identity_conflict',
                    'The durable Retry identity conflicts with the saved assistant message.',
                );
            }
            return index;
        }
        const normalizedCompletion = normalizeCompletionMetadata(stored?.completion);
        if (completionIdentity(normalizedCompletion) === marker.completion_identity) return index;
    }
    return -1;
}

function isProxyLoopbackUnavailable(error) {
    return Boolean(
        error instanceof CeraReviewRequestError
        && error.kind === 'server'
        && error.status === 502
        && error.payload?.error?.code === 'cera_loopback_unavailable'
        && error.payload?.error?.stage === 'sillytavern_cera_review_proxy'
        && error.payload?.error?.retryable === false
        && error.payload?.error?.fallback_used === false,
    );
}

function isTransportRetryNotFound(error) {
    return Boolean(
        error instanceof CeraReviewRequestError
        && error.status === 404
        && error.payload?.status === 'error'
        && error.payload?.story_state_committed === false
        && error.payload?.error?.schema_version === 'cera.error.v1'
        && error.payload?.error?.error_code === 'CERA_TRANSPORT_RETRY_NOT_FOUND'
        && error.payload?.error?.retry_transport_enabled === false,
    );
}

eventSource.on(event_types.CHAT_CHANGED, () => {
    for (let index = 0; index < chat.length; index += 1) {
        renderStoredCompletionMetadata(index);
        renderStoredSpeakerMarks(index);
        renderProviderStageFailureAttachment(index);
        if (chat[index]?.extra?.[META_KEY]?.provisional) {
            void resumeReview(index);
        }
    }
    restoreProviderStageFailureForCurrentChat();
    restoreTransportRetryForCurrentChat({ reconcile: true });
});

eventSource.on(event_types.GENERATION_ENDED, async () => {
    await attachPendingMetadata(chat.length - 1);
    if (providerStageFailureState?.critical_provider_stage_failure.stage === 'recorder') {
        await attachProviderStageFailureToAcceptedAssistant();
    }
    syncSendButtons();
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
    constructor(kind, message, payload = null, status = null) {
        super(message);
        this.name = 'CeraReviewRequestError';
        this.kind = kind;
        this.payload = payload;
        this.status = status;
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
        const acceptedSuccessor = acceptedRegenerateSuccessor(result);
        if (acceptedSuccessor) {
            chat[messageId].mes = acceptedSuccessor.story_text;
            chat[messageId].extra[META_KEY].candidate_id = successor.cera.candidate_id;
            chat[messageId].extra[META_KEY].completion = acceptedSuccessor.completion;
            updateMessageBlock(messageId, chat[messageId]);
            markCanonical(messageId, result);
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
    syncSendButtons();
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
    syncSendButtons();
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

function syncSendButtons() {
    if (hasPendingReview() || transportRetryState || providerStageFailureState) {
        deactivateSendButtons();
    }
    else activateSendButtons();
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
            payload,
            response.status,
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
