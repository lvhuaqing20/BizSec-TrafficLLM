"""Runtime protocols for pluggable task adapters and risk fusion."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Protocol


class AdapterBackend(Protocol):
    """Predict one validated task request using a model-specific backend."""

    @property
    def name(self) -> str:
        ...

    def predict(self, task: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        ...


class RiskFusionBackend(Protocol):
    """Produce risk fields after the gated adapter decisions are available."""

    @property
    def name(self) -> str:
        ...

    def fuse(
        self,
        *,
        business_output: Mapping[str, Any],
        detection_output: Mapping[str, Any],
        attack_type_output: Optional[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        ...
