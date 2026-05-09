import json
import socket
import urllib.error
import urllib.request
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from starlette.responses import HTMLResponse

from . import config
from .db import connect, init_db
from .metrics import (
    carrier_capacity,
    carrier_health,
    messages_submitted,
    queue_age_bucket,
    queue_depth,
    queue_oldest_age,
    render_metrics,
)
from .repository import (
    create_message,
    clear_messages,
    get_carrier_state,
    get_message,
    list_carriers,
    list_messages,
    queue_age_buckets,
    queue_depths,
    queue_oldest_ages,
    set_carrier_capacity,
    set_carrier_health,
    total_available_tps,
)

app = FastAPI(
    title="MMS Egress Queue Lab",
    description="Production-flavored PostgreSQL queue demo for carrier delivery backpressure.",
    version="0.1.0",
)


class MessageCreate(BaseModel):
    sender: str = Field(..., examples=["12065550100"])
    recipient: str = Field(..., examples=["12065550199"])
    media_url: str | None = Field(None, examples=["https://example.com/image.jpg"])
    text: str | None = Field(None, examples=["Demo MMS payload"])
    max_attempts: int = Field(
        config.DEFAULT_MAX_ATTEMPTS,
        ge=1,
        le=config.MAX_ATTEMPTS_LIMIT,
        examples=[100],
    )


class BurstCreate(BaseModel):
    count: int = Field(..., ge=1, le=5000, examples=[1000])
    sender: str = Field("12065550100", examples=["12065550100"])
    recipient_prefix: str = Field("1206555", examples=["1206555"])
    media_url: str | None = Field(None, examples=["https://example.com/image.jpg"])
    text: str = Field("Demo MMS payload", examples=["Demo MMS payload"])
    max_attempts: int = Field(
        config.DEFAULT_MAX_ATTEMPTS,
        ge=1,
        le=config.MAX_ATTEMPTS_LIMIT,
        examples=[100],
    )


class CapacityUpdate(BaseModel):
    tps_capacity: int = Field(..., ge=0, examples=[0, 10])


class HealthUpdate(BaseModel):
    healthy: bool = Field(..., examples=[False])


def serialize(row):
    if row is None:
        return None
    return dict(row)


def serialize_message(row):
    data = serialize(row)
    data["operator"] = "tmobile"
    data["assigned_bind"] = data.pop("carrier")
    return data


