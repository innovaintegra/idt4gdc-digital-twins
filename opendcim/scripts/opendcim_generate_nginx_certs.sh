#!/usr/bin/env bash
set -euo pipefail

# scripts/ is at: opendcim/scripts
OPENDCIM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ----------------------------
# Configuration (overridable)
# ----------------------------
CERT_DIR="${CERT_DIR:-${OPENDCIM_ROOT}/code/nginx/certs}"
CERT_NAME="${CERT_NAME:-localhost}"
DAYS="${DAYS:-3650}"

# Subject Alt Names
# Always include localhost + 127.0.0.1
SAN_DNS_EXTRA="${SAN_DNS_EXTRA:-}"
SAN_IP_EXTRA="${SAN_IP_EXTRA:-}"

# ----------------------------
# Paths
# ----------------------------
KEY_FILE="${CERT_DIR}/${CERT_NAME}.key"
CRT_FILE="${CERT_DIR}/${CERT_NAME}.crt"
CONF_FILE="$(mktemp)"

# ----------------------------
# Helpers
# ----------------------------
log() { echo -e "\n==> $*\n"; }

cleanup() {
  rm -f "${CONF_FILE}"
}
trap cleanup EXIT

# ----------------------------
# Prepare SAN list
# ----------------------------
SAN_LIST="DNS:localhost,IP:127.0.0.1"

if [[ -n "${SAN_DNS_EXTRA}" ]]; then
  SAN_LIST="${SAN_LIST},DNS:${SAN_DNS_EXTRA}"
fi

if [[ -n "${SAN_IP_EXTRA}" ]]; then
  SAN_LIST="${SAN_LIST},IP:${SAN_IP_EXTRA}"
fi

# ----------------------------
# Create output dir
# ----------------------------
mkdir -p "${CERT_DIR}"

# ----------------------------
# Safety check
# ----------------------------
if [[ -f "${CRT_FILE}" || -f "${KEY_FILE}" ]]; then
  log "Certificate already exists:"
  echo "  ${CRT_FILE}"
  echo "  ${KEY_FILE}"
  echo ""
  echo "Delete them first if you want to regenerate."
  exit 0
fi

# ----------------------------
# Generate OpenSSL config
# ----------------------------
cat > "${CONF_FILE}" <<EOF
[ req ]
default_bits       = 2048
distinguished_name = req_distinguished_name
req_extensions     = req_ext
prompt             = no

[ req_distinguished_name ]
CN = ${CERT_NAME}

[ req_ext ]
subjectAltName = ${SAN_LIST}
EOF

# ----------------------------
# Generate cert
# ----------------------------
log "Generating self-signed TLS certificate"
log "  CN  = ${CERT_NAME}"
log "  SAN = ${SAN_LIST}"
log "  Path: ${CERT_DIR}"

openssl req -x509 -nodes -newkey rsa:2048 \
  -days "${DAYS}" \
  -keyout "${KEY_FILE}" \
  -out "${CRT_FILE}" \
  -config "${CONF_FILE}" \
  -extensions req_ext

log "Certificate generated successfully"
log "  Key:  ${KEY_FILE}"
log "  Cert: ${CRT_FILE}"
