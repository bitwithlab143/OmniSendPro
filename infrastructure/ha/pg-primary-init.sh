#!/bin/sh
# Replication role + pg_hba entry for the streaming replica (runs once, on first start of the primary).
set -e
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD '${REPLICATION_PASSWORD}'"
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"
