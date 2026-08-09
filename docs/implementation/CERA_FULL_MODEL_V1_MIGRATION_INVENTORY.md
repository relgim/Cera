# CERA Full-Model V1 Migration Inventory

**Status:** active provider-free implementation baseline  
**Recorded:** 2026-08-09  
**Controlling roadmap:** `CERA_FULL_MODEL_DECISION_RATIONALE_ROADMAP_V1.md`

## Frozen execution baseline

- Repository checkout: `D:\Cera\worktrees\C78-quality-fixes`
- Branch: `fix/cera-runtime-quality-20260809`
- Source commit: `7e2a4151155dcbcb6d42612d2ed69a6b387d246a`
- Source tree: `b64611fd62126fa5f74805e4430123b7e29c074c`
- Roadmap SHA-256:
  `2862d226a7e6a7a25894fb13235cfbcf9568090611b508ae0c00af2186dcc3de`
- Tracked worktree state at freeze: clean
- Provider calls used by this implementation program at freeze: zero
- Direct creator ceiling after the provider-free source freeze: 500
  Codex-family operations and 500 DeepSeek-family operations

Historical accepted evidence, Genesis packages, accepted story data, provider
receipts, and review-cycle bytes are preserved. This program does not rewrite
them. Contract changes are additive or explicitly versioned.

## Runtime separation

The currently listening CERA service on `127.0.0.1:5101` is an older Queue
0072 runtime rooted in `D:\CP25\worktrees\cera-q0072-pi-lean`. It is not the
execution source for this program and is left unchanged during provider-free
implementation. The installed SillyTavern integration bytes match this
checkout's repository copies:

| Integration | SHA-256 |
|---|---|
| review proxy plugin | `241aa8eb3ec3868571741639e38f1ab43a9b8eace5ee2bd0b1c6547e97806d44` |
| creator review extension | `306f77bf08c115ced3fe959b4c3e851678d265d304f49a020c9bd2fc1841dd79` |

All later live tests use isolated runtime roots and ports. Installed-user
SillyTavern files are not changed merely to qualify source.

## Post-`1cc8ff8` classification

| Commit or surface | Disposition | Reason |
|---|---|---|
| `1b93b617dd4ab897b99b1c4da1c0f9bb3e472a96` | preserve | Reconciles the Validator/Filter architecture questions without changing runtime source. |
| `5cbefd3563d3cec625a1cd3ab492b2bdf14418af` | preserve as review input | Records the Vera reasoning/data handoff used to derive the final creator decisions. It is not runtime authority. |
| `7e2a4151155dcbcb6d42612d2ed69a6b387d246a` | integrate and control | Binds the final full-model roadmap and creator decisions for this branch. |
| Queue 0077 / Roadmap 0031 execution instructions | supersede for this branch | They target the frozen pre-roadmap route and must not be run unchanged. Their historical bytes remain untouched. |
| Earlier Pi Scene custody, world-workspace, branch-state, dispatch-guard, and session hardening commits | preserve and integrate | They provide valid deterministic foundations, subject to focused regressions and the gaps named by the new roadmap. |
| Historical Validator/Reader mandatory critical path | preserve as optional historical code; supersede as the target route | Ordinary qualification uses Luna validation; adult qualification uses the Adult Filter. Neither historical role may silently regain mandatory route ownership. |

## Migration rules

1. Python retains identity, branch, schema, visibility, transaction, recovery,
   and provider-accounting authority.
2. One visible candidate has one logic owner: Codex for ordinary turns and
   DeepSeek Adult Scene for adult turns.
3. Provider-authored semantics and Python custody envelopes remain separate.
4. New-chat worlds copy the newest accepted Genesis; forks copy exact accepted
   parent state and then diverge.
5. Global craft, voice, and adult examples are content-addressed Genesis
   dependencies. Story and character state remain world-local.
6. No provider dispatch is allowed until the complete provider-free source
   freeze and accounting reconciliation pass.

