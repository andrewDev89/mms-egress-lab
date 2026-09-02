from .mbuni import submit
from .metrics import messages_submitted


def total_available_tps(conn):
    row = conn.execute(
        """
        SELECT COALESCE(sum(tps_capacity), 0) AS total_tps
        FROM carrier_state
        WHERE healthy = TRUE
        """
    ).fetchone()
    return row["total_tps"]


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
    if str(message_id).isdigit():
        return conn.execute("SELECT * FROM lab_native_messages WHERE id = %s", (int(message_id),)).fetchone()
    return conn.execute("""
        SELECT m.* FROM lab_native_messages m WHERE m.id IN (
            SELECT qid FROM mms_message_headers WHERE item = 'H' AND lower(value) = lower(%s)
            UNION ALL
            SELECT qid FROM archived_mms_message_headers WHERE item = 'H' AND lower(value) = lower(%s)
        ) LIMIT 1
    """, ("X-Mbuni-TransactionID:" + message_id,) * 2).fetchone()


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
        f"SELECT * FROM lab_native_messages {where} ORDER BY created_at DESC LIMIT %s",
        params,
    ).fetchall()


def queue_depths(conn):
    rows = conn.execute(
        """
        SELECT COALESCE(carrier, 'unassigned') AS carrier, status, count(*) AS depth
        FROM lab_native_messages
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
        FROM lab_native_messages
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
            FROM lab_native_messages
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


def create_message(payload):
    transaction_id = submit(payload)
    messages_submitted.labels("accepted_for_delivery").inc()
    # Return Mbuni's transaction ID, which can also be searched in its logs.
    return {"id": transaction_id, "status": "queued", "carrier": None}


def clear_messages(conn):
    # Block the native writer during deletion, and remove queue envelopes atomically.
    # Already in-flight HTTP requests cannot be recalled.
    conn.execute("LOCK TABLE mms_messages IN ACCESS EXCLUSIVE MODE")
    rows = conn.execute("DELETE FROM mms_messages WHERE qdir = 'mmsbox_outgoing' RETURNING id").fetchall()
    return len(rows)
