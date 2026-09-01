#!/usr/bin/env python3
"""Run one schema-aware ChatGLM2 task inference from a v1 Task View record."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bizsec_trafficllm.inference import ChatGLM2InferenceInterface  # noqa: E402
from bizsec_trafficllm.serialization import PromptSerializer  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=("business", "detection", "attack_type"))
    parser.add_argument("--view-file", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--adapter-checkpoint",
        type=Path,
        help="PrefixEncoder-only checkpoint; omit for an explicit base-model run",
    )
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-length", type=int, default=1024)
    parser.add_argument(
        "--max-source-length",
        type=int,
        help="Source-token limit used during training; required for Adapter inference",
    )
    return parser.parse_args()


def assert_allowed_input(path: Path) -> None:
    if any("v2" in part.lower() for part in path.resolve().parts):
        raise ValueError(f"refusing excluded v2 path: {path}")


def assert_external_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("inference output must be outside the Git repository")


def read_first_view(path: Path, task: str):
    assert_allowed_input(path)
    with path.open("r", encoding="utf-8") as handle:
        line = handle.readline()
    if not line:
        raise ValueError(f"empty Task View file: {path}")
    record = json.loads(line)
    if record.get("task") != task:
        raise ValueError(f"Task View task mismatch: {record.get('task')!r} != {task!r}")
    view = record.get("view")
    if not isinstance(view, dict):
        raise ValueError("Task View record must contain a view object")
    return view


def main() -> None:
    args = parse_args()
    assert_external_output(args.output_dir)
    view = read_first_view(args.view_file, args.task)
    serializer = PromptSerializer(
        PROJECT_ROOT / "schemas",
        json.loads(
            (PROJECT_ROOT / "configs/serialization/prompt_templates_v1.json").read_text(
                encoding="utf-8"
            )
        ),
    )
    request = serializer.serialize_inference(view, args.task)
    if args.adapter_checkpoint is None:
        interface = ChatGLM2InferenceInterface.from_pretrained(
            args.model_dir, PROJECT_ROOT / "schemas", args.device
        )
        inference_mode = "base_model"
    else:
        if args.max_source_length is None:
            raise ValueError(
                "--max-source-length is required with --adapter-checkpoint and must "
                "match that checkpoint's training run"
            )
        interface = ChatGLM2InferenceInterface.from_adapter_checkpoint(
            PROJECT_ROOT,
            args.task,
            args.model_dir,
            args.adapter_checkpoint,
            PROJECT_ROOT / "schemas",
            args.device,
        )
        inference_mode = "prefix_adapter"
    result = interface.predict(
        request,
        max_length=args.max_length,
        max_source_length=args.max_source_length,
    )
    result.update(
        {
            "interface_version": "chatglm2-single-task-inference-v2",
            "inference_mode": inference_mode,
            "model_dir": str(args.model_dir.resolve()),
            "view_file": str(args.view_file.resolve()),
            "device": args.device,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir.resolve() / f"{args.task}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    result_path = run_dir / "inference-result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"result_path={result_path}")


if __name__ == "__main__":
    main()
