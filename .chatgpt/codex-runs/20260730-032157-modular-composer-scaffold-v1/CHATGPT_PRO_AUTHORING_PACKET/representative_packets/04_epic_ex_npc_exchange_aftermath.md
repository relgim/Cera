# 04_epic_ex_npc_exchange_aftermath

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

## PROMPT MODULE composer.depth.epic.v1@v1
[FIXTURE_ONLY]

Selector fixture: scene_depth_mode=epic. No final pacing or development prose
has been authored.

## PROMPT MODULE composer.adult.ex.v1@v1
[FIXTURE_ONLY]

Selector fixture: adult_rendering_mode=ex. Final extended-detail,
state-continuity, sensory, and progression guidance is unassigned.

## PROMPT MODULE composer.topology.npc_to_npc_exchange.v1@v1
[FIXTURE_ONLY]

Selector fixture: interaction_topology=npc_to_npc_exchange. Final dialogue
exchange and private-interiority ownership guidance is unassigned.

## PROMPT MODULE composer.scene.aftermath_recovery.v1@v1
[FIXTURE_ONLY]

Selector fixture: scene_function=aftermath_recovery. Final scene-dynamics
language is unassigned.

## CRAFT ITEM craft_reference:authoring-ex-0@v1
[NONCANONICAL][NONCOPYABLE]
Synthetic noncanonical ex craft placeholder 0. It names a distinct authoring slot but supplies no final writing prose.

## CRAFT ITEM craft_reference:authoring-ex-1@v1
[NONCANONICAL][NONCOPYABLE]
Synthetic noncanonical ex craft placeholder 1. It names a distinct authoring slot but supplies no final writing prose.

## CRAFT ITEM craft_reference:authoring-ex-2@v1
[NONCANONICAL][NONCOPYABLE]
Synthetic noncanonical ex craft placeholder 2. It names a distinct authoring slot but supplies no final writing prose.

