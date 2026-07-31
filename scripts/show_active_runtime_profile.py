"""Print and validate the canonical active CERA runtime profile."""

from __future__ import annotations

import json

from cera.active_runtime_validation import active_runtime_status


if __name__ == "__main__":
    print(json.dumps(active_runtime_status(), indent=2, sort_keys=True))
