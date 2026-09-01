"""Single-task ChatGLM2 inference with raw and schema-checked outputs."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from jsonschema import Draft202012Validator

from .checkpoint import load_prefix_encoder_checkpoint


SUPPORTED_TASKS = {"business", "detection", "attack_type"}
OUTPUT_SCHEMA_NAMES = {
    "business": "business_output.schema.json",
    "detection": "detection_output.schema.json",
    "attack_type": "attack_type_output.schema.json",
}


class InferenceInterfaceError(RuntimeError):
    """Raised when an inference request or model result violates the interface."""


def request_to_query(request: Mapping[str, Any]) -> str:
    """Convert one validated two-role inference request into a ChatGLM2 query."""

    task = request.get("task")
    if task not in SUPPORTED_TASKS:
        raise InferenceInterfaceError(f"unsupported task: {task!r}")
    messages = request.get("messages")
    if not isinstance(messages, list) or len(messages) != 2:
        raise InferenceInterfaceError("inference request must contain two messages")
    if not all(isinstance(message, Mapping) for message in messages):
        raise InferenceInterfaceError("each inference message must be an object")
    roles = [message.get("role") for message in messages]
    if roles != ["system", "user"]:
        raise InferenceInterfaceError(
            f"expected inference roles ['system', 'user'], got {roles!r}"
        )
    contents = [message.get("content") for message in messages]
    if not all(isinstance(content, str) and content for content in contents):
        raise InferenceInterfaceError("inference message content must be non-empty text")
    return f"{contents[0]}\n\nTraffic view:\n{contents[1]}"


class ChatGLM2InferenceInterface:
    """Load once, predict one task at a time, and preserve unmodified model output."""

    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        schema_root: Path,
        device: str,
        adapter_checkpoint: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
        self.adapter_checkpoint = (
            dict(adapter_checkpoint) if adapter_checkpoint is not None else None
        )
        adapter_root = Path(schema_root) / "adapters"
        self.validators = {
            task: Draft202012Validator(
                json.loads((adapter_root / filename).read_text(encoding="utf-8"))
            )
            for task, filename in OUTPUT_SCHEMA_NAMES.items()
        }

    @classmethod
    def from_pretrained(
        cls,
        model_dir: Path,
        schema_root: Path,
        device: str = "cuda:0",
    ) -> "ChatGLM2InferenceInterface":
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise InferenceInterfaceError(
                "PyTorch and Transformers are required for the real interface"
            ) from exc

        local_model_dir = Path(model_dir).resolve()
        if not local_model_dir.is_dir():
            raise InferenceInterfaceError(f"model directory does not exist: {local_model_dir}")
        if device.startswith("cuda") and not torch.cuda.is_available():
            raise InferenceInterfaceError("CUDA device requested but CUDA is unavailable")
        tokenizer = AutoTokenizer.from_pretrained(
            str(local_model_dir), trust_remote_code=True, local_files_only=True
        )
        model = AutoModel.from_pretrained(
            str(local_model_dir),
            trust_remote_code=True,
            local_files_only=True,
            torch_dtype=torch.float16,
            low_cpu_mem_usage=True,
        ).to(device).eval()
        return cls(model, tokenizer, schema_root, device)

    @classmethod
    def from_adapter_checkpoint(
        cls,
        project_root: Path,
        task: str,
        model_dir: Path,
        checkpoint_path: Path,
        schema_root: Path,
        device: str = "cuda:0",
    ) -> "ChatGLM2InferenceInterface":
        """Load the task Prefix contract, then strictly attach its checkpoint."""

        if task not in SUPPORTED_TASKS:
            raise InferenceInterfaceError(f"unsupported task: {task!r}")
        try:
            from bizsec_trafficllm.training import ChatGLM2TrainingInterface

            training_interface = ChatGLM2TrainingInterface.from_pretrained(
                project_root, task, model_dir, device
            )
            report = load_prefix_encoder_checkpoint(
                training_interface.model,
                checkpoint_path,
                expected_task=task,
            )
        except Exception as exc:
            if isinstance(exc, InferenceInterfaceError):
                raise
            raise InferenceInterfaceError(
                f"cannot initialize {task} Adapter inference: {exc}"
            ) from exc

        model = training_interface.model
        model.requires_grad_(False)
        model.eval()
        return cls(
            model,
            training_interface.tokenizer,
            schema_root,
            device,
            adapter_checkpoint=report,
        )

    def predict(
        self,
        request: Mapping[str, Any],
        max_length: int = 1024,
        max_source_length: Optional[int] = None,
    ) -> Dict[str, Any]:
        query = request_to_query(request)
        task = request["task"]
        if self.adapter_checkpoint is not None:
            metadata = self.adapter_checkpoint.get("training_metadata") or {}
            trained_source_length = metadata.get("max_source_length")
            if max_source_length != trained_source_length:
                raise InferenceInterfaceError(
                    "Adapter inference max_source_length must match training metadata: "
                    f"requested={max_source_length!r}, trained={trained_source_length!r}"
                )
        started = time.time()
        raw_source_tokens: Optional[int] = None
        used_source_tokens: Optional[int] = None
        source_truncated = False
        if max_source_length is None:
            response, history = self.model.chat(
                self.tokenizer,
                query,
                history=[],
                do_sample=False,
                num_beams=1,
                max_length=max_length,
            )
        else:
            if max_source_length <= 0:
                raise InferenceInterfaceError("max_source_length must be positive")
            if max_length <= max_source_length:
                raise InferenceInterfaceError(
                    "max_length must exceed max_source_length to leave room for output"
                )
            try:
                import torch
            except ImportError as exc:  # pragma: no cover
                raise InferenceInterfaceError(
                    "PyTorch is required for length-aligned inference"
                ) from exc
            build_prompt = getattr(self.tokenizer, "build_prompt", None)
            if not callable(build_prompt):
                raise InferenceInterfaceError("tokenizer does not provide build_prompt")
            prompt = build_prompt(query, history=None)
            source_ids = self.tokenizer.encode(prompt, add_special_tokens=True)
            raw_source_tokens = len(source_ids)
            source_ids = source_ids[:max_source_length]
            used_source_tokens = len(source_ids)
            source_truncated = raw_source_tokens > used_source_tokens
            input_ids = torch.tensor([source_ids], dtype=torch.long, device=self.device)
            with torch.inference_mode():
                generated = self.model.generate(
                    input_ids=input_ids,
                    do_sample=False,
                    num_beams=1,
                    max_length=max_length,
                )
            response_ids = generated[0, used_source_tokens:].tolist()
            response = self.tokenizer.decode(response_ids)
            process_response = getattr(self.model, "process_response", None)
            if callable(process_response):
                response = process_response(response)
            history = [(query, response)]
        elapsed = time.time() - started
        if not isinstance(response, str) or not response.strip():
            raise InferenceInterfaceError("model returned an empty response")
        raw_output = response.strip()
        parsed_output: Optional[Any]
        parse_error: Optional[str]
        try:
            parsed_output = json.loads(raw_output)
            parse_error = None
        except json.JSONDecodeError as exc:
            parsed_output = None
            parse_error = str(exc)

        schema_error: Optional[str] = None
        schema_valid = False
        if parsed_output is not None:
            errors = sorted(
                self.validators[task].iter_errors(parsed_output), key=lambda error: list(error.path)
            )
            if errors:
                schema_error = errors[0].message
            else:
                schema_valid = True
        return {
            "status": "passed",
            "scope": "single-task inference execution; schema validity is reported separately",
            "sample_id": request.get("sample_id"),
            "task": task,
            "raw_model_output": raw_output,
            "parsed_output": parsed_output,
            "json_parse_error": parse_error,
            "schema_valid": schema_valid,
            "schema_error": schema_error,
            "inference_seconds": round(elapsed, 3),
            "history_turns": len(history),
            "max_source_length": max_source_length,
            "source_tokens_raw": raw_source_tokens,
            "source_tokens_used": used_source_tokens,
            "source_truncated": source_truncated,
            "adapter_checkpoint": self.adapter_checkpoint,
        }
