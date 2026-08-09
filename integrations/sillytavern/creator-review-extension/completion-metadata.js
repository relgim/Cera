/** Closed, bounded projection of backend completion metadata for SillyTavern. */

export function validReviewId(value) {
    return typeof value === 'string' && /^review-[a-f0-9]{28}$/.test(value);
}

export function completionIdentity(value) {
    return [
        value?.request_id,
        value?.candidate_id,
        value?.accepted_turn_id,
        value?.provisional_review_id,
    ].find(item => typeof item === 'string' && item.length > 0) ?? null;
}

export function normalizeCompletionMetadata(value) {
    if (!plainObject(value)) return null;
    const profileId = boundedText(value.profile_id, 160);
    if (!profileId?.startsWith('cera.pi_scene.')) return null;
    const normalized = {
        schema_version: 'cera.sillytavern.completion_metadata.v1',
        profile_id: profileId,
        request_id: boundedText(value.request_id, 240),
        candidate_id: boundedText(value.candidate_id, 240),
        accepted_turn_id: boundedText(value.accepted_turn_id, 240),
        accepted_receipt_sha256: sha256Text(value.accepted_receipt_sha256),
        route_mode: enumText(value.route_mode ?? value.route, ['ordinary', 'adult']),
        logic_owner: boundedText(value.logic_owner, 160),
        provisional: value.provisional === true,
        provisional_review_id: validReviewId(value.provisional_review_id)
            ? value.provisional_review_id
            : null,
        status: boundedText(value.status ?? value.review_status, 160),
        story_state_committed: optionalBoolean(value.story_state_committed),
        canon_status: boundedText(value.canon_status, 120),
        generation: nonNegativeInteger(value.generation),
        recording_status: boundedText(value.recording_status, 160),
        recorder_required: optionalBoolean(value.recorder_required),
        current_logic_route: enumText(value.current_logic_route, ['ordinary', 'adult']),
        return_to_codex: optionalBoolean(value.return_to_codex),
        debug_log_path: boundedText(value.debug_log_path, 2_000),
        provider_operations: normalizeProviderOperations(value.provider_operations),
        creator_trace: normalizeCreatorTrace(creatorTraceInput(value), value),
    };
    return completionIdentity(normalized) ? normalized : null;
}

function creatorTraceInput(value) {
    if (plainObject(value.creator_trace)) return value.creator_trace;
    return {
        logic_owner: value.logic_owner,
        decision_records: value.decision_records,
        autonomy: value.autonomy ?? value.autonomy_application,
        route_transition: value.route_transition,
        validation: value.validation,
        recording: value.recording,
        provisional_dependencies: value.provisional_dependencies,
        provider_operations: value.provider_operations,
        debug_log_path: value.debug_log_path,
    };
}

export function normalizeCreatorTrace(value, completion) {
    const trace = plainObject(value) ? value : {};
    const decisionRecords = Array.isArray(trace.decision_records)
        ? trace.decision_records.slice(0, 24).map(normalizeDecisionRecord).filter(Boolean)
        : [];
    const autonomy = normalizeAutonomy(
        trace.autonomy,
        completion?.request_controls?.character_autonomy,
    );
    const routeTransition = normalizeRouteTransition(trace.route_transition, completion);
    const validation = normalizeValidation(trace.validation, completion);
    const recording = normalizeRecording(trace.recording, completion);
    const provisionalDependencies = Array.isArray(trace.provisional_dependencies)
        ? trace.provisional_dependencies
            .slice(0, 24)
            .map(normalizeProvisionalDependency)
            .filter(Boolean)
        : [];
    const providerOperations = normalizeProviderOperations(
        trace.provider_operations ?? completion?.provider_operations,
    );
    const debugLogPath = boundedText(
        trace.debug_log_path ?? completion?.debug_log_path,
        2_000,
    );
    const logicOwner = boundedText(trace.logic_owner ?? completion?.logic_owner, 160);
    if (
        !logicOwner
        && !decisionRecords.length
        && !autonomy
        && !routeTransition
        && !validation
        && !recording
        && !provisionalDependencies.length
        && !providerOperations
        && !debugLogPath
    ) return null;
    return {
        schema_version: 'cera.pi_scene.creator_trace.ui_projection.v1',
        logic_owner: logicOwner,
        decision_records: decisionRecords,
        autonomy,
        route_transition: routeTransition,
        validation,
        recording,
        provisional_dependencies: provisionalDependencies,
        provider_operations: providerOperations,
        debug_log_path: debugLogPath,
    };
}

