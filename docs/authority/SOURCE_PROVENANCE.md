# Source and Provenance Index

**Status:** controlling source qualification record  
**Important:** inclusion proves review relevance, not runtime authority

## 1. Authority classes

| Class | Meaning |
|---|---|
| `CREATOR_DECISION` | Explicit decision accepted in the current CERA owner review |
| `OWNER_SPEC` | Document in this repository derived from creator decisions |
| `AUDITED_REFERENCE` | Technical or writing source reviewed for lessons |
| `EXAMPLE_REFERENCE` | Craft source that may be copied only under later authorization |
| `HISTORICAL_IMPLEMENTATION` | Existing code used to understand failure modes or reusable patterns |

Runtime code may load only CERA-owned, installed, validated resources. It may never read a provenance path as a live dependency.

## 2. Creator sources

| Source | SHA-256 | Qualification |
|---|---|---|
| Documentation-only authorization attachment `d946.../pasted-text.txt` | `6125640ac00ebef988be2b6ddbbf9515c33e1c5a14281d5d11a2efe81022dd02` | `CREATOR_DECISION`; authorizes these documents only |
| Original Pro package attachment `8c14.../pasted-text.txt` | `585ea1402b35161a33c675ecc272c093cf2cbfa471cce91cd4484bfb43f37b6c` | `AUDITED_REFERENCE`; paths and architecture partly superseded |
| Draft Hana profile attachment `3add.../pasted-text.txt` | `99f2d2116ce1066e9d16f428b2f295c5c25f287038895f5bc4ff35017d6ecd85` | Mixed reference; objective corrections retained, trajectories/examples rejected as canon |
| Draft middleman attachment `db83.../pasted-text.txt` | `4432fac0ad7f150418023780e323e7dd754a9aee466e6a19e44d5033524a6eef` | `AUDITED_REFERENCE`; consent-valid split retained, direct/opaque handler behavior replaced by receipt contract |
| `genesis/cera_authority/HANEZAWA_CORE_GENESIS_V1_1.md` | `edf16b1204a0b8c656b0418e39b41c3306a995dc70a324c939b507483f783053` | `CREATOR_DECISION`; immutable human-readable V1.1 source compiled under D-035 |
| `genesis/cera_authority/HANEZAWA_CORE_GENESIS_V1_2.md` | `76b43c58f6e2c2c902a8af49027c3f9356d6f45078f8b88bec9ba39b11f9fd4a` | `CREATOR_DECISION`; immutable complete V1.2 child source installed under D-134 |
| `genesis/cera_authority/HANEZAWA_VISUAL_CANON_ANIMA_V1.json` | `58cbb3231ce5d09434f830b57f430171ff69479848b0795f40f92a86407d6da9` | `CREATOR_DECISION`; immutable machine-readable visual source, subject to D-038/D-039 prompt overlay |
| `genesis/cera_authority/ANIMA_PROMPT_POLICY_CREATOR_OVERLAY_V1.json` | `43680759edf68e57954bfc0c735525cfaba135f5c2594977a84f5804c7de12d4` | `CREATOR_DECISION`; later prompt-policy and Yuuni visual-routing correction |
| `genesis/provenance/hanezawa_v1_2_package/HANEZAWA_CORE_GENESIS_V1_2_COMPLETE_PACKAGE.zip` | `2dae15af672af68fb800ad6c5aa9a285d5113add3f4cabbe85226cce56ef68a4` | `CREATOR_DECISION` package provenance; immutable archive, never a runtime dependency |
| `config/cera/prompts/character_specific_speech_realization_v1_2.txt` | `e61c8b9a287e6be1223e4a2a2aebcbd8d5da5e5433bee8de781d833149cc11cf` | Creator-approved advisory prompt source; bound to the v5 Composer prompt without runtime file access |
| Creator decisions in the active conversation | n/a | `CREATOR_DECISION`; consolidated in decision register and creator facts |

Attachment paths and provenance archives are audit evidence only and must not
be runtime dependencies. V1.1 remains immutable prior-version evidence.
Runtime evidence uses only a validated, manifest-governed JSON projection of
the selected revision.

## 3. Audited staged architecture

Repository: `E:\AIChatBot\RPProxy_DeepSeek_Direct`

Reviewed:

- `docs/CERA_MASTER_ARCHITECTURE_LESSONS.md`
- `docs/CERA_COMPLETION_ROADMAP.md`
- every document under `docs/cera` named by its README;
- every staged source under `config/cera`, including the source manifest and adult-example manifest.

The staged package contributes:

- useful source/decision/writer separation;
- protected-user, cast, privacy, event coverage, and state-integration lessons;
- role-specific evaluation structure;
- example-versus-evaluation partitioning;
- exact-copy provenance practice;
- restart/replay/regeneration requirements.

It does not control:

- active repository path;
- live baseline preservation;
- old component names;
- automatic Detailer activation;
- direct non-consensual realization;
- fallback policy;
- runtime transport assumptions.

## 4. Historical implementation references

- `D:\AIChatBot\Vera_v2_d3`
- other Vera/V6 repositories
- `E:\AIChatBot\Sera`
- `E:\AIChatBot\RPProxy_DeepSeek_Direct`

The D-177 dialogue-readability adaptation specifically audited these Sera
files:

