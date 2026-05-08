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
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "1"))
WORKER_ID = os.getenv("WORKER_ID", "mms-worker")
WORKER_CARRIER = os.getenv("WORKER_CARRIER", "tmobile-sdg1")
WORKER_METRICS_PORT = int(os.getenv("WORKER_METRICS_PORT", "9102"))


def carrier_env_key(carrier):
    return f"{carrier.upper().replace('-', '_')}_URL"


CARRIER_URLS = {
    carrier: os.getenv(carrier_env_key(carrier), f"http://{carrier}:8080")
    for carrier in CARRIERS
}
