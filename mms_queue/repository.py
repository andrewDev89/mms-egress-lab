from datetime import datetime, timezone

from . import config


RETRY_DELAYS_SECONDS = [2, 5, 10]


def utcnow():
    return datetime.now(timezone.utc)


def retry_delay_for_attempt(attempts):
    index = max(0, min(attempts - 1, len(RETRY_DELAYS_SECONDS) - 1))
    return RETRY_DELAYS_SECONDS[index]


def is_capacity_available(carrier_state):
    return bool(carrier_state and carrier_state["healthy"] and carrier_state["tps_capacity"] > 0)


def is_any_bind_available(conn):
    row = conn.execute(
        """
        SELECT 1
        FROM carrier_state
        WHERE healthy = TRUE
          AND tps_capacity > 0
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def total_available_tps(conn):
    row = conn.execute(
        """
        SELECT COALESCE(sum(tps_capacity), 0) AS total_tps
        FROM carrier_state
        WHERE healthy = TRUE
        """
    ).fetchone()
    return row["total_tps"]


def create_message(conn, payload):
    row = conn.execute(
        """
        INSERT INTO messages (
            sender, recipient, media_url, text, status, max_attempts, accepted_at
        )
        VALUES (%s, %s, %s, %s, 'queued', %s, now())
        RETURNING *
        """,
        (
            payload["sender"],
            payload["recipient"],
            payload.get("media_url"),
            payload.get("text"),
            payload.get("max_attempts", config.DEFAULT_MAX_ATTEMPTS),
        ),
    ).fetchone()

    return row, is_any_bind_available(conn)


def get_carrier_state(conn, carrier):
    return conn.execute(
        "SELECT carrier, healthy, tps_capacity, updated_at FROM carrier_state WHERE carrier = %s",
        (carrier,),
    ).fetchone()


def list_carriers(conn):
    return conn.execute(
        "SELECT carrier, healthy, tps_capacity, updated_at FROM carrier_state ORDER BY carrier"
    ).fetchall()


def set_carrier_capacity(conn, carrier, tps_capacity):
    return conn.execute(
        """
        UPDATE carrier_state
        SET tps_capacity = %s, updated_at = now()
        WHERE carrier = %s
        RETURNING carrier, healthy, tps_capacity, updated_at
        """,
        (tps_capacity, carrier),
    ).fetchone()


def set_carrier_health(conn, carrier, healthy):
    return conn.execute(
        """
        UPDATE carrier_state
        SET healthy = %s, updated_at = now()
        WHERE carrier = %s
        RETURNING carrier, healthy, tps_capacity, updated_at
        """,
        (healthy, carrier),
    ).fetchone()


def get_message(conn, message_id):
    return conn.execute("SELECT * FROM messages WHERE id = %s", (message_id,)).fetchone()


def list_messages(conn, status=None, carrier=None, limit=50):
    filters = []
    params = []

    if status:
        filters.append("status = %s")
        params.append(status)
    if carrier:
        filters.append("carrier = %s")
        params.append(carrier)

    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    params.append(limit)

    return conn.execute(
        f"SELECT * FROM messages {where} ORDER BY created_at DESC LIMIT %s",
        params,
    ).fetchall()


def claim_next_message(conn, worker_id):
    row = conn.execute(
        """
        WITH next_message AS (
            SELECT id
            FROM messages
            WHERE status IN ('queued', 'retry')
              AND next_attempt_at <= now()
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE messages
        SET status = 'sending',
            carrier = NULL,
            attempts = attempts + 1,
            locked_by = %s,
            locked_at = now(),
            updated_at = now(),
            last_error = NULL
        WHERE id = (SELECT id FROM next_message)
        RETURNING *
        """,
        (worker_id,),
    ).fetchone()
    return row


def claim_messages(conn, worker_id, limit):
    rows = conn.execute(
        """
        WITH next_messages AS (
            SELECT id
            FROM messages
            WHERE status IN ('queued', 'retry')
              AND next_attempt_at <= now()
            ORDER BY created_at
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        UPDATE messages
        SET status = 'sending',
            carrier = NULL,
            attempts = attempts + 1,
            locked_by = %s,
            locked_at = now(),
            updated_at = now(),
            last_error = NULL
        WHERE id IN (SELECT id FROM next_messages)
        RETURNING *
        """,
        (limit, worker_id),
    ).fetchall()
    return rows


def mark_delivered(conn, message_id, carrier):
    return conn.execute(
        """
        UPDATE messages
        SET status = 'delivered',
            carrier = %s,
            delivered_at = now(),
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (carrier, message_id),
    ).fetchone()


def mark_delivered_many(conn, message_ids, carrier):
    if not message_ids:
        return []

    return conn.execute(
        """
        UPDATE messages
        SET status = 'delivered',
            carrier = %s,
            delivered_at = now(),
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now()
        WHERE id = ANY(%s)
        RETURNING *
        """,
        (carrier, message_ids),
    ).fetchall()


def mark_delivery_error(conn, message, error):
    if message["attempts"] >= message["max_attempts"]:
        status = "failed"
        next_attempt_sql = "next_attempt_at"
        params = (status, error, message["id"])
        carrier_sql = "carrier"
    else:
        status = "retry"
        delay = retry_delay_for_attempt(message["attempts"])
        next_attempt_sql = "now() + (%s * interval '1 second')"
        params = (status, error, delay, message["id"])
        carrier_sql = "NULL"

    return conn.execute(
        f"""
        UPDATE messages
        SET status = %s,
            carrier = {carrier_sql},
            last_error = %s,
            next_attempt_at = {next_attempt_sql},
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        params,
    ).fetchone()


def queue_depths(conn):
    rows = conn.execute(
        """
        SELECT COALESCE(carrier, 'unassigned') AS carrier, status, count(*) AS depth
        FROM messages
        GROUP BY COALESCE(carrier, 'unassigned'), status
        ORDER BY COALESCE(carrier, 'unassigned'), status
        """
    ).fetchall()
    return rows


def queue_oldest_ages(conn):
    rows = conn.execute(
        """
        SELECT
            COALESCE(carrier, 'unassigned') AS carrier,
            status,
            EXTRACT(EPOCH FROM now() - min(created_at)) AS age_seconds
        FROM messages
        WHERE status IN ('queued', 'sending', 'retry')
        GROUP BY COALESCE(carrier, 'unassigned'), status
        ORDER BY COALESCE(carrier, 'unassigned'), status
        """
    ).fetchall()
    return rows


def queue_age_buckets(conn):
    rows = conn.execute(
        """
        WITH active_messages AS (
            SELECT
                COALESCE(carrier, 'unassigned') AS carrier,
                EXTRACT(EPOCH FROM now() - created_at) AS age_seconds
            FROM messages
            WHERE status IN ('queued', 'sending', 'retry')
        )
        SELECT
            carrier,
            CASE
                WHEN age_seconds < 30 THEN '<30s'
                WHEN age_seconds < 60 THEN '30-60s'
                WHEN age_seconds < 300 THEN '1-5m'
                ELSE '>5m'
            END AS bucket,
            count(*) AS depth
        FROM active_messages
        GROUP BY carrier, bucket
        ORDER BY carrier, bucket
        """
    ).fetchall()
    return rows


def clear_messages(conn):
    row = conn.execute("DELETE FROM messages RETURNING id").fetchall()
    return len(row)
