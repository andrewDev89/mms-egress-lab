import json
import time
import urllib.error
import urllib.request

from prometheus_client import start_http_server

from . import config
from .db import connect, init_db
from .metrics import (
    delivered_total,
    delivery_attempts,
    failed_total,
    retry_total,
    worker_delivery_seconds,
)
from .repository import (
    claim_next_message,
    mark_delivered,
    mark_delivery_error,
    total_available_tps,
)


def submit_to_haproxy(message):
    url = f"{config.HAPROXY_EGRESS_URL}/submit"
    payload = json.dumps(
        {
            "message_id": message["id"],
            "sender": message["sender"],
            "recipient": message["recipient"],
            "media_url": message["media_url"],
            "text": message["text"],
        }
    ).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if response.status >= 300:
            raise RuntimeError(f"carrier returned HTTP {response.status}")
        return json.loads(response.read().decode())


def run_once(worker_id):
    with connect() as conn:
        available_tps = total_available_tps(conn)
        if available_tps <= 0:
            return "no_capacity"

        message = claim_next_message(conn, worker_id)
        if message is None:
            return "idle"

    delivery_attempts.labels("haproxy").inc()
    try:
        with worker_delivery_seconds.labels("haproxy").time():
            carrier_response = submit_to_haproxy(message)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        with connect() as conn:
            updated = mark_delivery_error(conn, message, str(exc))
        if updated["status"] == "failed":
            failed_total.labels("haproxy").inc()
            return "failed", available_tps
        retry_total.labels("haproxy").inc()
        return "retry", available_tps

    accepted_bind = carrier_response.get("carrier", "unknown")
    with connect() as conn:
        mark_delivered(conn, message["id"], accepted_bind)
    delivered_total.labels(accepted_bind).inc()
    return "delivered", available_tps


def main():
    init_db()
    worker_id = config.WORKER_ID
    start_http_server(config.WORKER_METRICS_PORT)
    print(
        f"worker started | operator={config.WORKER_OPERATOR} "
        f"egress_url={config.HAPROXY_EGRESS_URL} worker_id={worker_id} "
        f"metrics_port={config.WORKER_METRICS_PORT}",
        flush=True,
    )
    while True:
        result = run_once(worker_id)
        if result in {"idle", "no_capacity"}:
            time.sleep(config.WORKER_POLL_SECONDS)
        else:
            _status, available_tps = result
            time.sleep(1 / max(1, available_tps))


if __name__ == "__main__":
    main()
