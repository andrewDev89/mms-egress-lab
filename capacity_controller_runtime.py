#!/usr/bin/env python3

import csv
import socket
import time
import urllib.request
from io import StringIO

HAPROXY_STATS_URL = "http://localhost:8404/;csv"
RUNTIME_HOST = "127.0.0.1"
RUNTIME_PORT = 9999

RUNTIME_MAP_PATH = "/usr/local/etc/haproxy/capacity.map"
LOCAL_MAP_PATH = "capacity.map"

CAPACITY = {
    "tmobile-sdg1": 10,
    "tmobile-sdg2": 10,
}

RATE_WINDOW_SECONDS = 5
POLL_SECONDS = 2
MAP_KEY = "1"


def get_haproxy_stats():
    with urllib.request.urlopen(HAPROXY_STATS_URL, timeout=3) as response:
        raw = response.read().decode()

    lines = raw.splitlines()
    if lines and lines[0].startswith("# "):
        lines[0] = lines[0][2:]

    return list(csv.DictReader(StringIO("\n".join(lines))))


def calculate_capacity(rows):
    healthy = []

    for row in rows:
        if row.get("pxname") != "carriers":
            continue

        server = row.get("svname")
        status = row.get("status", "")

        if server in CAPACITY and status.startswith("UP"):
            healthy.append(server)

    allowed_tps = sum(CAPACITY[server] for server in healthy)
    threshold = allowed_tps * RATE_WINDOW_SECONDS

    return allowed_tps, threshold, healthy


def haproxy_runtime_cmd(command):
    with socket.create_connection((RUNTIME_HOST, RUNTIME_PORT), timeout=3) as sock:
        sock.sendall((command + "\n").encode())
        sock.shutdown(socket.SHUT_WR)
        return sock.recv(4096).decode(errors="replace").strip()


def update_runtime_map(threshold):
    response = haproxy_runtime_cmd(
        f"set map {RUNTIME_MAP_PATH} {MAP_KEY} {threshold}"
    )

    if "entry not found" in response.lower():
        response = haproxy_runtime_cmd(
            f"add map {RUNTIME_MAP_PATH} {MAP_KEY} {threshold}"
        )

    if "error" in response.lower() or "not found" in response.lower():
        raise RuntimeError(f"HAProxy runtime update failed: {response}")

    with open(LOCAL_MAP_PATH, "w") as f:
        f.write(f"{MAP_KEY} {threshold}\n")

    return response


def main():
    last_threshold = None

    while True:
        try:
            rows = get_haproxy_stats()
            allowed_tps, threshold, healthy = calculate_capacity(rows)

            if threshold != last_threshold:
                response = update_runtime_map(threshold)
                print(
                    f"capacity changed | healthy={healthy} "
                    f"allowed_tps={allowed_tps} threshold={threshold} "
                    f"runtime_response={response!r}",
                    flush=True,
                )
                last_threshold = threshold
            else:
                print(
                    f"no change | healthy={healthy} "
                    f"allowed_tps={allowed_tps} threshold={threshold}",
                    flush=True,
                )

        except Exception as exc:
            print(f"controller error: {exc}", flush=True)

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
