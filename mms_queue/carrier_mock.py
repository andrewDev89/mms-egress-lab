import os
import random
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

CARRIER_NAME = os.getenv("CARRIER_NAME", "carrier")
FAIL_RATE = float(os.getenv("FAIL_RATE", "0"))
SUBMIT_DELAY_SECONDS = float(os.getenv("SUBMIT_DELAY_SECONDS", "0"))

app = FastAPI(title=f"{CARRIER_NAME} Mock Operator")

carrier_healthy = True

submissions_total = Counter(
    "mock_carrier_submissions_total",
    "Mock carrier submission attempts.",
    ["carrier", "result"],
)
health_gauge = Gauge(
    "mock_carrier_healthy",
    "Mock carrier process health.",
    ["carrier"],
)
submit_seconds = Histogram(
    "mock_carrier_submit_seconds",
    "Mock carrier submit latency.",
    ["carrier"],
)


class CarrierMessage(BaseModel):
    message_id: int
    sender: str
    recipient: str
    media_url: str | None = None
    text: str | None = None


class MockHealthUpdate(BaseModel):
    healthy: bool = Field(..., examples=[False])


@app.post("/submit")
def submit(message: CarrierMessage):
    if not carrier_healthy:
        submissions_total.labels(CARRIER_NAME, "unhealthy").inc()
        raise HTTPException(status_code=503, detail="carrier unhealthy")

    with submit_seconds.labels(CARRIER_NAME).time():
        if SUBMIT_DELAY_SECONDS:
            time.sleep(SUBMIT_DELAY_SECONDS)
        if FAIL_RATE and random.random() < FAIL_RATE:
            submissions_total.labels(CARRIER_NAME, "failed").inc()
            raise HTTPException(status_code=503, detail="simulated carrier failure")

    submissions_total.labels(CARRIER_NAME, "accepted").inc()
    return {
        "carrier": CARRIER_NAME,
        "accepted": True,
        "operator_reference": f"{CARRIER_NAME}-{message.message_id}",
    }


@app.get("/health")
def health():
    health_gauge.labels(CARRIER_NAME).set(1 if carrier_healthy else 0)
    if not carrier_healthy:
        raise HTTPException(status_code=503, detail="carrier unhealthy")
    return {"carrier": CARRIER_NAME, "healthy": True}


@app.post("/health")
def update_health(payload: MockHealthUpdate):
    global carrier_healthy
    carrier_healthy = payload.healthy
    health_gauge.labels(CARRIER_NAME).set(1 if carrier_healthy else 0)
    return {"carrier": CARRIER_NAME, "healthy": carrier_healthy}


@app.get("/metrics")
def metrics():
    health_gauge.labels(CARRIER_NAME).set(1 if carrier_healthy else 0)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