function normalizeDecisionRecord(value) {
    if (!plainObject(value)) return null;
    const decisionKey = boundedText(value.decision_key, 240);
    const ownerId = boundedText(value.owner_id ?? value.character_id, 240);
    const conciseDecision = boundedText(
        value.concise_decision ?? value.selected_intent ?? value.decision,
        2_000,
    );
    if (!decisionKey && !ownerId && !conciseDecision) return null;
    const causalRefs = Array.isArray(value.causal_trigger_refs) ? value.causal_trigger_refs : [];
    const decisiveRefs = Array.isArray(value.decisive_factor_refs)
        ? value.decisive_factor_refs
        : [];
    return {
        decision_key: decisionKey,
        owner_id: ownerId,
        concise_decision: conciseDecision,
        concise_decision_basis: boundedText(
            value.concise_decision_basis ?? value.rationale,
            2_000,
        ),
        perceived_event_meaning: boundedText(value.perceived_event_meaning, 2_000),
        knowledge_certainty: boundedText(value.knowledge_certainty, 120),
        personal_and_social_meaning: boundedText(value.personal_and_social_meaning, 2_000),
        response_layers: normalizeResponseLayers(value.response_layers),
        material_pressures: Array.isArray(value.material_pressures)
            ? value.material_pressures.slice(0, 16).map(normalizePressure).filter(Boolean)
            : [],
        autonomy_application: normalizeAutonomyApplication(value.autonomy_application),
        anticipated_immediate_effect: boundedText(value.anticipated_immediate_effect, 2_000),
        close_alternative: normalizeCloseAlternative(value.close_alternative),
        uncertainty: boundedText(value.uncertainty, 120),
        evidence_refs: boundedTextArray(
            value.evidence_refs ?? [...causalRefs, ...decisiveRefs],
            32,
            240,
        ),
    };
}

function normalizeResponseLayers(value) {
    if (!plainObject(value)) return null;
    const normalized = {
        immediate_involuntary_reaction: boundedText(value.immediate_involuntary_reaction, 1_500),
        conscious_interpretation: boundedText(value.conscious_interpretation, 1_500),
        subconscious_pressure: boundedText(value.subconscious_pressure, 1_500),
        considered_judgment: boundedText(value.considered_judgment, 1_500),
    };
    return Object.values(normalized).some(Boolean) ? normalized : null;
}

function normalizePressure(value) {
    if (!plainObject(value)) return null;
    const kind = boundedText(value.kind, 160);
    const level = boundedText(value.level, 120);
    const direction = boundedText(value.direction, 500);
    if (!kind && !level && !direction) return null;
    return {
        kind,
        level,
        direction,
        evidence_refs: boundedTextArray(value.evidence_refs, 16, 240),
    };
}

function normalizeAutonomyApplication(value) {
    if (!plainObject(value)) return null;
    const normalized = {
        mind_precedence_applied: optionalBoolean(value.mind_precedence_applied),
        body_precedence_applied: optionalBoolean(value.body_precedence_applied),
        user_direction_disposition: boundedText(value.user_direction_disposition, 160),
        overwhelming_pressure_kind: boundedText(value.overwhelming_pressure_kind, 160),
        concise_effect: boundedText(value.concise_effect, 1_500),
    };
    return Object.values(normalized).some(item => item !== null) ? normalized : null;
}

function normalizeCloseAlternative(value) {
    if (!plainObject(value)) return null;
    const normalized = {
        intent: boundedText(value.intent, 1_500),
        why_not_selected: boundedText(value.why_not_selected, 1_500),
        remains_realistically_available: optionalBoolean(value.remains_realistically_available),
    };
    return Object.values(normalized).some(item => item !== null) ? normalized : null;
}

function normalizeAutonomy(value, fallbackMode) {
    const source = plainObject(value) ? value : {};
    const mode = enumText(source.mode ?? fallbackMode, ['off', 'mind', 'body', 'both']);
    const rawApplications = Array.isArray(value) ? value : source.applications;
    const applications = Array.isArray(rawApplications)
        ? rawApplications.slice(0, 24).map(item => {
            if (!plainObject(item)) return null;
            const normalized = {
                character_id: boundedText(item.character_id ?? item.owner_id, 240),
                mind_precedence_applied: optionalBoolean(item.mind_precedence_applied),
                body_precedence_applied: optionalBoolean(item.body_precedence_applied),
                user_direction_disposition: boundedText(item.user_direction_disposition, 160),
                concise_effect: boundedText(item.concise_effect, 1_500),
            };
            return Object.values(normalized).some(entry => entry !== null) ? normalized : null;
        }).filter(Boolean)
        : [];
    return mode || applications.length ? { mode, applications } : null;
}

function normalizeRouteTransition(value, completion) {
    const explicit = plainObject(value);
    const source = explicit ? value : {};
    const nextRoute = enumText(source.to_route ?? completion?.current_logic_route, ['ordinary', 'adult']);
    const returnToCodex = optionalBoolean(source.return_to_codex ?? completion?.return_to_codex);
    if (!explicit && !nextRoute && returnToCodex === null) return null;
    const normalized = {
        from_route: enumText(source.from_route ?? completion?.route_mode, ['ordinary', 'adult']),
        to_route: nextRoute,
        boundary_item_key: boundedText(source.boundary_item_key, 240),
        non_graphic_handoff_summary: boundedText(
            source.non_graphic_handoff_summary ?? source.reason,
            2_000,
        ),
        character_effect_refs: boundedTextArray(source.character_effect_refs, 24, 240),
        return_condition: boundedText(source.return_condition, 1_500),
        return_to_codex: returnToCodex,
    };
    return Object.values(normalized).some(item => (
        Array.isArray(item) ? item.length > 0 : item !== null
    )) ? normalized : null;
}

