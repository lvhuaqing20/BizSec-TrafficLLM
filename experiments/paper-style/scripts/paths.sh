#!/usr/bin/env bash
# Publication-only path adapter; historical optimization arguments are unchanged.
PAPER_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$PAPER_SCRIPTS/../../.." && pwd)"
XM_ROOT="${XM_ROOT:-$(dirname "$REPO_ROOT")}"
ROOT="${PAPER_RUN_ROOT:-$XM_ROOT/bizsec-paper-style-experiment}"
TRAFFICLLM_ROOT="${TRAFFICLLM_ROOT:-$XM_ROOT/trafficllm-eac-reproduction}"
ENV="${PAPER_ENV:-$TRAFFICLLM_ROOT/environment/trafficllm-py39}"
CODE="${TRAFFICLLM_CODE:-$TRAFFICLLM_ROOT/src/TrafficLLM-official/dual-stage-tuning}"
MODEL="${MODEL_DIR:-$XM_ROOT/models/chatglm2-6b}"
