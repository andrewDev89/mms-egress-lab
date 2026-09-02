# MMSC-side logs with Alloy and Loki

The dashboard combines Prometheus metrics with a Loki-backed **MMSC-side Logs** panel. Alloy reads logs and pushes them to Loki; Grafana queries Loki. There is no manual copy step when Alloy runs on the Mbuni host.

## Local demo

Run `docker compose up -d --build`. Loki, Alloy, and the Loki datasource are included. Choose **Lab MMSC** in the dashboard's **Log source** dropdown. Trigger an outage and send messages to see `retry_scheduled`, `delivery_failed`, and `delivered` events. **Log contains** accepts a literal text fragment such as `retry_scheduled` or `"message_id": 45`.

The demo collector only discovers containers labeled `mms.logs=mmsc` in its own Compose project: the API and egress worker. It does not collect HAProxy, SDG, or unrelated project logs. Lab events include message IDs, attempt counts, HTTP status, next retry time, and delivery bind; they omit message bodies and phone numbers. Real Mbuni files are preserved as-is and can contain subscriber information.

Lab logs are from the Python MMSC simulation, not a Mbuni binary. Loki and Alloy use persistent volumes, and both restart automatically with Docker unless explicitly stopped. Docker Desktop itself must be running on a Mac. Docker log rotation is bounded to three 10 MB files for the API and worker. Loki is configured for 72-hour retention; compaction removes expired logs asynchronously.

Alloy's Docker log input uses the Docker socket. A read-only socket mount does not make Docker API access read-only. The host file collector below has no Docker dependency or socket access.

## Run Alloy on each production Mbuni host

The supplied `alloy/mbuni-host.alloy` tails these live files:

- `/var/log/mbuni/mmsbox.log`
- `/var/log/mbuni/mmsc.log`
- `/var/log/mbuni/access-mmsbox.log`
- `/var/log/mbuni/access-mmsc.log`

Each record has `job="mmsc"`, `source="mbuni_file"`, `service_name="mbuni"`, `instance=<host name>`, and `filename=<full path>`. Select **Mbuni files** in the same dashboard. Expand a line to see the host and filename, or filter them in Grafana Explore.

Install the [official Alloy Linux package](https://grafana.com/docs/alloy/latest/set-up/install/linux/) on each Mbuni host. The package supplies the systemd service and an `alloy` service account. For a new dedicated collector, from this repository directory:

```bash
sudo install -m 0644 alloy/mbuni-host.alloy /etc/alloy/config.alloy
sudo install -m 0600 alloy/mbuni.env.example /etc/alloy/mbuni.env
sudo install -d /etc/systemd/system/alloy.service.d
sudo install -m 0644 alloy/mbuni-systemd.conf /etc/systemd/system/alloy.service.d/mbuni.conf
sudoedit /etc/alloy/mbuni.env
```

Set `LOKI_PUSH_URL` to the complete push URL of the reachable central Loki gateway, including `/loki/api/v1/push`. Add the gateway's authentication to the `endpoint` block as required; [loki.write supports basic authentication, bearer tokens, TLS, and tenant IDs](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.write/). The example deliberately does not invent a production endpoint or credentials. On an existing Alloy installation, merge the components and environment settings instead of replacing its current configuration.

The service account must be able to traverse `/var/log/mbuni` and read all four files. Use the host's existing log-reader group or a narrow ACL, and preserve that access when logrotate creates replacement files. Keep Mbuni's existing ownership and logging configuration. Confirm as the service user, for example:

```bash
sudo -u alloy test -r /var/log/mbuni/mmsbox.log
sudo -u alloy test -r /var/log/mbuni/mmsc.log
sudo -u alloy test -r /var/log/mbuni/access-mmsbox.log
sudo -u alloy test -r /var/log/mbuni/access-mmsc.log
```

Enable the packaged service and apply the configuration:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now alloy
sudo systemctl restart alloy
sudo systemctl status alloy
sudo journalctl -u alloy -n 50 --no-pager
```

The drop-in loads the endpoint environment file and restarts the collector if it exits. Keep the package's persistent Alloy storage directory (normally `/var/lib/alloy`); file offsets live under its configured `--storage.path`. No cron task or interactive terminal is needed. See [Alloy Linux configuration](https://grafana.com/docs/alloy/latest/configure/linux/).

The host configuration starts at the end of existing files on first discovery and follows new lines. Saved offsets take precedence on restart. Normal rename-and-recreate rotation is followed; rotated archives are not separately imported. As with any tailing collector, prolonged outages, discarded state, or files rotated away before collection can lose data. This is not an exactly-once archive.

Lines retain their original contents. Loki uses **collection time**, so the original Mbuni timestamp remains visible in the line; application-time parsing and multiline grouping need actual log samples and a known host timezone. Message IDs stay inside log content rather than becoming high-cardinality Loki labels.

## Where Loki runs

Alloy is lightweight and belongs on each Mbuni host. Loki and Grafana can run on a central monitoring host reachable by those collectors. Grafana's Loki datasource must point to that same Loki installation.

The default lab Loki has no published host port and no authentication; it is for the local Compose network. For a native Alloy collector on the same machine as the lab, the optional override exposes Loki on loopback only:

```bash
docker compose -f docker-compose.yml -f docker-compose.loki-local.yml up -d
```

A same-host collector can then use `LOKI_PUSH_URL=http://127.0.0.1:3100/loki/api/v1/push`. A production host cannot use that address to reach another machine. Use a reachable private/authenticated gateway for remote collectors; the lab does not publish an unauthenticated logging endpoint to your network.

## Optional local files

For a local smoke test, the Docker collector can also read the same four filenames from `logs/mbuni`, or from an explicit read-only bind mount:

```bash
MBUNI_LOG_DIR=/absolute/path/to/mbuni docker compose up -d alloy
```

This optional local-file input reads existing content at collection time. Production collection should use the host service above; it does not require copying files to a Mac. Do not run two collectors against the same files and Loki unless duplicate ingestion is intended.

## Verification

Validate both Alloy configurations using the pinned image:

```bash
docker run --rm -v "$PWD/alloy/config.alloy:/etc/alloy/config.alloy:ro" -e COMPOSE_PROJECT_NAME=mms-egress-lab grafana/alloy:v1.19.2 validate /etc/alloy/config.alloy
docker run --rm -v "$PWD/alloy/mbuni-host.alloy:/etc/alloy/config.alloy:ro" -e LOKI_PUSH_URL=http://loki:3100/loki/api/v1/push grafana/alloy:v1.19.2 validate /etc/alloy/config.alloy
```

Run the isolated logging acceptance suite (no host ports, disposable database and fixture logs):

```bash
docker compose -p mms-logging-test -f tests/compose.integration.yml -f tests/compose.logs.yml build mms-api
docker compose -p mms-logging-test -f tests/compose.integration.yml -f tests/compose.logs.yml up --abort-on-container-exit --exit-code-from tests
docker compose -p mms-logging-test -f tests/compose.integration.yml -f tests/compose.logs.yml down -v
```

The suite verifies sender outcomes in Loki, live ingestion of all four Mbuni filenames using the host configuration, file rotation, and queries through Grafana's provisioned Loki datasource. Its Mbuni lines are explicitly synthetic fixtures; no production logs are accessed. Always run the final cleanup command even if tests fail.
