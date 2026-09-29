"""range-partition event/log tables; event dedupe key table (design DS-18, ADR-013)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-29 20:10:00
"""

from collections.abc import Sequence
from datetime import timedelta

import sqlalchemy as sa
from alembic import op

from app.db import partitions

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# table -> (indexes (name, columns, unique, where), foreign keys (name, column, ref table, on delete))
SPEC: dict[str, tuple[list[tuple[str, str, bool, str | None]], list[tuple[str, str, str, str]]]] = {
    "email_events": (
        [("idx_events_campaign_time", "campaign_id, created_at", False, None),
         ("ix_email_events_created_at", "created_at", False, None),
         ("ix_email_events_user_id", "user_id", False, None)],
        [("fk_email_events_campaign_id_campaigns", "campaign_id", "campaigns", "CASCADE"),
         ("fk_email_events_job_id_jobs", "job_id", "jobs", "SET NULL"),
         ("fk_email_events_provider_id_providers", "provider_id", "providers", "SET NULL")],
    ),
    "audit_logs": (
        [("ix_audit_logs_admin_id", "admin_id", False, None),
         ("ix_audit_logs_resource", "resource, resource_id", False, None),
         ("ix_audit_logs_timestamp", "timestamp", False, None)],
        [],
    ),
    "provider_health_logs": (
        [("ix_provider_health_logs_provider_time", "provider_id, created_at", False, None)],
        [("fk_provider_health_logs_provider_id_providers", "provider_id", "providers", "CASCADE")],
    ),
    "worker_heartbeats": (
        [("ix_worker_heartbeats_worker_time", "worker_id, timestamp", False, None)],
        [("fk_worker_heartbeats_worker_id_workers", "worker_id", "workers", "CASCADE")],
    ),
}
DEDUPE = ("delivered", "bounced", "complained", "unsubscribed")
LEGACY_DEDUPE = (
    "CREATE UNIQUE INDEX uq_email_events_provider_dedupe ON email_events (provider_id, provider_message_id, "
    "event_type) WHERE provider_message_id IS NOT NULL AND event_type IN "
    "('delivered', 'bounced', 'complained', 'unsubscribed')"
)


def _rename_indexes(table: str, suffix: str) -> None:
    op.execute(f"""
        DO $$ DECLARE r record; BEGIN
          FOR r IN SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename = '{table}' LOOP
            EXECUTE format('ALTER INDEX %I RENAME TO %I', r.indexname, left(r.indexname, 55) || '{suffix}');
          END LOOP;
        END $$""")


def _add_indexes_and_fks(table: str) -> None:
    indexes, fks = SPEC[table]
    for name, cols, unique, where in indexes:
        op.execute(f"CREATE {'UNIQUE ' if unique else ''}INDEX {name} ON {table} ({cols})"
                   + (f" WHERE {where}" if where else ""))
    for name, col, ref, ondelete in fks:
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} FOREIGN KEY ({col}) REFERENCES {ref} (id) "
                   f"ON DELETE {ondelete}")


def upgrade() -> None:
    conn = op.get_bind()
    today = partitions.utc_today()
    op.create_table(
        'email_event_keys',
        sa.Column('provider_id', sa.UUID(), nullable=False),
        sa.Column('provider_message_id', sa.String(length=255), nullable=False),
        sa.Column('event_type', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('provider_id', 'provider_message_id', 'event_type', name=op.f('pk_email_event_keys')),
    )
    op.create_index(op.f('ix_email_event_keys_created_at'), 'email_event_keys', ['created_at'], unique=False)

    for t in partitions.TABLES:
        name, col = t.name, t.column
        op.execute(f"ALTER TABLE {name} RENAME TO {name}_old")
        _rename_indexes(f"{name}_old", "_old")
        op.execute(f"ALTER SEQUENCE {name}_id_seq RENAME TO {name}_old_id_seq")
        op.execute(f"CREATE SEQUENCE {name}_id_seq AS bigint")
        op.execute(f"CREATE TABLE {name} (LIKE {name}_old INCLUDING DEFAULTS INCLUDING CONSTRAINTS) "
                   f"PARTITION BY RANGE ({col})")
        op.execute(f"ALTER TABLE {name} ALTER COLUMN id SET DEFAULT nextval('{name}_id_seq'::regclass)")
        op.execute(f"ALTER SEQUENCE {name}_id_seq OWNED BY {name}.id")
        op.execute(f"ALTER TABLE {name} ADD CONSTRAINT pk_{name} PRIMARY KEY (id, {col})")
        op.execute(f"CREATE TABLE {name}_default PARTITION OF {name} DEFAULT")
        oldest = conn.execute(sa.text(f"SELECT min({col})::date FROM {name}_old")).scalar()
        # Old history beyond a few periods stays in the default partition (pruned by maintenance).
        floor = today - timedelta(days=14 if t.granularity == "day" else 400)
        since = max(oldest, floor) if oldest else None
        partitions.ensure_sync(conn, t, today, since=since)
        _add_indexes_and_fks(name)
        op.execute(f"INSERT INTO {name} SELECT * FROM {name}_old")
        op.execute(f"SELECT setval('{name}_id_seq', COALESCE((SELECT max(id) FROM {name}), 0) + 1, false)")
        if name == "email_events":
            op.execute(
                "INSERT INTO email_event_keys (provider_id, provider_message_id, event_type, created_at) "
                "SELECT provider_id, provider_message_id, event_type, min(created_at) FROM email_events_old "
                "WHERE provider_id IS NOT NULL AND provider_message_id IS NOT NULL "
                f"AND event_type IN {DEDUPE!r} GROUP BY 1, 2, 3 ON CONFLICT DO NOTHING"
            )
        op.execute(f"DROP TABLE {name}_old CASCADE")


def downgrade() -> None:
    for t in reversed(partitions.TABLES):
        name, col = t.name, t.column
        op.execute(f"ALTER TABLE {name} RENAME TO {name}_part")
        _rename_indexes(f"{name}_part", "_part")
        op.execute(f"CREATE TABLE {name} (LIKE {name}_part INCLUDING CONSTRAINTS)")
        op.execute(f"ALTER TABLE {name} ALTER COLUMN {col} SET DEFAULT now()")
        op.execute(f"INSERT INTO {name} SELECT * FROM {name}_part")
        op.execute(f"DROP TABLE {name}_part CASCADE")  # drops its partitions and the nextval sequence
        op.execute(f"ALTER TABLE {name} ALTER COLUMN id ADD GENERATED BY DEFAULT AS IDENTITY")
        op.execute(f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                   f"COALESCE((SELECT max(id) FROM {name}), 0) + 1, false)")
        op.execute(f"ALTER TABLE {name} ADD CONSTRAINT pk_{name} PRIMARY KEY (id)")
        _add_indexes_and_fks(name)
        if name == "email_events":
            op.execute(LEGACY_DEDUPE)
    op.drop_index(op.f('ix_email_event_keys_created_at'), table_name='email_event_keys')
    op.drop_table('email_event_keys')
