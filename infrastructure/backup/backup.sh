#!/bin/sh
# Daily logical backup of the OmniSendPro database (design DS-23, P4-06).
#
#   DATABASE_URL=postgresql://user:pw@host:5432/omnisend BACKUP_DIR=/backups ./backup.sh
#
# Writes <BACKUP_DIR>/omnisend-<UTC timestamp>.dump (pg_dump custom format, compressed) plus a .sha256
# checksum, optionally copies both to object storage (BACKUP_S3_URI, needs the aws CLI), and deletes local
# backups older than BACKUP_KEEP_DAYS (default 14). Point-in-time recovery additionally needs WAL
# archiving on the server: see postgresql-wal-archive.conf and docs/DEPLOYMENT.md.
set -eu
: "${DATABASE_URL:?set DATABASE_URL}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
URL=$(printf '%s' "$DATABASE_URL" | sed 's#^postgresql+asyncpg://#postgresql://#')
mkdir -p "$BACKUP_DIR"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
OUT="$BACKUP_DIR/omnisend-$STAMP.dump"
pg_dump --format=custom --compress=6 --no-owner --no-privileges --file="$OUT.partial" "$URL"
mv "$OUT.partial" "$OUT"
(cd "$BACKUP_DIR" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")
if [ -n "${BACKUP_S3_URI:-}" ]; then
  aws s3 cp "$OUT" "$BACKUP_S3_URI/" --only-show-errors
  aws s3 cp "$OUT.sha256" "$BACKUP_S3_URI/" --only-show-errors
fi
find "$BACKUP_DIR" -name 'omnisend-*.dump*' -type f -mtime +"$KEEP_DAYS" -delete
echo "$OUT"
