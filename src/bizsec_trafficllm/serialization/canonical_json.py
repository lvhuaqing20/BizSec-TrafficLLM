from __future__ import annotations

import json
from typing import Any


def dumps_canonical(value: Any) -> str:
    """Serialize JSON deterministically without model-specific tokens."""

    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
