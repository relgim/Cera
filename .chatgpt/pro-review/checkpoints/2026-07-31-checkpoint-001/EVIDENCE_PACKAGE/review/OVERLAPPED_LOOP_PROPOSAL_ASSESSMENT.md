# Overlapped Pro-Review Loop Proposal

## Advisory source

The creator supplied a ChatGPT Pro proposal titled
`FOUR-PROGRESSION OVERLAPPED PRO-REVIEW LOOP V1`. The pasted attachment has
SHA-256 `ed142841cd7511c751f4d91fd69a2833a2d020bbcd35d599198c36e37c2d134e`.

## Proposal summary

- A normal tranche contains at most three main progressions.
- After the main checkpoint is frozen, one previously selected bridge
  progression may run in an isolated branch or worktree during review.
- Pro recommends and bounds work; Ted explicitly authorizes execution.
- The bridge cannot be invented, expanded, or merged automatically.
- A repository-local, complete, SHA-matching `PRO_RESPONSE.md` is required.
- After the bridge, Codex either reconciles the response or becomes stationary.

## Codex assessment

The design is useful, but only with the same maximum-not-quota principle as the
main tranche. It reduces idle time without weakening checkpoint immutability
when the bridge is genuinely independent. Its principal operational cost is
additional branch/worktree and reconciliation complexity.

For Checkpoint 001, the evidence-package repair is the only safe bootstrap
bridge-like task. It touches review artifacts only, leaves the frozen checkpoint
SHA unchanged, makes zero provider calls, and does not alter runtime behavior.
The proposal itself is not installed as controlling governance in this pass;
that requires an explicit governance update after the checkpoint review.