## CRAFT ITEM craft_reference:authoring-ex-3@v1
[NONCANONICAL][NONCOPYABLE]
Synthetic noncanonical ex craft placeholder 3. It names a distinct authoring slot but supplies no final writing prose.
```

## User packet

```json
{
  "character_expression": [
    {
      "applicable_character_ids": [
        "character:hana_hanezawa"
      ],
      "kind": "character_expression",
      "material_id": "expression.0",
      "owner_character_id": "character:hana_hanezawa",
      "private_to_owner": true,
      "selection_reason": "selected active speaker fixture",
      "source_provenance": "synthetic_authoring_packet_fixture",
      "text": "Synthetic noncanonical expression packet for character:hana_hanezawa; fixture 0. No Genesis fact or past event is asserted.",
      "text_sha256": "52e8ef6ede0f350ee506fec554f94c5d1596f120a1b525be5fa1b57fc4c903ac"
    },
    {
      "applicable_character_ids": [
        "character:sakura_hanezawa"
      ],
      "kind": "character_expression",
      "material_id": "expression.1",
      "owner_character_id": "character:sakura_hanezawa",
      "private_to_owner": true,
      "selection_reason": "selected active speaker fixture",
      "source_provenance": "synthetic_authoring_packet_fixture",
      "text": "Synthetic noncanonical expression packet for character:sakura_hanezawa; fixture 1. No Genesis fact or past event is asserted.",
      "text_sha256": "88e8adce7ceaf117a0eae1614fb6146ca420645eae02df14082c1d5230fe82b1"
    }
  ],
  "compiler_state": "fixture_only",
  "conditional_future_events_included": false,
  "continuity_and_evidence": [],
  "craft_items": [
    {
      "fragment_id": "craft_reference:authoring-ex-0",
      "fragment_sha256": "c9034436efb846f57764814dc364907e8e9d56bbb365a097619d2588ebaf5f7b",
      "is_example": false,
      "noncanonical": true,
      "noncopyable": true,
      "selection_reason": "distinct synthetic craft skill 0",
      "source_provenance": "synthetic_authoring_packet_fixture",
      "version": "v1"
    },
    {
      "fragment_id": "craft_reference:authoring-ex-1",
      "fragment_sha256": "81fe35fdbfe36b63517fe77c8eaca3e679594fa3569fe22d30ab82bca76d1bdb",
      "is_example": false,
      "noncanonical": true,
      "noncopyable": true,
      "selection_reason": "distinct synthetic craft skill 1",
      "source_provenance": "synthetic_authoring_packet_fixture",
      "version": "v1"
    },
    {
      "fragment_id": "craft_reference:authoring-ex-2",
      "fragment_sha256": "2537347df503a3cadc6f8f70d19af20d773e5ef7289c07dee15d05d37ba4e760",
      "is_example": true,
      "noncanonical": true,
      "noncopyable": true,
      "selection_reason": "distinct synthetic craft skill 2",
      "source_provenance": "synthetic_authoring_packet_fixture",
      "version": "v1"
    },
    {
      "fragment_id": "craft_reference:authoring-ex-3",
      "fragment_sha256": "c69f6ee1cfddeba4ab9f02218f2f32020e0a1d7f1402280651b9647f5d3667b8",
      "is_example": true,
      "noncanonical": true,
      "noncopyable": true,
      "selection_reason": "distinct synthetic craft skill 3",
      "source_provenance": "synthetic_authoring_packet_fixture",
      "version": "v1"
    }
  ],
  "output_obligations": {
    "required_beat_authority_ids": [
      "beat:synthetic-1"
    ],
    "source_coverage_mode": "must_be_empty",
    "specificity_coverage_mode": "exact_sequence"
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
      "character:hana_hanezawa",
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
      "deterministic_order": 340,
      "module_id": "composer.depth.epic.v1",
      "selection_reason": "scene_depth_mode:epic",
      "sha256": "decafa9db2399bd8b49a97fadcb92d5c60ca427c38eae4f3d02e40cd62be7e62",
      "version": "v1"
    },
    {
      "deterministic_order": 430,
      "module_id": "composer.adult.ex.v1",
      "selection_reason": "adult_rendering_mode:ex",
      "sha256": "d84115d1cbabd3b6218114bf5b381360b46a47bece55d8167e84e50ab33b02dc",
      "version": "v1"
    },
    {
      "deterministic_order": 530,
      "module_id": "composer.topology.npc_to_npc_exchange.v1",
      "selection_reason": "interaction_topology:npc_to_npc_exchange",
      "sha256": "19ad280c8ab09dd30fe861d4e512e4d78b84f197fe7915a016d0acdb8d003419",
      "version": "v1"
    },
    {
      "deterministic_order": 650,
      "module_id": "composer.scene.aftermath_recovery.v1",
      "selection_reason": "scene_function:aftermath_recovery",
      "sha256": "d6abf55e6ee67185147ca8490ef82f52f45d2aa1a82054321eb75561fb7df7f3",
      "version": "v1"
    }
  ],
  "selectors": {
    "adult_rendering_mode": "ex",
    "interaction_topology": "npc_to_npc_exchange",
    "interiority_level": "high",
    "scene_depth_mode": "epic",
    "scene_function": "aftermath_recovery",
    "tone": "somber"
  },
  "sequence_plan": {
    "current_beats": [
      {
        "actor_id": "character:hana_hanezawa",
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
  "selection_request_sha256": "0bda1f59005e045d95e996d09cab15ff8e40dc555a83bb06becaa626393b9370",
  "sequence_plan_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "selected_cast_sha256": "d0b381a14588861a05483a4f4b08bbdd3ed1a3e0b4c3106b1742fb36fbca4b99",
  "target_provider_model": "deepseek-v4-flash",
  "scene_depth_mode": "epic",
  "adult_rendering_mode": "ex",
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
      "module_id": "composer.depth.epic.v1",
      "version": "v1",
      "layer": "depth",
      "sha256": "decafa9db2399bd8b49a97fadcb92d5c60ca427c38eae4f3d02e40cd62be7e62",
      "deterministic_order": 340,
      "relative_path": "modules/depth/epic_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "scene_depth_mode:epic",
      "noncanonical": true,
      "noncopyable": true
    },
    {
      "module_id": "composer.adult.ex.v1",
      "version": "v1",
      "layer": "adult_rendering",
      "sha256": "d84115d1cbabd3b6218114bf5b381360b46a47bece55d8167e84e50ab33b02dc",
      "deterministic_order": 430,
      "relative_path": "modules/adult/ex_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "adult_rendering_mode:ex",
      "noncanonical": true,
      "noncopyable": true
    },
    {
      "module_id": "composer.topology.npc_to_npc_exchange.v1",
      "version": "v1",
      "layer": "interaction_topology",
      "sha256": "19ad280c8ab09dd30fe861d4e512e4d78b84f197fe7915a016d0acdb8d003419",
      "deterministic_order": 530,
      "relative_path": "modules/topology/npc_to_npc_exchange_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "interaction_topology:npc_to_npc_exchange",
      "noncanonical": true,
      "noncopyable": true
    },
    {
      "module_id": "composer.scene.aftermath_recovery.v1",
      "version": "v1",
      "layer": "scene_function",
      "sha256": "d6abf55e6ee67185147ca8490ef82f52f45d2aa1a82054321eb75561fb7df7f3",
      "deterministic_order": 650,
      "relative_path": "modules/scene/aftermath_recovery_v1.md",
      "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
      "selection_reason": "scene_function:aftermath_recovery",
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
        "module_id": "composer.depth.epic.v1",
        "version": "v1",
        "layer": "depth",
        "sha256": "decafa9db2399bd8b49a97fadcb92d5c60ca427c38eae4f3d02e40cd62be7e62",
        "deterministic_order": 340,
        "relative_path": "modules/depth/epic_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "scene_depth_mode:epic",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 114,
      "estimated_tokens": 29
    },
    {
      "reference": {
        "module_id": "composer.adult.ex.v1",
        "version": "v1",
        "layer": "adult_rendering",
        "sha256": "d84115d1cbabd3b6218114bf5b381360b46a47bece55d8167e84e50ab33b02dc",
        "deterministic_order": 430,
        "relative_path": "modules/adult/ex_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "adult_rendering_mode:ex",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 150,
      "estimated_tokens": 38
    },
    {
      "reference": {
        "module_id": "composer.topology.npc_to_npc_exchange.v1",
        "version": "v1",
        "layer": "interaction_topology",
        "sha256": "19ad280c8ab09dd30fe861d4e512e4d78b84f197fe7915a016d0acdb8d003419",
        "deterministic_order": 530,
        "relative_path": "modules/topology/npc_to_npc_exchange_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "interaction_topology:npc_to_npc_exchange",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 159,
      "estimated_tokens": 40
    },
    {
      "reference": {
        "module_id": "composer.scene.aftermath_recovery.v1",
        "version": "v1",
        "layer": "scene_function",
        "sha256": "d6abf55e6ee67185147ca8490ef82f52f45d2aa1a82054321eb75561fb7df7f3",
        "deterministic_order": 650,
        "relative_path": "modules/scene/aftermath_recovery_v1.md",
        "source_provenance": "fixture_scaffold_only; ChatGPT Pro authoring pending",
        "selection_reason": "scene_function:aftermath_recovery",
        "noncanonical": true,
        "noncopyable": true
      },
      "content_bytes": 115,
      "estimated_tokens": 29
    }
  ],
  "selected_material_ids": [
    "expression.0",
    "expression.1"
  ],
  "selected_craft_fragment_ids": [
    "craft_reference:authoring-ex-0",
    "craft_reference:authoring-ex-1",
    "craft_reference:authoring-ex-2",
    "craft_reference:authoring-ex-3"
  ],
  "selected_example_ids": [
    "craft_reference:authoring-ex-2",
    "craft_reference:authoring-ex-3"
  ],
  "module_content_bytes": 995,
  "dynamic_material_bytes": 720,
  "system_prompt_bytes": 2081,
  "user_packet_bytes": 5099,
  "total_prompt_bytes": 7180,
  "estimated_total_tokens": 1796,
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
