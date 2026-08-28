from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(frozen=True)
class ParsedTraffic:
    representation_type: str
    representation: Dict[str, Any]
    missing_fields: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    privacy_transforms: List[str] = field(default_factory=list)

    @property
    def parse_status(self) -> str:
        return "partial" if self.missing_fields else "ok"
