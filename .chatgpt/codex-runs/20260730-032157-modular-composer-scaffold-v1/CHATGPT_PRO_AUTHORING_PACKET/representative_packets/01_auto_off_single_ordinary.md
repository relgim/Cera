# 01_auto_off_single_ordinary

**Synthetic, noncanonical, provider-free fixture.**

## System message

```text
## PROMPT MODULE composer.core.authority.v1@v1
[FIXTURE_ONLY]

Structural placeholder for the stable Composer role, source authority,
protected-user boundary, selected-cast boundary, continuity invariants, and
single-complete-reply obligation. Final writing-facing language is unassigned.

## PROMPT MODULE composer.core.structured_output.v1@v1
[FIXTURE_ONLY]

Structural placeholder for provider-schema teaching, ordered story segments,
coverage references, authority references, and the terminal handoff field.
Final writing-facing language is unassigned.

## PROMPT MODULE composer.depth.auto.v1@v1
[FIXTURE_ONLY]

Selector fixture: scene_depth_mode=auto. No final pacing or development prose
has been authored.

## PROMPT MODULE composer.adult.off.v1@v1
[FIXTURE_ONLY]

Selector fixture: adult_rendering_mode=off. No writing-facing Adult material
has been authored.

## PROMPT MODULE composer.topology.single_npc_floor.v1@v1
[FIXTURE_ONLY]

Selector fixture: interaction_topology=single_npc_floor. Final floor and
reaction-order guidance is unassigned.

## PROMPT MODULE composer.scene.ordinary_social.v1@v1
[FIXTURE_ONLY]

Selector fixture: scene_function=ordinary_social. Final scene-dynamics
language is unassigned.
```

## User packet

```json
{
  "character_expression": [
    {
      "applicable_character_ids": [
        "character:sakura_hanezawa"
      ],
      "kind": "character_expression",
      "material_id": "expression.0",
      "owner_character_id": "character:sakura_hanezawa",
      "private_to_owner": true,
      "selection_reason": "selected active speaker fixture",
      "source_provenance": "synthetic_authoring_packet_fixture",
      "text": "Synthetic noncanonical expression packet for character:sakura_hanezawa; fixture 0. No Genesis fact or past event is asserted.",
      "text_sha256": "351f8f1e4afd62949157ca486d88a8bf93f089a38362920cc29ed20d4e86535b"
    }
  ],
  "compiler_state": "fixture_only",
  "conditional_future_events_included": false,
  "continuity_and_evidence": [],
  "craft_items": [],
  "output_obligations": {
    "required_beat_authority_ids": [
      "beat:synthetic-1"
    ],
    "source_coverage_mode": "must_be_empty",
    "specificity_coverage_mode": "must_be_empty"
  },
  "output_schema": {
    "additionalProperties": false,
    "required": [
      "schema_version",
      "story_segments",
      "source_coverage",
      "realization_segments",
      "specificity_coverage",
      "terminal_segment_key"
    ],
    "type": "object"
  },
  "participant_boundaries": {
    "inactive_characters_forbidden": true,
    "protected_user_id": "character:ted",
    "selected_npc_ids": [
      "character:sakura_hanezawa"
    ]
  },
  "protected_source": {
    "source_units": [
      {
        "classification": "message",
        "exact_text": "Synthetic authoring-packet cue; not story canon.",
        "source_unit_id": "source_unit:synthetic-authoring-cue"
      }
    ],
    "synthetic": true
  },
  "schema_version": "cera.modular_composer_fixture_packet.v1",
  "selected_prompt_modules": [
    {
      "deterministic_order": 100,
      "module_id": "composer.core.authority.v1",
      "selection_reason": "stable_core_required",
      "sha256": "f439675e8a50e077ddbd04c45fdac17f1d98ece19e6599684ae8f9ff7b3f601d",
      "version": "v1"
    },
    {
      "deterministic_order": 200,
      "module_id": "composer.core.structured_output.v1",
      "selection_reason": "structured_output_teaching_required",
      "sha256": "602ffbf600d1108f07492ef25feb78b7d897e41523bc435587107e91ae42da87",
      "version": "v1"
    },
    {
      "deterministic_order": 320,
      "module_id": "composer.depth.auto.v1",
      "selection_reason": "scene_depth_mode:auto",
      "sha256": "4bea59e7bff227c1e59db3a0dc2050d50f8de441560053642c9341e1e079585d",
      "version": "v1"
    },
    {
      "deterministic_order": 410,
      "module_id": "composer.adult.off.v1",
      "selection_reason": "adult_rendering_mode:off",
      "sha256": "102631905f615c71c0ae31f0686b2874dcf56cb981a219734360b14f8caf887d",
      "version": "v1"
    },
    {
      "deterministic_order": 510,
      "module_id": "composer.topology.single_npc_floor.v1",
      "selection_reason": "interaction_topology:single_npc_floor",
      "sha256": "512ed34ff66a602c1d314fe840e60f134b03e753d08258043bd01cab273798d6",
      "version": "v1"
    },
    {
      "deterministic_order": 610,
      "module_id": "composer.scene.ordinary_social.v1",
      "selection_reason": "scene_function:ordinary_social",
      "sha256": "2ec7771184a787d0fecd61e7e44a9ad6967410a83eed19770b7e7b63ed859367",
      "version": "v1"
    }
  ],
  "selectors": {
    "adult_rendering_mode": "off",
    "interaction_topology": "single_npc_floor",
    "interiority_level": "low",
    "scene_depth_mode": "auto",
    "scene_function": "ordinary_social",
    "tone": "neutral"
  },
  "sequence_plan": {
    "current_beats": [
      {
        "actor_id": "character:sakura_hanezawa",
        "beat_id": "beat:synthetic-1",
        "neutral_event": "Synthetic current-scene beat for prompt rendering."
      }
    ],
    "sequence_plan_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "stop_before": "protected_user_next_open_choice"
  },
  "target_provider_model": "deepseek-v4-flash"
}
```

