# CERA Repository Baseline Inventory

**Inventory date:** 2026-07-31  
**Purpose:** safe local Git bootstrap before stabilization checkpoint 001  
**Repository:** `D:\AIChatBot\Cera`  
**Remote operations:** prohibited; no remote is configured

## Lossless backup

- Backup root: `D:\AIChatBot\Cera_Backups\2026-07-31-checkpoint-001-pre-stabilization`
- Source files: 5,044
- Backup files: 5,044
- Source bytes: 565,577,272
- Backup bytes: 565,577,272
- Per-file SHA-256 comparison: 5,044/5,044 matched
- Pre-edit path/size/content manifest SHA-256:
  `43ac1d1056b38cec8356a0c53bca37233094bd9b3252ccf3b74daf2e7c0f0dbd`
- Reparse points encountered: 0

The manifest hash is calculated over sorted UTF-8 rows of
`repository-relative-path`, byte length, and lowercase SHA-256. The verified
backup is the recovery copy for every path, including ignored runtime state.

## Exhaustive classification rules

The rules below are ordered and mutually exclusive. Together they classify
every file present in the pre-edit repository snapshot.

| Class | Pre-edit files | Pre-edit bytes | Paths | Git treatment |
|---|---:|---:|---|---|
| Runtime/generated/untracked | 4,096 | 516,699,230 | `.venv/**`, `.tmp/**`, `runtime/**`, `evaluation/tmp_*`, any `__pycache__/**`, and `src/*.egg-info/**` | Ignored; preserved in the verified backup |
| Immutable evidence | 305 | 38,808,611 | `evaluation/evidence/**`, `adult/provenance/**`, `genesis/cera_authority/**`, and `genesis/provenance/**` | Tracked byte-for-byte; never rewritten for current-status correction |
| Rebuildable generated output | 151 | 3,606,842 | `genesis/generated/**`, `genesis/packages/**`, and `adult/catalog/**` | Tracked because hashes, tests, and runtime selection depend on these views/packages |
| Source and authority | 492 | 6,462,589 | Every remaining file, including `AGENTS.md`, `README.md`, `.chatgpt/**`, `config/**`, `docs/**`, `integrations/**`, `scripts/**`, `src/**`, `tests/**`, `evaluation/suites/**`, Genesis authorizations, and top-level source archives | Tracked |

Directories inherit the class of their contained files. A directory containing
multiple classes is not itself treated as authority; each child path follows
the ordered rules above.

## Ignored and intentionally untracked path manifest

| Path rule | Contents | Preservation location | Reason |
|---|---|---|---|
| `.venv/**` | Installed Python runtime and third-party packages | Same path under the verified backup; reproducible from `pyproject.toml` | Machine-local dependency state; includes third-party certificates and caches |
| `.tmp/**` | Extracted packages, prompt captures, intermediate generated views, local logs | Same path under the verified backup | Disposable work products, duplicates, and diagnostics; not authority |
| `runtime/**` | Human-test SQLite databases, WAL/SHM state, service logs, smoke state | Same path under the verified backup | Mutable non-production runtime/story state; must never enter a source commit |
| `evaluation/tmp_*` | Temporary suite logs, exit markers, probe databases and WAL/SHM files | Same path under the verified backup | Regenerable local execution output; immutable qualification evidence lives only under `evaluation/evidence/**` |
| `**/__pycache__/**`, `*.py[cod]` | Python bytecode | Same path under the verified backup | Regenerable interpreter cache |
| `src/*.egg-info/**` | Editable-install package metadata | Same path under the verified backup | Regenerable packaging output |
| `.pytest_cache/**`, `.coverage`, `htmlcov/**`, `build/**`, `dist/**` | Test/build output when present | Backup if present at snapshot time | Regenerable output |
| `.env`, `.env.*`, `*.pem`, `*.key` | Potential local credentials or private keys | Not present in the pre-edit source snapshot; any future occurrence stays untracked | Secret-risk material must not enter Git |

No historical evidence path is ignored. The existing files under
`evaluation/evidence/**`, Genesis provenance/authority, and Adult provenance are
included in the baseline unchanged.

## Secret-risk scan

The pre-edit repository was scanned outside `.venv/**` and `runtime/**` without
printing matched values. Results:

- OpenAI-style key patterns: 0 files
- DeepSeek key-assignment patterns: 0 files
- bearer credential patterns: 0 files
- private-key headers: 0 files
- generic secret-assignment pattern: one false positive,
  `src/cera/providers/models.py`, containing the enum value
  `ENVIRONMENT_API_KEY = "environment_api_key"`

No credential material was identified for inclusion in the baseline.

## Baseline scope

The baseline commit must contain the complete tracked pre-stabilization D-180
implementation plus this Git-bootstrap inventory and the tightened ignore
policy. It must exclude all paths listed as intentionally untracked. The
stabilization protocol, runtime-identity correction, current-status correction,
and their tests belong to the later checkpoint commit, not this baseline.
