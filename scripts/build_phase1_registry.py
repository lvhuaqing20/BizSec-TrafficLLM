#!/usr/bin/env python3
"""Build the phase-1 dataset audit and label registry from TrafficLLM data.

The script reads the released datasets without modifying them. It writes a
deterministic registry containing every declared label, observed output counts,
task eligibility, semantic mappings, and review flags.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


DATASET_SPECS: dict[str, dict[str, Any]] = {
    "app53-2023": {
        "label_file": "app53-2023/app53-2023_label.json",
        "data_stem": "app53-2023/app53-2023_detection_packet",
        "task_name": "concept_drift_application_classification",
        "input_format": "tshark_packet_text",
        "business_domain": "application",
    },
    "csic-2010": {
        "label_file": "csic-2010/csic-2010_label.json",
        "data_stem": "csic-2010/csic-2010_detection_packet",
        "task_name": "web_attack_detection",
        "input_format": "http_request_json",
    },
    "cstnet-2023": {
        "label_file": "cstnet-2023/cstnet-2023_label.json",
        "data_stem": "cstnet-2023/cstnet-2023_detection_packet",
        "task_name": "encrypted_application_classification",
        "input_format": "tshark_packet_text",
        "business_domain": "application",
    },
    "cw100-2018": {
        "label_file": "cw100-2018-2024/cw100-2018_label.json",
        "data_stem": "cw100-2018-2024/cw100-2018_detection_packet",
        "task_name": "website_fingerprinting",
        "input_format": "direction_bit_sequence",
        "business_domain": "website",
    },
    "cw100-2024": {
        "label_file": "cw100-2018-2024/cw100-2024_label.json",
        "data_stem": "cw100-2018-2024/cw100-2024_detection_packet",
        "task_name": "website_fingerprinting",
        "input_format": "tshark_packet_text",
        "business_domain": "website",
    },
    "dapt-2020": {
        "label_file": "dapt-2020/dapt-2020_label.json",
        "data_stem": "dapt-2020/dapt-2020_detection_packet",
        "task_name": "apt_detection",
        "input_format": "tshark_packet_text",
    },
    "dohbrw-2020": {
        "label_file": "dohbrw-2020/dohbrw-2020_label.json",
        "data_stem": "dohbrw-2020/dohbrw-2020_detection_packet",
        "task_name": "malicious_doh_detection",
        "input_format": "tshark_packet_text",
    },
    "iscx-botnet-2014": {
        "label_file": "iscx-botnet-2014/iscx-botnet_label.json",
        "data_stem": "iscx-botnet-2014/iscx-botnet_detection_packet",
        "task_name": "botnet_detection",
        "input_format": "tshark_packet_text",
    },
    "iscx-tor-2016": {
        "label_file": "iscx-tor-2016/iscx-tor-2016_label.json",
        "data_stem": "iscx-tor-2016/iscx-tor-2016_detection_packet",
        "task_name": "tor_behavior_classification",
        "input_format": "tshark_packet_text",
        "business_domain": "network_behavior",
    },
    "iscx-vpn-2016": {
        "label_file": "iscx-vpn-2016/iscx-vpn-2016_label.json",
        "data_stem": "iscx-vpn-2016/iscx-vpn-2016_detection_packet",
        "task_name": "vpn_application_classification",
        "input_format": "tshark_packet_text",
        "business_domain": "application",
    },
    "ustc-tfc-2016": {
        "label_file": "ustc-tfc-2016/ustc-tfc-2016_label.json",
        "data_stem": "ustc-tfc-2016/ustc-tfc-2016_detection_packet",
        "task_name": "malware_and_application_classification",
        "input_format": "tshark_packet_text",
    },
}

USTC_BENIGN_APPLICATIONS = {
    "BitTorrent",
    "FTP",
    "Facetime",
    "Gmail",
    "MySQL",
    "Outlook",
    "SMB",
    "Skype",
    "Weibo",
    "WorldOfWarcraft",
}

USTC_MALWARE_FAMILIES = {
    "Cridex",
    "Geodo",
    "Htbot",
    "Miuref",
    "Neris",
    "Nsis-ay",
    "Shifu",
    "Tinba",
    "Virut",
    "Zeus",
}

DOH_OUTPUT_ALIASES = {
    "The traffic category is likely to be recognized as benign.": "benign",
    "The traffic category is likely to be recognized as malicious.": "malicious",
}


def normalize_output(dataset: str, output: str) -> str:
    value = output.strip()
    if dataset == "cw100-2018":
        value = value.removesuffix("。").strip()
    if dataset == "dohbrw-2020":
        value = DOH_OUTPUT_ALIASES.get(value, value)
    return value


def task_mapping(dataset: str, label: str, spec: dict[str, Any]) -> dict[str, Any]:
    tasks: dict[str, Any] = {
        "business": None,
        "detection": None,
        "attack_type": None,
    }
    basis = "direct_task_semantics"
    review_required = False
    notes: list[str] = []

    if "business_domain" in spec:
        tasks["business"] = {
            "business_domain": spec["business_domain"],
            "business_type": label,
        }

    if dataset == "csic-2010":
        is_attack = label == "malicious"
        tasks["detection"] = {"is_attack": is_attack}
        if is_attack:
            tasks["attack_type"] = {"attack_type": "web_attack", "attack_family": None}

    elif dataset == "dapt-2020":
        is_attack = label == "APT"
        tasks["detection"] = {"is_attack": is_attack}
        if is_attack:
            tasks["attack_type"] = {"attack_type": "apt", "attack_family": None}

    elif dataset == "dohbrw-2020":
        is_attack = label == "malicious"
        tasks["detection"] = {"is_attack": is_attack}
        if is_attack:
            tasks["attack_type"] = {"attack_type": "malicious_doh", "attack_family": None}

    elif dataset == "iscx-botnet-2014":
        is_attack = label != "normal"
        tasks["detection"] = {"is_attack": is_attack}
        if is_attack:
            tasks["attack_type"] = {"attack_type": "botnet", "attack_family": label}
        if label == "IRC":
            review_required = True
            notes.append("IRC is treated as attack by dataset-level botnet semantics; confirm desired production taxonomy.")
            basis = "dataset_semantics_review_required"

    elif dataset == "ustc-tfc-2016":
        if label in USTC_BENIGN_APPLICATIONS:
            tasks["business"] = {
                "business_domain": "application",
                "business_type": label,
            }
            tasks["detection"] = {"is_attack": False}
            basis = "published_benign_malware_partition"
        elif label in USTC_MALWARE_FAMILIES:
            tasks["detection"] = {"is_attack": True}
            tasks["attack_type"] = {"attack_type": "malware", "attack_family": label}
            basis = "published_benign_malware_partition"
        else:
            review_required = True
            notes.append("Label is absent from the explicit USTC benign/malware partition.")

    eligible_tasks = [name for name, target in tasks.items() if target is not None]
    return {
        "eligible_tasks": eligible_tasks,
        "targets": tasks,
        "mapping_basis": basis,
        "review_required": review_required,
        "notes": notes,
    }


def read_observations(data_root: Path, dataset: str, spec: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for split in ("train", "test"):
        path = data_root / f"{spec['data_stem']}_{split}.json"
        counts: Counter[str] = Counter()
        raw_counts: Counter[str] = Counter()
        malformed = 0
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                raw_output = row["output"]
                raw_counts[raw_output] += 1
                counts[normalize_output(dataset, raw_output)] += 1
        result[split] = {
            "path": str(path),
            "record_count": sum(counts.values()),
            "malformed_count": malformed,
            "normalized_output_counts": dict(sorted(counts.items())),
            "raw_output_variants": dict(sorted(raw_counts.items())),
        }
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_registry(data_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    registry: dict[str, Any] = {
        "registry_version": "label-registry-v1",
        "task_contract_version": "task-contract-v1",
        "generated_on": str(date.today()),
        "source_root": str(data_root),
        "semantics": {
            "null_target": "The source dataset does not provide valid supervision for this task.",
            "unknown_is_not_benign": True,
        },
        "datasets": {},
    }
    audit: dict[str, Any] = {
        "audit_version": "phase1-dataset-audit-v1",
        "generated_on": str(date.today()),
        "source_root": str(data_root),
        "datasets": {},
    }

    total_records = 0
    total_train = 0
    total_test = 0

    for dataset, spec in DATASET_SPECS.items():
        label_path = data_root / spec["label_file"]
        with label_path.open("r", encoding="utf-8") as handle:
            declared_labels: dict[str, int] = json.load(handle)

        observations = read_observations(data_root, dataset, spec)
        observed_all = set(observations["train"]["normalized_output_counts"])
        observed_all.update(observations["test"]["normalized_output_counts"])
        declared_set = set(declared_labels)

        labels = []
        for label, original_id in sorted(declared_labels.items(), key=lambda item: item[1]):
            mapping = task_mapping(dataset, label, spec)
            labels.append(
                {
                    "raw_label": label,
                    "normalized_label": label,
                    "original_id": original_id,
                    "observed_in_train": label in observations["train"]["normalized_output_counts"],
                    "observed_in_test": label in observations["test"]["normalized_output_counts"],
                    **mapping,
                }
            )

        output_normalization: dict[str, Any] = {"strip_whitespace": True}
        if dataset == "cw100-2018":
            output_normalization["strip_suffixes"] = ["。"]
        if dataset == "dohbrw-2020":
            output_normalization["aliases"] = DOH_OUTPUT_ALIASES

        train_count = observations["train"]["record_count"]
        test_count = observations["test"]["record_count"]
        total_train += train_count
        total_test += test_count
        total_records += train_count + test_count

        registry["datasets"][dataset] = {
            "task_name": spec["task_name"],
            "input_format": spec["input_format"],
            "label_file": str(label_path),
            "label_file_sha256": sha256_file(label_path),
            "output_normalization": output_normalization,
            "labels": labels,
        }
        audit["datasets"][dataset] = {
            "task_name": spec["task_name"],
            "input_format": spec["input_format"],
            "declared_class_count": len(declared_labels),
            "observed_class_count": len(observed_all),
            "unobserved_declared_labels": sorted(declared_set - observed_all),
            "undeclared_observed_outputs": sorted(observed_all - declared_set),
            "splits": observations,
        }

    audit["totals"] = {
        "dataset_variants": len(DATASET_SPECS),
        "train_records": total_train,
        "test_records": total_test,
        "all_records": total_records,
    }
    return registry, audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--registry-output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()

    registry, audit = build_registry(args.data_root.resolve())
    args.registry_output.parent.mkdir(parents=True, exist_ok=True)
    args.audit_output.parent.mkdir(parents=True, exist_ok=True)
    args.registry_output.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.audit_output.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(audit["totals"], ensure_ascii=False))


if __name__ == "__main__":
    main()
