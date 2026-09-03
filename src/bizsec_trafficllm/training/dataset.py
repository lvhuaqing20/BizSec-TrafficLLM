"""Read Messages v1 records and build deterministic training batches."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, Iterator, List, Mapping, Optional, Set, Tuple


SUPPORTED_TASKS = {"business", "detection", "attack_type"}
SUPPORTED_PARTITIONS = {"train", "validation", "all"}
PRIMARY_LABEL_FIELDS = {
    "business": "business_type",
    "detection": "is_attack",
    "attack_type": "attack_type",
}


class TrainingDataError(ValueError):
    """Raised when a Messages record cannot enter the training interface."""


def _reject_v2_path(path: Path) -> None:
    if any("v2" in part.lower() for part in path.parts):
        raise TrainingDataError(f"refusing excluded v2 path: {path}")


def is_validation_sample(sample_id: str, fraction: float, seed: int) -> bool:
    """Return a stable hash partition without reading the final test split."""

    if not isinstance(sample_id, str) or not sample_id:
        raise TrainingDataError("sample_id must be a non-empty string")
    if not 0.0 < fraction < 1.0:
        raise TrainingDataError("validation fraction must be between 0 and 1")
    digest = hashlib.sha256(f"{seed}:{sample_id}".encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], byteorder="big", signed=False)
    return bucket / float(2**64) < fraction


def iter_message_files(messages_root: Path) -> Iterator[Path]:
    """Yield non-v2 train files in deterministic dataset order."""

    root = Path(messages_root)
    _reject_v2_path(root)
    if not root.is_dir():
        raise TrainingDataError(f"messages root does not exist: {root}")
    paths = sorted(root.glob("*/train.jsonl"))
    if not paths:
        raise TrainingDataError(f"no train.jsonl files found under: {root}")
    for path in paths:
        _reject_v2_path(path)
        if path.is_file() and path.stat().st_size > 0:
            yield path


def iter_partition_records(
    messages_root: Path,
    task: str,
    validation_fraction: float,
    seed: int,
    partition: str = "train",
    limit: Optional[int] = None,
    included_dataset_ids: Optional[Iterable[str]] = None,
) -> Iterator[Dict[str, Any]]:
    """Stream validated records from a deterministic train/validation partition."""

    if task not in SUPPORTED_TASKS:
        raise TrainingDataError(f"unsupported task: {task!r}")
    if partition not in SUPPORTED_PARTITIONS:
        raise TrainingDataError(f"unsupported partition: {partition!r}")
    if limit is not None and limit <= 0:
        raise TrainingDataError("limit must be positive")

    included: Optional[Set[str]] = None
    if included_dataset_ids is not None:
        included = {
            str(dataset_id).strip()
            for dataset_id in included_dataset_ids
            if str(dataset_id).strip()
        }
        if not included:
            raise TrainingDataError("included_dataset_ids cannot be empty")

    paths = list(iter_message_files(Path(messages_root)))
    if included is not None:
        available = {path.parent.name for path in paths}
        unknown = sorted(included - available)
        if unknown:
            raise TrainingDataError(
                f"included datasets are not present under messages root: {unknown}"
            )
        paths = [path for path in paths if path.parent.name in included]

    yielded = 0
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise TrainingDataError(
                        f"invalid JSON at {path}:{line_number}: {exc}"
                    ) from exc
                if record.get("task") != task:
                    raise TrainingDataError(
                        f"task mismatch at {path}:{line_number}: "
                        f"{record.get('task')!r} != {task!r}"
                    )
                metadata = record.get("metadata")
                if not isinstance(metadata, Mapping) or metadata.get("split") != "train":
                    raise TrainingDataError(
                        f"training interface only reads train records: {path}:{line_number}"
                    )
                dataset_id = metadata.get("dataset_id")
                if not isinstance(dataset_id, str) or not dataset_id:
                    raise TrainingDataError(
                        f"record is missing metadata.dataset_id: {path}:{line_number}"
                    )
                if dataset_id != path.parent.name:
                    raise TrainingDataError(
                        f"dataset mismatch at {path}:{line_number}: "
                        f"{dataset_id!r} != {path.parent.name!r}"
                    )
                sample_id = record.get("sample_id")
                in_validation = is_validation_sample(
                    sample_id, validation_fraction, seed
                )
                if partition == "train" and in_validation:
                    continue
                if partition == "validation" and not in_validation:
                    continue
                yield record
                yielded += 1
                if limit is not None and yielded >= limit:
                    return


def _primary_label(record: Mapping[str, Any], task: str) -> Any:
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        raise TrainingDataError("training record must contain messages")
    assistant = messages[-1]
    if not isinstance(assistant, Mapping) or assistant.get("role") != "assistant":
        raise TrainingDataError("training record must end with an assistant target")
    try:
        target = json.loads(assistant["content"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise TrainingDataError(f"invalid assistant target: {exc}") from exc
    field = PRIMARY_LABEL_FIELDS[task]
    if not isinstance(target, Mapping) or field not in target:
        raise TrainingDataError(f"assistant target is missing primary label: {field}")
    label = target[field]
    if not isinstance(label, (str, bool)):
        raise TrainingDataError(f"primary label must be string or boolean: {label!r}")
    return label


def _stable_rank(seed: int, namespace: str, *values: Any) -> bytes:
    payload = json.dumps(
        values, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(f"{seed}:{namespace}:{payload}".encode("utf-8")).digest()


def _label_text(label: Any) -> str:
    if isinstance(label, bool):
        return "true" if label else "false"
    return str(label)


def select_dataset_label_balanced_records(
    records: Iterable[Mapping[str, Any]],
    task: str,
    limit: int,
    seed: int,
) -> Tuple[List[Mapping[str, Any]], Dict[str, Any]]:
    """Select reproducible records by dataset, then label, round-robin.

    Dataset turns keep source datasets represented evenly.  Within every dataset,
    label turns prevent a leading class from monopolising a bounded pilot.  Stable
    hashing chooses the dataset order, label order, and samples inside each group.
    """

    if task not in SUPPORTED_TASKS:
        raise TrainingDataError(f"unsupported task: {task!r}")
    if limit <= 0:
        raise TrainingDataError("limit must be positive")

    groups: DefaultDict[str, DefaultDict[Any, List[Mapping[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    population_labels = set()
    population_records = 0
    for record in records:
        if record.get("task") != task:
            raise TrainingDataError(
                f"record task mismatch: {record.get('task')!r} != {task!r}"
            )
        metadata = record.get("metadata")
        dataset_id = metadata.get("dataset_id") if isinstance(metadata, Mapping) else None
        if not isinstance(dataset_id, str) or not dataset_id:
            raise TrainingDataError("training record must contain metadata.dataset_id")
        sample_id = record.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise TrainingDataError("sample_id must be a non-empty string")
        label = _primary_label(record, task)
        groups[dataset_id][label].append(record)
        population_labels.add(label)
        population_records += 1

    if population_records < limit:
        raise TrainingDataError(
            f"requested {limit} training records, found {population_records}"
        )

    dataset_order = sorted(
        groups,
        key=lambda dataset_id: (
            _stable_rank(seed, "dataset", dataset_id),
            dataset_id,
        ),
    )
    label_orders = {
        dataset_id: sorted(
            groups[dataset_id],
            key=lambda label: (
                _stable_rank(seed, "label", dataset_id, label),
                _label_text(label),
            ),
        )
        for dataset_id in dataset_order
    }
    for dataset_id, label_groups in groups.items():
        for label, group in label_groups.items():
            group.sort(
                key=lambda record: (
                    _stable_rank(seed, "sample", record.get("sample_id")),
                    str(record.get("sample_id")),
                )
            )

    label_cursors = Counter({dataset_id: 0 for dataset_id in dataset_order})
    sample_offsets: Counter = Counter()
    selected: List[Mapping[str, Any]] = []
    while len(selected) < limit:
        made_progress = False
        for dataset_id in dataset_order:
            labels = label_orders[dataset_id]
            chosen_label = None
            for _ in range(len(labels)):
                cursor = label_cursors[dataset_id]
                label = labels[cursor % len(labels)]
                label_cursors[dataset_id] += 1
                if sample_offsets[(dataset_id, label)] < len(
                    groups[dataset_id][label]
                ):
                    chosen_label = label
                    break
            if chosen_label is None:
                continue
            offset_key = (dataset_id, chosen_label)
            selected.append(groups[dataset_id][chosen_label][sample_offsets[offset_key]])
            sample_offsets[offset_key] += 1
            made_progress = True
            if len(selected) == limit:
                break
        if not made_progress:
            break
    if len(selected) != limit:
        raise TrainingDataError(
            f"balanced sampler produced {len(selected)} of {limit} records"
        )

    dataset_distribution = Counter()
    label_distribution = Counter()
    group_distribution = Counter()
    for record in selected:
        dataset_id = record["metadata"]["dataset_id"]
        label_text = _label_text(_primary_label(record, task))
        dataset_distribution[dataset_id] += 1
        label_distribution[label_text] += 1
        group_distribution[f"{dataset_id}::{label_text}"] += 1
    audit = {
        "strategy": "dataset_then_label_round_robin",
        "seed": seed,
        "population_records": population_records,
        "population_datasets": len(groups),
        "population_labels": len(population_labels),
        "population_dataset_label_groups": sum(
            len(label_groups) for label_groups in groups.values()
        ),
        "selected_records": len(selected),
        "selected_dataset_distribution": dict(sorted(dataset_distribution.items())),
        "selected_label_distribution": dict(sorted(label_distribution.items())),
        "selected_dataset_label_distribution": dict(sorted(group_distribution.items())),
    }
    return selected, audit


def collate_training_features(
    features: Iterable[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Stack fixed-length ChatGLM2 features and retain traceability metadata."""

    try:
        import torch
    except ImportError as exc:  # pragma: no cover - exercised in the server runtime
        raise TrainingDataError("PyTorch is required to collate training features") from exc

    items: List[Mapping[str, Any]] = list(features)
    if not items:
        raise TrainingDataError("cannot collate an empty feature list")
    lengths = {len(item["input_ids"]) for item in items}
    if len(lengths) != 1:
        raise TrainingDataError(f"inconsistent sequence lengths: {sorted(lengths)}")
    tasks = {item.get("task") for item in items}
    if len(tasks) != 1:
        raise TrainingDataError(f"a training batch must contain one task: {sorted(tasks)}")

    return {
        "input_ids": torch.tensor(
            [item["input_ids"] for item in items], dtype=torch.long
        ),
        "attention_mask": torch.tensor(
            [item["attention_mask"] for item in items], dtype=torch.long
        ),
        "labels": torch.tensor([item["labels"] for item in items], dtype=torch.long),
        "sample_ids": [item["sample_id"] for item in items],
        "task": next(iter(tasks)),
        "source_truncated": [bool(item["source_truncated"]) for item in items],
        "target_truncated": [bool(item["target_truncated"]) for item in items],
    }
