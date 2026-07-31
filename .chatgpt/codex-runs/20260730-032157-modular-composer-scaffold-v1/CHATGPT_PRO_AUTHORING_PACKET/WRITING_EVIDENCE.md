# Sanitized current writing evidence

No user transcript, provider prompt, private evidence, secret, or database is
copied here. Two assistant-only artifacts were read from the non-production
human-test database in read-only mode; only their accepted prose is reproduced.

## Successful ordinary Composer result

Source: `evaluation/evidence/continuous_ten_turn_qualification_2026-07-29_v20/turns/01_sakura-doorway-shoe-and-entry-boundary.json`

> Sakura stands inside the doorway, her posture straight and her gaze fixed on Ted without warmth. She does not step back to invite him in. "Shoes are removed and left here, at the threshold," she says, her tone crisp and procedural. She points with a short, precise gesture to the designated space just inside the entrance. "The suitcase remains outside. A place for it will be decided later. Do not bring it past the door."

## Short/under-developed telephone result

Artifact: `artifact:0a2a0a60-8f9d-5be9-b2fb-65050b2037f2`
Length: `274` characters

> A click, then a measured pause. "This is the Hanezawa residence." Sakura's voice carries a cool, precise weight, no warmth offered. "Identify yourself and state your business." The receiver stays close to her ear, ready for whatever comes next. She waits, still and guarded.

This is evidence of insufficient development for the creator's desired mode,
not proof that every atomic reply must be long.

## Overwritten/over-developed Sakura telephone result

Artifact: `artifact:9cea1daa-a7f6-5fc9-ad79-2ed4aaf12ed1`
Length: `1431` characters

> Sakura lifts the receiver with her usual economy of motion, the handset's weight a familiar presence against her ear. She does not return the greeting in kind. Her voice is cool and clean, each syllable placed with the precision of someone who has answered the Hanezawa line many times before. "This is the Hanezawa reception line. How may I direct your call?" The phrasing offers no confirmation of occupants, no acknowledgment of the name already spoken. It is a channel, not an invitation.
>
> A deliberate pause follows, during which she listens to the ambient silence on the line as carefully as she would a speaker's words. Then she continues, her tone unchanged. "Before I proceed, please identify yourself. I need your full name and your connection to this household." The request is not softened by apology or hesitation. It lands as a procedural requirement, the sort of gatekeeping that leaves no room to slip through unnamed.
>
> She does not lower the receiver while she waits. One finger rests lightly against the base of the phone, a habit from countless administrative calls. When she speaks again, her voice carries the same administrative composure. "Once I have that, state the specific reason for your call. I will not be able to transfer you or answer further inquiries until I have both pieces of information." The words hang in the quiet, a clear boundary drawn, the floor now fully yielded to the caller's answer.

This result is useful because it adds unsupported routine/history language
(`reception line`, repeated administrative calls, habitual phone behavior)
while expanding a simple exchange. Pro should improve development without
turning elaboration into biography.

## Schema-semantic teaching retained in the active prompt

The following current instructions correlate with later structurally accepted
Composer outputs. They must not be removed without a matched test proving that
Flash still satisfies the domain contract:

```text
Every realization_segments entry must contain exactly one authority_id. Do not return owner_id: Python derives the character owner deterministically from that authority. For a selected NPC, authority_id must be an exact current beat ID from the decision, and the prose must realize that beat's advertised actor. For protected_user_id, authority_id must be an exact supplied source_unit_id and the realization kind must be allowed by that unit. Every required current decision beat needs at least one realization_segments entry naming that exact beat authority_id. Every selected NPC needs at least one of that NPC's beat authorities realized. The kind field is only a general story function from its advertised enum; never put adult channel names such as physiology or narration in kind.
When creator_event_coverage_required is false, source_coverage must be empty. When it is true, include every supplied source unit as final story prose, then provide one source_coverage entry for each source unit in packet order. Every returned entry's segment_keys array must contain at least one existing story segment key; an empty segment_keys array is always invalid. An entry may name multiple ordered unique segment_keys when one source obligation is realized across separated prose.
When a SpecificityContract is absent, specificity_coverage must be empty. When present, provide exactly one specificity_coverage entry for every advertised obligation_key. Every returned entry's segment_keys array must contain at least one existing story segment key; an empty segment_keys array is always invalid. Map it to the ordered unique segment_keys where that adult channel is actually realized. Adult channel coverage never changes or substitutes the general realization kind.
Follow output_obligations literally. Realize every required beat authority_id and use only allowed authority_ids; the advertised authority-owner associations tell you whose prose each authority must represent, but Python owns and derives the owner field. For source_coverage and specificity_coverage, mode must_be_empty requires an empty output array; mode exact_sequence requires exactly the advertised values in the advertised order. For every returned coverage entry, each_returned_entry_requires_one_or_more_existing_segment_keys is mandatory: never emit segment_keys: []. These are one canonical Python-owned obligation object, not creative choices.
```

This is preservation evidence, not a causal claim that each sentence is
individually necessary.

## Sera read-only comparison provenance

- `E:\AIChatBot\Sera\config\scene_execution_contract.md` — `ad9939416eb27ae05bfa3358f29c82ecf68eda9605dc0402f3155e4c50d86808`
- `E:\AIChatBot\Sera\config\writing_styles.md` — `7596e9e365138c9c5629389993a0ae241d9cdb4e9fe39e572eb8e074dd53086f`
- `E:\AIChatBot\Sera\config\dialogue_detailer_profile.md` — `bf3c9502cc452760814621e3147e3ea76f3dc725b294a6ed8248b0bde4c0937b`
- `E:\AIChatBot\Sera\docs\PROMPT_AUTHORING_FORMULA.md` — `ee9f5a042a788871c8c299dcd73691e29775f387615fe0365144517f02e53f7a`

Useful Sera principles to adapt are causal progression, tactical dialogue
shifts, active-viewpoint ownership, state/geography continuity, and natural
handoff. Do not import Sera's monolithic Writer, automatic Detailer, numeric
length targets, runtime memory ownership, renderer markers, or full legacy
character context.
