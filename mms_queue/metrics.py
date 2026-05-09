from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

delivery_attempts = Counter(
    "mms_delivery_attempts_total",
    "Total carrier delivery attempts.",
    ["carrier"],
)
messages_submitted = Counter(
    "mms_messages_submitted_total",
    "Total T-Mobile operator-level messages accepted into PostgreSQL.",
    ["result"],
)
delivered_total = Counter(
    "mms_delivered_total",
    "Total messages delivered to carriers.",
    ["carrier"],
)
failed_total = Counter(
    "mms_failed_total",
    "Total messages that reached terminal failure.",
    ["carrier"],
)
retry_total = Counter(
    "mms_retry_total",
    "Total messages scheduled for retry.",
    ["carrier"],
)
queue_depth = Gauge(
    "mms_queue_depth",
    "Messages in PostgreSQL by carrier and status.",
    ["carrier", "status"],
)
queue_oldest_age = Gauge(
    "mms_queue_oldest_age_seconds",
    "Age in seconds of the oldest active PostgreSQL queue entry by carrier and status.",
    ["carrier", "status"],
)
carrier_health = Gauge(
    "mms_carrier_healthy",
    "Carrier health state, 1 for healthy and 0 for unhealthy.",
    ["carrier"],
)
carrier_capacity = Gauge(
    "mms_carrier_tps_capacity",
    "Configured carrier TPS capacity.",
    ["carrier"],
)
worker_delivery_seconds = Histogram(
    "mms_worker_delivery_seconds",
    "Time spent delivering a message to a carrier.",
    ["carrier"],
)


def render_metrics():
    return generate_latest(), CONTENT_TYPE_LATEST
