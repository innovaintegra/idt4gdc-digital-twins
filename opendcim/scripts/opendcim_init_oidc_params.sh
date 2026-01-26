#!/usr/bin/env bash
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-opendcim_db}"
DB_NAME="${DB_NAME:-dcim}"
DB_USER="${DB_USER:-root}"
DB_PASS="${DB_PASS:-rootpass}"

# ---- OIDC parameters ----
OIDC_ENDPOINT="${OIDC_ENDPOINT:-http://keycloak:8080/realms/idt4gdc}"
OIDC_USERID="${OIDC_USERID:-test}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-opendcim}"
OIDC_CLIENT_SECRET="${OIDC_CLIENT_SECRET:-CHANGE_ME}"

echo "==> Updating OpenDCIM OIDC configuration"

docker exec -i "${DB_CONTAINER}" mariadb \
  -u"${DB_USER}" -p"${DB_PASS}" "${DB_NAME}" <<SQL
UPDATE fac_Config SET Value='${OIDC_ENDPOINT}'       WHERE Parameter='OIDCEndpoint';
UPDATE fac_Config SET Value='${OIDC_USERID}'         WHERE Parameter='OIDCUserID';
UPDATE fac_Config SET Value='${OIDC_CLIENT_ID}'      WHERE Parameter='OIDCClientID';
UPDATE fac_Config SET Value='${OIDC_CLIENT_SECRET}'  WHERE Parameter='OIDCClientSecret';
SQL

echo "==> Current OIDC configuration:"
docker exec -i "${DB_CONTAINER}" mariadb \
  -u"${DB_USER}" -p"${DB_PASS}" "${DB_NAME}" <<'SQL'
SELECT Parameter, Value
FROM fac_Config
WHERE Parameter LIKE 'OIDC%'
ORDER BY Parameter;
SQL
