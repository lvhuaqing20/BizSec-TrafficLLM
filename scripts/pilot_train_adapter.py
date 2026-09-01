#!/usr/bin/env python3
"""Run a bounded real optimizer-step pilot for one ChatGLM2 PrefixEncoder."""

from __future__ import annotations

import argparse
import json
import sys
import time
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
    select_dataset_label_balanced_records,
)
from bizsec_trafficllm.training.pilot import (  # noqa: E402
    run_pilot_training,
    save_prefix_encoder_checkpoint,
)


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
    parser.add_argument(
        "--sampling-strategy",
        choices=("sequential", "dataset-label-balanced"),
        default="sequential",
    )
    parser.add_argument("--sampling-seed", type=int)
    parser.add_argument("--log-every-steps", type=int, default=10)
    parser.add_argument("--checkpoint-every-steps", type=int, default=0)
    parser.add_argument("--summary-only", action="store_true")
    parser.add_argument("--disable-gradient-checkpointing", action="store_true")
    return parser.parse_args()


def assert_external_output(path: Path) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        return
    raise ValueError("pilot output must be outside the Git repository")


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
    if args.log_every_steps <= 0:
        raise ValueError("log-every-steps must be positive")
    if args.checkpoint_every_steps < 0:
        raise ValueError("checkpoint-every-steps cannot be negative")
    messages_root = PROJECT_ROOT / task_config["messages_root"]
    validation = task_config["validation"]
    print(
        f"[{args.task}] selecting {required_records} train records "
        f"with strategy={args.sampling_strategy}",
        flush=True,
    )
    if args.sampling_strategy == "dataset-label-balanced":
        sampling_seed = (
            int(args.sampling_seed)
            if args.sampling_seed is not None
            else int(optimization["seed"])
        )
        records, sampling_audit = select_dataset_label_balanced_records(
            iter_partition_records(
                messages_root,
                args.task,
                float(validation["fraction"]),
                int(validation["seed"]),
                partition="train",
            ),
            args.task,
            required_records,
            sampling_seed,
        )
    else:
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
        sampling_audit = {
            "strategy": "sequential",
            "seed": None,
            "selected_records": len(records),
        }
    if len(records) != required_records:
        raise RuntimeError(
            f"requested {required_records} records but found {len(records)}"
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = args.output_dir.resolve() / f"{args.task}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=False)
    print(
        f"[{args.task}] records_ready={len(records)} "
        f"datasets={sampling_audit.get('population_datasets', 'not-audited')} "
        f"labels={sampling_audit.get('population_labels', 'not-audited')}",
        flush=True,
    )
    print(f"[{args.task}] run_dir={run_dir}", flush=True)
    print(f"[{args.task}] loading model from {args.model_dir.resolve()}", flush=True)

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
    started = time.monotonic()
    periodic_checkpoints = []
    print(
        f"[{args.task}] training_start steps={args.optimizer_steps} "
        f"gradient_accumulation={args.gradient_accumulation_steps} "
        f"max_source_length={args.max_source_length}",
        flush=True,
    )

    def report_optimizer_step(step_report):
        step = int(step_report["optimizer_step"])
        if (
            step == 1
            or step % args.log_every_steps == 0
            or step == args.optimizer_steps
        ):
            elapsed = time.monotonic() - started
            rate = step / elapsed if elapsed > 0 else 0.0
            remaining = args.optimizer_steps - step
            eta = remaining / rate if rate > 0 else 0.0
            print(
                f"[{args.task}] step={step}/{args.optimizer_steps} "
                f"loss={step_report['mean_micro_loss']:.6f} "
                f"grad_norm={step_report['gradient_norm']:.6f} "
                f"elapsed={elapsed:.1f}s eta={eta:.1f}s",
                flush=True,
            )
        if (
            args.checkpoint_every_steps
            and step % args.checkpoint_every_steps == 0
            and step < args.optimizer_steps
        ):
            checkpoint = save_prefix_encoder_checkpoint(
                interface.model.transformer.prefix_encoder,
                run_dir
                / f"checkpoint-step-{step:06d}"
                / "pytorch_model.bin",
            )
            periodic_checkpoints.append({"optimizer_step": step, **checkpoint})
            print(
                f"[{args.task}] checkpoint_saved step={step} "
                f"sha256={checkpoint['sha256']}",
                flush=True,
            )

    result = run_pilot_training(
        interface,
        records,
        optimizer_steps=args.optimizer_steps,
        gradient_accumulation_steps=args.gradient_accumulation_steps,
        learning_rate=learning_rate,
        enable_gradient_checkpointing=not args.disable_gradient_checkpointing,
        optimizer_step_callback=report_optimizer_step,
    )

    prefix_encoder = interface.model.transformer.prefix_encoder
    checkpoint_path = run_dir / "pytorch_model.bin"
    final_checkpoint = save_prefix_encoder_checkpoint(prefix_encoder, checkpoint_path)

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
            "sampling": sampling_audit,
            "progress": {
                "log_every_steps": args.log_every_steps,
                "checkpoint_every_steps": args.checkpoint_every_steps,
                "elapsed_seconds": time.monotonic() - started,
            },
            "periodic_checkpoints": periodic_checkpoints,
            "checkpoint": final_checkpoint,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }
    )
    result_path = run_dir / "pilot-training-result.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.summary_only:
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "task": result["task"],
                    "optimizer_steps": result["optimizer_steps"],
                    "elapsed_seconds": result["progress"]["elapsed_seconds"],
                    "periodic_checkpoints": len(result["periodic_checkpoints"]),
                    "checkpoint": result["checkpoint"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"result_path={result_path}")


if __name__ == "__main__":
    main()
