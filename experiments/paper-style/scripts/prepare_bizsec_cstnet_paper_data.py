#!/usr/bin/env python3
"""Convert BizSec CSTNET Messages v1 into a deterministic paper-style split."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", required=True, type=Path)
    parser.add_argument("--test", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


def reject_v2(path: Path) -> None:
    if any("v2" in part.lower() for part in path.parts):
        raise ValueError(f"refusing excluded v2 path: {path}")


def file_hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def load_messages(path: Path) -> list[dict[str, Any]]:
    reject_v2(path)
    records = []
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            source = json.loads(line)
            messages = source.get("messages")
            if not isinstance(messages, list) or len(messages) != 3:
                raise ValueError(f"{path}:{line_number}: expected 3 messages")
            roles = [message.get("role") for message in messages]
            if roles != ["system", "user", "assistant"]:
                raise ValueError(f"{path}:{line_number}: unexpected roles {roles}")
            target = json.loads(messages[2]["content"])
            label = target.get("business_type")
            if not isinstance(label, str) or not label:
                raise ValueError(f"{path}:{line_number}: missing business_type")
            sample_id = source.get("sample_id")
            if not isinstance(sample_id, str) or not sample_id:
                raise ValueError(f"{path}:{line_number}: missing sample_id")
            records.append(
                {
                    "instruction": (
                        messages[0]["content"]
                        + "\n\nTraffic view:\n"
                        + messages[1]["content"]
                    ),
                    "output": messages[2]["content"],
                    "sample_id": sample_id,
                    "business_type": label,
                    "source_dataset": "cstnet-2023",
                }
            )
    return records


def stable_rank(seed: int, label: str, sample_id: str) -> bytes:
    return sha256(f"{seed}\0{label}\0{sample_id}".encode("utf-8")).digest()


def main() -> None:
    args = parse_args()
    reject_v2(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    source_records = load_messages(args.train) + load_messages(args.test)
    ids = [record["sample_id"] for record in source_records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate sample_id found across source files")

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in source_records:
        groups[record["business_type"]].append(record)

    splits: dict[str, list[dict[str, Any]]] = {
        "train": [],
        "validation": [],
        "test": [],
    }
    per_class: dict[str, dict[str, int]] = {}
    for label in sorted(groups):
        ranked = sorted(
            groups[label],
            key=lambda record: stable_rank(args.seed, label, record["sample_id"]),
        )
        count = len(ranked)
        train_end = int(count * 0.8)
        validation_end = train_end + int(count * 0.1)
        pieces = {
            "train": ranked[:train_end],
            "validation": ranked[train_end:validation_end],
            "test": ranked[validation_end:],
        }
        for split, records in pieces.items():
            splits[split].extend(records)
        per_class[label] = {
            "total": count,
            **{split: len(records) for split, records in pieces.items()},
        }

    outputs: dict[str, dict[str, Any]] = {}
    split_ids: dict[str, set[str]] = {}
    for split, records in splits.items():
        records.sort(key=lambda record: sha256(
            f"{args.seed}\0{split}\0{record['sample_id']}".encode("utf-8")
        ).digest())
        output_path = args.output_dir / f"bizsec_cstnet_business_{split}.json"
        with output_path.open("w", encoding="utf-8", newline="\n") as stream:
            for record in records:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        split_ids[split] = {record["sample_id"] for record in records}
        outputs[split] = {
            "path": str(output_path),
            "records": len(records),
            "sha256": file_hash(output_path),
            "labels": dict(sorted(Counter(r["business_type"] for r in records).items())),
        }

    manifest = {
        "description": "BizSec CSTNET Business Messages v1 converted for TrafficLLM-style training",
        "seed": args.seed,
        "split_algorithm": "per-label stable SHA-256 ranking; 8:1:1",
        "instruction_mapping": "system content + two newlines + 'Traffic view:' + newline + user content",
        "target_mapping": "unchanged assistant JSON string",
        "sources": {
            "train": {"path": str(args.train), "sha256": file_hash(args.train)},
            "test": {"path": str(args.test), "sha256": file_hash(args.test)},
        },
        "total_records": len(source_records),
        "unique_sample_ids": len(set(ids)),
        "per_class": per_class,
        "outputs": outputs,
        "cross_split_overlap": {
            "train_validation": len(split_ids["train"] & split_ids["validation"]),
            "train_test": len(split_ids["train"] & split_ids["test"]),
            "validation_test": len(split_ids["validation"] & split_ids["test"]),
        },
    }
    manifest_path = args.output_dir / "split_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
