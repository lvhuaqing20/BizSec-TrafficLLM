#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paths.sh"
DATA="$ROOT/data/cstnet-business-8-1-1"
OUTPUT="$ROOT/runs/screen-1000-constant-ddp3-ga5"
RESUME="$OUTPUT/checkpoint-1000"
CACHE="$ROOT/cache/screen-1000-constant-ddp3-ga5"
LOG="$ROOT/logs/resume-1000-to-2000-constant-ddp3-ga5.log"

for path in "$ROOT" "$TRAFFICLLM_ROOT" "$ENV" "$CODE" "$MODEL" "$DATA" "$RESUME"; do
  if [[ ! -e "$path" ]]; then
    echo "required path missing: $path" >&2
    exit 1
  fi
done

for file in pytorch_model.bin optimizer.pt scheduler.pt trainer_state.json rng_state_0.pth rng_state_1.pth rng_state_2.pth; do
  if [[ ! -f "$RESUME/$file" ]]; then
    echo "resume state missing: $RESUME/$file" >&2
    exit 1
  fi
done

if printf '%s\n' "$ROOT" "$DATA" "$OUTPUT" "$CACHE" "$LOG" | grep -Eq '(^|/|[-_])v2($|/|[-_.])'; then
  echo "refusing to use a v2 path" >&2
  exit 1
fi

cd "$CODE"

CUDA_VISIBLE_DEVICES=0,1,2 \
"$ENV/bin/torchrun" --standalone --nnodes=1 --nproc-per-node=3 main.py \
  --do_train \
  --train_file "$DATA/bizsec_cstnet_business_train.json" \
  --validation_file "$DATA/bizsec_cstnet_business_validation.json" \
  --preprocessing_num_workers 10 \
  --prompt_column instruction \
  --response_column output \
  --cache_dir "$CACHE" \
  --model_name_or_path "$MODEL" \
  --output_dir "$OUTPUT" \
  --overwrite_output_dir \
  --resume_from_checkpoint "$RESUME" \
  --max_source_length 1024 \
  --max_target_length 32 \
  --per_device_train_batch_size 1 \
  --gradient_accumulation_steps 5 \
  --max_steps 2000 \
  --logging_steps 10 \
  --save_steps 250 \
  --save_total_limit 8 \
  --learning_rate 2e-2 \
  --lr_scheduler_type constant \
  --pre_seq_len 128 \
  --seed 42 \
  --report_to none 2>&1 | tee "$LOG"
