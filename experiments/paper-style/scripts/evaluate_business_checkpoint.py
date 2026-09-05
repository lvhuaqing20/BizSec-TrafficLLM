#!/usr/bin/env python3
"""Evaluate one paper-style PrefixEncoder on fixed BizSec Business validation."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import random
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.metrics import precision_recall_fscore_support
import torch
from tqdm import tqdm
from transformers import AutoConfig, AutoModel, AutoTokenizer


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--per-label", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--top-p", type=float, default=0.90)
    parser.add_argument("--temperature", type=float, default=0.10)
    return parser.parse_args()


def reject_v2(path: Path) -> None:
    if any("v2" in part.lower() for part in path.parts):
        raise ValueError(f"refusing excluded v2 path: {path}")


def load_balanced(path: Path, per_label: int) -> list[dict[str, Any]]:
    reject_v2(path)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            groups[record["business_type"]].append(record)
    selected = []
    for label in sorted(groups):
        if len(groups[label]) < per_label:
            raise ValueError(f"label {label} has only {len(groups[label])} records")
        selected.extend(groups[label][:per_label])
    selected.sort(key=lambda record: record["sample_id"])
    return selected


def extract_json(text: str) -> dict[str, Any] | None:
    candidates = [text.strip()]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def main() -> None:
    args = parse_args()
    reject_v2(args.checkpoint)
    reject_v2(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = args.output_dir / "predictions.jsonl"

    records = load_balanced(args.data, args.per_label)
    label_names = sorted({record["business_type"] for record in records})
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)

    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True, local_files_only=True)
    config = AutoConfig.from_pretrained(
        args.model, trust_remote_code=True, local_files_only=True, pre_seq_len=128
    )
    model = AutoModel.from_pretrained(
        args.model, config=config, trust_remote_code=True, local_files_only=True
    )
    state = torch.load(args.checkpoint / "pytorch_model.bin", map_location="cpu")
    prefix = {
        key.removeprefix("transformer.prefix_encoder."): value
        for key, value in state.items()
        if key.startswith("transformer.prefix_encoder.")
    }
    if not prefix:
        raise ValueError("checkpoint has no PrefixEncoder weights")
    model.transformer.prefix_encoder.load_state_dict(prefix, strict=True)
    model = model.half().cuda().eval()
    model.transformer.prefix_encoder.float()

    rows = []
    with predictions_path.open("w", encoding="utf-8", buffering=1) as output:
        for index, record in enumerate(tqdm(records, desc="Business validation")):
            with torch.inference_mode():
                response, _ = model.chat(
                    tokenizer,
                    record["instruction"],
                    history=[],
                    top_p=args.top_p,
                    temperature=args.temperature,
                    max_length=1057,
                )
            parsed = extract_json(response)
            prediction = parsed.get("business_type") if parsed else None
            schema_valid = bool(
                parsed
                and set(parsed) == {"business_domain", "business_type"}
                and parsed.get("business_domain") == "application"
                and prediction in label_names
            )
            row = {
                "index": index,
                "sample_id": record["sample_id"],
                "target": record["business_type"],
                "response": response,
                "parsed": parsed,
                "prediction": prediction if prediction in label_names else None,
                "schema_valid": schema_valid,
            }
            rows.append(row)
            output.write(json.dumps(row, ensure_ascii=False) + "\n")

    invalid = "__INVALID__"
    y_true = [row["target"] for row in rows]
    y_pred = [row["prediction"] or invalid for row in rows]
    metric_labels = label_names + [invalid]
    weighted = precision_recall_fscore_support(
        y_true, y_pred, labels=metric_labels, average="weighted", zero_division=0
    )
    macro = precision_recall_fscore_support(
        y_true, y_pred, labels=label_names, average="macro", zero_division=0
    )
    metrics = {
        "checkpoint": str(args.checkpoint),
        "data": str(args.data),
        "selection": {"type": "fixed_first_per_label", "per_label": args.per_label},
        "samples": len(rows),
        "labels": len(label_names),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "weighted": {
            "precision": float(weighted[0]),
            "recall": float(weighted[1]),
            "f1": float(weighted[2]),
        },
        "macro": {
            "precision": float(macro[0]),
            "recall": float(macro[1]),
            "f1": float(macro[2]),
        },
        "schema_valid": sum(row["schema_valid"] for row in rows),
        "schema_valid_rate": sum(row["schema_valid"] for row in rows) / len(rows),
        "invalid_predictions": sum(row["prediction"] is None for row in rows),
        "target_counts": dict(sorted(Counter(y_true).items())),
        "prediction_counts": dict(sorted(Counter(y_pred).items())),
        "confusion_labels": metric_labels,
        "confusion_matrix": confusion_matrix(y_true, y_pred, labels=metric_labels).tolist(),
        "classification_report": classification_report(
            y_true, y_pred, labels=metric_labels, output_dict=True, zero_division=0
        ),
        "generation": {
            "top_p": args.top_p,
            "temperature": args.temperature,
            "seed": args.seed,
            "max_length": 1057,
        },
    }
    (args.output_dir / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "samples": metrics["samples"],
        "accuracy": metrics["accuracy"],
        "weighted": metrics["weighted"],
        "macro": metrics["macro"],
        "schema_valid_rate": metrics["schema_valid_rate"],
        "invalid_predictions": metrics["invalid_predictions"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
