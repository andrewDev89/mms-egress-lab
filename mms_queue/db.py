import time
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row

from . import config


SCHEMA = """
CREATE TABLE IF NOT EXISTS carrier_state (
    carrier TEXT PRIMARY KEY,
    healthy BOOLEAN NOT NULL DEFAULT TRUE,
    tps_capacity INTEGER NOT NULL DEFAULT 10 CHECK (tps_capacity >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    sender TEXT NOT NULL,
    recipient TEXT NOT NULL,
    media_url TEXT,
    text TEXT,
    carrier TEXT NOT NULL REFERENCES carrier_state(carrier),
    status TEXT NOT NULL CHECK (status IN ('queued', 'sending', 'retry', 'delivered', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by TEXT,
    locked_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    accepted_at TIMESTAMPTZ,
    delivered_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_messages_carrier_status_due
    ON messages (carrier, status, next_attempt_at);

CREATE INDEX IF NOT EXISTS idx_messages_created_at
    ON messages (created_at DESC);
"""

LEGACY_CARRIER_RENAMES = {
    "carrier1": "tmobile-sdg1",
    "carrier2": "tmobile-sdg2",
}


@contextmanager
def connect():
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        yield conn


def wait_for_database(max_wait_seconds=30):
    deadline = time.monotonic() + max_wait_seconds
    while True:
        try:
            with connect() as conn:
                conn.execute("SELECT 1")
                return
        except psycopg.OperationalError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def init_db():
    wait_for_database()
    with connect() as conn:
        conn.execute("SELECT pg_advisory_lock(424242)")
        try:
            conn.execute(SCHEMA)
            for old_carrier, new_carrier in LEGACY_CARRIER_RENAMES.items():
                conn.execute(
                    """
                    INSERT INTO carrier_state (carrier, healthy, tps_capacity, updated_at)
                    SELECT %s, healthy, tps_capacity, now()
                    FROM carrier_state
                    WHERE carrier = %s
                    ON CONFLICT (carrier) DO NOTHING
                    """,
                    (new_carrier, old_carrier),
                )
                conn.execute(
                    "UPDATE messages SET carrier = %s WHERE carrier = %s",
                    (new_carrier, old_carrier),
                )
                conn.execute(
                    "DELETE FROM carrier_state WHERE carrier = %s",
                    (old_carrier,),
                )
            for carrier in config.CARRIERS:
                conn.execute(
                    """
                    INSERT INTO carrier_state (carrier, healthy, tps_capacity)
                    VALUES (%s, TRUE, %s)
                    ON CONFLICT (carrier) DO NOTHING
                    """,
                    (carrier, config.DEFAULT_TPS_CAPACITY),
                )
        finally:
            conn.execute("SELECT pg_advisory_unlock(424242)")
