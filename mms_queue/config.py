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

DEFAULT_TPS_CAPACITY = int(os.getenv("DEFAULT_TPS_CAPACITY", "300"))
BLAST_MESSAGE_COUNT = int(os.getenv("BLAST_MESSAGE_COUNT", "150000"))
BLAST_RATE_PER_SECOND = int(os.getenv("BLAST_RATE_PER_SECOND", "900"))
BLAST_MAX_MESSAGES = int(os.getenv("BLAST_MAX_MESSAGES", "250000"))
BLAST_MAX_RATE_PER_SECOND = int(os.getenv("BLAST_MAX_RATE_PER_SECOND", "5000"))
HAPROXY_RUNTIME_HOST = os.getenv("HAPROXY_RUNTIME_HOST", "haproxy")
HAPROXY_RUNTIME_PORT = int(os.getenv("HAPROXY_RUNTIME_PORT", "9999"))
HAPROXY_RATE_WINDOW_SECONDS = int(os.getenv("HAPROXY_RATE_WINDOW_SECONDS", "5"))
HAPROXY_SYNC_SECONDS = float(os.getenv("HAPROXY_SYNC_SECONDS", "10"))
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
