#!/usr/bin/env python3
"""Run a bounded real optimizer-step pilot for one ChatGLM2 PrefixEncoder."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from bizsec_trafficllm.tokenization import ChatGLM2FeatureAdapter  # noqa: E402
from bizsec_trafficllm.training import (  # noqa: E402
    ChatGLM2TrainingInterface,
    iter_partition_records,
)
from bizsec_trafficllm.training.pilot import run_pilot_training  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True, choices=("business", "detection", "attack_type"))
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--physical-gpu-index", type=int)
    parser.add_argument("--optimizer-steps", type=int, default=2)
    parser.add_argument("--gradient-accumulation-steps", type=int, default=1)
    parser.add_argument("--learning-rate", type=float)
    parser.add_argument("--max-source-length", type=int, default=256)
    parser.add_argument("--disable-gradient-checkpointing", action="store_true")
    return parser.parse_args()


def assert_external_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("pilot output must be outside the Git repository")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    args = parse_args()
    assert_external_output(args.output_dir)
    task_config_path = (
        PROJECT_ROOT / "configs" / "training" / f"{args.task}_ptuning_v2.json"
    )
    task_config = json.loads(task_config_path.read_text(encoding="utf-8"))
    optimization = task_config["optimization"]
    learning_rate = (
        float(args.learning_rate)
        if args.learning_rate is not None
        else float(optimization["learning_rate"])
    )
    required_records = args.optimizer_steps * args.gradient_accumulation_steps
    messages_root = PROJECT_ROOT / task_config["messages_root"]
    validation = task_config["validation"]
    records = list(
        iter_partition_records(
            messages_root,
            args.task,
            float(validation["fraction"]),
            int(validation["seed"]),
            partition="train",
            limit=required_records,
        )
    )
    if len(records) != required_records:
        raise RuntimeError(
            f"requested {required_records} records but found {len(records)}"
        )

    interface = ChatGLM2TrainingInterface.from_pretrained(
        PROJECT_ROOT, args.task, args.model_dir, args.device
    )
    formal_max_source_length = int(task_config["max_source_length"])
    if args.max_source_length <= 0 or args.max_source_length > formal_max_source_length:
        raise ValueError(
            f"max-source-length must be in [1, {formal_max_source_length}]"
        )
    interface.feature_adapter = ChatGLM2FeatureAdapter(
        interface.tokenizer,
        args.max_source_length,
        int(task_config["max_target_length"]),
    )

    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("PyTorch is required") from exc
    if args.device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats()
    result = run_pilot_training(
        interface,
        records,
        optimizer_steps=args.optimizer_steps,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=learning_rate,
        enable_gradient_checkpointing=not args.disable_gradient_checkpointing,
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir.resolve() / f"{args.task}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    prefix_encoder = interface.model.transformer.prefix_encoder
    checkpoint_state = {
        f"transformer.prefix_encoder.{name}": value.detach().cpu()
        for name, value in prefix_encoder.state_dict().items()
    }
    checkpoint_path = run_dir / "pytorch_model.bin"
    torch.save(checkpoint_state, checkpoint_path)
    loaded_state = torch.load(checkpoint_path, map_location="cpu")
    reload_verified = (
        loaded_state.keys() == checkpoint_state.keys()
        and all(
            torch.equal(loaded_state[name], checkpoint_state[name])
            for name in checkpoint_state
        )
    )
    if not reload_verified:
        raise RuntimeError("saved PrefixEncoder checkpoint failed reload verification")

    result.update(
        {
            "interface_version": "chatglm2-prefix-pilot-v1",
            "model_dir": str(args.model_dir.resolve()),
            "messages_root": str(messages_root.relative_to(PROJECT_ROOT)),
            "device": args.device,
            "physical_gpu_index": args.physical_gpu_index,
            "formal_max_source_length": formal_max_source_length,
            "pilot_max_source_length": args.max_source_length,
            "max_target_length": int(task_config["max_target_length"]),
            "checkpoint": {
                "path": str(checkpoint_path),
                "format": "PrefixEncoder-only PyTorch state_dict",
                "size_bytes": checkpoint_path.stat().st_size,
                "sha256": file_sha256(checkpoint_path),
                "reload_verified": reload_verified,
            },
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    result_path = run_dir / "pilot-training-result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"result_path={result_path}")


if __name__ == "__main__":
    main()