- `E:\AIChatBot\Sera\sillytavern_extension\index.js`, SHA-256
  `5980f0c7d0125388e15ed8ca353b05a1be93c1ea252fd9f0240381ad8ed88dbe`;
- `E:\AIChatBot\Sera\sillytavern_extension\style.css`, SHA-256
  `3d5256d2bea84598e6befbc78e0aa0d3827101c9b2611fa1fc2b20fa57f9265e`.

Only the presentation lesson was adapted into CERA-owned code. The active
extension reads CERA's card-local seven-character palette and has no Sera
runtime dependency.

They may be audited during a separately authorized implementation phase. Reuse requires:

1. exact source path and hash;
2. license/ownership suitability where applicable;
3. a statement of what invariant it satisfies;
4. adaptation into the CERA-owned repository;
5. tests against current contracts;
6. no runtime dependency back to the reference path.

## 5. Adult example status

The separately authorized provider-free Adult ON/EX gate preserved 24 audited Sera artifacts byte-for-byte under `adult/provenance/sera_adult_source_v1/`. `SOURCE_INTEGRITY.json` binds every relative path, byte count, and SHA-256; its source-integrity hash is `d2fce7f452e0c8149641bd573beee65904229fdc6bee82073b954818eb3f0d38`.

The preserved source set is provenance, never runtime authority. `adult/catalog/adult_craft_v1/CURATION.json` records the section-level disposition. Two sources are provenance-only, one coverage file is audit-only, and approved source sections compile deterministically into 82 CERA-owned `cera.adult_craft_fragment.v1` records. The catalog manifest has ID `craft_catalog:cac0c3b1-7ed3-5d98-8f05-b6ca39101de4` and domain hash `5bb4476d5dc2091a54af3c6941a1bb5d663024143d8e74503af784021c1a9767`.

Compiled fragments are craft-only and excluded from evaluation evidence. They cannot establish character facts, current acts, consent, climax, aftermath, or story truth. Obsolete Director/Detailer ownership and character-authority material remain retired. Blocked non-consensual generation is excluded, and the manifest contains no E-drive runtime dependency.

## 6. Installed Hanezawa V1.1 compilation

- source integrity record: `genesis/cera_authority/SOURCE_INTEGRITY.json`;
- creator authorization envelope: `genesis/authorizations/hanezawa_core_v1_1.json`;
- authoritative compiled package: `genesis/packages/hanezawa_core_v1_1/`;
- generated read-only views: `genesis/generated/hanezawa_core_v1_1/`;
- manifest SHA-256: `99e070eb66e926fc38a53102db1fd1915ae06d672157d2672de9f83b9907c6dc`;
- compiled bundle SHA-256: `f4663b67b868f454159026670575d2508deb9ea866d88564a629050477b5ae4b`;
- no production-world binding, provider call, Adult EX import, or story-database seed occurred.

The compiled package contains later creator corrections as typed records. Conflicting V1.1 source statements remain preserved in the immutable artifact and supersession ledger but are not simultaneously active runtime truth.

The current hashes supersede the historical Phase 3 projection after provider-free integration exposed and corrected section-expansion and unequal-knowledge visibility defects. The three immutable creator source hashes are unchanged.

## 7. Provenance for generated views

Future Markdown cards, master indexes, summaries, and search documents must include:

- generator version;
- generation time;
- authoritative input record IDs and hashes;
- branch scope;
- privacy/knowledge view;
- “generated, not authority” status.

If a generated view conflicts with an authoritative record, the view is discarded and rebuilt.

## 7A. Installed Hanezawa V1.2 child revision

- package provenance integrity:
  `genesis/provenance/hanezawa_v1_2_package/PACKAGE_INTEGRITY.json`;
- source integrity:
  `genesis/cera_authority/SOURCE_INTEGRITY_V1_2.json`;
- creator authorization:
  `genesis/authorizations/hanezawa_core_v1_2.json`;
- compiled package:
  `genesis/packages/hanezawa_core_v1_2/`;
- generated read-only views:
  `genesis/generated/hanezawa_core_v1_2/`;
- manifest SHA-256:
  `fb7a2186617717170539a0940a4b13bd4a1f48a1162d81a8cef375be33144471`;
- compiled bundle SHA-256:
  `a02751faa9a7b67676f4496a670cc2ac69e915232386feb26cd9c17feba67c32`.

V1.2 is revision 2 with immutable V1.1 as its parent. Its 637 compiled
records include 79 events, 84 owner memories, seven embodied-identity
profiles, 77 response-mode records, and 154 explicitly non-executable style
examples. Two V1.2 correction records supersede same-revision ledger entries,
leaving 635 active child records after all 503 active V1.1 records are retired.
The old revision and hashes remain readable history.

## 8. Evaluation provenance

Phase 10 evaluation manifests record only repository-owned case/expectation/provenance hashes. Holdout cases are sealed and never selected as prompt examples or craft context. Known Adult EX or other craft-asset hashes must appear in the suite's exclusion set and cannot overlap any case input, expected contract, or provenance hash.

Disposable real-Genesis evaluation may cite an installed V1.1 or V1.2
manifest and bundle hash. That establishes which creator package supported
the test; it does not convert temporary output into production story truth or
live-provider qualification.
