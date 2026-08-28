#!/usr/bin/env python3
"""Build deterministic chat-message training examples from task View JSONL."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bizsec_trafficllm.serialization import PromptSerializer, SerializationError  # noqa: E402


TASKS = ("business", "detection", "attack_type")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--schema-root", type=Path, default=PROJECT_ROOT / "schemas")
    parser.add_argument(
        "--prompt-config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "serialization" / "prompt_templates_v1.json",
    )
    parser.add_argument("--task", action="append", choices=[*TASKS, "all"], default=[])
    parser.add_argument("--limit-per-file", type=int)
    args = parser.parse_args()
    if args.limit_per_file is not None and args.limit_per_file <= 0:
        parser.error("--limit-per-file must be positive")
    tasks = list(TASKS) if not args.task or "all" in args.task else list(dict.fromkeys(args.task))
    serializer = PromptSerializer(args.schema_root, load_json(args.prompt_config))

    counts = Counter()
    errors = Counter()
    runs = []
    for task in tasks:
        task_root = args.view_dir / "examples" / task
        for source_path in sorted(task_root.rglob("*.jsonl")):
            relative = source_path.relative_to(args.view_dir / "examples")
            if len(relative.parts) != 3:
                errors["invalid_source_path"] += 1
                continue
            _, dataset_id, filename = relative.parts
            split = Path(filename).stem
            output_path = args.output_dir / "examples" / relative
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with source_path.open("r", encoding="utf-8") as source, output_path.open(
                "w", encoding="utf-8"
            ) as output:
                for line_number, line in enumerate(source, 1):
                    if args.limit_per_file is not None and line_number > args.limit_per_file:
                        break
                    counts["source_examples_read"] += 1
                    try:
                        example = json.loads(line)
                        message = serializer.serialize_training(example, dataset_id, split)
                    except json.JSONDecodeError:
                        errors["invalid_source_json"] += 1
                        continue
                    except SerializationError as exc:
                        errors[exc.code] += 1
                        continue
                    output.write(
                        json.dumps(
                            message,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        )
                        + "\n"
                    )
                    counts[f"{task}_messages"] += 1
            runs.append(
                {
                    "task": task,
                    "dataset_id": dataset_id,
                    "split": split,
                    "source_file": str(source_path),
                    "output_file": output_path.relative_to(args.output_dir).as_posix(),
                    "sha256": sha256_file(output_path),
                }
            )

    report = {
        "generation_version": "task-views-to-training-messages-v1",
        "status": "passed" if not errors else "completed_with_failures",
        "tasks": tasks,
        "limit_per_file": args.limit_per_file,
        "prompt_config": str(args.prompt_config),
        "counts": dict(sorted(counts.items())),
        "errors": dict(sorted(errors.items())),
        "runs": runs,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "generation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
