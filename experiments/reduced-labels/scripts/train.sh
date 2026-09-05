#!/usr/bin/env bash
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paths.sh"
output_root="${REDUCED_RUN_ROOT:-$XM_ROOT/runs/large-only-review/train}"
gpu_index="${GPU_INDEX:-0}"
mkdir -p "$output_root"
cd "$project_root"
# The data filtering implementation already exists in the main training CLI.
CUDA_VISIBLE_DEVICES="$gpu_index" "$python_bin" scripts/pilot_train_adapter.py \
  --task business \
  --model-dir "$model_dir" \
  --output-dir "$output_root" \
  --device cuda:0 \
  --physical-gpu-index "$gpu_index" \
  --optimizer-steps 20000 \
  --gradient-accumulation-steps 1 \
  --learning-rate 0.02 \
  --max-source-length 1024 \
  --sampling-strategy dataset-label-balanced \
  --sampling-seed 42 \
  --checkpoint-every-steps 1000 \
  --log-every-steps 10 \
  --summary-only \
  --include-dataset app53-2023 \
  --include-dataset cstnet-2023 \
  --include-dataset cw100-2024 \
  --include-dataset iscx-tor-2016 \
  --include-dataset iscx-vpn-2016
