/** DOM-only presentation for already-sanitized CERA completion metadata. */

export function renderCompletionPanel(panel, completion, storedState, reviewIdIsValid) {
    panel.innerHTML = '';
    const accepted = completion.story_state_committed === true || storedState === 'accepted';
    const provisionalCanon = accepted && completion.canon_status === 'provisional';
    const badge = document.createElement('div');
    badge.className = `cera-review-badge ${accepted ? 'cera-review-accepted' : 'cera-review-error'}`;
    badge.textContent = provisionalCanon
        ? 'CERA - PROVISIONAL CANON'
        : accepted
            ? 'CERA - ACCEPTED'
            : 'CERA - NOT ACCEPTED';
    panel.appendChild(badge);

    const heading = document.createElement('div');
    heading.className = 'cera-review-heading';
    heading.textContent = provisionalCanon
        ? `${displayRoute(completion.route_mode)} turn accepted as provisional canon`
        : accepted
            ? `${displayRoute(completion.route_mode)} turn completed`
        : `${displayRoute(completion.route_mode)} candidate requires attention`;
    panel.appendChild(heading);

    const status = document.createElement('div');
    status.className = accepted
        ? 'cera-review-result cera-review-severity-good'
        : 'cera-review-result cera-review-severity-concern';
    status.textContent = provisionalCanon
        ? 'The turn is committed as provisional canon, not settled final truth.'
        : accepted
            ? 'Accepted state and processing details are available below.'
        : reviewIdIsValid
            ? 'Use the durable creator-review actions below.'
            : 'The backend did not supply a valid durable review ID, so no action was fabricated.';
    panel.appendChild(status);
    appendCreatorTrace(panel, completion);
}

export function appendCreatorTrace(panel, completion) {
    panel.querySelector('.cera-trace-details')?.remove();
    if (!completion) return;
    const trace = completion.creator_trace;
    const details = document.createElement('details');
    details.className = 'cera-trace-details';
    const summary = document.createElement('summary');
    summary.textContent = 'CERA decision and processing details';
    details.appendChild(summary);

    const facts = document.createElement('dl');
    facts.className = 'cera-trace-facts';
    appendFact(facts, 'Logic owner', trace?.logic_owner ?? completion.logic_owner);
    appendFact(facts, 'Completion status', completion.status);
    appendFact(facts, 'Canon status', completion.canon_status);
    appendFact(facts, 'Route used', completion.route_mode);
    appendFact(facts, 'Current / next route', completion.current_logic_route);
    appendFact(facts, 'Autonomy mode', trace?.autonomy?.mode);
    appendFact(facts, 'Validation owner', trace?.validation?.owner);
    appendFact(facts, 'Validation state', trace?.validation?.status);
    appendFact(facts, 'Recording state', trace?.recording?.status ?? completion.recording_status);
    appendFact(facts, 'Projection state', trace?.recording?.projection_status);
    appendFact(facts, 'Protected record', trace?.recording?.protected_record_status);
    details.appendChild(facts);

    appendDecisionRecords(details, trace?.decision_records);
    appendAutonomy(details, trace?.autonomy);
    appendRouteTransition(details, trace?.route_transition);
    appendValidation(details, trace?.validation);
    appendProvisionalDependencies(details, trace?.provisional_dependencies);
    appendProviderOperations(details, trace?.provider_operations);
    appendDebugPath(details, trace?.debug_log_path ?? completion.debug_log_path);
    panel.appendChild(details);
}

function appendDecisionRecords(parent, records) {
    if (!records?.length) return;
    const section = traceSection('Relevant decisions');
    for (const [index, record] of records.entries()) {
        section.appendChild(decisionDetails(record, index));
    }
    parent.appendChild(section);
}

function appendAutonomy(parent, autonomy) {
    if (!autonomy?.applications?.length) return;
    const section = traceSection('Autonomy application');
    for (const item of autonomy.applications) {
        const row = document.createElement('div');
        row.className = 'cera-trace-note';
        row.textContent = [
            item.character_id,
            autonomyPrecedence(item),
            item.user_direction_disposition,
            item.concise_effect,
        ].filter(Boolean).join(' — ');
        section.appendChild(row);
    }
    parent.appendChild(section);
}

function appendRouteTransition(parent, routeTransition) {
    if (!routeTransition) return;
    const section = traceSection('Route transition');
    const transition = document.createElement('dl');
    transition.className = 'cera-trace-facts';
    appendFact(
        transition,
        'Route',
        [routeTransition.from_route, routeTransition.to_route].filter(Boolean).join(' → '),
    );
    appendFact(transition, 'Boundary', routeTransition.boundary_item_key);
    appendFact(transition, 'Handoff', routeTransition.non_graphic_handoff_summary);
    appendFact(transition, 'Return condition', routeTransition.return_condition);
    appendFact(transition, 'Return to Codex', routeTransition.return_to_codex);
    section.appendChild(transition);
    parent.appendChild(section);
}

function appendValidation(parent, validationValue) {
    if (!validationValue) return;
    const section = traceSection('Validation');
    const validation = document.createElement('dl');
    validation.className = 'cera-trace-facts';
    appendFact(validation, 'Owner', validationValue.owner);
    appendFact(validation, 'Result', validationValue.status);
    appendFact(validation, 'Automatic repairs', validationValue.automatic_repair_count);
    appendFact(validation, 'Review flags', validationValue.review_flags);
    appendFact(validation, 'Conflict class', validationValue.conflict?.conflict_class);
    appendFact(validation, 'Decision', validationValue.conflict?.decision_key);
    appendFact(validation, 'Explanation', validationValue.conflict?.concise_explanation);
    section.appendChild(validation);
    parent.appendChild(section);
}

