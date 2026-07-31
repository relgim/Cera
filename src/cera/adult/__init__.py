"""Synthetic-only Phase 7 consent-valid adult-route contracts."""

from .models import *
from .route import AdultContextSelector, AdultRouteCoordinator, AdultRouteFailure
from .fake import *
from .orchestrator import (
    AdultMechanicsCoordinator,
    AdultMechanicsFailure,
    build_adult_composer_binding,
)

__all__ = [name for name in globals() if not name.startswith("_")]
