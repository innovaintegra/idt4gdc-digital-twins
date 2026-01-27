#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="$(pwd)"
uvicorn dt_bridge.api:app --reload --host 0.0.0.0 --port "${BRIDGE_PORT:-8000}"
