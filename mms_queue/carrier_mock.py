import os
import random
import asyncio
import uuid
import xml.etree.ElementTree as ET
from email import policy
from email.parser import BytesParser
from xml.sax.saxutils import escape

from fastapi import FastAPI, HTTPException, Request
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


class MockHealthUpdate(BaseModel):
    healthy: bool = Field(..., examples=[False])


@app.post("/submit")
async def submit(request: Request):
    raw = await request.body()
    content_type = request.headers.get("content-type", "")
    try:
        envelope = parse_mm7(content_type, raw)
    except (ValueError, ET.ParseError) as exc:
        raise HTTPException(400, "Expected MM7 SubmitReq with an attached MIME payload") from exc
    if not carrier_healthy:
        submissions_total.labels(CARRIER_NAME, "unhealthy").inc()
        raise HTTPException(status_code=503, detail="carrier unhealthy")

    with submit_seconds.labels(CARRIER_NAME).time():
        if SUBMIT_DELAY_SECONDS:
            await asyncio.sleep(SUBMIT_DELAY_SECONDS)
        if FAIL_RATE and random.random() < FAIL_RATE:
            submissions_total.labels(CARRIER_NAME, "failed").inc()
            raise HTTPException(status_code=503, detail="simulated carrier failure")

    submissions_total.labels(CARRIER_NAME, "accepted").inc()
    transaction = escape(envelope["transaction"])
    version = escape(envelope["version"])
    namespace = escape(envelope["namespace"])
    reference = f"{CARRIER_NAME}-{uuid.uuid4()}"
    return Response(f'''<?xml version="1.0"?>
<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/" xmlns:mm7="{namespace}">
  <env:Header><mm7:TransactionID env:mustUnderstand="1">{transaction}</mm7:TransactionID></env:Header>
  <env:Body><mm7:SubmitRsp><mm7:MM7Version>{version}</mm7:MM7Version>
    <mm7:Status><mm7:StatusCode>1000</mm7:StatusCode><mm7:StatusText>Success</mm7:StatusText></mm7:Status>
    <mm7:MessageID>{reference}</mm7:MessageID>
  </mm7:SubmitRsp></env:Body>
</env:Envelope>''', media_type="text/xml")


def parse_mm7(content_type, raw):
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + raw
    )
    if not message.is_multipart():
        raise ValueError("Missing multipart SOAP payload")
    parts = list(message.iter_parts())
    start = message.get_param("start")
    soap = next((p for p in parts if p.get("Content-ID") == start), parts[0])
    root = ET.fromstring(soap.get_payload(decode=True))
    req = root.find(".//{*}SubmitReq")
    transaction = root.findtext(".//{*}TransactionID")
    if req is None or not transaction or req.find(".//{*}Recipients") is None:
        raise ValueError("Missing MM7 fields")
    content = req.find("{*}Content")
    if content is None or not content.get("href", "").startswith("cid:"):
        raise ValueError("Missing attachment reference")
    cid = content.attrib["href"][4:].strip("<>")
    if not any(p.get("Content-ID", "").strip("<>") == cid for p in parts if p is not soap):
        raise ValueError("Attachment reference does not resolve")
    return {"transaction": transaction, "version": req.findtext("{*}MM7Version") or "5.3.0",
            "namespace": req.tag.split("}")[0].lstrip("{")}



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
