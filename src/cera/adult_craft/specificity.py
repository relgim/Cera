"""Beat- and channel-scoped lexical specificity validation."""

from __future__ import annotations

import re

from cera.composer.models import (
    ComposerCandidate,
    RealizationKind,
    RealizationManifest,
    RealizationSpan,
)
from cera.ids import IdKind, TypedId, deterministic_id

from .models import (
    RealizationChannel,
    SpecificityContract,
    SpecificityFinding,
    SpecificityValidationReceipt,
)


_CHANNEL_KINDS = {
    RealizationChannel.NARRATION: {
        RealizationKind.ACTION,
        RealizationKind.REACTION,
        RealizationKind.EMOTION,
    },
    RealizationChannel.DIALOGUE: {
        RealizationKind.DIALOGUE,
        RealizationKind.VOCALIZATION,
    },
    RealizationChannel.INNER_VOICE: {
        RealizationKind.PRIVATE_STATE,
        RealizationKind.MOTIVE,
        RealizationKind.EMOTION,
    },
    RealizationChannel.SOUND_EFFECT: {RealizationKind.SOUND_EFFECT},
    RealizationChannel.PHYSIOLOGY: {
        RealizationKind.REACTION,
        RealizationKind.ACTION,
    },
}


class SpecificityValidator:
    """Checks required wording only inside the declared beat/channel span."""

    def validate(
        self,
        contract: SpecificityContract,
        candidate: ComposerCandidate,
        manifest: RealizationManifest,
    ) -> SpecificityValidationReceipt:
        findings: list[SpecificityFinding] = []
        for requirement in contract.beat_requirements:
            text = _channel_text(
                candidate.story_text,
                manifest.character_spans,
                manifest.adult_specificity_coverage,
                requirement.obligation_key,
                requirement.beat_id,
                requirement.channel,
                requirement.character_id,
            )
            if not text.strip():
                findings.append(
                    SpecificityFinding(
                        requirement.beat_id,
                        requirement.channel,
                        requirement.character_id,
                        "channel_span",
                        "CERA_ADULT_SPECIFICITY_CHANNEL_MISSING",
                    )
                )
                continue
            for term_requirement in requirement.terms:
                matches = sum(
                    1
                    for term in term_requirement.alternatives
                    if _contains_term(text, term)
                )
                if matches < term_requirement.minimum_matches:
                    findings.append(
                        SpecificityFinding(
                            requirement.beat_id,
                            requirement.channel,
                            requirement.character_id,
                            term_requirement.concept,
                            "CERA_ADULT_SPECIFICITY_TERM_MISSING",
                        )
                    )
        repairable = tuple(dict.fromkeys(value.beat_id for value in findings))
        key = (
            f"{contract.contract_sha256}|{candidate.candidate_sha256}|"
            f"{manifest.manifest_sha256}|"
            + "|".join(
                f"{value.beat_id}:{value.channel.value}:{value.concept}:{value.code}"
                for value in findings
            )
        )
        return SpecificityValidationReceipt(
            schema_version=SpecificityValidationReceipt.SCHEMA_VERSION,
            validation_receipt_id=deterministic_id(
                IdKind.SPECIFICITY_VALIDATION,
                "cera.specificity_validation.v1",
                key,
            ),
            specificity_contract_sha256=contract.contract_sha256,
            candidate_sha256=candidate.candidate_sha256,
            manifest_sha256=manifest.manifest_sha256,
            status="accepted" if not findings else "repair_required",
            findings=tuple(findings),
            repairable_beat_ids=repairable,
            semantic_quality_proven=False,
            story_state_committed=False,
        )


def _channel_text(
    story_text: str,
    spans: tuple[RealizationSpan, ...],
    adult_coverage,
    obligation_key: str,
    beat_id: TypedId,
    channel: RealizationChannel,
    character_id: TypedId | None,
) -> str:
    # Active contracts use Python-bound obligation coverage. The adult craft
    # channel is independent from the general story function (RealizationKind),
    # so an action/reaction span cannot automatically prove physiology or
    # narration. The legacy kind mapping remains only for historical/fake
    # manifests that predate Structural Contract v4.
    if adult_coverage:
        matches = [
            value
            for value in adult_coverage
            if value.obligation_key == obligation_key
            and value.beat_id == beat_id
            and value.channel is channel
            and value.character_id == character_id
        ]
        if len(matches) != 1:
            return ""
        return "\n".join(
            story_text[value.start : value.end]
            for value in matches[0].ranges
        )
    relevant = [
        span
        for span in spans
        if span.beat_id == beat_id
        and span.kind in _CHANNEL_KINDS[channel]
        and (character_id is None or span.owner_id == character_id)
    ]
    relevant.sort(key=lambda value: (value.start, value.end))
    return "\n".join(story_text[value.start : value.end] for value in relevant)


def _contains_term(text: str, term: str) -> bool:
    normalized = term.strip()
    if not normalized:
        return False
    return re.search(
        rf"(?<![\w]){re.escape(normalized)}(?![\w])",
        text,
        flags=re.IGNORECASE,
    ) is not None