## Receipt

```json
{
  "schema_version": "cera.prompt_compilation_receipt.v1",
  "compiler_version": "cera.modular_composer_compiler.v1",
  "compiler_state": "fixture_only",
  "registry_manifest_sha256": "ea338c93387ae9a7a18dd0aa14a46c221f66b604e35cfa376a6fb4786588d4d9",
  "selection_request_sha256": "f906d3e1e039a4409e2ec4dadec8e29386cf833d7e083d972ee795dedbd0db9a",
  "sequence_plan_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "selected_cast_sha256": "bc1fff791792c439e0707af216a36485bdb5d99056d5ac005a380e3f718b5125",
  "target_provider_model": "deepseek-v4-flash",
  "scene_depth_mode": "auto",
  "adult_rendering_mode": "off",
  "selected_prompt_modules": [
    {
      "module_id": "composer.core.authority.v1",
      "version": "v1",
      "layer": "core_authority",
      "sha256": "f439675e8a50e077ddbd04c45fdac17f1d98ece19e6599684ae8f9ff7b3f601d",
      "deterministic_order": 100,
      "relative_path": "modules/core/authority_and_hard_boundaries_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "stable_core_required",
      "noncanonical": true,
      "noncopyable": true
    },
    {
      "module_id": "composer.core.structured_output.v1",
      "version": "v1",
      "layer": "structured_output",
      "sha256": "602ffbf600d1108f07492ef25feb78b7d897e41523bc435587107e91ae42da87",
      "deterministic_order": 200,
      "relative_path": "modules/core/structured_output_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "structured_output_teaching_required",
      "noncanonical": true,
      "noncopyable": true
    },
    {
      "module_id": "composer.depth.auto.v1",
      "version": "v1",
      "layer": "depth",
      "sha256": "4bea59e7bff227c1e59db3a0dc2050d50f8de441560053642c9341e1e079585d",
      "deterministic_order": 320,
      "relative_path": "modules/depth/auto_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "scene_depth_mode:auto",
      "noncanonical": true,
      "noncopyable": true
    },
    {
      "module_id": "composer.adult.off.v1",
      "version": "v1",
      "layer": "adult_rendering",
      "sha256": "102631905f615c71c0ae31f0686b2874dcf56cb981a219734360b14f8caf887d",
      "deterministic_order": 410,
      "relative_path": "modules/adult/off_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "adult_rendering_mode:off",
      "noncanonical": true,
      "noncopyable": true
    },
    {
      "module_id": "composer.topology.single_npc_floor.v1",
      "version": "v1",
      "layer": "interaction_topology",
      "sha256": "512ed34ff66a602c1d314fe840e60f134b03e753d08258043bd01cab273798d6",
      "deterministic_order": 510,
      "relative_path": "modules/topology/single_npc_floor_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "interaction_topology:single_npc_floor",
      "noncanonical": true,
      "noncopyable": true
    },
    {
      "module_id": "composer.scene.ordinary_social.v1",
      "version": "v1",
      "layer": "scene_function",
      "sha256": "2ec7771184a787d0fecd61e7e44a9ad6967410a83eed19770b7e7b63ed859367",
      "deterministic_order": 610,
      "relative_path": "modules/scene/ordinary_social_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "scene_function:ordinary_social",
      "noncanonical": true,
      "noncopyable": true
    }
  ],
  "module_contributions": [
    {
      "reference": {
        "module_id": "composer.core.authority.v1",
        "version": "v1",
        "layer": "core_authority",
        "sha256": "f439675e8a50e077ddbd04c45fdac17f1d98ece19e6599684ae8f9ff7b3f601d",
        "deterministic_order": 100,
        "relative_path": "modules/core/authority_and_hard_boundaries_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "stable_core_required",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 243,
      "estimated_tokens": 61
    },
    {
      "reference": {
        "module_id": "composer.core.structured_output.v1",
        "version": "v1",
        "layer": "structured_output",
        "sha256": "602ffbf600d1108f07492ef25feb78b7d897e41523bc435587107e91ae42da87",
        "deterministic_order": 200,
        "relative_path": "modules/core/structured_output_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "structured_output_teaching_required",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 214,
      "estimated_tokens": 54
    },
    {
      "reference": {
        "module_id": "composer.depth.auto.v1",
        "version": "v1",
        "layer": "depth",
        "sha256": "4bea59e7bff227c1e59db3a0dc2050d50f8de441560053642c9341e1e079585d",
        "deterministic_order": 320,
        "relative_path": "modules/depth/auto_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "scene_depth_mode:auto",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 114,
      "estimated_tokens": 29
    },
    {
      "reference": {
        "module_id": "composer.adult.off.v1",
        "version": "v1",
        "layer": "adult_rendering",
        "sha256": "102631905f615c71c0ae31f0686b2874dcf56cb981a219734360b14f8caf887d",
        "deterministic_order": 410,
        "relative_path": "modules/adult/off_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "adult_rendering_mode:off",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 112,
      "estimated_tokens": 28
    },
    {
      "reference": {
        "module_id": "composer.topology.single_npc_floor.v1",
        "version": "v1",
        "layer": "interaction_topology",
        "sha256": "512ed34ff66a602c1d314fe840e60f134b03e753d08258043bd01cab273798d6",
        "deterministic_order": 510,
        "relative_path": "modules/topology/single_npc_floor_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "interaction_topology:single_npc_floor",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 129,
      "estimated_tokens": 33
    },
    {
      "reference": {
        "module_id": "composer.scene.ordinary_social.v1",
        "version": "v1",
        "layer": "scene_function",
        "sha256": "2ec7771184a787d0fecd61e7e44a9ad6967410a83eed19770b7e7b63ed859367",
        "deterministic_order": 610,
        "relative_path": "modules/scene/ordinary_social_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "scene_function:ordinary_social",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 112,
      "estimated_tokens": 28
    }
  ],
  "selected_material_ids": [
    "expression.0"
  ],
  "selected_craft_fragment_ids": [],
  "selected_example_ids": [],
  "module_content_bytes": 924,
  "dynamic_material_bytes": 125,
  "system_prompt_bytes": 1222,
  "user_packet_bytes": 3279,
  "total_prompt_bytes": 4501,
  "estimated_total_tokens": 1126,
  "technical_safety_policy_version": "cera.prompt_registry_technical_safety.v1",
  "creative_prompt_limit_enforced": false,
  "fixed_example_count_enforced": false,
  "content_truncated": false,
  "provider_dispatch_allowed": false,
  "external_provider_calls": 0,
  "story_authority_writes": 0,
  "automatic_detailer_calls": 0,
  "automatic_retry_count": 0,
  "fallback_enabled": false,
  "adult_publication_activated": false
}
```
