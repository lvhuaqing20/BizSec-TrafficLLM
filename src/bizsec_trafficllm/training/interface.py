"""ChatGLM2 P-Tuning training interface with a no-update forward smoke mode."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from bizsec_trafficllm.tokenization import ChatGLM2FeatureAdapter

from .dataset import collate_training_features


SUPPORTED_TASKS = {"business", "detection", "attack_type"}


class TrainingInterfaceError(RuntimeError):
    """Raised when the model/configuration cannot satisfy the training contract."""


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TrainingInterfaceError(f"cannot load JSON config {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise TrainingInterfaceError(f"config must contain a JSON object: {path}")
    return value


def configure_prefix_trainability(model: Any) -> Dict[str, Any]:
    """Freeze the base model and leave only PrefixEncoder parameters trainable."""

    trainable_names = []
    trainable_parameters = 0
    total_parameters = 0
    for name, parameter in model.named_parameters():
        total_parameters += parameter.numel()
        is_prefix = "prefix_encoder" in name
        parameter.requires_grad = is_prefix
        if is_prefix:
            trainable_names.append(name)
            trainable_parameters += parameter.numel()
    if not trainable_names:
        raise TrainingInterfaceError(
            "model has no PrefixEncoder parameters; check pre_seq_len before loading"
        )
    return {
        "trainable_parameter_names": trainable_names,
        "trainable_parameters": trainable_parameters,
        "total_parameters": total_parameters,
    }


def initialize_prefix_encoder(prefix_encoder: Any, seed: int, std: float) -> None:
    """Deterministically initialize parameters missing from the base checkpoint.

    ChatGLM2 intentionally implements ``_init_weights`` as a no-op.  A newly
    attached PrefixEncoder can therefore retain uninitialized storage after
    ``from_pretrained`` unless the training entry initializes it explicitly.
    """

    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise TrainingInterfaceError("PyTorch is required to initialize PrefixEncoder") from exc
    with torch.random.fork_rng(devices=[]):
        torch.manual_seed(seed)
        with torch.no_grad():
            for name, parameter in prefix_encoder.named_parameters():
                if parameter.ndim >= 2:
                    torch.nn.init.normal_(parameter, mean=0.0, std=std)
                elif name.endswith("bias"):
                    torch.nn.init.zeros_(parameter)
                else:
                    torch.nn.init.normal_(parameter, mean=0.0, std=std)


class ChatGLM2TrainingInterface:
    """Prepare task batches and verify a finite forward loss without optimization."""

    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        task_config: Mapping[str, Any],
        device: str,
    ) -> None:
        task = task_config.get("task")
        if task not in SUPPORTED_TASKS:
            raise TrainingInterfaceError(f"unsupported task config: {task!r}")
        self.model = model
        self.tokenizer = tokenizer
        self.task_config = dict(task_config)
        self.device = device
        self.feature_adapter = ChatGLM2FeatureAdapter(
            tokenizer,
            int(task_config["max_source_length"]),
            int(task_config["max_target_length"]),
        )
        self.parameter_summary = configure_prefix_trainability(model)

    @classmethod
    def from_pretrained(
        cls,
        project_root: Path,
        task: str,
        model_dir: Path,
        device: str = "cuda:0",
    ) -> "ChatGLM2TrainingInterface":
        """Load the local base model with the repository's Prefix contract."""

        if task not in SUPPORTED_TASKS:
            raise TrainingInterfaceError(f"unsupported task: {task!r}")
        try:
            import torch
            from transformers import AutoConfig, AutoModel, AutoTokenizer
        except ImportError as exc:
            raise TrainingInterfaceError(
                "PyTorch and Transformers are required for the real interface"
            ) from exc

        root = Path(project_root).resolve()
        task_config_path = root / "configs" / "training" / f"{task}_ptuning_v2.json"
        task_config = _load_json(task_config_path)
        model_contract_path = root / task_config["model_contract"]
        model_contract = _load_json(model_contract_path)
        tuning = model_contract.get("tuning", {})
        if tuning.get("method") != "p_tuning_v2":
            raise TrainingInterfaceError("model contract must use p_tuning_v2")

        local_model_dir = Path(model_dir).resolve()
        if not local_model_dir.is_dir():
            raise TrainingInterfaceError(f"model directory does not exist: {local_model_dir}")
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise TrainingInterfaceError("CUDA device requested but CUDA is unavailable")

        tokenizer = AutoTokenizer.from_pretrained(
            str(local_model_dir), trust_remote_code=True, local_files_only=True
        )
        config = AutoConfig.from_pretrained(
            str(local_model_dir), trust_remote_code=True, local_files_only=True
        )
        config.pre_seq_len = int(tuning["pre_seq_len"])
        config.prefix_projection = bool(tuning["prefix_projection"])
        dtype_name = model_contract.get("dtype")
        dtype = {"float16": torch.float16, "float32": torch.float32}.get(dtype_name)
        if dtype is None:
            raise TrainingInterfaceError(f"unsupported model dtype: {dtype_name!r}")
        model = AutoModel.from_pretrained(
            str(local_model_dir),
            config=config,
            trust_remote_code=True,
            local_files_only=True,
            torch_dtype=dtype,
            low_cpu_mem_usage=True,
        )
        prefix_encoder = getattr(getattr(model, "transformer", None), "prefix_encoder", None)
        if prefix_encoder is None:
            raise TrainingInterfaceError("loaded model does not expose PrefixEncoder")
        initialize_prefix_encoder(
            prefix_encoder,
            seed=int(task_config["optimization"]["seed"]),
            std=float(getattr(config, "initializer_range", 0.02)),
        )
        prefix_encoder.float()
        model.to(device)
        model.eval()
        return cls(model, tokenizer, task_config, device)

    def encode_records(self, records: Any) -> Dict[str, Any]:
        features = [self.feature_adapter.encode(record) for record in records]
        return collate_training_features(features)

    def forward_loss(self, batch: Mapping[str, Any]) -> Dict[str, Any]:
        """Compute loss with no optimizer, backward pass, or parameter update."""

        try:
            import torch
        except ImportError as exc:  # pragma: no cover
            raise TrainingInterfaceError("PyTorch is required for forward loss") from exc
        model_inputs = {
            name: batch[name].to(self.device)
            for name in ("input_ids", "attention_mask", "labels")
        }
        self.model.eval()
        with torch.no_grad():
            outputs = self.model(**model_inputs)
        loss = getattr(outputs, "loss", None)
        if loss is None and isinstance(outputs, (tuple, list)) and outputs:
            loss = outputs[0]
        if loss is None:
            raise TrainingInterfaceError("model forward result does not contain loss")
        loss_value = float(loss.detach().float().cpu().item())
        if not math.isfinite(loss_value):
            raise TrainingInterfaceError(f"non-finite forward loss: {loss_value}")
        return {
            "status": "passed",
            "scope": "forward loss only; no optimizer, backward pass, or checkpoint",
            "task": batch["task"],
            "batch_size": int(model_inputs["input_ids"].shape[0]),
            "sequence_length": int(model_inputs["input_ids"].shape[1]),
            "loss": loss_value,
            "sample_ids": list(batch["sample_ids"]),
            "source_truncated": list(batch["source_truncated"]),
            "target_truncated": list(batch["target_truncated"]),
            **self.parameter_summary,
        }
