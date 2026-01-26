#!/usr/bin/env bash
set -euo pipefail

OPENDCIM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Compose lives here
CODE_DIR="${OPENDCIM_ROOT}/code"
COMPOSE_FILE="${CODE_DIR}/docker-compose.yml"

log() { echo -e "\n==> $*\n"; }

# ---- Safety checks ----
if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "ERROR: docker-compose.yml not found at:"
  echo "  ${COMPOSE_FILE}"
  exit 1
fi

log "Starting OpenDCIM stack"
(
  cd "${CODE_DIR}"
  docker compose -f "${COMPOSE_FILE}" up -d
)

log "Running OpenDCIM containers:"
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
