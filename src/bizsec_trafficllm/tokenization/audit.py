"""Streaming token-length audit for ChatGLM2 training messages."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, Mapping, MutableMapping

from .chatglm2 import ChatGLM2FeatureAdapter


QUANTILES = (0.5, 0.9, 0.95, 0.99)


def _percentile(sorted_values, quantile: float) -> int:
    if not sorted_values:
        return 0
    index = max(0, math.ceil(quantile * len(sorted_values)) - 1)
    return int(sorted_values[index])


def _summary(values) -> Dict[str, Any]:
    ordered = sorted(values)
    if not ordered:
        return {"min": 0, "max": 0, "mean": 0.0, "p50": 0, "p90": 0, "p95": 0, "p99": 0}
    return {
        "min": ordered[0],
        "max": ordered[-1],
        "mean": round(sum(ordered) / len(ordered), 3),
        **{f"p{int(q * 100)}": _percentile(ordered, q) for q in QUANTILES},
    }


def audit_messages(
    records: Iterable[Mapping[str, Any]],
    tokenizer: Any,
    task_limits: Mapping[str, Mapping[str, int]],
) -> Dict[str, Any]:
    """Audit raw token lengths and truncation risk without materializing features."""

    lengths: MutableMapping[str, MutableMapping[str, list]] = defaultdict(
        lambda: defaultdict(list)
    )
    truncations: MutableMapping[str, Counter] = defaultdict(Counter)
    counts = Counter()
    adapters = {
        task: ChatGLM2FeatureAdapter(
            tokenizer,
            max_source_length=int(limits["max_source_length"]),
            max_target_length=int(limits["max_target_length"]),
        )
        for task, limits in task_limits.items()
    }

    for record in records:
        task = record.get("task")
        if task not in adapters:
            raise ValueError(f"missing token limits for task {task!r}")
        raw = adapters[task].raw_lengths(record)
        counts[task] += 1
        for name, value in raw.items():
            lengths[task][name].append(value)
        if raw["source_tokens"] > adapters[task].max_source_length:
            truncations[task]["source"] += 1
        if raw["target_tokens"] > adapters[task].max_target_length:
            truncations[task]["target"] += 1

    task_reports = {}
    for task in sorted(counts):
        count = counts[task]
        task_reports[task] = {
            "records": count,
            "configured_limits": dict(task_limits[task]),
            "lengths": {
                name: _summary(values) for name, values in sorted(lengths[task].items())
            },
            "truncation": {
                "source_records": truncations[task]["source"],
                "source_rate": round(truncations[task]["source"] / count, 8),
                "target_records": truncations[task]["target"],
                "target_rate": round(truncations[task]["target"] / count, 8),
            },
        }
    return {
        "records": sum(counts.values()),
        "task_counts": dict(sorted(counts.items())),
        "tasks": task_reports,
    }
