#!/usr/bin/env bash
set -euo pipefail

DB_CONTAINER="${DB_CONTAINER:-opendcim_db}"
DB_NAME="${DB_NAME:-dcim}"
DB_USER="${DB_USER:-root}"
DB_PASS="${DB_PASS:-rootpass}"

OPENDCIM_USERID="${OPENDCIM_USERID:-test}"
OPENDCIM_USER_APIKEY="${OPENDCIM_USER_APIKEY:-0ff38b1e9b8052611d418c5cd6fe5ff0}"

echo "==> Promoting OpenDCIM user '${OPENDCIM_USERID}' to Site Admin"

docker exec -i "${DB_CONTAINER}" mariadb \
  -u"${DB_USER}" -p"${DB_PASS}" "${DB_NAME}" <<SQL
UPDATE fac_People
SET
  AdminOwnDevices=1,
  ContactAdmin=1,
  RackRequest=1,
  RackAdmin=1,
  BulkOperations=1,
  SiteAdmin=1,
  ReadAccess=1,
  WriteAccess=1,
  DeleteAccess=1,
  APIKey='${OPENDCIM_USER_APIKEY}'
WHERE USERID='';
SQL

echo "==> Resulting privileges:"
docker exec -i "${DB_CONTAINER}" mariadb \
  -u"${DB_USER}" -p"${DB_PASS}" "${DB_NAME}" <<SQL
SELECT
  UserID,
  SiteAdmin,
  ReadAccess,
  WriteAccess,
  DeleteAccess
FROM fac_People
WHERE UserID='${OPENDCIM_USERID}';
SQL