def sync_mock_bind_health(carrier, healthy):
    url = f"{config.CARRIER_URLS[carrier]}/health"
    request = urllib.request.Request(
        url,
        data=json.dumps({"healthy": healthy}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            response.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"carrier state saved, but mock bind health sync failed: {exc}",
        ) from exc


def haproxy_runtime_cmd(command):
    with socket.create_connection(
        (config.HAPROXY_RUNTIME_HOST, config.HAPROXY_RUNTIME_PORT),
        timeout=3,
    ) as sock:
        sock.sendall((command + "\n").encode())
        sock.shutdown(socket.SHUT_WR)
        return sock.recv(4096).decode(errors="replace").strip()


def sync_haproxy_capacity(total_tps):
    threshold = total_tps * config.HAPROXY_RATE_WINDOW_SECONDS
    command = (
        f"set map {config.HAPROXY_CAPACITY_MAP} "
        f"{config.HAPROXY_CAPACITY_KEY} {threshold}"
    )
    response = haproxy_runtime_cmd(command)

    if "entry not found" in response.lower():
        response = haproxy_runtime_cmd(
            f"add map {config.HAPROXY_CAPACITY_MAP} "
            f"{config.HAPROXY_CAPACITY_KEY} {threshold}"
        )

    if "error" in response.lower() or "not found" in response.lower():
        raise HTTPException(
            status_code=502,
            detail=f"HAProxy capacity sync failed: {response}",
        )

    return {
        "allowed_tps": total_tps,
        "haproxy_threshold": threshold,
        "haproxy_response": response,
    }


@app.on_event("startup")
def startup():
    init_db()


@app.post("/messages")
def enqueue_message(payload: MessageCreate, response: Response):
    with connect() as conn:
        row, immediate_capacity = create_message(conn, payload.model_dump())

    if immediate_capacity:
        messages_submitted.labels("accepted_for_delivery").inc()
        response.status_code = status.HTTP_202_ACCEPTED
        return {
            "message_id": row["id"],
            "status": row["status"],
            "operator": "tmobile",
            "assigned_bind": row["carrier"],
            "delivery": "accepted_for_delivery",
        }

    messages_submitted.labels("queued_due_to_carrier_backpressure").inc()
    response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
    return {
        "message_id": row["id"],
        "status": row["status"],
        "operator": "tmobile",
        "assigned_bind": row["carrier"],
        "delivery": "queued_due_to_carrier_backpressure",
    }


@app.post("/demo/messages/burst")
def enqueue_message_burst(payload: BurstCreate, response: Response):
    message_ids = []
    accepted_for_delivery = 0
    queued_due_to_backpressure = 0

    with connect() as conn:
        for index in range(payload.count):
            message_payload = {
                "sender": payload.sender,
                "recipient": f"{payload.recipient_prefix}{index:04d}",
                "media_url": payload.media_url,
                "text": f"{payload.text} #{index + 1}",
                "max_attempts": payload.max_attempts,
            }
            row, immediate_capacity = create_message(conn, message_payload)
            message_ids.append(row["id"])
            if immediate_capacity:
                accepted_for_delivery += 1
            else:
                queued_due_to_backpressure += 1

            if (index + 1) % config.BURST_COMMIT_INTERVAL == 0:
                conn.commit()

        conn.commit()

    messages_submitted.labels("accepted_for_delivery").inc(accepted_for_delivery)
    messages_submitted.labels("queued_due_to_carrier_backpressure").inc(
        queued_due_to_backpressure
    )

    if queued_due_to_backpressure:
        response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
    else:
        response.status_code = status.HTTP_202_ACCEPTED

    return {
        "operator": "tmobile",
        "requested": payload.count,
        "enqueued": len(message_ids),
        "accepted_for_delivery": accepted_for_delivery,
        "queued_due_to_carrier_backpressure": queued_due_to_backpressure,
        "first_message_id": message_ids[0],
        "last_message_id": message_ids[-1],
    }


@app.post("/demo/messages/clear")
def clear_demo_messages():
    with connect() as conn:
        deleted = clear_messages(conn)

    for carrier in [*config.CARRIERS, "unassigned"]:
        for message_status in ("queued", "sending", "retry", "delivered", "failed"):
            queue_depth.labels(carrier, message_status).set(0)
            queue_oldest_age.labels(carrier, message_status).set(0)
        for bucket in ("<30s", "30-60s", "1-5m", ">5m"):
            queue_age_bucket.labels(carrier, bucket).set(0)

    return {
        "operator": "tmobile",
        "deleted_messages": deleted,
        "note": "Prometheus counters remain historical; queue depth resets on the next scrape.",
    }


@app.get("/demo/control", response_class=HTMLResponse)
def demo_control():
    return """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MMS Egress Demo Control</title>
  <style>
    :root {
      color-scheme: dark;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #11151b;
      color: #e7edf5;
    }
    body {
      margin: 0;
      min-height: 100vh;
      background: #11151b;
    }
    main {
      width: min(1100px, calc(100% - 40px));
      margin: 0 auto;
      padding: 28px 0 40px;
    }
    header {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: center;
      border-bottom: 1px solid #263241;
      padding-bottom: 18px;
      margin-bottom: 22px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 28px;
      font-weight: 700;
      letter-spacing: 0;
    }
    p {
      margin: 0;
      color: #9fb0c3;
      line-height: 1.5;
    }
    a {
      color: #8bd3ff;
      text-decoration: none;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
    }
    .panel {
      background: #171d25;
      border: 1px solid #293545;
      border-radius: 8px;
      padding: 18px;
    }
    .bind-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
    }
    .bind-title h2 {
      margin: 0;
      font-size: 20px;
      letter-spacing: 0;
    }
    .state {
      min-width: 84px;
      text-align: center;
      padding: 6px 10px;
      border-radius: 999px;
      font-weight: 700;
      font-size: 13px;
      background: #314054;
      color: #d7e3f2;
    }
    .state.up {
      background: #153f2a;
      color: #80f0a8;
    }
    .state.down {
      background: #4a1f27;
      color: #ff9cad;
    }
    .controls {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    button {
      border: 1px solid transparent;
      border-radius: 6px;
      padding: 12px 14px;
      color: #f8fbff;
      font-size: 15px;
      font-weight: 700;
      cursor: pointer;
      background: #2a6df4;
    }
    button.danger {
      background: #c7374a;
    }
    button.secondary {
      background: #263241;
      border-color: #405167;
    }
    button:disabled {
      opacity: 0.55;
      cursor: wait;
    }
    .capacity {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
      margin-top: 10px;
    }
    input {
      width: 100%;
      box-sizing: border-box;
      border-radius: 6px;
      border: 1px solid #405167;
      background: #10161f;
      color: #e7edf5;
      padding: 11px 12px;
      font-size: 15px;
    }
    .links {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }
    .danger-wide {
      width: 100%;
      margin-top: 12px;
    }
    .links a {
      border: 1px solid #344459;
      border-radius: 6px;
      padding: 10px 12px;
      background: #151b23;
    }
    pre {
      min-height: 120px;
      white-space: pre-wrap;
      word-break: break-word;
      background: #0c1117;
      border: 1px solid #293545;
      border-radius: 8px;
      padding: 14px;
      color: #b7c8dc;
      overflow: auto;
    }
    @media (max-width: 760px) {
      header, .grid {
        display: block;
      }
      .panel {
        margin-bottom: 14px;
      }
      header > div:last-child {
        margin-top: 14px;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>T-Mobile SDG Bind Control</h1>
        <p>Bring a bind down live, restore it, or tune TPS while watching Grafana drain and rebalance traffic.</p>
      </div>
      <button class="secondary" onclick="refreshState()">Refresh</button>
    </header>

    <section class="grid" id="binds"></section>

    <section class="panel">
      <h2>Traffic Burst</h2>
      <p>Generate a visible queue/worker load for Grafana.</p>
      <div class="capacity">
        <input id="burstCount" type="number" min="1" max="5000" value="1000">
        <button onclick="sendBurst()">Send Burst</button>
      </div>
      <button class="danger danger-wide" onclick="clearQueue()">Clear Queue</button>
      <div class="links">
        <a href="/docs" target="_blank">API Docs</a>
        <a href="http://localhost:3000/d/mms-egress-tmobile/mms-egress-lab-t-mobile-queue" target="_blank">Grafana Dashboard</a>
        <a href="http://localhost:9090" target="_blank">Prometheus</a>
      </div>
    </section>

    <section>
      <pre id="log">Loading bind state...</pre>
    </section>
  </main>

  <script>
    const bindContainer = document.getElementById("binds");
    const log = document.getElementById("log");

    function writeLog(value) {
      log.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
    }

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: {"Content-Type": "application/json"},
        ...options
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(JSON.stringify(data));
      }
      return data;
    }

    function bindCard(bind) {
      const stateClass = bind.healthy ? "up" : "down";
      const stateText = bind.healthy ? "UP" : "DOWN";
      return `
        <article class="panel">
          <div class="bind-title">
            <h2>${bind.carrier}</h2>
            <span class="state ${stateClass}">${stateText}</span>
          </div>
          <div class="controls">
            <button class="danger" onclick="setHealth('${bind.carrier}', false)">Bring Down</button>
            <button onclick="setHealth('${bind.carrier}', true)">Restore</button>
          </div>
          <p>Configured TPS: <strong>${bind.tps_capacity}</strong></p>
          <div class="capacity">
            <input id="cap-${bind.carrier}" type="number" min="0" value="${bind.tps_capacity}">
            <button class="secondary" onclick="setCapacity('${bind.carrier}')">Set TPS</button>
          </div>
        </article>
      `;
    }

    async function refreshState() {
      try {
        const binds = await api("/carriers");
        bindContainer.innerHTML = binds.map(bindCard).join("");
        writeLog({updated: new Date().toISOString(), binds});
      } catch (error) {
        writeLog(error.message);
      }
    }

    async function setHealth(carrier, healthy) {
      try {
        const result = await api(`/carriers/${carrier}/health`, {
          method: "POST",
          body: JSON.stringify({healthy})
        });
        writeLog(result);
        await refreshState();
      } catch (error) {
        writeLog(error.message);
      }
    }

    async function setCapacity(carrier) {
      const input = document.getElementById(`cap-${carrier}`);
      try {
        const result = await api(`/carriers/${carrier}/capacity`, {
          method: "POST",
          body: JSON.stringify({tps_capacity: Number(input.value)})
        });
        writeLog(result);
        await refreshState();
      } catch (error) {
        writeLog(error.message);
      }
    }

    async function sendBurst() {
      const count = Number(document.getElementById("burstCount").value);
      try {
        const result = await api("/demo/messages/burst", {
          method: "POST",
          body: JSON.stringify({
            count,
            sender: "12065550100",
            recipient_prefix: "1206555",
            text: "Grafana control page traffic",
            max_attempts: 100
          })
        });
        writeLog(result);
      } catch (error) {
        writeLog(error.message);
      }
    }

    async function clearQueue() {
      const ok = window.confirm("Clear all demo messages from PostgreSQL?");
      if (!ok) {
        return;
      }
      try {
        const result = await api("/demo/messages/clear", {
          method: "POST",
          body: "{}"
        });
        writeLog(result);
        await refreshState();
      } catch (error) {
        writeLog(error.message);
      }
    }

    refreshState();
    setInterval(refreshState, 5000);
  </script>
</body>
</html>
    """


@app.get("/messages/{message_id}")
def read_message(message_id: int):
    with connect() as conn:
        row = get_message(conn, message_id)
    if row is None:
        raise HTTPException(status_code=404, detail="message not found")
    return serialize_message(row)


@app.get("/messages")
def read_messages(
    status_filter: Literal["queued", "sending", "retry", "delivered", "failed"] | None = Query(
        None, alias="status"
    ),
    bind: str | None = None,
    limit: int = Query(50, ge=1, le=200),
):
    with connect() as conn:
        rows = list_messages(conn, status=status_filter, carrier=bind, limit=limit)
    return [serialize_message(row) for row in rows]


@app.get("/carriers")
def read_carriers():
    with connect() as conn:
        return [serialize(row) for row in list_carriers(conn)]


@app.post("/carriers/{carrier}/capacity")
def update_capacity(carrier: str, payload: CapacityUpdate):
    with connect() as conn:
        row = set_carrier_capacity(conn, carrier, payload.tps_capacity)
        total_tps = total_available_tps(conn)
    if row is None:
        raise HTTPException(status_code=404, detail="carrier not found")
    haproxy_state = sync_haproxy_capacity(total_tps)
    carrier_capacity.labels(carrier).set(payload.tps_capacity)
    data = serialize(row)
    data["haproxy"] = haproxy_state
    return data


@app.post("/carriers/{carrier}/health")
def update_health(carrier: str, payload: HealthUpdate):
    with connect() as conn:
        row = set_carrier_health(conn, carrier, payload.healthy)
        total_tps = total_available_tps(conn)
    if row is None:
        raise HTTPException(status_code=404, detail="carrier not found")
    sync_mock_bind_health(carrier, payload.healthy)
    haproxy_state = sync_haproxy_capacity(total_tps)
    carrier_health.labels(carrier).set(1 if payload.healthy else 0)
    data = serialize(row)
    data["haproxy"] = haproxy_state
    return data


@app.get("/metrics")
def metrics():
    with connect() as conn:
        for carrier in list_carriers(conn):
            carrier_health.labels(carrier["carrier"]).set(1 if carrier["healthy"] else 0)
            carrier_capacity.labels(carrier["carrier"]).set(carrier["tps_capacity"])

        seen = set()
        for row in queue_depths(conn):
            labels = (row["carrier"], row["status"])
            seen.add(labels)
            queue_depth.labels(*labels).set(row["depth"])

        for carrier in [*config.CARRIERS, "unassigned"]:
            for message_status in ("queued", "sending", "retry", "delivered", "failed"):
                if (carrier, message_status) not in seen:
                    queue_depth.labels(carrier, message_status).set(0)

        seen_age = set()
        for row in queue_oldest_ages(conn):
            labels = (row["carrier"], row["status"])
            seen_age.add(labels)
            queue_oldest_age.labels(*labels).set(float(row["age_seconds"]))

        for carrier in [*config.CARRIERS, "unassigned"]:
            for message_status in ("queued", "sending", "retry"):
                if (carrier, message_status) not in seen_age:
                    queue_oldest_age.labels(carrier, message_status).set(0)

        seen_buckets = set()
        for row in queue_age_buckets(conn):
            labels = (row["carrier"], row["bucket"])
            seen_buckets.add(labels)
            queue_age_bucket.labels(*labels).set(row["depth"])

        for carrier in [*config.CARRIERS, "unassigned"]:
            for bucket in ("<30s", "30-60s", "1-5m", ">5m"):
                if (carrier, bucket) not in seen_buckets:
                    queue_age_bucket.labels(carrier, bucket).set(0)

    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}
