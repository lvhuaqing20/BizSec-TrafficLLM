#!/usr/bin/env bash
set -euo pipefail

if [[ "$#" -lt 2 ]]; then
    printf 'usage: %s GPU_INDEX STEP [STEP ...]\n' "$0" >&2
    exit 2
fi

gpu_index=$1
shift

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paths.sh"
training_run="${TRAINING_RUN:?Set TRAINING_RUN to the completed business-* run directory}"
evaluation_root="${EVALUATION_ROOT:-$XM_ROOT/runs/large-only-review/checkpoint-validation-300}"

mkdir -p "$evaluation_root"
cd "$project_root"

for step in "$@"; do
    if [[ "$step" -eq 20000 ]]; then
        checkpoint="$training_run/pytorch_model.bin"
    else
        checkpoint=$(printf '%s/checkpoint-step-%06d/pytorch_model.bin' "$training_run" "$step")
    fi
    if [[ ! -s "$checkpoint" ]]; then
        printf 'missing checkpoint: %s\n' "$checkpoint" >&2
        exit 1
    fi
    step_output=$(printf '%s/step-%06d' "$evaluation_root" "$step")
    if find "$step_output" -name evaluation-summary.json -print -quit 2>/dev/null | grep -q .; then
        printf 'step=%d already evaluated; skipping\n' "$step"
        continue
    fi
    mkdir -p "$step_output"
    printf 'gpu=%d evaluating step=%d checkpoint=%s\n' "$gpu_index" "$step" "$checkpoint"
    CUDA_VISIBLE_DEVICES="$gpu_index" "$python_bin" scripts/evaluate_adapter_checkpoint.py \
        --task business \
        --model-dir "$model_dir" \
        --checkpoint "$checkpoint" \
        --output-dir "$step_output" \
        --device cuda:0 \
        --partition validation \
        --selection-strategy label-balanced \
        --limit 300 \
        --log-every 50 \
        --include-dataset app53-2023 \
        --include-dataset cstnet-2023 \
        --include-dataset cw100-2024 \
        --include-dataset iscx-tor-2016 \
        --include-dataset iscx-vpn-2016 \
        2>&1 | tee "$step_output/console.log"
done

printf 'gpu=%d worker complete\n' "$gpu_index"
