"""Demo intake adapter. Mbuni alone owns queueing, SOAP delivery and retries."""
import os
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from fastapi import HTTPException

SEND_URL = os.getenv("MBUNI_SEND_URL", "http://mbuni:10001/")
ADMIN_URL = os.getenv("MBUNI_ADMIN_URL", "http://mbuni:10002/status?password=lab-admin")


def submit(payload):
    params = {
        "username": "lab", "password": "lab-send", "mmsc": "tmobile",
        "from": payload["sender"], "to": payload["recipient"],
    }
    if payload.get("media_url"):
        params["content-url"] = payload["media_url"]
        if payload.get("text"):
            params["subject"] = payload["text"]
    else:
        params["text"] = payload.get("text") or "Mbuni demo MMS"
    # Mbuni's native SendMMS CGI parser accepts URL parameters and POST bodies.
    request = urllib.request.Request(SEND_URL + "?" + urllib.parse.urlencode(params), data=b"")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            reply = response.read().decode().strip()
    except (urllib.error.URLError, TimeoutError) as exc:
        # Never retry intake automatically: the native queue may already own it.
        raise HTTPException(502, "Mbuni intake failed; acceptance may be unknown. Check Mbuni logs before resubmitting.") from exc
    # Upstream can return HTTP 200 and log "Queued" even when fetching content fails.
    # Only an explicit Accepted response confirms submission.
    if reply.lower().startswith("failed to fetch content from url"):
        raise HTTPException(502, "Mbuni could not fetch media_url; acceptance was not confirmed. Use http://mms-api:8000/demo/media/pixel.gif or set media_url to null for text-only MMS.")
    if not reply.startswith("Accepted: "):
        raise HTTPException(502, "Mbuni did not confirm acceptance")
    return reply.removeprefix("Accepted: ").strip()


def native_status():
    with urllib.request.urlopen(ADMIN_URL, timeout=3) as response:
        root = ET.fromstring(response.read())
    return [{
        "id": item.attrib["id"],
        "throughput": float(item.findtext("throughput", "0")),
        "sent": int(item.findtext("stats/mt/pdus", "0")),
        "errors": int(item.findtext("stats/mt/errors", "0")),
    } for item in root.findall("mmsc")]
