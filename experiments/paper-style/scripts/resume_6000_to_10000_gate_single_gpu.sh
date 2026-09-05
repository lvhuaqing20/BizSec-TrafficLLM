#!/usr/bin/env bash
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/paths.sh"
DATA="$ROOT/data/cstnet-business-8-1-1"
OUTPUT="$ROOT/runs/full-20000-linear-resumed-from-2000"
RESUME="$OUTPUT/checkpoint-6000"
TARGET="$OUTPUT/checkpoint-10000"
CACHE="$ROOT/cache/screen-1000-constant-ddp3-ga5"
LOG="$ROOT/logs/resume-6000-to-10000-gate-single-gpu-ga16.log"

for path in "$ROOT" "$TRAFFICLLM_ROOT" "$ENV" "$CODE" "$MODEL" "$DATA" "$RESUME"; do
  if [[ ! -e "$path" ]]; then
    echo "required path missing: $path" >&2
    exit 1
  fi
done

for file in pytorch_model.bin optimizer.pt scheduler.pt trainer_state.json rng_state_0.pth; do
  if [[ ! -f "$RESUME/$file" ]]; then
    echo "resume state missing: $RESUME/$file" >&2
    exit 1
  fi
done

if [[ -e "$TARGET" ]]; then
  echo "target checkpoint already exists: $TARGET" >&2
  exit 1
fi

if printf '%s\n' "$ROOT" "$DATA" "$RESUME" "$OUTPUT" "$CACHE" "$LOG" | grep -Eq '(^|/|[-_])v2($|/|[-_.])'; then
  echo "refusing to use a v2 path" >&2
  exit 1
fi

cd "$CODE"

TRAIN_PID=""
stop_training() {
  if [[ -n "$TRAIN_PID" ]] && kill -0 "$TRAIN_PID" 2>/dev/null; then
    kill -INT -- "-$TRAIN_PID" 2>/dev/null || true
    wait "$TRAIN_PID" 2>/dev/null || true
  fi
}
trap stop_training INT TERM

CUDA_VISIBLE_DEVICES=0 setsid \
"$ENV/bin/torchrun" --standalone --nnodes=1 --nproc-per-node=1 main.py \
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
  --gradient_accumulation_steps 16 \
  --max_steps 20000 \
  --logging_steps 10 \
  --save_steps 1000 \
  --save_total_limit 18 \
  --learning_rate 2e-2 \
  --lr_scheduler_type linear \
  --pre_seq_len 128 \
  --seed 42 \
  --report_to none > >(tee "$LOG") 2>&1 &
TRAIN_PID=$!

echo "training pid=$TRAIN_PID; target checkpoint=10000"
while kill -0 "$TRAIN_PID" 2>/dev/null; do
  if [[ -s "$TARGET/pytorch_model.bin" && -s "$TARGET/optimizer.pt" && -s "$TARGET/scheduler.pt" && -s "$TARGET/trainer_state.json" ]]; then
    if "$ENV/bin/python" -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["global_step"] == 10000 else 1)' "$TARGET/trainer_state.json"; then
      echo "checkpoint-10000 is complete; stopping training"
      stop_training
      echo "training stopped at saved checkpoint-10000"
      exit 0
    fi
  fi
  sleep 3
done

wait "$TRAIN_PID"
echo "training exited before checkpoint-10000 was completed" >&2
exit 1
