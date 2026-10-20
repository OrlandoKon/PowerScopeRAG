#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="/root/.local/share/mamba/envs/PowerScopeRAG/bin/python"

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

exec "$PYTHON" "$ROOT/tests/bailian_ragas_eval.py" \
  --judge-provider bailian \
  --refresh \
  "$@"