function appendProvisionalDependencies(parent, dependencies) {
    if (!dependencies?.length) return;
    const section = traceSection('Provisional canon dependencies');
    const list = document.createElement('ul');
    list.className = 'cera-trace-list';
    for (const dependency of dependencies) {
        const item = document.createElement('li');
        item.textContent = [
            dependency.provisional_record_id,
            dependency.assumed_value,
            dependency.concise_dependency,
        ].filter(Boolean).join(' — ');
        list.appendChild(item);
    }
    section.appendChild(list);
    parent.appendChild(section);
}

function appendProviderOperations(parent, operations) {
    if (!operations) return;
    const section = traceSection('Provider operations');
    const counts = document.createElement('dl');
    counts.className = 'cera-trace-facts cera-provider-counts';
    for (const [role, count] of Object.entries(operations)) {
        appendFact(counts, displayLabel(role), count);
    }
    section.appendChild(counts);
    parent.appendChild(section);
}

function appendDebugPath(parent, debugPath) {
    if (!debugPath) return;
    const section = traceSection('Readable debug log');
    const path = document.createElement('code');
    path.className = 'cera-debug-path';
    path.textContent = debugPath;
    section.appendChild(path);
    parent.appendChild(section);
}

function decisionDetails(record, index) {
    const details = document.createElement('details');
    details.className = 'cera-decision-record';
    const summary = document.createElement('summary');
    summary.textContent = [
        `Decision ${index + 1}`,
        record.owner_id,
        record.concise_decision,
    ].filter(Boolean).join(' — ');
    details.appendChild(summary);
    const facts = document.createElement('dl');
    facts.className = 'cera-trace-facts';
    appendFact(facts, 'Decision key', record.decision_key);
    appendFact(facts, 'Selected decision', record.concise_decision);
    appendFact(facts, 'Basis', record.concise_decision_basis);
    appendFact(facts, 'Perceived meaning', record.perceived_event_meaning);
    appendFact(facts, 'Knowledge certainty', record.knowledge_certainty);
    appendFact(facts, 'Personal / social meaning', record.personal_and_social_meaning);
    appendFact(facts, 'Immediate reaction', record.response_layers?.immediate_involuntary_reaction);
    appendFact(facts, 'Conscious interpretation', record.response_layers?.conscious_interpretation);
    appendFact(facts, 'Subconscious pressure', record.response_layers?.subconscious_pressure);
    appendFact(facts, 'Considered judgment', record.response_layers?.considered_judgment);
    appendFact(facts, 'Autonomy', autonomyPrecedence(record.autonomy_application));
    appendFact(facts, 'User direction', record.autonomy_application?.user_direction_disposition);
    appendFact(facts, 'Autonomy effect', record.autonomy_application?.concise_effect);
    appendFact(facts, 'Overwhelming pressure', record.autonomy_application?.overwhelming_pressure_kind);
    appendFact(
        facts,
        'Material pressures',
        record.material_pressures?.map(item => (
            [item.kind, item.level, item.direction].filter(Boolean).join(': ')
        )),
    );
    appendFact(facts, 'Immediate effect', record.anticipated_immediate_effect);
    appendFact(facts, 'Close alternative', record.close_alternative?.intent);
    appendFact(facts, 'Why not selected', record.close_alternative?.why_not_selected);
    appendFact(
        facts,
        'Alternative remains realistic',
        record.close_alternative?.remains_realistically_available,
    );
    appendFact(facts, 'Uncertainty', record.uncertainty);
    appendFact(facts, 'Evidence references', record.evidence_refs);
    details.appendChild(facts);
    return details;
}

function traceSection(title) {
    const section = document.createElement('section');
    section.className = 'cera-trace-section';
    const heading = document.createElement('div');
    heading.className = 'cera-trace-heading';
    heading.textContent = title;
    section.appendChild(heading);
    return section;
}

function appendFact(container, label, value) {
    const display = displayValue(value);
    if (display === null) return;
    const term = document.createElement('dt');
    term.textContent = label;
    const description = document.createElement('dd');
    description.textContent = display;
    container.append(term, description);
}

function displayValue(value) {
    if (typeof value === 'boolean') return value ? 'Yes' : 'No';
    if (typeof value === 'number') return String(value);
    if (typeof value === 'string' && value) return value;
    if (Array.isArray(value) && value.length) return value.join(', ');
    return null;
}

function autonomyPrecedence(value) {
    if (!value) return null;
    const applied = [];
    if (value.mind_precedence_applied) applied.push('mind precedence');
    if (value.body_precedence_applied) applied.push('body precedence');
    return applied.length ? applied.join(' + ') : 'user direction precedence';
}

function displayRoute(value) {
    return value === 'adult' ? 'Adult' : value === 'ordinary' ? 'Ordinary' : 'CERA';
}

function displayLabel(value) {
    return String(value)
        .split('_')
        .map(word => word ? word[0].toUpperCase() + word.slice(1) : '')
        .join(' ');
}
