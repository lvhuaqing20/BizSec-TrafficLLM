#!/usr/bin/env python3
"""Validate the ChatGLM2/P-Tuning model contract and all task configs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from jsonschema import Draft202012Validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASKS = ("business", "detection", "attack_type")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
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
        "--schema-root", type=Path, default=PROJECT_ROOT / "schemas/training"
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_ROOT / "reports/phase7/training_contract_validation_v1.json",
    )
    args = parser.parse_args()

    model_schema = load_json(
        args.schema_root / "chatglm2_ptuning_model_contract.schema.json"
    )
    task_schema = load_json(
        args.schema_root / "chatglm2_ptuning_task_config.schema.json"
    )
    errors = []
    model_config = load_json(args.model_config)
    for error in Draft202012Validator(model_schema).iter_errors(model_config):
        errors.append(f"model:{'/'.join(map(str, error.absolute_path))}: {error.message}")

    task_configs = {}
    for task in TASKS:
        path = args.training_config_dir / f"{task}_ptuning_v2.json"
        config = load_json(path)
        task_configs[task] = config
        for error in Draft202012Validator(task_schema).iter_errors(config):
            errors.append(f"{task}:{'/'.join(map(str, error.absolute_path))}: {error.message}")
        if config.get("task") != task:
            errors.append(f"{task}: filename/task mismatch")
        if not config.get("messages_root", "").endswith(f"/{task}"):
            errors.append(f"{task}: messages_root does not end with task name")

    output_dirs = [config.get("output_dir") for config in task_configs.values()]
    if len(set(output_dirs)) != len(output_dirs):
        errors.append("task output directories must be distinct")

    report = {
        "validation_version": "chatglm2-training-contract-validation-v1",
        "status": "passed" if not errors else "failed",
        "model_contract": model_config.get("contract_version"),
        "tasks": list(TASKS),
        "checks": {
            "model_schema": "passed" if not errors else "see errors",
            "task_config_schema": "passed" if not errors else "see errors",
            "task_path_consistency": "passed" if not errors else "see errors",
            "checkpoint_isolation": "passed" if not errors else "see errors",
        },
        "errors": errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
