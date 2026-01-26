#!/usr/bin/env bash
set -euo pipefail

EXADIGIT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SIMSERVER_DIR="${EXADIGIT_ROOT}/code"

SIMSERVER_REPO_URL="${SIMSERVER_REPO_URL:-https://code.ornl.gov/exadigit/simulationserver.git}"
SIMSERVER_REF="${SIMSERVER_REF:-main}"

log() { echo -e "\n==> $*\n"; }

log "Preparing ExaDigiT SimulationServer source tree"
log "Upstream directory: ${SIMSERVER_DIR}"
log "Repository: ${SIMSERVER_REPO_URL}"
log "Ref: ${SIMSERVER_REF}"

# Clone if missing
if [[ ! -d "${SIMSERVER_DIR}/.git" ]]; then
  mkdir -p "${SIMSERVER_DIR}"
  log "Cloning upstream repository into exadigit/code"
  git clone "${SIMSERVER_REPO_URL}" "${SIMSERVER_DIR}"
fi

log "Fetching upstream updates"
git -C "${SIMSERVER_DIR}" fetch --all --tags

log "Checking out ref: ${SIMSERVER_REF}"
git -C "${SIMSERVER_DIR}" checkout "${SIMSERVER_REF}"

# Keep upstream clean and reproducible
if git -C "${SIMSERVER_DIR}" show-ref --verify --quiet "refs/remotes/origin/${SIMSERVER_REF}"; then
  log "Resetting working tree to origin/${SIMSERVER_REF}"
  git -C "${SIMSERVER_DIR}" reset --hard "origin/${SIMSERVER_REF}"
fi

log "Updating submodules (dashboard, raps, etc.)"
git -C "${SIMSERVER_DIR}" submodule update --init --recursive

log "SimulationServer upstream build step complete"