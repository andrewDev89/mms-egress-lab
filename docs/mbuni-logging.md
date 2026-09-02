# Mbuni logs in Grafana

The default lab runs real Mbuni `mmsbox`. Its `mmsbox.log`, `access-mmsbox.log`, and `mmsbox-cdr.log` are written to the shared `logs/mbuni` directory and collected automatically by Alloy. The native `mmsc` process is not part of this SOAP egress flow, so the lab does not fabricate its logs. The collector also reads `mmsc.log` and `access-mmsc.log` when present.

The Grafana dashboard defaults to **Mbuni files**. **Demo control** selects the Python API's control/acceptance events; these are distinct from Mbuni's native delivery logs. Native logs may contain phone numbers and metadata. Loki timestamps are collection time.

Mbuni, Alloy and Loki restart unless explicitly stopped; Docker Desktop itself must be running on a Mac. Docker log rotation for the API is bounded to three 10 MB files. Mbuni's host files require their own rotation/cleanup for extended runs. Loki retains 72 hours independently of those local files.

Alloy's optional Docker log input uses the Docker socket. A read-only socket mount does not make Docker API access read-only. The host file collector below has no Docker dependency or socket access.

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

The demo uses `logs/mbuni` by default. To choose another dedicated demo directory, set `MBUNI_LOG_DIR` for both Mbuni and Alloy:

```bash
MBUNI_LOG_DIR=/absolute/path/to/demo-logs docker compose up -d mbuni alloy
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
