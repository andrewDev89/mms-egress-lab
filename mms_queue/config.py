import os


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mms:mms@psql-mms:5432/psql_mms",
)

CARRIERS = [
    carrier.strip()
    for carrier in os.getenv("CARRIERS", "tmobile-sdg1,tmobile-sdg2").split(",")
    if carrier.strip()
]

DEFAULT_TPS_CAPACITY = int(os.getenv("DEFAULT_TPS_CAPACITY", "10"))
DEFAULT_MAX_ATTEMPTS = int(os.getenv("DEFAULT_MAX_ATTEMPTS", "3"))
MAX_ATTEMPTS_LIMIT = int(os.getenv("MAX_ATTEMPTS_LIMIT", "1000"))
BURST_COMMIT_INTERVAL = int(os.getenv("BURST_COMMIT_INTERVAL", "100"))
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "1"))
WORKER_CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "100"))
WORKER_BATCH_SIZE = int(os.getenv("WORKER_BATCH_SIZE", "1000"))
WORKER_ID = os.getenv("WORKER_ID", "mms-worker")
WORKER_OPERATOR = os.getenv("WORKER_OPERATOR", "tmobile")
WORKER_METRICS_PORT = int(os.getenv("WORKER_METRICS_PORT", "9102"))
HAPROXY_EGRESS_URL = os.getenv("HAPROXY_EGRESS_URL", "http://haproxy:8080")
HAPROXY_RUNTIME_HOST = os.getenv("HAPROXY_RUNTIME_HOST", "haproxy")
HAPROXY_RUNTIME_PORT = int(os.getenv("HAPROXY_RUNTIME_PORT", "9999"))
HAPROXY_RATE_WINDOW_SECONDS = int(os.getenv("HAPROXY_RATE_WINDOW_SECONDS", "5"))
HAPROXY_CAPACITY_MAP = os.getenv(
    "HAPROXY_CAPACITY_MAP",
    "/usr/local/etc/haproxy/capacity.map",
)
HAPROXY_CAPACITY_KEY = os.getenv("HAPROXY_CAPACITY_KEY", "1")


def carrier_env_key(carrier):
    return f"{carrier.upper().replace('-', '_')}_URL"


CARRIER_URLS = {
    carrier: os.getenv(carrier_env_key(carrier), f"http://{carrier}:8080")
    for carrier in CARRIERS
}
