"""Convert held-out Messages records and summarize Adapter predictions."""

from __future__ import annotations

import json
import hashlib
from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Mapping, Tuple


SUPPORTED_TASKS = {"business", "detection", "attack_type"}


class AdapterEvaluationError(ValueError):
    """Raised when an evaluation record or prediction is malformed."""


def _reject_v2_path(path: Path) -> None:
    if any("v2" in part.lower() for part in path.parts):
        raise AdapterEvaluationError(f"refusing excluded v2 path: {path}")


def iter_test_records(messages_root: Path, task: str):
    """Stream the held-out Messages v1 test split for final evaluation only."""

    if task not in SUPPORTED_TASKS:
        raise AdapterEvaluationError(f"unsupported task: {task!r}")
    root = Path(messages_root)
    _reject_v2_path(root)
    if not root.is_dir():
        raise AdapterEvaluationError(f"messages root does not exist: {root}")
    paths = sorted(root.glob("*/test.jsonl"))
    if not paths:
        raise AdapterEvaluationError(f"no test.jsonl files found under: {root}")
    for path in paths:
        _reject_v2_path(path)
        if not path.is_file() or path.stat().st_size == 0:
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise AdapterEvaluationError(
                        f"invalid JSON at {path}:{line_number}: {exc}"
                    ) from exc
                if record.get("task") != task:
                    raise AdapterEvaluationError(
                        f"task mismatch at {path}:{line_number}: "
                        f"{record.get('task')!r} != {task!r}"
                    )
                metadata = record.get("metadata")
                if (
                    not isinstance(metadata, Mapping)
                    or metadata.get("split") != "test"
                ):
                    raise AdapterEvaluationError(
                        f"final evaluation only reads test records: {path}:{line_number}"
                    )
                yield record


def training_record_to_inference(
    record: Mapping[str, Any],
    evaluation_partition: str = "deterministic_validation",
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    task = record.get("task")
    if task not in SUPPORTED_TASKS:
        raise AdapterEvaluationError(f"unsupported task: {task!r}")
    messages = record.get("messages")
    if not isinstance(messages, list) or len(messages) != 3:
        raise AdapterEvaluationError("training record must contain three messages")
    roles = [message.get("role") for message in messages if isinstance(message, Mapping)]
    if roles != ["system", "user", "assistant"]:
        raise AdapterEvaluationError(f"unexpected message roles: {roles!r}")
    try:
        expected = json.loads(messages[2]["content"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AdapterEvaluationError(f"invalid assistant target: {exc}") from exc
    if not isinstance(expected, dict):
        raise AdapterEvaluationError("assistant target must be a JSON object")
    request = {
        "message_version": "inference-message-request-v1",
        "sample_id": record.get("sample_id"),
        "task": task,
        "messages": [dict(messages[0]), dict(messages[1])],
        "metadata": {
            "template_version": (record.get("metadata") or {}).get("template_version"),
            "evaluation_partition": evaluation_partition,
        },
    }
    return request, expected


def _primary_label(task: str, value: Mapping[str, Any]) -> Any:
    if task == "business":
        return value.get("business_type")
    if task == "detection":
        return value.get("is_attack")
    if task == "attack_type":
        return value.get("attack_type")
    raise AdapterEvaluationError(f"unsupported task: {task!r}")


def _stable_rank(seed: int, namespace: str, value: Any) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{seed}:{namespace}:{payload}".encode("utf-8")).digest()


def select_balanced_records(
    task: str,
    records: Iterable[Mapping[str, Any]],
    limit: int,
    seed: int,
) -> List[Mapping[str, Any]]:
    """Choose a deterministic label-balanced subset from a held-out partition."""

    if task not in SUPPORTED_TASKS:
        raise AdapterEvaluationError(f"unsupported task: {task!r}")
    if limit <= 0:
        raise AdapterEvaluationError("limit must be positive")
    groups: DefaultDict[Any, List[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        record_task = record.get("task")
        if record_task != task:
            raise AdapterEvaluationError(
                f"record task mismatch: {record_task!r} != {task!r}"
            )
        _, expected = training_record_to_inference(record)
        label = _primary_label(task, expected)
        if label is None:
            raise AdapterEvaluationError("primary label cannot be null")
        groups[label].append(record)
    total = sum(len(group) for group in groups.values())
    if total < limit:
        raise AdapterEvaluationError(
            f"requested {limit} validation records, found {total}"
        )
    labels = sorted(groups, key=lambda label: _stable_rank(seed, "label", label))
    for label, group in groups.items():
        group.sort(
            key=lambda record: _stable_rank(seed, "sample", record.get("sample_id"))
        )
    selected: List[Mapping[str, Any]] = []
    offset = 0
    while len(selected) < limit:
        made_progress = False
        for label in labels:
            group = groups[label]
            if offset < len(group):
                selected.append(group[offset])
                made_progress = True
                if len(selected) == limit:
                    break
        if not made_progress:
            break
        offset += 1
    if len(selected) != limit:
        raise AdapterEvaluationError(
            f"balanced selection produced {len(selected)} of {limit} records"
        )
    return selected


def _multiclass_metrics(expected, predicted) -> Dict[str, Any]:
    classes = sorted(set(expected), key=lambda value: str(value))
    per_class = {}
    f1_values = []
    for label in classes:
        tp = sum(e == label and p == label for e, p in zip(expected, predicted))
        fp = sum(e != label and p == label for e, p in zip(expected, predicted))
        fn = sum(e == label and p != label for e, p in zip(expected, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[str(label)] = {
            "support": sum(e == label for e in expected),
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
        f1_values.append(f1)
    return {
        "classes": len(classes),
        "macro_f1": sum(f1_values) / len(f1_values) if f1_values else None,
        "per_class": per_class,
    }


def summarize_adapter_predictions(
    task: str, rows: Iterable[Mapping[str, Any]]
) -> Dict[str, Any]:
    if task not in SUPPORTED_TASKS:
        raise AdapterEvaluationError(f"unsupported task: {task!r}")
    items = list(rows)
    if not items:
        raise AdapterEvaluationError("evaluation rows cannot be empty")
    schema_valid = sum(bool(item.get("schema_valid")) for item in items)
    exact_matches = sum(
        item.get("schema_valid") and item.get("prediction") == item.get("expected")
        for item in items
    )
    expected_labels = [_primary_label(task, item["expected"]) for item in items]
    predicted_labels = [
        _primary_label(task, item["prediction"])
        if item.get("schema_valid") and isinstance(item.get("prediction"), Mapping)
        else "__invalid__"
        for item in items
    ]
    primary_correct = sum(
        expected == predicted
        for expected, predicted in zip(expected_labels, predicted_labels)
    )
    summary = {
        "samples": len(items),
        "schema_valid": schema_valid,
        "schema_valid_rate": schema_valid / len(items),
        "exact_output_matches": exact_matches,
        "exact_output_accuracy": exact_matches / len(items),
        "primary_label_correct": primary_correct,
        "primary_label_accuracy": primary_correct / len(items),
        "multiclass": _multiclass_metrics(expected_labels, predicted_labels),
    }
    if task == "detection":
        tp = sum(e is True and p is True for e, p in zip(expected_labels, predicted_labels))
        tn = sum(e is False and p is False for e, p in zip(expected_labels, predicted_labels))
        fp = sum(e is False and p is True for e, p in zip(expected_labels, predicted_labels))
        fn = sum(e is True and p is not True for e, p in zip(expected_labels, predicted_labels))
        invalid = sum(p == "__invalid__" for p in predicted_labels)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        summary["binary"] = {
            "tp": tp,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "invalid": invalid,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
    return summary
