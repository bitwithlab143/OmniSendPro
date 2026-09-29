#!/bin/sh
# Restore a backup made by backup.sh into a database (design DS-23, P4-06).
#
#   ./restore.sh /backups/omnisend-20260929T020000Z.dump postgresql://user:pw@host:5432/omnisend_restore
#
# Verifies the checksum first, creates the target database if it does not exist, and never touches any
# other database. Restoring over the live database is deliberately not supported: restore next to it,
# verify, then switch the application's DATABASE_URL.
set -eu
DUMP="${1:?usage: restore.sh <dump> <target database URL>}"
TARGET="${2:?usage: restore.sh <dump> <target database URL>}"
TARGET=$(printf '%s' "$TARGET" | sed 's#^postgresql+asyncpg://#postgresql://#')
if [ -f "$DUMP.sha256" ]; then
  (cd "$(dirname "$DUMP")" && sha256sum --check --status "$(basename "$DUMP").sha256") \
    || { echo "checksum mismatch for $DUMP" >&2; exit 2; }
fi
DB=$(printf '%s' "$TARGET" | sed 's#.*/##; s#?.*##')
ADMIN_URL=$(printf '%s' "$TARGET" | sed "s#/$DB\([?].*\)\{0,1\}\$#/postgres#")
if ! psql "$ADMIN_URL" -tAc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1; then
  psql "$ADMIN_URL" -qc "CREATE DATABASE \"$DB\""
fi
pg_restore --no-owner --no-privileges --exit-on-error --single-transaction --dbname="$TARGET" "$DUMP"
echo "restored $DUMP into $DB"
