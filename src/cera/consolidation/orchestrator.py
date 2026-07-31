"""Stable Phase 9 consolidation facade."""

from .fake import *
from .models import *
from .validator import ConsolidationValidator

__all__ = [name for name in globals() if not name.startswith("_")]
