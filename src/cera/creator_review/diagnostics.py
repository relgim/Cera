"""Human-readable, non-story creator-correction report generation."""

from __future__ import annotations

from pathlib import Path

from .models import CreatorCorrectionDiagnostic


def render_creator_correction_report(
    diagnostics: tuple[CreatorCorrectionDiagnostic, ...],
) -> str:
    lines = [
        "# CERA Creator Correction Needs",
        "",
        "This is a development diagnosis queue, not story canon, character knowledge,",
        "or an automatic instruction to edit Genesis, prompts, or code. Each item must",
        "be reviewed as a behavior class before a shared correction is implemented.",
        "",
    ]
    if not diagnostics:
        lines.extend(("No creator corrections are pending review.", ""))
        return "\n".join(lines)
    for index, item in enumerate(diagnostics, start=1):
        reasons = ", ".join(item.reason_codes) if item.reason_codes else "none"
        lines.extend(
            (
                f"## {index}. {item.diagnostic_kind.value}",
                "",
                f"- Diagnostic ID: `{item.diagnostic_id}`",
                f"- Review ID: `{item.review_id}`",
                f"- Creator action: `{item.action.value}`",
                f"- Likely owner: `{item.likely_owner.value}`",
                f"- Sol reason codes: `{reasons}`",
                f"- Status: `{item.status}`",
                "",
                "Creator adjustment:",
                "",
                f"> {item.creator_feedback.replace(chr(10), chr(10) + '> ')}",
                "",
                "Shared-review questions:",
                "",
                "- Is the likely owner correct, or did an upstream omission cause the symptom?",
                "- What general behavior class does this expose beyond the single scene?",
                "- Does the fix belong in retrieval, Python contracts, Reasoner logic, Composer prompt material, or tests?",
                "- What adversarial regression proves the class without hard-coding this example?",
                "",
            )
        )
    return "\n".join(lines)


def export_creator_correction_report(store, path: str | Path) -> Path:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = render_creator_correction_report(
        store.creator_correction_diagnostics()
    )
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    temporary.replace(target)
    return target