function normalizeValidation(value, completion) {
    const source = plainObject(value) ? value : {};
    const semantic = plainObject(completion?.semantic_validation)
        ? completion.semantic_validation
        : {};
    const adultFilter = plainObject(completion?.adult_filter) ? completion.adult_filter : {};
    const rawConflict = plainObject(source.conflict)
        ? source.conflict
        : plainObject(semantic.conflict)
            ? semantic.conflict
            : plainObject(adultFilter.conflict)
                ? adultFilter.conflict
                : {};
    const conflict = {
        conflict_class: boundedText(
            rawConflict.conflict_class ?? rawConflict.classification ?? rawConflict.kind,
            160,
        ),
        decision_key: boundedText(rawConflict.decision_key, 240),
        concise_explanation: boundedText(
            rawConflict.concise_explanation ?? rawConflict.explanation ?? rawConflict.summary,
            2_000,
        ),
    };
    const rawFlags = Array.isArray(source.review_flags)
        ? source.review_flags
        : Array.isArray(semantic.review_flags)
            ? semantic.review_flags
            : [];
    const flags = rawFlags.map(item => (
            plainObject(item)
                ? [item.flag_code ?? item.code, item.concise_explanation ?? item.summary]
                    .filter(Boolean)
                    .join(': ')
                : item
        ));
    const normalized = {
        owner: boundedText(source.owner, 160),
        status: boundedText(
            source.status ?? source.verdict ?? semantic.verdict ?? adultFilter.verdict,
            160,
        ),
        automatic_repair_count: nonNegativeInteger(source.automatic_repair_count),
        review_flags: boundedTextArray(flags, 24, 1_000),
        conflict: Object.values(conflict).some(Boolean) ? conflict : null,
    };
    if (!normalized.owner) {
        normalized.owner = completion?.route_mode === 'adult'
            ? 'deepseek_adult_filter'
            : semantic.verdict
                ? 'codex_luna_semantic_validator'
                : null;
    }
    return Object.values(normalized).some(item => (
        Array.isArray(item) ? item.length > 0 : item !== null
    )) ? normalized : null;
}

function normalizeRecording(value, completion) {
    const source = plainObject(value) ? value : {};
    const normalized = {
        status: boundedText(source.status ?? completion?.recording_status, 160),
        recorder_required: optionalBoolean(
            source.recorder_required ?? completion?.recorder_required,
        ),
        projection_status: boundedText(source.projection_status, 160),
        protected_record_status: boundedText(source.protected_record_status, 160),
    };
    return Object.values(normalized).some(item => item !== null) ? normalized : null;
}

function normalizeProvisionalDependency(value) {
    if (typeof value === 'string') {
        const id = boundedText(value, 240);
        return id ? { provisional_record_id: id, assumed_value: null, concise_dependency: null } : null;
    }
    if (!plainObject(value)) return null;
    const normalized = {
        provisional_record_id: boundedText(
            value.provisional_record_id ?? value.record_id ?? value.id,
            240,
        ),
        assumed_value: boundedText(value.assumed_value ?? value.status, 120),
        concise_dependency: boundedText(value.concise_dependency ?? value.summary, 1_500),
    };
    return Object.values(normalized).some(Boolean) ? normalized : null;
}

function normalizeProviderOperations(value) {
    if (!plainObject(value)) return null;
    const normalized = {};
    for (const key of [
        'planner', 'writer', 'luna', 'validator', 'adult_scene', 'adult_filter', 'recorder', 'total',
    ]) {
        const count = nonNegativeInteger(value[key]);
        if (count !== null) normalized[key] = count;
    }
    if (!Object.keys(normalized).length) return null;
    if (normalized.total === undefined) {
        normalized.total = Object.entries(normalized)
            .filter(([key]) => key !== 'total')
            .reduce((sum, [, count]) => sum + count, 0);
    }
    return normalized;
}

function plainObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function boundedText(value, maximum) {
    if (typeof value !== 'string') return null;
    const text = value.trim();
    return text ? text.slice(0, maximum) : null;
}

function boundedTextArray(value, maximumItems, maximumLength) {
    if (!Array.isArray(value)) return [];
    return value
        .slice(0, maximumItems)
        .map(item => boundedText(item, maximumLength))
        .filter(Boolean);
}

function sha256Text(value) {
    return typeof value === 'string' && /^[a-f0-9]{64}$/.test(value) ? value : null;
}

function enumText(value, allowed) {
    return typeof value === 'string' && allowed.includes(value) ? value : null;
}

function optionalBoolean(value) {
    return typeof value === 'boolean' ? value : null;
}

function nonNegativeInteger(value) {
    return Number.isInteger(value) && value >= 0 ? value : null;
}
