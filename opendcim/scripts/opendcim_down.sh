#!/usr/bin/env bash
set -euo pipefail

OPENDCIM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CODE_DIR="${OPENDCIM_ROOT}/code"
COMPOSE_FILE="${CODE_DIR}/docker-compose.yml"

log() { echo -e "\n==> $*\n"; }

# ---- Safety check ----
if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "ERROR: docker-compose.yml not found at:"
  echo "  ${COMPOSE_FILE}"
  echo "Did you run opendcim_run.sh first?"
  exit 1
fi

log "Stopping OpenDCIM stack"
(
  cd "${CODE_DIR}"
  docker compose -f "${COMPOSE_FILE}" down
)

log "OpenDCIM stack stopped"
