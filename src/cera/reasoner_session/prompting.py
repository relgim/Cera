"""Shadow-only split of stable Reasoner instructions from variable turn data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from cera.errors import ContractValidationError
from cera.reasoner.codex import build_codex_reasoner_prompt
from cera.serialization import canonical_json, domain_sha256, text_sha256


_PACKET_MARKER = "The complete authoritative packet follows as canonical JSON:"


@dataclass(frozen=True, slots=True)
class ReasonerSessionPromptCompilation:
    """Exact decomposition; recombination must equal the active prompt."""

    SCHEMA_VERSION: ClassVar[str] = "cera.reasoner_session_prompt_compilation.v1"

    schema_version: str
    stable_instructions: str
    stable_instructions_sha256: str
    variable_turn_prompt: str
    variable_turn_prompt_sha256: str
    packet_sha256: str
    provider_schema_sha256: str
    legacy_full_prompt_sha256: str

    def __post_init__(self) -> None:
        if self.schema_version != self.SCHEMA_VERSION:
            raise ContractValidationError("session prompt compilation schema changed")
        for text, digest, label in (
            (
                self.stable_instructions,
                self.stable_instructions_sha256,
                "stable instructions",
            ),
            (
                self.variable_turn_prompt,
                self.variable_turn_prompt_sha256,
                "variable turn prompt",
            ),
        ):
            if not text.strip() or text_sha256(text) != digest:
                raise ContractValidationError(f"{label} hash mismatch")
        for value in (
            self.packet_sha256,
            self.provider_schema_sha256,
            self.legacy_full_prompt_sha256,
        ):
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise ContractValidationError("session prompt digest is invalid")
        if not self.variable_turn_prompt.startswith(_PACKET_MARKER + "\n"):
            raise ContractValidationError("variable turn prompt lost its authority marker")

    @property
    def compilation_sha256(self) -> str:
        return domain_sha256(self.SCHEMA_VERSION, self)

    @property
    def recombined_prompt(self) -> str:
        return self.stable_instructions + "\n" + self.variable_turn_prompt


def compile_shadow_reasoner_session_prompt(
    packet: dict[str, object],
    *,
    provider_schema: dict[str, object],
) -> ReasonerSessionPromptCompilation:
    """Split the active prompt without changing one byte of its current meaning."""

    full_prompt = build_codex_reasoner_prompt(packet)
    delimiter = "\n" + _PACKET_MARKER + "\n"
    if full_prompt.count(delimiter) != 1:
        raise ContractValidationError("active Reasoner prompt marker is ambiguous")
    stable, packet_json = full_prompt.split(delimiter, 1)
    expected_packet_json = canonical_json(packet)
    if packet_json != expected_packet_json:
        raise ContractValidationError("active Reasoner prompt changed packet serialization")
    variable = _PACKET_MARKER + "\n" + packet_json
    compilation = ReasonerSessionPromptCompilation(
        schema_version=ReasonerSessionPromptCompilation.SCHEMA_VERSION,
        stable_instructions=stable,
        stable_instructions_sha256=text_sha256(stable),
        variable_turn_prompt=variable,
        variable_turn_prompt_sha256=text_sha256(variable),
        packet_sha256=text_sha256(packet_json),
        provider_schema_sha256=domain_sha256(
            "cera.provider_reasoner_schema_projection.v1", provider_schema
        ),
        legacy_full_prompt_sha256=text_sha256(full_prompt),
    )
    if compilation.recombined_prompt != full_prompt:
        raise ContractValidationError("session prompt split changed active prompt bytes")
    return compilation
