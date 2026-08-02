# Continuous Physical-Thread Lifecycle V3 Result

## Outcome

The continuous shadow route now owns every physical Planner and Validator
thread it touches through one closed Python lineage. A thread cannot disappear
between provider creation and terminal publication: it is either the explicitly
authorized active route handle or is archived with verified resume failure,
backend non-selectability, and coordinator non-selectability.

## Lineage contract

`ContinuousThreadLineageLedger` registers primary, resumed, provider-forked,
and reconstructed threads at the physical transport seam. Each immutable entry
binds:

- Planner or Validator role and closed purpose;
- world, branch, and session-compatibility identity;
- provider-thread hash and exact parent relationship;
- create, resume, fork, or reconstruction operation;
- resume, adoption, supersession, abandonment, and archive lifecycle events;
  and
- exactly one authorized-active or verified-archived terminal disposition.

The frozen `cera.continuous_thread_lineage_receipt.v1` derives its own status
and failure codes. Duplicate, unknown, self-parented, wrong-role, wrong-world,
same-branch fork, orphaned, contradictory, unresolved, resumable, selectable,
or unverified archive evidence fails closed. Archive evidence is copied into a
deeply immutable typed record before the ledger freezes.

## Fork, adoption, reconstruction, and cleanup

A physical fork child is registered before summary-delivery reconstruction.
Every later setup failure archives and verifies that child, including failures
after branch validation, descriptor append, either accepted-reference save,
and before or after snapshot persistence. A successful child is explicitly
adopted after its parent is archived, or explicitly classified as auxiliary
and terminally archived.

Reconstruction registers the replacement immediately after physical creation.
Failures during summary reconstruction, reference persistence, or adoption
archive the replacement. Successful reconstruction archives the superseded
Planner, records the parent/child adoption relation, and changes route ownership
only after those checks pass. Partial primary-thread startup is also covered:
if the Planner exists but Validator construction fails, the Planner is still
archived and represented in the frozen lineage.

## Terminal and recovery custody

`cera.continuous_job4_terminal_evidence.v5` additively retains all V4 root
transaction, nine-port capability, canonical-effect, archive, immutable
publication, completion, and recovery behavior while binding the complete
thread-lineage receipt. Terminal Job 4 permits no authorized active thread and
requires each verified primary archive DTO to resolve to the same archived
lineage entry. Historical terminal V1 through V4 artifacts remain decodeable
and unchanged.

Restart reads and republishes frozen lineage evidence through the existing
terminal transaction path. It does not reconstruct coordinators, repeat
semantic work, or invoke a provider.

## Provider-free verification

The dedicated matrix covers exact ledger round-trip and tamper rejection,
every required fork and reconstruction cut, both accepted-reference save
positions, explicit child adoption, auxiliary cleanup, archive-request,
resume-verification and selectability failures, partial startup, integrated
initial/fork/reconstruction/final/Validator reconciliation, terminal V5, and
restart non-reentry.

Retained branch-materialization, lean-context, Planner/Validator, world, Job 4,
and repository-cycle tests continue to pass. This work makes zero external
provider calls and changes no story or production database, active route,
service, installed SillyTavern, deployment, merge, remote, or push state.
