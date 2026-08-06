from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
BASE_BUILDER = (
    ROOT
    / ".chatgpt"
    / "pf"
    / "q0063_sequence_first_stage3_live_v12"
    / "governed_runner_builder_v12.py"
)
BASE_BUILDER_SHA256 = (
    "5972f1f72b2fd827c44f9822677b1144512c1fbc4efbc2a7c730808a0316c856"
)


def _replace_once(source: str, old: str, new: str) -> str:
    if source.count(old) != 1:
        raise RuntimeError(f"V14 builder expected one frozen role line: {old!r}")
    return source.replace(old, new)


if sha256(BASE_BUILDER.read_bytes()).hexdigest() != BASE_BUILDER_SHA256:
    raise RuntimeError("immutable V12 governed runner builder changed")
_source = BASE_BUILDER.read_text(encoding="utf-8")
_source = _replace_once(
    _source,
    'ProviderRoleAuthorityV1("planner", "cera_sequence_first_planner_sol_medium_v7", "gpt-5.6-sol", "medium", "cera.sequence_first.planner_adapter.v7", "cera.sequence_first.planner_prompt.v5", "cera.sequence_first.sequence_draft.v4", "8ffe4f5a78170e1f41bc3f5c58c6e7d232b67df91f8507b1481ada028042dd8b"),',
    'ProviderRoleAuthorityV1("planner", "cera_sequence_first_planner_sol_medium_v8", "gpt-5.6-sol", "medium", "cera.sequence_first.planner_adapter.v8", "cera.sequence_first.planner_prompt.v6", "cera.sequence_first.sequence_draft.v4", "5d5610248aa77223fac8435c257b61ff1843efd4ac192e3576a87c19ce384fd1"),',
)
_source = _replace_once(
    _source,
    'ProviderRoleAuthorityV1("validator", "cera_sequence_first_validator_gpt-5.6-sol_medium_v9", "gpt-5.6-sol", "medium", "cera.sequence_first.validator_adapter.v9", "cera.sequence_first.validator_prompt.v7", "cera.sequence_first.validator_decision.v3", "2ed985ba658f62a13af89d06a58415d3e6289727bef00aeb95b03c308f8e40b5"),',
    'ProviderRoleAuthorityV1("validator", "cera_sequence_first_validator_gpt-5.6-sol_medium_v11", "gpt-5.6-sol", "medium", "cera.sequence_first.validator_adapter.v11", "cera.sequence_first.validator_prompt.v9", "cera.sequence_first.validator_decision.v3", "1e16700fe2c77045c49aa9e54dfdfd414af81eddbe1f3417f2c075d77e92c73b"),',
)
_namespace: dict[str, Any] = {
    "__file__": str(BASE_BUILDER),
    "__name__": "cera_q0063_v14_governed_runner_builder",
}
exec(compile(_source, str(BASE_BUILDER), "exec"), _namespace)

build_runner_namespace = _namespace["build_runner_namespace"]
prove_source = _namespace["prove_source"]
