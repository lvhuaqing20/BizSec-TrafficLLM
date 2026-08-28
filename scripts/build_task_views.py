#!/usr/bin/env python3
"""Build label-separated task training examples from canonical JSONL files."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from bizsec_trafficllm.views import TrainingViewGenerator, ViewConstructionError, ViewEngine  # noqa: E402


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
    parser.add_argument("--canonical-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--schema-root", type=Path, default=PROJECT_ROOT / "schemas")
    parser.add_argument("--config-root", type=Path, default=PROJECT_ROOT / "configs" / "views")
    parser.add_argument("--task", action="append", choices=["business", "detection", "attack_type", "all"], default=[])
    parser.add_argument("--limit-per-file", type=int)
    args = parser.parse_args()
    if args.limit_per_file is not None and args.limit_per_file <= 0:
        parser.error("--limit-per-file must be positive")
    tasks = ["business", "detection", "attack_type"] if not args.task or "all" in args.task else list(dict.fromkeys(args.task))

    engine = ViewEngine(
        schema_root=args.schema_root,
        selection_policy=load_json(args.config_root / "representation_selection_v1.json"),
        token_policy=load_json(args.config_root / "token_budget_v1.json"),
    )
    generator = TrainingViewGenerator(engine)
    counts = Counter()
    errors = Counter()
    runs = []
    canonical_files = sorted(args.canonical_dir.rglob("*.jsonl"))
    for source_path in canonical_files:
        relative = source_path.relative_to(args.canonical_dir)
        if len(relative.parts) < 2:
            continue
        dataset_id = relative.parts[-2]
        split = source_path.stem
        writers = {}
        output_paths = {}
        try:
            for task in tasks:
                output_path = args.output_dir / "examples" / task / dataset_id / f"{split}.jsonl"
                output_path.parent.mkdir(parents=True, exist_ok=True)
                writers[task] = output_path.open("w", encoding="utf-8")
                output_paths[task] = output_path
            with source_path.open("r", encoding="utf-8") as source:
                for line_number, line in enumerate(source, 1):
                    if args.limit_per_file is not None and line_number > args.limit_per_file:
                        break
                    counts["canonical_read"] += 1
                    sample = json.loads(line)
                    targets = sample.get("labels", {}).get("targets", {})
                    for task in tasks:
                        if targets.get(task) is None:
                            counts[f"{task}_skipped_no_target"] += 1
                            continue
                        try:
                            example = generator.build_example(sample, task)
                        except ViewConstructionError as exc:
                            counts[f"{task}_failed"] += 1
                            errors[f"{task}:{exc.code}"] += 1
                            continue
                        writers[task].write(json.dumps(example, ensure_ascii=False, sort_keys=True) + "\n")
                        counts[f"{task}_examples"] += 1
        finally:
            for writer in writers.values():
                writer.close()
        runs.append(
            {
                "dataset_id": dataset_id,
                "split": split,
                "source_file": str(source_path),
                "outputs": {
                    task: {
                        "file": output_paths[task].relative_to(args.output_dir).as_posix(),
                        "sha256": sha256_file(output_paths[task]),
                    }
                    for task in tasks
                },
            }
        )

    report = {
        "generation_version": "canonical-to-task-training-views-v1",
        "status": "passed" if not errors else "completed_with_failures",
        "business_prior_strategy": "null",
        "tasks": tasks,
        "limit_per_file": args.limit_per_file,
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
