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
"""

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
            if conn.execute("SELECT to_regclass('public.mms_messages') AS name").fetchone()["name"] is None:
                from pathlib import Path
                conn.execute(Path("/app/mbuni/tables.sql").read_text())
            conn.execute(NATIVE_VIEW)
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


# Archives do not distinguish success from expiry/fatal failure. Do not infer delivery.
NATIVE_VIEW = """
CREATE OR REPLACE VIEW lab_native_messages AS
SELECT id, qfname, sender, created AS created_at, num_attempts AS attempts,
       CASE WHEN isfinite(send_time) THEN send_time ELSE NULL END AS next_attempt_at,
       'unassigned'::text AS carrier,
       CASE WHEN num_attempts = 0 THEN 'queued' ELSE 'retry' END AS status
FROM mms_messages WHERE qdir = 'mmsbox_outgoing'
UNION ALL
SELECT id, qfname, sender, created, num_attempts,
       CASE WHEN isfinite(send_time) THEN send_time ELSE NULL END,
       'unassigned', 'archived'
FROM archived_mms_messages WHERE qdir = 'mmsbox_outgoing';
"""

if __name__ == "__main__":
    init_db()
