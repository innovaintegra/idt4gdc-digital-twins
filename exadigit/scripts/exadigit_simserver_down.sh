#!/usr/bin/env bash
set -euo pipefail

EXADIGIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Upstream clone directory
CODE_DIR="${EXADIGIT_ROOT}/code"

# Compose file actually used at runtime
CODE_COMPOSE="${CODE_DIR}/docker-compose.yml"

log() { echo -e "\n==> $*\n"; }

# ---- Safety check ----
if [[ ! -f "${CODE_COMPOSE}" ]]; then
  echo "ERROR: docker-compose.yml not found at:"
  echo "  ${CODE_COMPOSE}"
  echo "Did you run exadigit_simserver_up.sh first?"
  exit 1
fi

# ---- Stop stack ----
log "Stopping ExaDigiT Simulation Server stack"
(
  cd "${CODE_DIR}"
  docker compose -f "${CODE_COMPOSE}" down
)

log "ExaDigiT stack stopped"
