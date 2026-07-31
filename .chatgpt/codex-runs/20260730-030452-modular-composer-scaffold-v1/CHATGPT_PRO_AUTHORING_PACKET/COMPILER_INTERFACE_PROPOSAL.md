# Compiler interface proposal for prompt authorship

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
target_provider_model: deepseek-v4-flash
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
