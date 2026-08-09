import { validReviewId } from './completion-metadata.js';

const REPROJECTION_SCHEMA = 'cera.pi_scene.adult_provisional_acceptance_blocked.v1';
const REPROJECTION_DISPOSITION = 'reprojection_required';
const REPROJECTION_REASON = 'filter_rejection_has_no_promotable_projection';
const REPROJECTION_NEXT_ACTION = 'protected_reprojection_provider_operation_required';
const REQUIRED_ARTIFACTS = Object.freeze([
    'protected_full_record',
    'non_explicit_codex_projection',
    'route_transition',
]);

/** The backend, rather than the UI, owns provisional-accept availability. */
export function provisionalAcceptEnabled(review) {
    return review?.provisional_accept_enabled === true;
}

/**
 * Project only the exact fail-closed adult provisional-accept result.
 * Anything looser remains an ordinary unresolved review response.
 */
export function normalizeReprojectionRequired(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    if (
        value.schema_version !== REPROJECTION_SCHEMA
        || value.disposition !== REPROJECTION_DISPOSITION
        || value.reason_code !== REPROJECTION_REASON
        || value.next_action !== REPROJECTION_NEXT_ACTION
        || value.story_state_committed !== false
        || value.accepted_effect_created !== false
        || !validReviewId(value.review_id)
        || !Array.isArray(value.required_artifacts)
        || value.required_artifacts.length !== REQUIRED_ARTIFACTS.length
        || REQUIRED_ARTIFACTS.some((item, index) => value.required_artifacts[index] !== item)
    ) return null;
    return {
        schema_version: REPROJECTION_SCHEMA,
        review_id: value.review_id,
        disposition: REPROJECTION_DISPOSITION,
        reason_code: REPROJECTION_REASON,
        required_artifacts: [...REQUIRED_ARTIFACTS],
        story_state_committed: false,
        accepted_effect_created: false,
        next_action: REPROJECTION_NEXT_ACTION,
    };
}

/** Ordinary provisional acceptance is complete only after backend commitment. */
export function provisionalAcceptanceCommitted(value) {
    if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
    const committed = value.story_state_committed === true
        || value.review?.story_state_committed === true;
    const canonStatus = value.canon_status ?? value.review?.canon_status;
    return committed && canonStatus === 'provisional';
}

/** Validate one automatically accepted Regenerate successor before UI replacement. */
export function acceptedRegenerateSuccessor(value) {
    if (
        !value
        || value.schema_version !== 'cera.pi_scene.review_decision.v1'
        || value.creator_action !== 'regenerate'
        || value.story_state_committed !== true
        || value.successor?.cera?.status !== 'accepted'
        || value.successor?.cera?.story_state_committed !== true
    ) return null;
    const storyText = value.successor?.choices?.[0]?.message?.content;
    if (typeof storyText !== 'string' || !storyText.trim()) return null;
    return {
        story_text: storyText,
        completion: structuredClone(value.successor.cera),
        accepted_turn_id: value.accepted_turn_id ?? null,
        accepted_receipt_sha256: value.accepted_receipt_sha256 ?? null,
    };
}
