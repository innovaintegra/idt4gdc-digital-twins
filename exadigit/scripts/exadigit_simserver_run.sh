#!/usr/bin/env bash
set -euo pipefail

EXADIGIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Upstream clone
CODE_DIR="${EXADIGIT_ROOT}/code"
CUSTOM_COMPOSE="${EXADIGIT_ROOT}/exadigit/docker-compose.yml"
CODE_COMPOSE="${CODE_DIR}/docker-compose.yml"
CUSTOM_DRUID_ENV="${EXADIGIT_ROOT}/exadigit/druid-environment.txt"
CODE_DRUID_ENV="${CODE_DIR}/druid-environment.txt"

log() { echo -e "\n==> $*\n"; }

# ---- Safety checks ----
if [[ ! -d "${CODE_DIR}/.git" ]]; then
  echo "ERROR: Upstream simulationserver repo not found at:"
  echo "  ${CODE_DIR}"
  echo "Run: exadigit/scripts/exadigit_simserver_build.sh"
  exit 1
fi

if [[ ! -f "${CUSTOM_COMPOSE}" ]]; then
  echo "ERROR: Custom docker-compose.yml not found at:"
  echo "  ${CUSTOM_COMPOSE}"
  exit 1
fi

if [[ ! -f "${CUSTOM_DRUID_ENV}" ]]; then
  echo "ERROR: Custom druid-environment.txt not found at:"
  echo "  ${CUSTOM_DRUID_ENV}"
  exit 1
fi

# ---- Replace upstream compose ----
log "Installing custom docker-compose.yml into upstream tree"
cp "${CUSTOM_COMPOSE}" "${CODE_COMPOSE}"

# ---- Replace upstream Druid env (tuned-down indexer memory, see file comments) ----
log "Installing custom druid-environment.txt into upstream tree"
cp "${CUSTOM_DRUID_ENV}" "${CODE_DRUID_ENV}"

# ---- Run docker compose ----
log "Starting ExaDigiT Simulation Server stack"
(
  cd "${CODE_DIR}"
  docker compose -f "${CODE_COMPOSE}" up -d --build
)

log "Running containers:"
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"