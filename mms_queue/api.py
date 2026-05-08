from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from . import config
from .db import connect, init_db
from .metrics import (
    carrier_capacity,
    carrier_health,
    queue_depth,
    render_metrics,
)
from .repository import (
    create_message,
    get_carrier_state,
    get_message,
    list_carriers,
    list_messages,
    queue_depths,
    set_carrier_capacity,
    set_carrier_health,
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


@app.on_event("startup")
def startup():
    init_db()


@app.post("/messages")
def enqueue_message(payload: MessageCreate, response: Response):
    with connect() as conn:
        row, immediate_capacity = create_message(conn, payload.model_dump())

    if immediate_capacity:
        response.status_code = status.HTTP_202_ACCEPTED
        return {
            "message_id": row["id"],
            "status": row["status"],
            "operator": "tmobile",
            "assigned_bind": row["carrier"],
            "delivery": "accepted_for_delivery",
        }

    response.status_code = status.HTTP_429_TOO_MANY_REQUESTS
    return {
        "message_id": row["id"],
        "status": row["status"],
        "operator": "tmobile",
        "assigned_bind": row["carrier"],
        "delivery": "queued_due_to_carrier_backpressure",
    }


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
    if row is None:
        raise HTTPException(status_code=404, detail="carrier not found")
    carrier_capacity.labels(carrier).set(payload.tps_capacity)
    return serialize(row)


@app.post("/carriers/{carrier}/health")
def update_health(carrier: str, payload: HealthUpdate):
    with connect() as conn:
        row = set_carrier_health(conn, carrier, payload.healthy)
    if row is None:
        raise HTTPException(status_code=404, detail="carrier not found")
    carrier_health.labels(carrier).set(1 if payload.healthy else 0)
    return serialize(row)


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

        for carrier in config.CARRIERS:
            for message_status in ("queued", "sending", "retry", "delivered", "failed"):
                if (carrier, message_status) not in seen:
                    queue_depth.labels(carrier, message_status).set(0)

    body, content_type = render_metrics()
    return Response(content=body, media_type=content_type)


@app.get("/health")
def health():
    with connect() as conn:
        conn.execute("SELECT 1")
    return {"ok": True}
