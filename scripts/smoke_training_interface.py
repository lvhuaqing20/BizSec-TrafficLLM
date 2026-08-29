#!/usr/bin/env python3
"""Run one no-update ChatGLM2 training-interface forward smoke."""

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

from bizsec_trafficllm.training import (  # noqa: E402
    ChatGLM2TrainingInterface,
    iter_partition_records,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=("business", "detection", "attack_type"))
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--max-samples", type=int, default=1)
    return parser.parse_args()


def assert_external_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("training smoke output must be outside the Git repository")


def main() -> None:
    args = parse_args()
    assert_external_output(args.output_dir)
    task_config = json.loads(
        (PROJECT_ROOT / "configs" / "training" / f"{args.task}_ptuning_v2.json").read_text(
            encoding="utf-8"
        )
    )
    messages_root = PROJECT_ROOT / task_config["messages_root"]
    validation = task_config["validation"]
    records = list(
        iter_partition_records(
            messages_root,
            args.task,
            float(validation["fraction"]),
            int(validation["seed"]),
            partition="train",
            limit=args.max_samples,
        )
    )
    if len(records) != args.max_samples:
        raise RuntimeError(
            f"requested {args.max_samples} records but found {len(records)}"
        )

    interface = ChatGLM2TrainingInterface.from_pretrained(
        PROJECT_ROOT, args.task, args.model_dir, args.device
    )
    batch = interface.encode_records(records)
    result = interface.forward_loss(batch)
    result.update(
        {
            "interface_version": "chatglm2-training-forward-smoke-v1",
            "model_dir": str(args.model_dir.resolve()),
            "messages_root": str(messages_root.relative_to(PROJECT_ROOT)),
            "device": args.device,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir.resolve() / f"{args.task}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    result_path = run_dir / "training-forward-smoke.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"result_path={result_path}")


if __name__ == "__main__":
    main()
