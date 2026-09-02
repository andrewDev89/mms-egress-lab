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
egress_rejections = Counter(
    "mms_egress_rejections_total",
    "Rejected HTTP delivery attempts, including repeated attempts for the same message.",
    ["status_code"],
)
# Initialize bounded label values so rate panels have a zero baseline before failures.
for status_code in ("429", "503", "other"):
    egress_rejections.labels(status_code)
transport_errors = Counter(
    "mms_egress_transport_errors_total",
    "Delivery attempts that failed without an HTTP response.",
)
worker_send_tps = Gauge(
    "mms_worker_send_tps",
    "Configured worker attempt rate, independent of healthy carrier capacity.",
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
queue_age_bucket = Gauge(
    "mms_queue_age_bucket",
    "Active PostgreSQL queue entries grouped by queue age bucket.",
    ["carrier", "bucket"],
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
