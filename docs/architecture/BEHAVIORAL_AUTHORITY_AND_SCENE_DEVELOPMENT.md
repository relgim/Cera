# Behavioral Authority and Scene Development

**Status:** controlling behavioral architecture under D-160  
**Scope:** ordinary and consent-valid adult reasoning, composition, creator review, and later memory derivation

## 1. Purpose

CERA separates five things that older turns blurred together:

1. exact statements in the user's message;
2. the authority Python permits each statement to have;
3. Codex/Sol's causal scene plan;
4. DeepSeek's creative realization of that plan;
5. facts and development that may become durable only after validation and creator acceptance.

The user is the initial domino. Runtime Codex/Sol chooses the supported causal
path of the remaining dominoes. DeepSeek chooses the prose, pacing, transitions,
sensory presentation, and exact voice used to realize that path. Python owns
identity, evidence, validation, transactions, and final publication.

## 2. Claim-level ingress authority

The raw message remains immutable. The Reasoner proposes exact-quote-anchored
claim classifications; Python validates their spans, hashes, allowed authority,
and cross-field semantics before any claim is used.

Claims distinguish protected-user action/dialogue, observable NPC action,
NPC dialogue, NPC mental state, involuntary NPC body state, material state,
time/place context, relationship/history, creative direction, and meta
instruction. Their permitted dispositions are:

- `source_authorized`: usable as supplied source authority;
- `nonbinding_input`: visible as a request but not allowed to determine the NPC;
- `modification_proposal`: usable only under explicit Modification mode;
- `requires_creator_confirmation`: provisional fact that cannot become durable yet;
- `hard_rejected`: unavailable to planning or prose realization.

Search results, examples, summaries, provisional prose, and model explanations
cannot upgrade a claim's authority.

## 3. Character autonomy

`CharacterAutonomyMode` is creator-owned transport state:

| Mode | User-authored NPC mind | User-authored involuntary NPC body state |
|---|---|---|
| `off` | may be treated as source direction | may be treated as source direction |
| `mind` | nonbinding | may be treated as source direction |
| `body` | may be treated as source direction | nonbinding |
| `both` | nonbinding | nonbinding |

The creator default is `both`. Observable acts, spoken words, positions, and
material outcomes remain separately classifiable; autonomy does not erase an
event merely because a character performed it. It prevents the user from
declaring what an NPC privately feels, thinks, desires, senses, or
involuntarily experiences when that channel is protected.

## 4. Adjustment and Modification

`Adjustment` is the default. It preserves supplied objective actions, dialogue
meaning, ordering, and outcome while allowing normalization, causal framing,
and writer-facing transitions. It cannot create a new consequential Ted choice.

`Modification` is an explicit creator request allowing CERA to reshape wording,
minor action ordering, or a reversible connective action to better realize the
likely requested scene. It never authorizes identity, consent, privacy,
knowledge, branch, or hard-event changes. Every material modification remains
visible in the provisional review package before acceptance.

## 5. Broad event blocks and writer scaffolds

The Reasoner does not prewrite DeepSeek's prose. It produces broad
`SceneEventBlock` records. Each block owns:

- its purpose and causal basis;
- participating actors;
- one or more materially distinct event advances;
- the resulting scene state;
- exact evidence and source-claim bindings;
- the protected-user realization allowance;
- a `WriterScaffold`.

The Writer scaffold supplies motivation/subtext, voice and selective
interiority, physical/material continuity, transition obligations, and explicit
creative space. It describes what the prose must accomplish without dictating
the final wording, timing, gestures, or imagery.

Fine-grained prose beats are not counted as scene development. A detailed
description of one unchanged action does not satisfy multiple event advances.

## 6. Adaptive runway and stopping

Depth controls supported causal development, not a fixed beat count:

- `off`: smallest complete consequential exchange;
- `auto`: adaptive atomic, developed, domino, or multi-scene reply;
- `long`: the full supported current causal chain and meaningful follow-on;
- `epic`: all materially distinct supported progression available without padding.

The Reasoner explicitly decides whether to continue beyond the literal endpoint
of the prompt. A question or confession does not automatically end the reply;
NPC reaction, hesitation, interrupted speech, follow-through, a changed routine,
or another rational participant's intervention may continue when supported.

The response stops only when a causal unit or scene transition is complete, a
genuinely meaningful unsupplied user choice is required, a hard authority
boundary is reached, or uncertainty is intentionally preserved. It must not
manufacture a Ted response merely to avoid a natural pause.

## 7. Realistic development and heightened action

Characters develop with human psychological continuity. Distinctive traits and
defenses are durable priors, not one-event costumes. Positive attachment,
romance, trust, forgiveness, and identity-level change require accumulated,
character-specific evidence. Darker, confrontational, protective, strategic,
or dramatic actions may be more decisive than ordinary real life when they are
still rational for the exact character, evidence, opportunity, and cost.

This is not a general escalation license. Strong action must remain the
character's best supported tactic. A dramatic choice cannot be selected merely
because it is more interesting.

Development is represented atomically. A visible scene may propose separate
body signals, immediate affect, noticed signals, interpretation hypotheses,
micro-development, relationship observations, and reproductive state. One
event may advance at most one adjacent strength level. A weak signal cannot be
laundered through a chain of summaries into love, jealousy, trauma, submission,
forgiveness, or another established trait.

## 8. Selected-character context

Only characters selected for the validated current plan receive runtime
realization context. Python builds one `EffectiveCharacterProjection` per
selected character from the immutable Genesis revision, branch/generation,
snapshot-authorized current state, owner-valid evidence, voice records, and
validated development. Inactive characters are omitted unless a current block
causally selects them.

Examples remain voice/craft references and never become dialogue history,
character knowledge, consent, preferences, or canon.

## 9. Protected-user realization

The protected-user boundary is precise rather than absolute. The Composer may:

- reproduce an exact supplied action or exact supplied dialogue anchored by a
  validated source claim;
- preserve an already established position or contact;
- add minimal nonbranching connective motion when explicitly allowed.

It may not invent Ted's thought, emotion, motive, consent, refusal, strategy,
destination, commitment, new dialogue, or consequential choice. Direct source
authority does not authorize a broader interpretation.

## 10. Provisional display and creator acceptance

Validated DeepSeek prose is a provisional candidate, not yet canon. It may be
shown to the creator after deterministic pre-display checks. Sol then performs
one independent background review and prepares the candidate's event,
development, relationship, thread, and material-state proposal. Python
validates that package.

The creator chooses among:

- `Accept`: commit the validated candidate and approved package atomically;
- `Correction/Adjustment`: preserve the useful candidate while returning a
  typed correction to the owning stage;
- `Rewrite Prose`: retain the validated plan but request new realization;
- `Replan Logic`: discard the plan and candidate;
- `Discard`: retain no story authority.

Every review finding, including a critical quality concern, is visible to the
creator with a reason. Hard privacy, identity, branch, consent/capacity,
protected-user, evidence, or structural invalidity is not overridable. If a
required Sol stage is unavailable, CERA returns an error and disables
acceptance; it does not fall back.

Review UI is removed from the visible chat after the creator acts. A compact,
privacy-safe audit record remains outside future story context.

## 11. Provisional facts and diagnostics

Invented biography, habits, routines, or history may appear only as labeled
provisional candidates. They require explicit creator fact approval before
becoming durable authority.

Creator corrections produce a privacy-safe diagnostic identifying the behavior
class, likely owner, evidence, confidence, proposed shared correction, and
needed regression test. Adult craft gaps use a separate human-readable report
for an adult-content authoring AI. Neither report automatically edits prompts,
Genesis, craft catalogs, code, or durable story state.
