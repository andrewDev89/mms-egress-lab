# MMS Egress Lab — Mbuni 1.6.0

This demo runs **real open-source Mbuni 1.6.0**. Mbuni's `mmsbox` executable owns the PostgreSQL queue, constructs MM7/SOAP messages, sends them through HAProxy, and schedules retries. The Python service provides the demo control page, traffic generation, and a read-only view of the native queue. The two SDG endpoints remain mocks.

```text
Demo control / REST API → Mbuni SendMMS → native PostgreSQL queue
                                              ↓
                                     Mbuni mmsbox (type=soap)
                                              ↓
                                          HAProxy
                                              ↓
                                   SDG1 / SDG2 MM7 mocks

Mbuni log files → Alloy → Loki → Grafana
Native queue / Mbuni admin status / HAProxy → Prometheus → Grafana
```

This is the public Mbuni code, not Skycore's private build. The image builds both `mmsc` and `mmsbox`; this HTTP/SOAP egress scenario runs **mmsbox**, the Mbuni component that supports outbound `type=soap`. It does not simulate handset MM1, WAP push, an SMPP bind, or final handset delivery. In this lab, “bind down” means an unavailable mock SDG HTTP endpoint.

## Start on your Mac

With Docker Desktop running:

```sh
git pull
docker compose up -d --build --remove-orphans
```

The first build downloads and compiles Mbuni and takes longer than subsequent starts. The public source is pinned to the `1.6.0` tag commit and verified by SHA-256; see [the build notes](mbuni/README.md).

