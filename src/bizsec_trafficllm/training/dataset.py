"""Read Messages v1 records and build deterministic training batches."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional


SUPPORTED_TASKS = {"business", "detection", "attack_type"}
SUPPORTED_PARTITIONS = {"train", "validation", "all"}


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
) -> Iterator[Dict[str, Any]]:
    """Stream validated records from a deterministic train/validation partition."""

    if task not in SUPPORTED_TASKS:
        raise TrainingDataError(f"unsupported task: {task!r}")
    if partition not in SUPPORTED_PARTITIONS:
        raise TrainingDataError(f"unsupported partition: {partition!r}")
    if limit is not None and limit <= 0:
        raise TrainingDataError("limit must be positive")

    yielded = 0
    for path in iter_message_files(Path(messages_root)):
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
