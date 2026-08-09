"""Public cumulative branch-state reducer/checkpoint interface.

Accepted receipts are canonical prose ancestry even while Recorder work is
pending.  Derived state advances only from complete ordinary records or the
non-explicit adult projection.  Explicit provisional canon is a separate
lower-confidence ID lineage.  Recent prose and provider sessions never act as
state authority.
"""

from ._branch_state_models import (
    AcceptedBranchContextSource,
    AcceptedBranchEventV1,
    AcceptedBranchPayloadSource,
    AcceptedLineageEntryV1,
    AdultPublicContinuityV1,
    BranchLineageHopV1,
    BranchStateCheckpointV1,
    BranchStateRecordV1,
    DurableBranchChangeV1,
    GenesisBranchStateV1,
    PresenceChangeV1,
    ProvisionalCanonLineageEntryV1,
)
from ._branch_state_reducer import BranchStateReducerV1

__all__ = [
    "AcceptedBranchContextSource",
    "AcceptedBranchEventV1",
    "AcceptedBranchPayloadSource",
    "AcceptedLineageEntryV1",
    "AdultPublicContinuityV1",
    "BranchLineageHopV1",
    "BranchStateCheckpointV1",
    "BranchStateRecordV1",
    "BranchStateReducerV1",
    "DurableBranchChangeV1",
    "GenesisBranchStateV1",
    "PresenceChangeV1",
    "ProvisionalCanonLineageEntryV1",
]
