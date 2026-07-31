"""Transactional storage API."""

from .models import (
    AuthorityRecord,
    BranchState,
    CommitMode,
    JournalEntry,
    JournalStatus,
    PostPublicationWork,
    PostPublicationWorkKind,
    PostPublicationWorkRequest,
    PostPublicationWorkStatus,
    RecoveryReport,
    ReceiptCategory,
    ReceiptRecord,
    SourceRecord,
    StoredCommit,
    TurnCommitBundle,
)
__all__ = [
    "AuthorityRecord",
    "BranchState",
    "CommitMode",
    "JournalEntry",
    "JournalStatus",
    "PostPublicationWork",
    "PostPublicationWorkKind",
    "PostPublicationWorkRequest",
    "PostPublicationWorkStatus",
    "RecoveryReport",
    "ReceiptCategory",
    "ReceiptRecord",
    "SourceRecord",
    "StoredCommit",
    "TurnCommitBundle",
    "SQLiteAuthorityStore",
]


def __getattr__(name: str):
    if name == "SQLiteAuthorityStore":
        from .sqlite_store import SQLiteAuthorityStore

        return SQLiteAuthorityStore
    raise AttributeError(name)
