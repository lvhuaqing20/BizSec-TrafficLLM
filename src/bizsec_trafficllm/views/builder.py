from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .budget import PreTokenBudgetManager
from .errors import ViewConstructionError
from .selector import RepresentationSelector
from .validation import ViewValidator


GRANULARITY = {
    "packet": "packet",
    "http_request": "request",
    "direction_sequence": "direction_sequence",
}
VIEW_PROFILE = {
    "business": ("business-view-v1", "business_classification"),
    "detection": ("detection-view-v1", "attack_detection"),
    "attack_type": ("attack-type-view-v1", "attack_type_classification"),
}


class ViewEngine:
    def __init__(
        self,
        schema_root: Path,
        selection_policy: Mapping[str, Any],
        token_policy: Mapping[str, Any],
    ) -> None:
        self._selector = RepresentationSelector(selection_policy)
        self._budget = PreTokenBudgetManager(token_policy)
        self._validator = ViewValidator(schema_root)

    @staticmethod
    def _business_prior(value: Optional[Mapping[str, Any]]) -> Optional[Dict[str, str]]:
        if value is None:
            return None
        if set(value) != {"business_domain", "business_type"}:
            raise ViewConstructionError(
                "invalid_business_prior",
                "business prior must contain exactly business_domain and business_type",
            )
        domain = value.get("business_domain")
        business_type = value.get("business_type")
        if domain not in {"application", "website", "network_behavior", "unknown"}:
            raise ViewConstructionError("invalid_business_prior", f"invalid business_domain: {domain}")
        if not isinstance(business_type, str) or not business_type:
            raise ViewConstructionError("invalid_business_prior", "business_type must be non-empty")
        return {"business_domain": domain, "business_type": business_type}

    def build(
        self,
        sample: Mapping[str, Any],
        task: str,
        business_prior: Optional[Mapping[str, Any]] = None,
        security_context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        if task not in VIEW_PROFILE:
            raise ViewConstructionError("unknown_task", task)
        if task == "business" and business_prior is not None:
            raise ViewConstructionError("business_prior_forbidden", "Business View cannot receive a prior")
        representation_type, representation, used_fallback = self._selector.select(sample, task)
        bounded_representation, budget_warnings = self._budget.apply(task, representation)
        quality = sample.get("quality", {})
        warnings = list(quality.get("warnings", [])) + budget_warnings
        if used_fallback:
            warnings.append("view_used_non_primary_representation")
        view_version, task_name = VIEW_PROFILE[task]
        view: Dict[str, Any] = {
            "view_version": view_version,
            "task": task_name,
            "sample_id": sample.get("sample_id"),
            "granularity": GRANULARITY[representation_type],
            "traffic": {
                "representation": bounded_representation,
                "statistics": copy.deepcopy(sample.get("traffic", {}).get("statistics")),
            },
            "context": {},
            "priors": {},
            "quality": {
                "parse_status": quality.get("parse_status"),
                "source_representation": representation_type,
                "missing_fields": list(quality.get("missing_fields", [])),
                "warnings": list(dict.fromkeys(warnings)),
            },
        }
        if task == "business":
            context = sample.get("context", {})
            view["context"] = {
                "asset_type": context.get("asset_type"),
                "service_name": context.get("service_name"),
            }
        else:
            security = dict(security_context or {})
            rule_hits = security.get("rule_hits", [])
            if not isinstance(rule_hits, list) or any(not isinstance(item, str) or not item for item in rule_hits):
                raise ViewConstructionError("invalid_security_context", "rule_hits must be non-empty strings")
            view["context"] = {"security": {"rule_hits": list(dict.fromkeys(rule_hits))}}
            if task == "detection":
                threat_intel_hit = security.get("threat_intel_hit")
                if threat_intel_hit not in (True, False, None):
                    raise ViewConstructionError(
                        "invalid_security_context", "threat_intel_hit must be boolean or null"
                    )
                view["context"]["security"]["threat_intel_hit"] = threat_intel_hit
            view["priors"] = {"business": self._business_prior(business_prior)}
        self._validator.validate(task, view)
        return view

    def build_business(self, sample: Mapping[str, Any]) -> Dict[str, Any]:
        return self.build(sample, "business")

    def build_detection(
        self,
        sample: Mapping[str, Any],
        business_prior: Optional[Mapping[str, Any]],
        security_context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.build(sample, "detection", business_prior, security_context)

    def build_attack_type(
        self,
        sample: Mapping[str, Any],
        business_prior: Optional[Mapping[str, Any]],
        security_context: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.build(sample, "attack_type", business_prior, security_context)
