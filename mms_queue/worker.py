import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

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
    claim_messages,
    mark_delivered_many,
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


def process_message(message):
    delivery_attempts.labels("haproxy").inc()
    try:
        with worker_delivery_seconds.labels("haproxy").time():
            carrier_response = submit_to_haproxy(message)
    except (urllib.error.URLError, TimeoutError, RuntimeError) as exc:
        return {
            "status": "error",
            "message": message,
            "error": str(exc),
        }

    accepted_bind = carrier_response.get("carrier", "unknown")
    return {
        "status": "delivered",
        "message": message,
        "carrier": accepted_bind,
    }


def run_batch(worker_id, executor):
    with connect() as conn:
        available_tps = total_available_tps(conn)
        if available_tps <= 0:
            return "no_capacity", 0

        batch_size = max(1, min(available_tps, config.WORKER_BATCH_SIZE))
        messages = claim_messages(conn, worker_id, batch_size)
        if not messages:
            return "idle", available_tps

    futures = [executor.submit(process_message, message) for message in messages]
    delivered_by_carrier = {}
    errors = []

    for future in as_completed(futures):
        result = future.result()
        message = result["message"]
        if result["status"] == "delivered":
            delivered_by_carrier.setdefault(result["carrier"], []).append(message["id"])
        else:
            errors.append(result)

    with connect() as conn:
        for carrier, message_ids in delivered_by_carrier.items():
            mark_delivered_many(conn, message_ids, carrier)
            delivered_total.labels(carrier).inc(len(message_ids))

        for result in errors:
            message = result["message"]
            updated = mark_delivery_error(conn, message, result["error"])
            if updated["status"] == "failed":
                failed_total.labels("haproxy").inc()
            else:
                retry_total.labels("haproxy").inc()

    return "sent", available_tps


def main():
    init_db()
    worker_id = config.WORKER_ID
    start_http_server(config.WORKER_METRICS_PORT)
    print(
        f"worker started | operator={config.WORKER_OPERATOR} "
        f"egress_url={config.HAPROXY_EGRESS_URL} worker_id={worker_id} "
        f"metrics_port={config.WORKER_METRICS_PORT} "
        f"concurrency={config.WORKER_CONCURRENCY} "
        f"batch_size={config.WORKER_BATCH_SIZE}",
        flush=True,
    )
    with ThreadPoolExecutor(max_workers=config.WORKER_CONCURRENCY) as executor:
        while True:
            started_at = time.monotonic()
            status, _available_tps = run_batch(worker_id, executor)
            if status in {"idle", "no_capacity"}:
                time.sleep(config.WORKER_POLL_SECONDS)
            else:
                elapsed = time.monotonic() - started_at
                time.sleep(max(0, 1 - elapsed))


if __name__ == "__main__":
    main()
