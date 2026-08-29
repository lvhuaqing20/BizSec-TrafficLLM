"""Single-task ChatGLM2 inference with raw and schema-checked outputs."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from jsonschema import Draft202012Validator


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
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.device = device
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

    def predict(
        self,
        request: Mapping[str, Any],
        max_length: int = 1024,
    ) -> Dict[str, Any]:
        query = request_to_query(request)
        task = request["task"]
        started = time.time()
        response, history = self.model.chat(
            self.tokenizer,
            query,
            history=[],
            do_sample=False,
            num_beams=1,
            max_length=max_length,
        )
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
        }
