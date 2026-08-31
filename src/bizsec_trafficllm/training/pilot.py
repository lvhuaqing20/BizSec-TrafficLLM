"""Minimal, auditable optimizer-step runner for PrefixEncoder pilot training."""

from __future__ import annotations

import hashlib
import math
from typing import Any, Dict, Iterable, Mapping


class PilotTrainingError(RuntimeError):
    """Raised when a pilot run cannot prove a safe parameter update."""


def _loss_from_outputs(outputs: Any) -> Any:
    loss = getattr(outputs, "loss", None)
    if loss is None and isinstance(outputs, (tuple, list)) and outputs:
        loss = outputs[0]
    if loss is None:
        raise PilotTrainingError("model forward result does not contain loss")
    return loss


def _tensor_digest(named_parameters: Iterable[Any]) -> str:
    digest = hashlib.sha256()
    for name, parameter in named_parameters:
        value = parameter.detach().float().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tuple(value.shape)).encode("ascii"))
        digest.update(value.numpy().tobytes())
    return digest.hexdigest()


def run_pilot_training(
    interface: Any,
    records: Iterable[Mapping[str, Any]],
    optimizer_steps: int,
    gradient_accumulation_steps: int,
    learning_rate: float,
    enable_gradient_checkpointing: bool = True,
) -> Dict[str, Any]:
    """Run a bounded PrefixEncoder update and return verification metrics.

    This intentionally implements only the mechanics needed for a pilot:
    single-record micro-batches, AdamW, finite-value checks, and proof that
    trainable parameters changed. Scheduling and full-training resume state are
    deliberately left out of scope.
    """

    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise PilotTrainingError("PyTorch is required for pilot training") from exc

    if optimizer_steps <= 0:
        raise PilotTrainingError("optimizer_steps must be positive")
    if gradient_accumulation_steps <= 0:
        raise PilotTrainingError("gradient_accumulation_steps must be positive")
    if not math.isfinite(learning_rate) or learning_rate <= 0:
        raise PilotTrainingError("learning_rate must be finite and positive")

    named_trainable = [
        (name, parameter)
        for name, parameter in interface.model.named_parameters()
        if parameter.requires_grad
    ]
    if not named_trainable:
        raise PilotTrainingError("model has no trainable parameters")
    if any("prefix_encoder" not in name for name, _ in named_trainable):
        raise PilotTrainingError("pilot refuses non-PrefixEncoder trainable parameters")

    before = {
        name: parameter.detach().float().cpu().clone()
        for name, parameter in named_trainable
    }
    digest_before = _tensor_digest(named_trainable)
    optimizer = torch.optim.AdamW(
        [parameter for _, parameter in named_trainable], lr=learning_rate
    )
    record_iterator = iter(records)
    sample_ids = []
    source_truncated = []
    target_truncated = []
    sequence_lengths = []
    step_reports = []

    checkpointing_enabled = False
    if enable_gradient_checkpointing and hasattr(
        interface.model, "gradient_checkpointing_enable"
    ):
        interface.model.gradient_checkpointing_enable()
        checkpointing_enabled = True

    interface.model.train()
    optimizer.zero_grad(set_to_none=True)
    for step_index in range(optimizer_steps):
        micro_losses = []
        for _ in range(gradient_accumulation_steps):
            try:
                record = next(record_iterator)
            except StopIteration as exc:
                required = optimizer_steps * gradient_accumulation_steps
                raise PilotTrainingError(
                    f"pilot requires {required} records but the iterator ended early"
                ) from exc
            batch = interface.encode_records([record])
            model_inputs = {
                name: batch[name].to(interface.device)
                for name in ("input_ids", "attention_mask", "labels")
            }
            outputs = interface.model(**model_inputs)
            loss = _loss_from_outputs(outputs)
            loss_value = float(loss.detach().float().cpu().item())
            if not math.isfinite(loss_value):
                raise PilotTrainingError(f"non-finite training loss: {loss_value}")
            (loss / gradient_accumulation_steps).backward()
            micro_losses.append(loss_value)
            sample_ids.extend(batch["sample_ids"])
            source_truncated.extend(bool(v) for v in batch["source_truncated"])
            target_truncated.extend(bool(v) for v in batch["target_truncated"])
            sequence_lengths.append(int(model_inputs["input_ids"].shape[1]))

        gradient_square_sum = 0.0
        gradient_parameter_count = 0
        for _, parameter in named_trainable:
            if parameter.grad is None:
                continue
            gradient_parameter_count += parameter.numel()
            finite = bool(torch.isfinite(parameter.grad).all().item())
            if not finite:
                raise PilotTrainingError("non-finite PrefixEncoder gradient")
            gradient_square_sum += float(
                parameter.grad.detach().float().pow(2).sum().cpu().item()
            )
        if gradient_parameter_count == 0:
            raise PilotTrainingError("PrefixEncoder received no gradients")
        gradient_norm = math.sqrt(gradient_square_sum)
        if not math.isfinite(gradient_norm) or gradient_norm == 0.0:
            raise PilotTrainingError(f"invalid gradient norm: {gradient_norm}")

        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        for _, parameter in named_trainable:
            if not bool(torch.isfinite(parameter).all().item()):
                raise PilotTrainingError("optimizer produced non-finite PrefixEncoder values")
        step_reports.append(
            {
                "optimizer_step": step_index + 1,
                "mean_micro_loss": sum(micro_losses) / len(micro_losses),
                "gradient_norm": gradient_norm,
            }
        )

    interface.model.eval()
    delta_square_sum = 0.0
    for name, parameter in named_trainable:
        difference = parameter.detach().float().cpu() - before[name]
        delta_square_sum += float(difference.pow(2).sum().item())
    parameter_delta_norm = math.sqrt(delta_square_sum)
    digest_after = _tensor_digest(named_trainable)
    if parameter_delta_norm == 0.0 or digest_after == digest_before:
        raise PilotTrainingError("optimizer completed but PrefixEncoder did not change")

    peak_memory_bytes = None
    if str(interface.device).startswith("cuda") and torch.cuda.is_available():
        peak_memory_bytes = int(torch.cuda.max_memory_allocated())

    return {
        "status": "passed",
        "scope": "bounded PrefixEncoder pilot; not a full training run",
        "task": interface.task_config["task"],
        "optimizer": "AdamW",
        "learning_rate": learning_rate,
        "optimizer_steps": optimizer_steps,
        "gradient_accumulation_steps": gradient_accumulation_steps,
        "micro_batches": optimizer_steps * gradient_accumulation_steps,
        "gradient_checkpointing_enabled": checkpointing_enabled,
        "sample_ids": sample_ids,
        "source_truncated": source_truncated,
        "target_truncated": target_truncated,
        "sequence_lengths": sequence_lengths,
        "steps": step_reports,
        "trainable_parameter_names": [name for name, _ in named_trainable],
        "trainable_parameters": sum(p.numel() for _, p in named_trainable),
        "parameter_digest_before": digest_before,
        "parameter_digest_after": digest_after,
        "parameter_delta_norm": parameter_delta_norm,
        "peak_memory_allocated_bytes": peak_memory_bytes,
    }
