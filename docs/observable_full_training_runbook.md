# Observable Full-Training Runbook

## Scope

This runbook starts the next bounded training gate with deterministic
dataset-then-label sampling, live terminal progress, and periodic PrefixEncoder
checkpoints. It uses Messages v1 only.

The planned budgets are:

- Business: 5,000 optimizer steps on physical GPU 0;
- Detection: 2,500 optimizer steps on physical GPU 1;
- Attack-Type: 2,500 optimizer steps on physical GPU 2.

Open one Termius tab per task. The three commands can run concurrently.

## Preparation in every tab

```bash
cd /root/autodl-tmp/xm/BizSec-TrafficLLM
source /root/miniconda3/etc/profile.d/conda.sh
conda activate /root/autodl-tmp/xm/envs/bizsec-chatglm2
mkdir -p /root/autodl-tmp/xm/runs/next-balanced-visible
```

Before starting, verify that the branch is
`feature/training-inference-interfaces` and that the intended GPU is idle.

## Business: GPU 0, 5,000 steps

```bash
CUDA_VISIBLE_DEVICES=0 python -u scripts/pilot_train_adapter.py \
  --task business \
  --model-dir /root/autodl-tmp/xm/models/chatglm2-6b \
  --output-dir /root/autodl-tmp/xm/runs/next-balanced-visible \
  --device cuda:0 \
  --physical-gpu-index 0 \
  --optimizer-steps 5000 \
  --gradient-accumulation-steps 1 \
  --max-source-length 1024 \
  --sampling-strategy dataset-label-balanced \
  --sampling-seed 42 \
  --log-every-steps 10 \
  --checkpoint-every-steps 500 \
  --summary-only \
  2>&1 | tee /root/autodl-tmp/xm/runs/next-balanced-visible/business-5000.console.log
```

## Detection: GPU 1, 2,500 steps

```bash
CUDA_VISIBLE_DEVICES=1 python -u scripts/pilot_train_adapter.py \
  --task detection \
  --model-dir /root/autodl-tmp/xm/models/chatglm2-6b \
  --output-dir /root/autodl-tmp/xm/runs/next-balanced-visible \
  --device cuda:0 \
  --physical-gpu-index 1 \
  --optimizer-steps 2500 \
  --gradient-accumulation-steps 1 \
  --max-source-length 1024 \
  --sampling-strategy dataset-label-balanced \
  --sampling-seed 42 \
  --log-every-steps 10 \
  --checkpoint-every-steps 500 \
  --summary-only \
  2>&1 | tee /root/autodl-tmp/xm/runs/next-balanced-visible/detection-2500.console.log
```

## Attack-Type: GPU 2, 2,500 steps

```bash
CUDA_VISIBLE_DEVICES=2 python -u scripts/pilot_train_adapter.py \
  --task attack_type \
  --model-dir /root/autodl-tmp/xm/models/chatglm2-6b \
  --output-dir /root/autodl-tmp/xm/runs/next-balanced-visible \
  --device cuda:0 \
  --physical-gpu-index 2 \
  --optimizer-steps 2500 \
  --gradient-accumulation-steps 1 \
  --max-source-length 1024 \
  --sampling-strategy dataset-label-balanced \
  --sampling-seed 42 \
  --log-every-steps 10 \
  --checkpoint-every-steps 500 \
  --summary-only \
  2>&1 | tee /root/autodl-tmp/xm/runs/next-balanced-visible/attack-type-2500.console.log
```

## What appears in the terminal

The script reports record selection, model loading, and the timestamped result
directory before training. During training it emits lines such as:

```text
[detection] step=500/2500 loss=0.123456 grad_norm=0.234567 elapsed=240.0s eta=960.0s
[detection] checkpoint_saved step=500 sha256=...
```

`loss` is the current micro-batch loss, not validation accuracy. Checkpoint save lines
confirm that the file was written, reloaded, and hashed.

## Monitoring

Use a fourth Termius tab for GPU status:

```bash
watch -n 1 nvidia-smi
```

The console logs remain under
`/root/autodl-tmp/xm/runs/next-balanced-visible`. Timestamped task directories contain
periodic folders such as `checkpoint-step-000500`, the final `pytorch_model.bin`, and
`pilot-training-result.json`.

## Interruption behavior

`Ctrl+C` stops only the command in the current tab. Already written periodic
checkpoints remain usable, but the current pilot does not save optimizer state and
cannot resume at the exact next step. A restarted command begins a new run with the
same deterministic samples and initialization. Do not close the AutoDL instance or
stop the container while training is active.

## Expected duration

Based on the 500-step gate, Business should take roughly 40–50 minutes and Detection
and Attack-Type roughly 20–25 minutes each when the three GPUs run concurrently.
Periodic checkpoint writes add a small amount of time.
