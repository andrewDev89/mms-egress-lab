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
    get_carrier_state,
    is_capacity_available,
    mark_delivered,
    mark_delivery_error,
)


def submit_to_carrier(message):
    url = f"{config.CARRIER_URLS[message['carrier']]}/submit"
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
        return response.read().decode()


def run_once(carrier, worker_id):
    with connect() as conn:
        state = get_carrier_state(conn, carrier)
        if not is_capacity_available(state):
            return "no_capacity"

        message = claim_next_message(conn, carrier, worker_id)
        if message is None:
            return "idle"

    delivery_attempts.labels(carrier).inc()
    try:
        with worker_delivery_seconds.labels(carrier).time():
            submit_to_carrier(message)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        with connect() as conn:
            updated = mark_delivery_error(conn, message, str(exc))
        if updated["status"] == "failed":
            failed_total.labels(carrier).inc()
            return "failed"
        retry_total.labels(carrier).inc()
        return "retry"

    with connect() as conn:
        mark_delivered(conn, message["id"])
    delivered_total.labels(carrier).inc()
    return "delivered"


def main():
    init_db()
    carrier = config.WORKER_CARRIER
    worker_id = config.WORKER_ID
    start_http_server(config.WORKER_METRICS_PORT)
    print(
        f"worker started | carrier={carrier} worker_id={worker_id} "
        f"metrics_port={config.WORKER_METRICS_PORT}",
        flush=True,
    )
    while True:
        result = run_once(carrier, worker_id)
        if result in {"idle", "no_capacity"}:
            time.sleep(config.WORKER_POLL_SECONDS)
        else:
            with connect() as conn:
                state = get_carrier_state(conn, carrier)
            tps_capacity = max(1, state["tps_capacity"] if state else 1)
            time.sleep(1 / tps_capacity)


if __name__ == "__main__":
    main()
