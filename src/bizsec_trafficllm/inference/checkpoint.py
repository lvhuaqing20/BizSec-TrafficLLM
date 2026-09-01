"""Strict loading for PrefixEncoder-only inference checkpoints."""

from __future__ import annotations

import hashlib
import json
import warnings
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


PREFIX_STATE_KEY = "transformer.prefix_encoder."


class AdapterCheckpointError(RuntimeError):
    """Raised when a PrefixEncoder checkpoint cannot be loaded exactly."""


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _state_digest(state: Mapping[str, Any]) -> str:
    """Return a stable digest over tensor names, metadata, and raw values."""

    import torch

    digest = hashlib.sha256()
    for name in sorted(state):
        tensor = state[name].detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(str(tuple(tensor.shape)).encode("ascii"))
        digest.update(tensor.view(-1).view(torch.uint8).numpy().tobytes())
    return digest.hexdigest()


def _load_tensor_mapping(path: Path) -> Mapping[str, Any]:
    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise AdapterCheckpointError("PyTorch is required to load an Adapter") from exc

    try:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="TypedStorage is deprecated.*",
                category=UserWarning,
            )
            try:
                value = torch.load(path, map_location="cpu", weights_only=True)
            except TypeError:  # PyTorch versions before weights_only support
                value = torch.load(path, map_location="cpu")
    except Exception as exc:
        raise AdapterCheckpointError(f"cannot read checkpoint {path}: {exc}") from exc
    if not isinstance(value, Mapping) or not value:
        raise AdapterCheckpointError("checkpoint must contain a non-empty state_dict mapping")
    return value


def _load_training_metadata(
    checkpoint_path: Path, checkpoint_sha256: str, expected_task: str
) -> Dict[str, Any]:
    result_path = checkpoint_path.with_name("pilot-training-result.json")
    if not result_path.is_file():
        raise AdapterCheckpointError(
            f"task-bound Adapter load requires training metadata: {result_path}"
        )
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterCheckpointError(f"cannot read training metadata {result_path}: {exc}") from exc
    task = value.get("task")
    if task != expected_task:
        raise AdapterCheckpointError(
            f"checkpoint task mismatch: metadata={task!r}, requested={expected_task!r}"
        )
    reported_sha256 = value.get("checkpoint", {}).get("sha256")
    if reported_sha256 != checkpoint_sha256:
        raise AdapterCheckpointError(
            "checkpoint SHA256 does not match its training metadata"
        )
    return {
        "path": str(result_path.resolve()),
        "task": task,
        "interface_version": value.get("interface_version"),
        "optimizer_steps": value.get("optimizer_steps"),
        "max_source_length": value.get("pilot_max_source_length"),
        "max_target_length": value.get("max_target_length"),
    }


def load_prefix_encoder_checkpoint(
    model: Any,
    checkpoint_path: Path,
    expected_task: Optional[str] = None,
) -> Dict[str, Any]:
    """Load one PrefixEncoder-only checkpoint and return auditable proof.

    The function intentionally rejects missing, unexpected, differently shaped,
    differently typed, or non-finite tensors.  This prevents a base-model run
    from being mistaken for an Adapter-backed inference run.
    """

    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise AdapterCheckpointError("PyTorch is required to load an Adapter") from exc

    path = Path(checkpoint_path).resolve()
    if not path.is_file():
        raise AdapterCheckpointError(f"checkpoint file does not exist: {path}")
    checkpoint_sha256 = _file_sha256(path)
    training_metadata = (
        _load_training_metadata(path, checkpoint_sha256, expected_task)
        if expected_task is not None
        else None
    )
    prefix_encoder = getattr(getattr(model, "transformer", None), "prefix_encoder", None)
    if prefix_encoder is None:
        raise AdapterCheckpointError(
            "model does not expose PrefixEncoder; load it with the task P-Tuning contract"
        )

    target_state = prefix_encoder.state_dict()
    if not target_state:
        raise AdapterCheckpointError("model PrefixEncoder has no parameters")
    checkpoint_state = _load_tensor_mapping(path)
    if not all(isinstance(name, str) for name in checkpoint_state):
        raise AdapterCheckpointError("checkpoint state_dict keys must all be strings")
    expected_names = {f"{PREFIX_STATE_KEY}{name}" for name in target_state}
    actual_names = set(checkpoint_state)
    missing = sorted(expected_names - actual_names)
    unexpected = sorted(actual_names - expected_names)
    if missing or unexpected:
        raise AdapterCheckpointError(
            f"checkpoint keys do not match PrefixEncoder: missing={missing}, "
            f"unexpected={unexpected}"
        )

    normalized = {}
    loaded_parameters = 0
    for local_name, target in target_state.items():
        full_name = f"{PREFIX_STATE_KEY}{local_name}"
        value = checkpoint_state[full_name]
        if not isinstance(value, torch.Tensor):
            raise AdapterCheckpointError(f"checkpoint value is not a tensor: {full_name}")
        if tuple(value.shape) != tuple(target.shape):
            raise AdapterCheckpointError(
                f"shape mismatch for {full_name}: checkpoint={tuple(value.shape)}, "
                f"model={tuple(target.shape)}"
            )
        if value.dtype != target.dtype:
            raise AdapterCheckpointError(
                f"dtype mismatch for {full_name}: checkpoint={value.dtype}, model={target.dtype}"
            )
        if value.is_floating_point() and not bool(torch.isfinite(value).all().item()):
            raise AdapterCheckpointError(f"non-finite tensor in checkpoint: {full_name}")
        normalized[local_name] = value
        loaded_parameters += value.numel()

    digest_before = _state_digest(target_state)
    prefix_encoder.load_state_dict(normalized, strict=True)
    loaded_state = prefix_encoder.state_dict()
    digest_after = _state_digest(loaded_state)
    for name, expected in normalized.items():
        actual = loaded_state[name].detach().cpu()
        if not torch.equal(actual, expected.detach().cpu()):
            raise AdapterCheckpointError(f"post-load verification failed for {name}")
    return {
        "status": "loaded",
        "format": "PrefixEncoder-only PyTorch state_dict",
        "path": str(path),
        "sha256": checkpoint_sha256,
        "state_keys": sorted(actual_names),
        "loaded_parameters": loaded_parameters,
        "parameter_digest_before": digest_before,
        "parameter_digest_after": digest_after,
        "post_load_verified": True,
        "training_metadata": training_metadata,
    }
