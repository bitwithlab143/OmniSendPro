#!/bin/sh
# Backup/restore drill (design DS-23, P4-06): an untested backup is not a backup.
#
#   DATABASE_URL=postgresql://user:pw@host:5432/omnisend ./restore_drill.sh
#
# Takes a fresh backup, restores it into a scratch database next to the source, compares exact row counts
# of every table (partitions included) and the Alembic revision, then drops the scratch database.
# Exits non-zero on any difference. Run it on a schedule (e.g. weekly) and alert on failure.
set -eu
: "${DATABASE_URL:?set DATABASE_URL}"
HERE=$(cd "$(dirname "$0")" && pwd)
SRC=$(printf '%s' "$DATABASE_URL" | sed 's#^postgresql+asyncpg://#postgresql://#')
DB=$(printf '%s' "$SRC" | sed 's#.*/##; s#?.*##')
SCRATCH="${DB}_restore_drill_$(date -u +%Y%m%d%H%M%S)"
TARGET=$(printf '%s' "$SRC" | sed "s#/$DB\([?].*\)\{0,1\}\$#/$SCRATCH#")
ADMIN_URL=$(printf '%s' "$SRC" | sed "s#/$DB\([?].*\)\{0,1\}\$#/postgres#")
WORK=$(mktemp -d)
cleanup() { psql "$ADMIN_URL" -qc "DROP DATABASE IF EXISTS \"$SCRATCH\"" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT

COUNTS="SELECT string_agg(format('%s=%s', c.relname,
          (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM %I.%I', n.nspname, c.relname),
                                                false, true, '')))[1]::text), ',' ORDER BY c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'"

DUMP=$(DATABASE_URL="$SRC" BACKUP_DIR="$WORK" BACKUP_KEEP_DAYS=0 sh "$HERE/backup.sh")
BEFORE=$(psql "$SRC" -tAc "$COUNTS")
sh "$HERE/restore.sh" "$DUMP" "$TARGET" >/dev/null
AFTER=$(psql "$TARGET" -tAc "$COUNTS")
REV_SRC=$(psql "$SRC" -tAc "SELECT version_num FROM alembic_version" 2>/dev/null || true)
REV_DST=$(psql "$TARGET" -tAc "SELECT version_num FROM alembic_version" 2>/dev/null || true)
if [ "$BEFORE" != "$AFTER" ] || [ "$REV_SRC" != "$REV_DST" ]; then
  echo "RESTORE DRILL FAILED" >&2
  echo "source:   $REV_SRC $BEFORE" >&2
  echo "restored: $REV_DST $AFTER" >&2
  exit 1
fi
TABLES=$(printf '%s' "$AFTER" | tr ',' '\n' | wc -l)
echo "restore drill OK: $TABLES tables, revision $REV_DST, $(du -h "$DUMP" | cut -f1) backup"
