import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

from prometheus_client import start_http_server

from . import config
from .db import connect, init_db
from .event_log import log_event
from .metrics import (
    delivered_total,
    delivery_attempts,
    egress_rejections,
    failed_total,
    retry_total,
    transport_errors,
    worker_delivery_seconds,
    worker_send_tps,
)
from .repository import (
    claim_messages,
    mark_delivered_many,
    mark_delivery_error,
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
        return json.loads(response.read().decode())


def process_message(message):
    delivery_attempts.labels("haproxy").inc()
    try:
        with worker_delivery_seconds.labels("haproxy").time():
            carrier_response = submit_to_haproxy(message)
    except urllib.error.HTTPError as exc:
        code = str(exc.code) if exc.code in (429, 503) else "other"
        egress_rejections.labels(code).inc()
        exc.close()
        return {
            "status": "error",
            "message": message,
            "error": str(exc),
            "http_status": exc.code,
            "error_type": "http_rejection",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        transport_errors.inc()
        return {
            "status": "error",
            "message": message,
            "error": str(exc),
            "error_type": type(exc).__name__,
        }

    accepted_bind = carrier_response.get("carrier", "unknown")
    return {
        "status": "delivered",
        "message": message,
        "carrier": accepted_bind,
    }


def run_batch(worker_id, executor):
    with connect() as conn:
        batch_size = min(config.WORKER_SEND_TPS, config.WORKER_BATCH_SIZE)
        messages = claim_messages(conn, worker_id, batch_size)
        if not messages:
            return "idle", 0

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

    events = []
    with connect() as conn:
        for carrier, message_ids in delivered_by_carrier.items():
            delivered = mark_delivered_many(conn, message_ids, carrier)
            delivered_total.labels(carrier).inc(len(delivered))
            for row in delivered:
                events.append({
                    "event": "delivered",
                    "message_id": row["id"],
                    "attempt": row["attempts"],
                    "carrier": carrier,
                })

        for result in errors:
            message = result["message"]
            updated = mark_delivery_error(conn, message, result["error"])
            if updated is None:  # The control page may clear an in-flight message.
                continue
            if updated["status"] == "failed":
                failed_total.labels("haproxy").inc()
            else:
                retry_total.labels("haproxy").inc()
            events.append({
                "event": "delivery_failed" if updated["status"] == "failed" else "retry_scheduled",
                "level": "error" if updated["status"] == "failed" else "warning",
                "message_id": message["id"],
                "attempt": message.get("attempts"),
                "http_status": result.get("http_status"),
                "error_type": result.get("error_type", "delivery_error"),
                "next_attempt_at": updated.get("next_attempt_at") if updated["status"] == "retry" else None,
            })

    # Emit outcomes only after the transaction commits.
    for event in events:
        log_event("sender", worker_id=worker_id, **event)

    return "sent", len(messages)


def main():
    init_db()
    worker_id = config.WORKER_ID
    worker_send_tps.set(config.WORKER_SEND_TPS)
    start_http_server(config.WORKER_METRICS_PORT)
    log_event(
        "sender", "sender_started", worker_id=worker_id,
        operator=config.WORKER_OPERATOR,
        send_tps=config.WORKER_SEND_TPS,
        concurrency=config.WORKER_CONCURRENCY,
        batch_size=config.WORKER_BATCH_SIZE,
    )
    with ThreadPoolExecutor(max_workers=config.WORKER_CONCURRENCY) as executor:
        while True:
            started_at = time.monotonic()
            status, attempted = run_batch(worker_id, executor)
            if status == "idle":
                time.sleep(config.WORKER_POLL_SECONDS)
            else:
                elapsed = time.monotonic() - started_at
                time.sleep(max(0, attempted / config.WORKER_SEND_TPS - elapsed))


if __name__ == "__main__":
    main()
