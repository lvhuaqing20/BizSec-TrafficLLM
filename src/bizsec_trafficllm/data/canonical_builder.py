from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from typing import Any, Dict, Mapping, Optional

from .errors import ConversionError
from .label_resolver import LabelResolver
from .models import ParsedTraffic


class CanonicalSampleBuilder:
    canonical_version = "canonical-traffic-sample-v1"

    def __init__(self, label_resolver: LabelResolver) -> None:
        self._label_resolver = label_resolver

    @staticmethod
    def sample_id(dataset_id: str, split: str, source_file: str, record_index: int) -> str:
        material = "\0".join((dataset_id, split, source_file, str(record_index)))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def source_record_sha256(raw_record_bytes: bytes) -> str:
        return hashlib.sha256(raw_record_bytes.rstrip(b"\r\n")).hexdigest()

    def build_dataset_sample(
        self,
        dataset_id: str,
        split: str,
        source_file: str,
        record_index: int,
        source_format: str,
        raw_record_bytes: bytes,
        decoded_record: Mapping[str, Any],
        parsed: ParsedTraffic,
        context: Optional[Mapping[str, Optional[str]]] = None,
    ) -> Dict[str, Any]:
        path = PurePosixPath(source_file)
        if path.is_absolute() or ".." in path.parts:
            raise ConversionError("unsafe_source_path", "source_file must be a safe dataset-relative path")
        labels = self._label_resolver.resolve(dataset_id, decoded_record.get("output"))
        representations = {"packet": None, "http_request": None, "direction_sequence": None}
        representations[parsed.representation_type] = parsed.representation
        transforms = list(dict.fromkeys(parsed.privacy_transforms))
        normalized_context = dict(context or {})
        return {
            "canonical_version": self.canonical_version,
            "sample_id": self.sample_id(dataset_id, split, source_file, record_index),
            "source": {
                "source_kind": "dataset",
                "dataset_id": dataset_id,
                "split": split,
                "source_file": source_file,
                "record_index": record_index,
                "source_format": source_format,
                "source_record_sha256": self.source_record_sha256(raw_record_bytes),
            },
            "traffic": {
                "primary_representation": parsed.representation_type,
                "representations": representations,
                "statistics": None,
            },
            "context": {
                "asset_type": normalized_context.get("asset_type"),
                "service_name": normalized_context.get("service_name"),
            },
            "labels": labels,
            "quality": {
                "parse_status": parsed.parse_status,
                "available_representations": [parsed.representation_type],
                "missing_fields": list(dict.fromkeys(parsed.missing_fields)),
                "warnings": list(dict.fromkeys(parsed.warnings)),
                "privacy": {
                    "status": "applied" if transforms else "not_required",
                    "contains_direct_identifiers": False,
                    "transforms": transforms,
                },
            },
        }
