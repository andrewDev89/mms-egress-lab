#!/usr/bin/env python3

import csv
import subprocess
import time
import urllib.request
from io import StringIO

HAPROXY_STATS_URL = "http://localhost:8404/;csv"
HAPROXY_CFG = "haproxy.cfg"

# Per-endpoint TPS capacity
CAPACITY = {
    "carrier1": 10,
    "carrier2": 10,
}

POLL_SECONDS = 5
RATE_WINDOW_SECONDS = 10


def get_haproxy_stats():
    with urllib.request.urlopen(HAPROXY_STATS_URL, timeout=3) as response:
        raw = response.read().decode()

    lines = raw.splitlines()
    if lines[0].startswith("# "):
        lines[0] = lines[0][2:]

    return list(csv.DictReader(StringIO("\n".join(lines))))


def calculate_allowed_tps(rows):
    healthy = []

    for row in rows:
        if row.get("pxname") != "carriers":
            continue

        server = row.get("svname")
        status = row.get("status", "")

        if server in CAPACITY and status.startswith("UP"):
            healthy.append(server)

    allowed_tps = sum(CAPACITY[s] for s in healthy)
    return allowed_tps, healthy


def write_haproxy_config(allowed_tps):
    # HAProxy http_req_rate(10s) compares requests per 10-second window
    threshold = allowed_tps * RATE_WINDOW_SECONDS

    cfg = f"""
global
    log stdout format raw local0

defaults
    mode http
    timeout connect 5s
    timeout client 30s
    timeout server 30s

frontend fe_http
    bind *:8080

    stick-table type ip size 100k expire 30s store http_req_rate(10s)
    http-request track-sc0 src

    # Dynamic capacity limit
    # allowed_tps={allowed_tps}
    # threshold={threshold} requests per {RATE_WINDOW_SECONDS}s
    http-request deny deny_status 429 if {{ sc_http_req_rate(0) gt {threshold} }}

    default_backend carriers

backend carriers
    balance roundrobin
    option httpchk GET /
    server carrier1 carrier1:80 check
    server carrier2 carrier2:80 check

listen stats
    bind *:8404
    stats enable
    stats uri /
""".lstrip()

    with open(HAPROXY_CFG, "w") as f:
        f.write(cfg)


def restart_haproxy():
    subprocess.run(["docker", "compose", "restart", "haproxy"], check=True)


def main():
    last_allowed_tps = None

    while True:
        try:
            rows = get_haproxy_stats()
            allowed_tps, healthy = calculate_allowed_tps(rows)

            if allowed_tps != last_allowed_tps:
                print(f"Capacity changed: healthy={healthy}, allowed_tps={allowed_tps}")
                write_haproxy_config(allowed_tps)
                restart_haproxy()
                last_allowed_tps = allowed_tps
            else:
                print(f"No change: healthy={healthy}, allowed_tps={allowed_tps}")

        except Exception as e:
            print(f"Controller error: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
