import os


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mms:mms@psql-mms:5432/psql_mms",
)

CARRIERS = [carrier.strip() for carrier in os.getenv("CARRIERS", "carrier1,carrier2").split(",") if carrier.strip()]

DEFAULT_TPS_CAPACITY = int(os.getenv("DEFAULT_TPS_CAPACITY", "10"))
DEFAULT_MAX_ATTEMPTS = int(os.getenv("DEFAULT_MAX_ATTEMPTS", "3"))
WORKER_POLL_SECONDS = float(os.getenv("WORKER_POLL_SECONDS", "1"))
WORKER_ID = os.getenv("WORKER_ID", "mms-worker")
WORKER_CARRIER = os.getenv("WORKER_CARRIER", "carrier1")
WORKER_METRICS_PORT = int(os.getenv("WORKER_METRICS_PORT", "9102"))

CARRIER_URLS = {
    carrier: os.getenv(f"{carrier.upper()}_URL", f"http://{carrier}:8080")
    for carrier in CARRIERS
}