- [Control page](http://localhost:8000/demo/control)
- [Grafana](http://localhost:3000), initially `admin` / `admin`
- [API docs](http://localhost:8000/docs)
- [HAProxy stats](http://localhost:8404)
- [Prometheus](http://localhost:9090)

Open the **MMS Egress Lab — Mbuni 1.6.0** dashboard. Native Mbuni logs are selected by default. The control page injects messages into real Mbuni, and controls the two mock endpoints and HAProxy's aggregate allowance.

Existing Grafana credentials, bind capacities, and PostgreSQL data are preserved. `--remove-orphans` stops the former Python worker. Old simulator rows in the `messages` table are retained but are not sent or shown in the native dashboard. New native tables are initialized even when the PostgreSQL volume already exists; old simulator payloads are not silently migrated into a live sender.

To restart the database without clearing it:

```sh
docker compose restart psql-mms
docker compose restart mbuni mms-api
```

A restart does not make old data new. The control page's Clear Queue action deletes active native outgoing messages; it preserves archives and historical Prometheus samples, and cannot recall HTTP requests already in flight.

## Show backpressure

1. Send a small burst, such as 100 messages, and confirm SDG acceptance and native Mbuni logs.
2. Bring both binds down, then send another burst. HAProxy's allowance becomes zero and it returns HTTP 429. Mbuni keeps retrying from its native PostgreSQL queue.
3. Watch active queue depth/age, **HAProxy HTTP Errors / sec**, **Mbuni Outbound Errors / sec**, and the native log lines containing `HTTP returned status=[429]`.
4. Restore one bind at a small positive capacity, then restore the other. Queued messages are sent when their native retry time becomes due.

Partial capacity loss does not necessarily cause rejected requests: offered traffic must exceed the remaining allowance. The cumulative HTTP-error panel stays at its previous value after recovery; the rate panel drops toward zero. HTTP 5xx includes 503 when no backend is available despite a positive configured allowance.

HAProxy uses an aggregate five-second rolling allowance and returns errors; it does not send a target TPS value to Mbuni. Mbuni decides when to try again. The SDG mocks validate multipart MM7 `SubmitReq` and return a correlated SOAP `SubmitRsp` with status 1000. This proves SDG acceptance, not handset delivery.

## Sender and retry settings

Set these in `.env`, then recreate the Mbuni container with `docker compose up -d --build mbuni`:

```dotenv
MBUNI_SEND_TPS=600
MBUNI_MAX_SEND_ATTEMPTS=100
SEND_ATTEMPT_BACK_OFF_SECONDS=2
```

`MBUNI_SEND_TPS` sets Mbuni's native `max-throughput`. Upstream sleeps `1 / max-throughput` after a send, outside its connection mutex; multiple send threads and HTTP/SQL latency affect the aggregate rate. It is **not a strict global TPS cap** or a promise of 600 TPS. HAProxy independently enforces its aggregate capacity. The lab uses five native send threads.

Mbuni's own retry logic uses `send-attempt-back-off × attempts`, queue polling, expiry, and the global `maximum-send-attempts`. Retry settings apply to the process, not individual messages. The former REST `max_attempts` parameter is rejected. No Python delivery worker or retry scheduler remains.

## Messages and database access

```sh
curl -X POST http://localhost:8000/messages \
  -H 'Content-Type: application/json' \
  -d '{"sender":"12065550100","recipient":"12065550199","text":"Real Mbuni MMS"}'

docker compose exec psql-mms psql -U mms -d psql_mms
```

HTTP 202 means Mbuni confirmed native acceptance. `message_id` is Mbuni's transaction ID; use `/messages/{message_id}` or search it in the logs. `/messages` lists native queue row IDs, stored retry counts, and queue timestamps. A `media_url` is fetched by Mbuni as the message content; accompanying `text` becomes its subject. Text-only requests are MMS payloads too.

For a Mac database GUI, connect to `localhost:15432`, database `psql_mms`, user/password `mms` / `mms`. PostgreSQL is its own lab container; it is not inside HAProxy.

```sql
SELECT id, qfname, num_attempts, send_time FROM mms_messages;
SELECT * FROM lab_native_messages ORDER BY created_at DESC LIMIT 20;
```

`mms_messages` / `mms_message_headers` are Mbuni's native tables. Completed queue entries move to `archived_mms_messages` and `archived_mms_message_headers`. **Archived includes success and terminal failure**; it must not be interpreted as delivered. Consult `mmsbox.log` and `mmsbox-cdr.log` (`sent` / `dropped`) for outcomes. `carrier_state` only stores demo control settings. Archives retain message content and need periodic cleanup for long-running labs.

If an intake request times out, acceptance may be unknown. Check native logs/queue before resubmitting: the adapter deliberately does not automatically retry an ambiguous submission.

## Logs and autonomous operation

Mbuni writes actual `mmsbox.log`, `access-mmsbox.log`, and `mmsbox-cdr.log` under `./logs/mbuni` on the Mac (mounted as `/var/log/mbuni`). Alloy sends them to Loki automatically. `mmsc.log` and `access-mmsc.log` are collected if present; this egress scenario does not run a separate `mmsc` process or fabricate its logs. Native logs can contain phone numbers and message metadata; use demo data.

Set `MBUNI_LOG_DIR` to override the shared host directory. Keep it a dedicated demo directory, since the container writes there. Logs are raw lines; Loki timestamps represent collection time. Loki retains 72 hours; local Mbuni log files are separate and require rotation/cleanup for extended runs.

For Alloy running directly on a production host, [the host logging guide](docs/mbuni-logging.md) includes a systemd service that automatically collects `/var/log/mbuni/{mmsbox,mmsc,access-mmsbox,access-mmsc}.log`. It does not require Python or the demo control service on that host.

## Validation

The disposable integration stack has no published host ports and uses separate volumes. It validates real multipart SOAP, zero-capacity 429 retries, positive-capacity 503 retries, recovery, native retry exhaustion, native queue inspection, and logs through Alloy/Loki/Grafana.

```sh
COMPOSE_PROJECT_NAME=mbuni-test docker compose -p mbuni-test \
  -f tests/compose.integration.yml -f tests/compose.logs.yml up -d --build
COMPOSE_PROJECT_NAME=mbuni-test docker compose -p mbuni-test \
  -f tests/compose.integration.yml -f tests/compose.logs.yml logs -f tests
# Clean up only the disposable test project:
COMPOSE_PROJECT_NAME=mbuni-test docker compose -p mbuni-test \
  -f tests/compose.integration.yml -f tests/compose.logs.yml down -v
```
