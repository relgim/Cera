"""Phase-safe configuration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar, Mapping

from .errors import ConfigurationError
from .schema import from_mapping, require_schema


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


@dataclass(frozen=True, slots=True)
class FoundationConfig:
    SCHEMA_VERSION: ClassVar[str] = "cera.foundation_config.v1"

    schema_version: str
    environment: Environment
    project_root: str
    maximum_packet_bytes: int = 131_072
    maximum_evidence_hits: int = 64
    live_provider_calls_enabled: bool = False
    reference_runtime_dependencies_enabled: bool = False

    def __post_init__(self) -> None:
        require_schema(self.schema_version, self.SCHEMA_VERSION, type(self).__name__)
        if not self.project_root.strip():
            raise ConfigurationError("project_root must be non-empty")
        if not Path(self.project_root).is_absolute():
            raise ConfigurationError("project_root must be absolute")
        if self.maximum_packet_bytes < 4096:
            raise ConfigurationError("maximum_packet_bytes must be at least 4096")
        if not 1 <= self.maximum_evidence_hits <= 512:
            raise ConfigurationError("maximum_evidence_hits must be between 1 and 512")
        if self.live_provider_calls_enabled:
            raise ConfigurationError("foundation configuration cannot enable live provider calls")
        if self.reference_runtime_dependencies_enabled:
            raise ConfigurationError(
                "reference repositories cannot be enabled as runtime dependencies"
            )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "FoundationConfig":
        return from_mapping(cls, payload)

