#!/usr/bin/env bash
REDUCED_SCRIPTS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$REDUCED_SCRIPTS/../../.." && pwd)"
XM_ROOT="${XM_ROOT:-$(dirname "$project_root")}"
python_bin="${BIZSEC_PYTHON:-$XM_ROOT/envs/bizsec-chatglm2/bin/python3.9}"
model_dir="${MODEL_DIR:-$XM_ROOT/models/chatglm2-6b}"
