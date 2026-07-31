from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sqlite3

from cera.composer.deepseek import (
    DEEPSEEK_COMPOSER_ADAPTER_VERSION,
    DEEPSEEK_COMPOSER_PACKET_VERSION,
    DEEPSEEK_COMPOSER_PROMPT_VERSION,
    build_deepseek_composer_messages,
)
from cera.contracts import AdultRenderingMode, SceneDepthMode
from cera.ids import TypedId
from cera.prompting import (
    InteractionTopology,
    InteriorityLevel,
    ModularComposerCompileRequest,
    ModularComposerCompiler,
    PromptCompilerState,
    PromptCraftItem,
    PromptMaterialBlock,
    PromptMaterialKind,
    PromptSelectionRequest,
    PromptTone,
    SceneFunction,
    load_prompt_module_registry,
)
from cera.providers import DEFAULT_DEEPSEEK_COMPOSER_MODEL
from cera.serialization import bytes_sha256, canonical_json, text_sha256, to_primitive


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_MANIFEST = ROOT / "tests" / "fixtures" / "modular_composer" / "MANIFEST.json"
HUMAN_TEST_DB = ROOT / "runtime" / "development" / "hanezawa_human_test_v1_2.sqlite3"

SOURCE_PATHS = (
    "src/cera/composer/deepseek.py",
    "src/cera/composer/context.py",
    "src/cera/composer/obligations.py",
    "src/cera/composer/models.py",
    "src/cera/contracts/turn.py",
    "src/cera/adult_craft/models.py",
    "src/cera/adult_craft/selector.py",
    "src/cera/reasoner/codex.py",
    "src/cera/providers/schema_dialects.py",
    "src/cera/providers/routes.py",
    "src/cera/providers/deepseek.py",
    "src/cera/sillytavern/models.py",
    "src/cera/sillytavern/adapter.py",
    "src/cera/prompting/models.py",
    "src/cera/prompting/registry.py",
    "src/cera/prompting/selector.py",
    "src/cera/prompting/compiler.py",
    "src/cera/prompting/adult_bridge.py",
    "config/cera/prompts/character_specific_speech_realization_v1_2.txt",
    "config/cera/prompts/MANIFEST.json",
    "config/cera/prompts/composer/README.md",
    "config/cera/prompts/composer/MANIFEST.schema.json",
    "adult/catalog/adult_craft_v1/manifest.json",
    "tests/fixtures/modular_composer/MANIFEST.json",
    "tests/test_modular_composer_prompting.py",
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8", newline="\n")


def _material(character: TypedId, suffix: str) -> PromptMaterialBlock:
    text = (
        f"Synthetic noncanonical expression packet for {character}; fixture {suffix}. "
        "No Genesis fact or past event is asserted."
    )
    return PromptMaterialBlock(
        material_id=f"expression.{suffix}",
        kind=PromptMaterialKind.CHARACTER_EXPRESSION,
        owner_character_id=character,
        applicable_character_ids=(character,),
        private_to_owner=True,
        text=text,
        text_sha256=text_sha256(text),
        source_provenance="synthetic_authoring_packet_fixture",
        selection_reason="selected active speaker fixture",
    )


def _craft(mode: AdultRenderingMode, index: int, *, example: bool) -> PromptCraftItem:
    text = (
        f"Synthetic noncanonical {mode.value} craft placeholder {index}. "
        "It names a distinct authoring slot but supplies no final writing prose."
    )
    return PromptCraftItem(
        fragment_id=f"craft_reference:authoring-{mode.value}-{index}",
        version="v1",
        fragment_sha256=text_sha256(text),
        allowed_adult_rendering_modes=(mode,),
        deterministic_order=index,
        text=text,
        source_provenance="synthetic_authoring_packet_fixture",
        selection_reason=f"distinct synthetic craft skill {index}",
        noncanonical=True,
        noncopyable=True,
        is_example=example,
    )


def _compile(
    *,
    registry,
    depth: SceneDepthMode,
    adult: AdultRenderingMode,
    topology: InteractionTopology,
    scene: SceneFunction,
    characters: tuple[TypedId, ...],
    tone: PromptTone,
    interiority: InteriorityLevel,
):
    selection = PromptSelectionRequest(
        schema_version=PromptSelectionRequest.SCHEMA_VERSION,
        scene_depth_mode=depth,
        adult_rendering_mode=adult,
        interaction_topology=topology,
        scene_function=scene,
        tone=tone,
        interiority_level=interiority,
        selected_character_ids=characters,
        adult_route_eligible=adult is not AdultRenderingMode.OFF,
        route_blocked=False,
        target_provider_model=DEFAULT_DEEPSEEK_COMPOSER_MODEL,
        sequence_plan_sha256="b" * 64,
    )
    craft = ()
    if adult is AdultRenderingMode.ON:
        craft = (_craft(adult, 0, example=False), _craft(adult, 1, example=True))
    elif adult is AdultRenderingMode.EX:
        craft = tuple(_craft(adult, index, example=index >= 2) for index in range(4))
    request = ModularComposerCompileRequest(
        selection=selection,
        protected_source_json=canonical_json(
            {
                "synthetic": True,
                "source_units": [
                    {
                        "source_unit_id": "source_unit:synthetic-authoring-cue",
                        "classification": "message",
                        "exact_text": "Synthetic authoring-packet cue; not story canon.",
                    }
                ],
            }
        ),
        sequence_plan_json=canonical_json(
            {
                "sequence_plan_sha256": selection.sequence_plan_sha256,
                "current_beats": [
                    {
                        "beat_id": "beat:synthetic-1",
                        "actor_id": str(characters[0]),
                        "neutral_event": "Synthetic current-scene beat for prompt rendering.",
                    }
                ],
                "stop_before": "protected_user_next_open_choice",
            }
        ),
        participant_boundaries_json=canonical_json(
            {
                "protected_user_id": "character:ted",
                "selected_npc_ids": [str(value) for value in characters],
                "inactive_characters_forbidden": True,
            }
        ),
        character_expression_blocks=tuple(
            _material(character, str(index)) for index, character in enumerate(characters)
        ),
        continuity_evidence_blocks=(),
        craft_items=craft,
        output_obligations_json=canonical_json(
            {
                "required_beat_authority_ids": ["beat:synthetic-1"],
                "source_coverage_mode": "must_be_empty",
                "specificity_coverage_mode": (
                    "must_be_empty" if adult is AdultRenderingMode.OFF else "exact_sequence"
                ),
            }
        ),
        output_schema_json=canonical_json(
            {
                "type": "object",
                "required": [
                    "schema_version",
                    "story_segments",
                    "source_coverage",
                    "realization_segments",
                    "specificity_coverage",
                    "terminal_segment_key",
                ],
                "additionalProperties": False,
            }
        ),
        conditional_future_events_present=False,
    )
    return ModularComposerCompiler(state=PromptCompilerState.FIXTURE_ONLY).compile(
        registry,
        request,
    )


def _copy_sources(packet_root: Path) -> list[dict[str, object]]:
    rows = []
    for relative in SOURCE_PATHS:
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(relative)
        target = packet_root / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        raw = target.read_bytes()
        rows.append(
            {
                "repository_path": relative,
                "packet_path": target.relative_to(packet_root).as_posix(),
                "bytes": len(raw),
                "sha256": bytes_sha256(raw),
            }
        )
    fixture_modules = ROOT / "tests" / "fixtures" / "modular_composer" / "modules"
    for source in sorted(fixture_modules.rglob("*.md")):
        relative = source.relative_to(ROOT).as_posix()
        target = packet_root / "sources" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        raw = target.read_bytes()
        rows.append(
            {
                "repository_path": relative,
                "packet_path": target.relative_to(packet_root).as_posix(),
                "bytes": len(raw),
                "sha256": bytes_sha256(raw),
            }
        )
    rows.sort(key=lambda value: value["repository_path"])
    return rows


def _accepted_prose(artifact_id: str) -> str | None:
    if not HUMAN_TEST_DB.is_file():
        return None
    uri = f"file:{HUMAN_TEST_DB.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            "SELECT accepted_prose FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
    return row[0] if row else None


def _markdown_quote(value: str) -> str:
    """Render a block quote without trailing spaces on blank quote lines."""

    return "\n".join(f"> {line}" if line else ">" for line in value.splitlines())


def _current_prompt_anatomy() -> str:
    system = build_deepseek_composer_messages({})[0].content
    return f"""# Current assembled Composer prompt anatomy

This describes the active route at packet freeze time. The modular compiler is
not connected to it.

## Active identity

- Composer adapter: `{DEEPSEEK_COMPOSER_ADAPTER_VERSION}`
- Composer prompt: `{DEEPSEEK_COMPOSER_PROMPT_VERSION}`
- Composer packet: `{DEEPSEEK_COMPOSER_PACKET_VERSION}`
- Implementation model default: `{DEFAULT_DEEPSEEK_COMPOSER_MODEL}`
- Active static system bytes: `{len(system.encode('utf-8'))}`
- Active static system words: `{len(system.split())}`
- Active static system SHA-256: `{text_sha256(system)}`

## Current message assembly

1. `build_deepseek_composer_messages()` emits one large static system message.
2. It emits one user message containing canonical JSON from
   `build_deepseek_composer_packet()`.
3. The JSON packet contains `schema_version`, `prompt_version`,
   `composition_dto`, `authority_policy`, `segment_policy`,
   `output_obligations`, and `output_schema`.
4. `composition_dto` contains identity, exact source, binding decision/current
   beats, scene scope, response profile, Python-owned Depth contract,
   creator-event coverage, hard boundaries, optional Adult specificity, and
   selected realization context.
5. Character voice/expression and Adult craft are already selected before
   DeepSeek. DeepSeek does not retrieve files or choose examples.

## Mixed concerns in the current static system message

The active system text currently combines stable authority, protected-user
rules, biography/history restrictions, Depth realization, character voice,
Adult specificity, future-segment handling, JSON segment semantics, coverage
obligations, and output bookkeeping. The new seam separates these concerns but
does not replace this active text.

## Confirmed manifest correction

`config/cera/prompts/MANIFEST.json` previously bound the character-expression
source to Composer prompt v6 although active prompt v18 embeds the source hash.
The frozen source copy contains the corrected v18 binding. The source artifact
and active prompt text were not modified.
"""


def _compiler_interface() -> str:
    return f"""# Compiler interface proposal for prompt authorship

## Activation boundary

Compiler v1 accepts only `fixture_only` or `shadow_only`. There is no active
state and every receipt sets `provider_dispatch_allowed=false`. The active
Composer imports none of this package.

## Python-owned selector input

```text
scene_depth_mode: off | auto | long | epic
adult_rendering_mode: off | on | ex
interaction_topology: single_npc_floor | multi_npc_shared_floor | npc_to_npc_exchange
scene_function: ordinary_social | emotional_vulnerability | confrontation_boundary | urgent_physical_action | aftermath_recovery
tone: neutral | warm | playful | tense | urgent | somber
interiority_level: none | low | medium | high
selected_character_ids: exact validated cast
adult_route_eligible: Python authority
route_blocked: Python authority
target_provider_model: {DEFAULT_DEEPSEEK_COMPOSER_MODEL}
sequence_plan_sha256: immutable input binding
```

The Reasoner may propose bounded semantic values such as scene function, tone,
multi-character floor pattern, and interiority. It cannot return a module ID,
path, filename, or prompt text. Python maps the validated fields to exactly one
module in each required layer.

## Required module files

```text
core/authority_and_hard_boundaries_v1.md
core/structured_output_v1.md
depth/off_v1.md
depth/auto_v1.md
depth/long_v1.md
depth/epic_v1.md
adult/off_v1.md
adult/on_v1.md
adult/ex_v1.md
topology/single_npc_floor_v1.md
topology/multi_npc_shared_floor_v1.md
topology/npc_to_npc_exchange_v1.md
scene/ordinary_social_v1.md
scene/emotional_vulnerability_v1.md
scene/confrontation_boundary_v1.md
scene/urgent_physical_action_v1.md
scene/aftermath_recovery_v1.md
```

## Deterministic assembly

The system material is ordered as stable authority, structured-output
teaching, selected Depth, selected Adult rendering, selected topology,
selected scene function, followed by the ordered Adult craft items. The user
packet carries exact protected source, the immutable current SequencePlan,
participant boundaries, active-character expression, selected continuity and
evidence, craft provenance, output obligations, and provider schema.

Conditional future events are rejected from creative material. They may later
be represented only by a derived stopping/handoff guard.

## Adult catalog interface

`prompt_craft_items_from_selection()` accepts the existing ordered
`AdultCraftSelectionResult`, verifies receipt IDs/hashes/order, preserves
source provenance and selection reasons, and identifies micro-examples. OFF
rejects all craft. ON/EX require exact mode agreement. There is no fragment or
example count cap in the compiler.

## Prompt-size policy

The compiler records module bytes, diagnostic token estimates, dynamic bytes,
total bytes, fragment count, and example count. It never uses these values to
truncate, summarize, remove, or downgrade material. Registry loader limits are
separate technical protections against malformed megabyte-scale files:
128 modules, 1 MiB per module, and 8 MiB total.

## Semantic and publication ownership

DeepSeek produces candidate prose only. Reasoner/Sol interfaces may propose
event meaning, consequences, continuity corrections, diagnostic
interpretations, and publication-package candidates. Python validates and
binds durable records, and creator acceptance remains required before commit.

Review severity and publication eligibility remain independent:

```text
review_severity: good | concern | critical
publication_eligibility: accept_allowed | accept_blocked
```

Critical quality findings may still be creator-accepted in development.
Structural invalidity remains `accept_blocked` and cannot be converted by the
ordinary Accept action. A future explicit developer-authority override, if
authorized, must be a separate audited workflow.
"""


def _writing_evidence() -> str:
    success_path = (
        ROOT
        / "evaluation/evidence/continuous_ten_turn_qualification_2026-07-29_v20/turns/01_sakura-doorway-shoe-and-entry-boundary.json"
    )
    success_payload = json.loads(success_path.read_text(encoding="utf-8"))
    success = success_payload["publication"]["artifact"]["accepted_prose"]
    short = _accepted_prose("artifact:0a2a0a60-8f9d-5be9-b2fb-65050b2037f2")
    overwritten = _accepted_prose("artifact:9cea1daa-a7f6-5fc9-ad79-2ed4aaf12ed1")
    system = build_deepseek_composer_messages({})[0].content
    schema_lines = [
        line
        for line in system.splitlines()
        if "output_obligations" in line
        or "segment_keys array" in line
        or "Every required current decision beat" in line
    ]
    sera_paths = (
        Path(r"E:\AIChatBot\Sera\config\scene_execution_contract.md"),
        Path(r"E:\AIChatBot\Sera\config\writing_styles.md"),
        Path(r"E:\AIChatBot\Sera\config\dialogue_detailer_profile.md"),
        Path(r"E:\AIChatBot\Sera\docs\PROMPT_AUTHORING_FORMULA.md"),
    )
    sera_rows = []
    for path in sera_paths:
        raw = path.read_bytes()
        sera_rows.append(f"- `{path}` — `{bytes_sha256(raw)}`")
    return f"""# Sanitized current writing evidence

No user transcript, provider prompt, private evidence, secret, or database is
copied here. Two assistant-only artifacts were read from the non-production
human-test database in read-only mode; only their accepted prose is reproduced.

## Successful ordinary Composer result

Source: `{success_path.relative_to(ROOT).as_posix()}`

{_markdown_quote(success)}

## Short/under-developed telephone result

Artifact: `artifact:0a2a0a60-8f9d-5be9-b2fb-65050b2037f2`
Length: `{len(short) if short else 'unavailable'}` characters

{_markdown_quote(short or '[Exact assistant prose unavailable; not reconstructed.]')}

This is evidence of insufficient development for the creator's desired mode,
not proof that every atomic reply must be long.

## Overwritten/over-developed Sakura telephone result

Artifact: `artifact:9cea1daa-a7f6-5fc9-ad79-2ed4aaf12ed1`
Length: `{len(overwritten) if overwritten else 'unavailable'}` characters

{_markdown_quote(overwritten or '[Exact assistant prose unavailable; not reconstructed.]')}

This result is useful because it adds unsupported routine/history language
(`reception line`, repeated administrative calls, habitual phone behavior)
while expanding a simple exchange. Pro should improve development without
turning elaboration into biography.

## Schema-semantic teaching retained in the active prompt

The following current instructions correlate with later structurally accepted
Composer outputs. They must not be removed without a matched test proving that
Flash still satisfies the domain contract:

```text
{chr(10).join(schema_lines)}
```

This is preservation evidence, not a causal claim that each sentence is
individually necessary.

## Sera read-only comparison provenance

{chr(10).join(sera_rows)}

Useful Sera principles to adapt are causal progression, tactical dialogue
shifts, active-viewpoint ownership, state/geography continuity, and natural
handoff. Do not import Sera's monolithic Writer, automatic Detailer, numeric
length targets, runtime memory ownership, renderer markers, or full legacy
character context.
"""


def build(run_root: Path) -> None:
    run_root = run_root.resolve()
    if run_root.exists():
        raise FileExistsError(f"refusing to overwrite frozen run: {run_root}")
    packet_root = run_root / "CHATGPT_PRO_AUTHORING_PACKET"
    packet_root.mkdir(parents=True)
    source_rows = _copy_sources(packet_root)
    _write(
        packet_root / "SOURCE_SNAPSHOT_MANIFEST.json",
        json.dumps(
            {
                "schema_version": "cera.prompt_authoring_source_snapshot.v1",
                "frozen": True,
                "contains_secrets": False,
                "contains_user_transcripts": False,
                "sources": source_rows,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )
    _write(packet_root / "CURRENT_ASSEMBLED_PROMPT_ANATOMY.md", _current_prompt_anatomy())
    _write(packet_root / "COMPILER_INTERFACE_PROPOSAL.md", _compiler_interface())
    _write(packet_root / "WRITING_EVIDENCE.md", _writing_evidence())

    registry = load_prompt_module_registry(
        FIXTURE_MANIFEST,
        compiler_state=PromptCompilerState.FIXTURE_ONLY,
    )
    hana = TypedId.parse("character:hana_hanezawa")
    sakura = TypedId.parse("character:sakura_hanezawa")
    mia = TypedId.parse("character:mia_hanezawa")
    representatives = (
        (
            "01_auto_off_single_ordinary",
            SceneDepthMode.AUTO,
            AdultRenderingMode.OFF,
            InteractionTopology.SINGLE_NPC_FLOOR,
            SceneFunction.ORDINARY_SOCIAL,
            (sakura,),
            PromptTone.NEUTRAL,
            InteriorityLevel.LOW,
        ),
        (
            "02_long_off_multi_confrontation",
            SceneDepthMode.LONG,
            AdultRenderingMode.OFF,
            InteractionTopology.MULTI_NPC_SHARED_FLOOR,
            SceneFunction.CONFRONTATION_BOUNDARY,
            (sakura, mia),
            PromptTone.TENSE,
            InteriorityLevel.MEDIUM,
        ),
        (
            "03_auto_on_single_vulnerability",
            SceneDepthMode.AUTO,
            AdultRenderingMode.ON,
            InteractionTopology.SINGLE_NPC_FLOOR,
            SceneFunction.EMOTIONAL_VULNERABILITY,
            (hana,),
            PromptTone.WARM,
            InteriorityLevel.HIGH,
        ),
        (
            "04_epic_ex_npc_exchange_aftermath",
            SceneDepthMode.EPIC,
            AdultRenderingMode.EX,
            InteractionTopology.NPC_TO_NPC_EXCHANGE,
            SceneFunction.AFTERMATH_RECOVERY,
            (hana, sakura),
            PromptTone.SOMBER,
            InteriorityLevel.HIGH,
        ),
    )
    for name, depth, adult, topology, scene, characters, tone, interiority in representatives:
        compiled = _compile(
            registry=registry,
            depth=depth,
            adult=adult,
            topology=topology,
            scene=scene,
            characters=characters,
            tone=tone,
            interiority=interiority,
        )
        _write(
            packet_root / "representative_packets" / f"{name}.md",
            "\n".join(
                (
                    f"# {name}",
                    "",
                    "**Synthetic, noncanonical, provider-free fixture.**",
                    "",
                    "## System message",
                    "",
                    "```text",
                    compiled.system_prompt,
                    "```",
                    "",
                    "## User packet",
                    "",
                    "```json",
                    json.dumps(json.loads(compiled.user_packet_json), indent=2, ensure_ascii=False),
                    "```",
                    "",
                    "## Receipt",
                    "",
                    "```json",
                    json.dumps(to_primitive(compiled.receipt), indent=2, ensure_ascii=False),
                    "```",
                )
            ),
        )

    matrix = []
    for depth in SceneDepthMode:
        for adult in AdultRenderingMode:
            compiled = _compile(
                registry=registry,
                depth=depth,
                adult=adult,
                topology=InteractionTopology.SINGLE_NPC_FLOOR,
                scene=SceneFunction.ORDINARY_SOCIAL,
                characters=(sakura,),
                tone=PromptTone.NEUTRAL,
                interiority=InteriorityLevel.MEDIUM,
            )
            receipt = compiled.receipt
            matrix.append(
                {
                    "scene_depth_mode": depth.value,
                    "adult_rendering_mode": adult.value,
                    "selected_module_ids": [value.module_id for value in receipt.selected_prompt_modules],
                    "module_order": [value.deterministic_order for value in receipt.selected_prompt_modules],
                    "adult_fragments_included": bool(receipt.selected_craft_fragment_ids),
                    "selected_fragment_count": len(receipt.selected_craft_fragment_ids),
                    "selected_example_count": len(receipt.selected_example_ids),
                    "module_content_bytes": receipt.module_content_bytes,
                    "system_prompt_bytes": receipt.system_prompt_bytes,
                    "user_packet_bytes": receipt.user_packet_bytes,
                    "total_prompt_bytes": receipt.total_prompt_bytes,
                    "estimated_total_tokens": receipt.estimated_total_tokens,
                    "receipt_sha256": receipt.receipt_sha256,
                    "provider_dispatch_allowed": receipt.provider_dispatch_allowed,
                }
            )
    _write(
        packet_root / "COMBINATION_MATRIX.json",
        json.dumps(
            {"schema_version": "cera.modular_prompt_combination_matrix.v1", "rows": matrix},
            indent=2,
            ensure_ascii=False,
        ),
    )
    matrix_lines = [
        "# Depth and Adult combination matrix",
        "",
        "All values are fixture-only diagnostics; none qualify Flash or activate a route.",
        "",
        "| Depth | Adult | Modules | Fragments | Examples | Bytes | Est. tokens |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in matrix:
        matrix_lines.append(
            "| {scene_depth_mode} | {adult_rendering_mode} | {modules} | "
            "{selected_fragment_count} | {selected_example_count} | "
            "{total_prompt_bytes} | {estimated_total_tokens} |".format(
                **row,
                modules=len(row["selected_module_ids"]),
            )
        )
    _write(packet_root / "COMBINATION_MATRIX.md", "\n".join(matrix_lines))

    files = []
    for path in sorted(packet_root.rglob("*")):
        if path.is_file() and path.name != "PACKET_MANIFEST.json":
            raw = path.read_bytes()
            files.append(
                {
                    "relative_path": path.relative_to(packet_root).as_posix(),
                    "bytes": len(raw),
                    "sha256": bytes_sha256(raw),
                }
            )
    _write(
        packet_root / "PACKET_MANIFEST.json",
        json.dumps(
            {
                "schema_version": "cera.chatgpt_pro_authoring_packet_manifest.v1",
                "frozen": True,
                "active_route_changed": False,
                "provider_calls": 0,
                "database_copied": False,
                "files": files,
            },
            indent=2,
            ensure_ascii=False,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.output)


if __name__ == "__main__":
    main()
