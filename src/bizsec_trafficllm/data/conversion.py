from __future__ import annotations

import hashlib
import heapq
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .canonical_builder import CanonicalSampleBuilder
from .canonical_validation import CanonicalValidator
from .errors import ConversionError
from .label_resolver import LabelResolver
from .parser_router import ParserRouter


class DatasetConverter:
    """Stream TrafficLLM JSONL files into validated canonical JSONL outputs."""

    def __init__(
        self,
        data_root: Path,
        schema_root: Path,
        source_mapping: Mapping[str, Any],
        label_registry: Mapping[str, Any],
        privacy_policy: Mapping[str, Any],
    ) -> None:
        self.data_root = data_root.resolve()
        self.schema_root = schema_root
        self._mappings = {item["dataset_id"]: item for item in source_mapping["datasets"]}
        self._router = ParserRouter(privacy_policy)
        unknown_parsers = {item["parser_id"] for item in self._mappings.values()} - self._router.parser_ids
        if unknown_parsers:
            raise ValueError(f"source mappings reference unknown parsers: {sorted(unknown_parsers)}")
        self._label_resolver = LabelResolver(label_registry)
        self._builder = CanonicalSampleBuilder(self._label_resolver)
        self._validator = CanonicalValidator(schema_root)

    @property
    def dataset_ids(self) -> List[str]:
        return sorted(self._mappings)

    @property
    def validation_mode(self) -> str:
        return self._validator.validation_mode

    def source_path(self, dataset_id: str, split: str) -> Tuple[Path, str]:
        mapping = self._mappings.get(dataset_id)
        if mapping is None:
            raise ValueError(f"unknown dataset: {dataset_id}")
        relative = Path(mapping["relative_directory"]) / f"{mapping['file_stem']}_{split}.json"
        absolute = (self.data_root / relative).resolve()
        try:
            absolute.relative_to(self.data_root)
        except ValueError as exc:
            raise ValueError(f"unsafe source path for {dataset_id}/{split}") from exc
        return absolute, relative.as_posix()

    @staticmethod
    def _failure(
        dataset_id: str,
        split: str,
        source_file: str,
        record_index: int,
        raw_line: bytes,
        code: str,
        message: str,
    ) -> Dict[str, Any]:
        return {
            "dataset_id": dataset_id,
            "split": split,
            "source_file": source_file,
            "record_index": record_index,
            "source_record_sha256": hashlib.sha256(raw_line.rstrip(b"\r\n")).hexdigest(),
            "error_code": code,
            "message": message[:500],
        }

    def convert_record(
        self,
        dataset_id: str,
        split: str,
        source_file: str,
        record_index: int,
        raw_line: bytes,
    ) -> Dict[str, Any]:
        mapping = self._mappings[dataset_id]
        try:
            decoded_text = raw_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConversionError("invalid_utf8", str(exc)) from exc
        try:
            record = json.loads(decoded_text)
        except json.JSONDecodeError as exc:
            raise ConversionError("invalid_json", str(exc)) from exc
        if not isinstance(record, dict) or set(record) != {"instruction", "output"}:
            raise ConversionError("invalid_raw_record", "record must contain exactly instruction and output")
        parsed = self._router.parse(mapping["parser_id"], mapping["expected_representation"], record)
        sample = self._builder.build_dataset_sample(
            dataset_id=dataset_id,
            split=split,
            source_file=source_file,
            record_index=record_index,
            source_format=mapping["source_format"],
            raw_record_bytes=raw_line,
            decoded_record=record,
            parsed=parsed,
        )
        issues = self._validator.issues(sample)
        if issues:
            raise ConversionError("canonical_validation_failed", " | ".join(issues[:3]))
        return sample

    def convert_split(
        self,
        dataset_id: str,
        split: str,
        output_root: Path,
        limit: Optional[int] = None,
        sample_per_label: Optional[int] = None,
    ) -> Dict[str, Any]:
        source_path, relative_source = self.source_path(dataset_id, split)
        if not source_path.exists():
            raise FileNotFoundError(source_path)
        canonical_path = output_root / "canonical" / dataset_id / f"{split}.jsonl"
        failure_path = output_root / "failures" / dataset_id / f"{split}.jsonl"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.parent.mkdir(parents=True, exist_ok=True)

        counts = Counter()
        error_codes = Counter()
        representations = Counter()
        normalized_labels = Counter()
        if sample_per_label is not None:
            source_records, sampling_scan_errors = self._stratified_records(
                source_path, dataset_id, sample_per_label
            )
        else:
            source_records = None
            sampling_scan_errors = Counter()
        with source_path.open("rb") as source, canonical_path.open("w", encoding="utf-8") as accepted, failure_path.open("w", encoding="utf-8") as rejected:
            if sample_per_label is None:
                source_records = enumerate(source)
            for selected_position, record in enumerate(source_records or []):
                record_index, raw_line = record
                if sample_per_label is None and limit is not None and selected_position >= limit:
                    break
                counts["total"] += 1
                try:
                    sample = self.convert_record(dataset_id, split, relative_source, record_index, raw_line)
                except ConversionError as exc:
                    failure = self._failure(
                        dataset_id, split, relative_source, record_index, raw_line, exc.code, exc.message
                    )
                    rejected.write(json.dumps(failure, ensure_ascii=False, sort_keys=True) + "\n")
                    counts["failed"] += 1
                    error_codes[exc.code] += 1
                    continue
                accepted.write(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n")
                counts["converted"] += 1
                counts[sample["quality"]["parse_status"]] += 1
                representations[sample["traffic"]["primary_representation"]] += 1
                normalized_labels[sample["labels"]["raw"]["normalized_value"]] += 1

        return {
            "dataset_id": dataset_id,
            "split": split,
            "source_file": relative_source,
            "limit": limit,
            "sample_per_label": sample_per_label,
            "counts": dict(counts),
            "error_codes": dict(sorted(error_codes.items())),
            "sampling_scan_errors": dict(sorted(sampling_scan_errors.items())),
            "representations": dict(sorted(representations.items())),
            "observed_labels": sorted(normalized_labels),
            "label_sample_counts": dict(sorted(normalized_labels.items())),
            "canonical_file": canonical_path.relative_to(output_root).as_posix(),
            "failure_file": failure_path.relative_to(output_root).as_posix(),
            "canonical_sha256": self._sha256_file(canonical_path),
            "failure_sha256": self._sha256_file(failure_path),
        }

    def _stratified_records(
        self,
        source_path: Path,
        dataset_id: str,
        sample_per_label: int,
    ) -> Tuple[List[Tuple[int, bytes]], Counter]:
        heaps: Dict[str, List[Tuple[int, int, bytes]]] = {}
        scan_errors = Counter()
        declared_labels = self._label_resolver.declared_labels(dataset_id)
        with source_path.open("rb") as source:
            for record_index, raw_line in enumerate(source):
                try:
                    record = json.loads(raw_line)
                    raw_output = record["output"]
                    if not isinstance(raw_output, str):
                        raise TypeError("output is not a string")
                    label = self._label_resolver.normalize(dataset_id, raw_output)
                    if label not in declared_labels:
                        raise KeyError(label)
                except (json.JSONDecodeError, KeyError, TypeError, UnicodeDecodeError):
                    scan_errors["unstratifiable_record"] += 1
                    continue
                rank = int.from_bytes(hashlib.sha256(raw_line.rstrip(b"\r\n")).digest(), "big")
                heap = heaps.setdefault(label, [])
                entry = (-rank, record_index, raw_line)
                if len(heap) < sample_per_label:
                    heapq.heappush(heap, entry)
                elif rank < -heap[0][0]:
                    heapq.heapreplace(heap, entry)
        selected = [
            (record_index, raw_line)
            for heap in heaps.values()
            for _, record_index, raw_line in heap
        ]
        selected.sort(key=lambda item: item[0])
        return selected, scan_errors

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def convert_many(
        self,
        dataset_ids: Sequence[str],
        splits: Sequence[str],
        output_root: Path,
        limit: Optional[int] = None,
        sample_per_label: Optional[int] = None,
    ) -> Dict[str, Any]:
        runs = [
            self.convert_split(dataset_id, split, output_root, limit, sample_per_label)
            for dataset_id in dataset_ids
            for split in splits
        ]
        totals = Counter()
        error_codes = Counter()
        representations = Counter()
        for run in runs:
            totals.update(run["counts"])
            error_codes.update(run["error_codes"])
            representations.update(run["representations"])
        coverage = {}
        for dataset_id in dataset_ids:
            declared = self._label_resolver.declared_labels(dataset_id)
            observed = {
                label
                for run in runs
                if run["dataset_id"] == dataset_id
                for label in run["observed_labels"]
            }
            coverage[dataset_id] = {
                "declared": len(declared),
                "observed": len(observed),
                "missing": sorted(declared - observed),
            }
        report = {
            "conversion_version": "trafficllm-to-canonical-v1",
            "status": "passed" if totals["failed"] == 0 else "completed_with_failures",
            "validation_mode": self.validation_mode,
            "data_root": str(self.data_root),
            "limit_per_split": limit,
            "sample_per_label_per_split": sample_per_label,
            "datasets": list(dataset_ids),
            "splits": list(splits),
            "totals": dict(totals),
            "representations": dict(sorted(representations.items())),
            "error_codes": dict(sorted(error_codes.items())),
            "label_coverage": coverage,
            "declared_label_entries": sum(item["declared"] for item in coverage.values()),
            "observed_label_entries": sum(item["observed"] for item in coverage.values()),
            "runs": runs,
        }
        output_root.mkdir(parents=True, exist_ok=True)
        report_path = output_root / "conversion_report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
