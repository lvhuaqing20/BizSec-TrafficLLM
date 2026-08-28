#!/usr/bin/env python3
"""Audit the full Messages Dataset with the native ChatGLM2 tokenizer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterator, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bizsec_trafficllm.tokenization import audit_messages  # noqa: E402


TASKS = ("business", "detection", "attack_type")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def iter_records(
    messages_dir: Path,
    tasks,
    splits,
    max_samples_per_task: Optional[int],
) -> Iterator[dict]:
    for task in tasks:
        files = [
            path
            for path in sorted((messages_dir / task).rglob("*.jsonl"))
            if path.stem in splits
        ]
        emitted = 0
        for path in files:
            with path.open("r", encoding="utf-8") as lines:
                for line in lines:
                    if not line.strip():
                        continue
                    record = json.loads(line)
                    if record.get("task") != task:
                        raise ValueError(f"task/path mismatch under {messages_dir / task}")
                    yield record
                    emitted += 1
                    if max_samples_per_task is not None and emitted >= max_samples_per_task:
                        break
            if max_samples_per_task is not None and emitted >= max_samples_per_task:
                break


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--messages-dir",
        type=Path,
        default=PROJECT_ROOT / "artifacts/datasets/messages/v1/examples",
    )
    parser.add_argument(
        "--model-config",
        type=Path,
        default=PROJECT_ROOT / "configs/models/chatglm2_6b_ptuning_v2.json",
    )
    parser.add_argument(
        "--training-config-dir",
        type=Path,
        default=PROJECT_ROOT / "configs/training",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "reports/phase7/chatglm2_token_audit_v1.json",
    )
    parser.add_argument("--task", choices=("all",) + TASKS, default="all")
    parser.add_argument("--split", choices=("all", "train", "test"), default="all")
    parser.add_argument("--max-samples-per-task", type=int)
    args = parser.parse_args()
    if args.max_samples_per_task is not None and args.max_samples_per_task <= 0:
        parser.error("--max-samples-per-task must be positive")

    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            "transformers is required for the real ChatGLM2 audit; install the locked "
            "server training environment first"
        ) from exc

    model_config = load_json(args.model_config)
    tasks = TASKS if args.task == "all" else (args.task,)
    splits = ("train", "test") if args.split == "all" else (args.split,)
    task_limits: Dict[str, dict] = {}
    for task in tasks:
        config = load_json(args.training_config_dir / f"{task}_ptuning_v2.json")
        task_limits[task] = {
            "max_source_length": config["max_source_length"],
            "max_target_length": config["max_target_length"],
        }

    tokenizer = AutoTokenizer.from_pretrained(
        model_config["tokenizer_id"],
        revision=model_config["tokenizer_revision"],
        trust_remote_code=model_config["trust_remote_code"],
    )
    audit = audit_messages(
        iter_records(
            args.messages_dir,
            tasks,
            splits,
            args.max_samples_per_task,
        ),
        tokenizer,
        task_limits,
    )
    report = {
        "audit_version": "chatglm2-token-audit-v1",
        "status": "passed",
        "model_contract": model_config["contract_version"],
        "tokenizer": {
            "id": model_config["tokenizer_id"],
            "requested_revision": model_config["tokenizer_revision"],
            "resolved_commit": getattr(tokenizer, "init_kwargs", {}).get("_commit_hash"),
            "vocab_size": len(tokenizer),
            "eos_token_id": tokenizer.eos_token_id,
            "pad_token_id": tokenizer.pad_token_id,
            "padding_side": tokenizer.padding_side,
            "truncation_side": tokenizer.truncation_side,
        },
        "scope": {
            "tasks": list(tasks),
            "splits": list(splits),
            "max_samples_per_task": args.max_samples_per_task,
        },
        **audit,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
