#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paths.sh"
DATA="$ROOT/data/cstnet-business-8-1-1"
OUTPUT="$ROOT/runs/short-200-ddp3-ga5"
CACHE="$ROOT/cache/short-200-ddp3-ga5"
LOG="$ROOT/logs/short-200-ddp3-ga5.log"

if [[ -d "$OUTPUT" ]] && [[ -n "$(ls -A "$OUTPUT")" ]]; then
  echo "refusing to overwrite nonempty output: $OUTPUT" >&2
  exit 1
fi

mkdir -p "$OUTPUT" "$CACHE" "$ROOT/logs"
cd "$CODE"

CUDA_VISIBLE_DEVICES=0,1,2 \
"$ENV/bin/torchrun" --standalone --nnodes=1 --nproc-per-node=3 main.py \
  --do_train \
  --train_file "$DATA/bizsec_cstnet_business_train.json" \
  --validation_file "$DATA/bizsec_cstnet_business_validation.json" \
  --preprocessing_num_workers 10 \
  --prompt_column instruction \
  --response_column output \
  --overwrite_cache \
  --cache_dir "$CACHE" \
  --model_name_or_path "$MODEL" \
  --output_dir "$OUTPUT" \
  --overwrite_output_dir \
  --max_source_length 1024 \
  --max_target_length 32 \
  --per_device_train_batch_size 1 \
  --gradient_accumulation_steps 5 \
  --max_steps 200 \
  --logging_steps 10 \
  --save_steps 100 \
  --save_total_limit 2 \
  --learning_rate 2e-2 \
  --lr_scheduler_type linear \
  --pre_seq_len 128 \
  --seed 42 \
  --report_to none 2>&1 | tee "$LOG"
