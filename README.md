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

This is the public Mbuni code. The image builds both `mmsc` and `mmsbox`; this HTTP/SOAP egress scenario runs **mmsbox**, the Mbuni component that supports outbound `type=soap`. It does not simulate handset MM1, WAP push, an SMPP bind, or final handset delivery. In this lab, “bind down” means an unavailable mock SDG HTTP endpoint.

## Start on your Mac

With Docker Desktop running:

```sh
git pull
docker compose up -d --build --remove-orphans
```

The first build downloads and compiles Mbuni and takes longer than subsequent starts. The public source is pinned to the `1.6.0` tag commit and verified by SHA-256; see [the build notes](mbuni/README.md).

### Apple Silicon Macs

Mbuni builds for the Docker host's architecture: `linux/arm64` on Apple Silicon and `linux/amd64` on Intel/AMD. Its old bundled CPU-detection scripts are replaced during the build so they recognize ARM64. You do not need to force Mbuni to run as `linux/amd64`.

If an earlier build failed with `config.guess: unable to guess system type`, update this checkout and rebuild. If you previously added `platform: linux/amd64` to Mbuni in a local Compose override, remove that setting first. In your Mac terminal, run:

```sh
unset DOCKER_DEFAULT_PLATFORM
docker compose build --pull mbuni
docker compose up -d --build --remove-orphans
docker compose exec mbuni uname -m
```

The last command should print `aarch64` on Apple Silicon. These commands preserve the database and other named volumes. If startup still fails, collect `docker compose ps -a` and `docker compose logs --tail=100 mbuni db-init`; an architecture warning alone does not identify which service failed.

### Open the demo

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

### PostgreSQL CPU during large queue tests

Docker CPU percentages add usage across cores: around 600% means roughly six cores. A large demo queue can generate substantial database work, but the original Mbuni schema also omits an index on `mms_message_headers.qid`. Mbuni looks up, replaces, and archives headers by that column on every attempt. Without the index, these operations can repeatedly scan the whole active header table. The lab's database initialization now adds this index for both new and existing databases without changing queue contents.

To upgrade an existing lab, stop the writers before applying the startup schema change (index creation can briefly block header writes):

```sh
docker compose stop mms-api mbuni
git pull --ff-only
docker compose build db-init mms-api
docker compose run --rm --no-deps db-init
docker compose up -d --build --remove-orphans
```

The native queue and archives are preserved. An unfinished API traffic-generation job is not resumed after stopping the API; messages already accepted by Mbuni remain queued.

For an already running lab that needs the index before updating the code, run this single statement separately from any transaction:

```sh
docker compose exec -T psql-mms psql -U mms -d psql_mms -v ON_ERROR_STOP=1 -c \
  "CREATE INDEX CONCURRENTLY IF NOT EXISTS mms_message_headers_qid_idx ON mms_message_headers (qid);"
```

The concurrent build permits normal queue writes but consumes resources while it runs. A successful index build should help header operations as the queue grows; it is not a guarantee of a particular CPU percentage or end-to-end TPS. It does not index or clear the archives. If database CPU remains high, inspect active queries and check that `docker compose exec psql-mms uname -m` reports `aarch64` on Apple Silicon.

## Show backpressure

1. Send a small burst, such as 100 messages, and confirm SDG acceptance and native Mbuni logs.
2. Bring both binds down, then send another burst. HAProxy's allowance becomes zero and it returns HTTP 429. Mbuni keeps retrying from its native PostgreSQL queue.
3. Watch active queue depth/age, **HAProxy HTTP Errors / sec**, **Mbuni Outbound Errors / sec**, and the native log lines containing `HTTP returned status=[429]`.
4. Restore one bind at a small positive capacity, then restore the other. Queued messages are sent when their native retry time becomes due.

Partial capacity loss does not necessarily cause rejected requests: offered traffic must exceed the remaining allowance. The cumulative HTTP-error panel stays at its previous value after recovery; the rate panel drops toward zero. HTTP 5xx includes 503 when no backend is available despite a positive configured allowance.

HAProxy uses an aggregate five-second rolling allowance and returns errors; it does not send a target TPS value to Mbuni. Mbuni decides when to try again. The SDG mocks validate multipart MM7 `SubmitReq` and return a correlated SOAP `SubmitRsp` with status 1000. This proves SDG acceptance, not handset delivery.

## How HAProxy controls egress: counting, throttling, and retries

**HAProxy can count HTTP requests and enforce a rate limit. Mbuni owns the persistent MMS queue and decides when to retry.** Together, those behaviors let this lab protect the SDG endpoints while retaining messages upstream during a capacity reduction. HAProxy does not need to parse SOAP or understand an MMS delivery receipt to apply the HTTP limit. Native stick tables and request rules provide the counting and enforcement; this configuration uses no Lua script or custom HAProxy module. HAProxy documents this behavior in its [traffic policing guide](https://www.haproxy.com/documentation/haproxy-configuration-tutorials/security/traffic-policing/).

| Component | Responsibility in this lab |
| --- | --- |
| Demo control API | Sums the capacities of binds marked healthy in PostgreSQL and updates HAProxy's allowance. |
| HAProxy | Tracks HTTP requests, compares the measured rate with the allowance, returns 429 for excess attempts, and routes admitted requests to healthy SDGs. |
| Mbuni | Stores MMS messages, constructs SOAP requests, interprets the response, and schedules retries or terminal failure. |
| PostgreSQL | Stores Mbuni's active/archived messages and the separate demo bind settings. |
| Prometheus / Grafana | Observe counters and queue state; they do not enforce the limit. |

### What HAProxy counts

The relevant rules in [haproxy.cfg](haproxy.cfg) are:

```haproxy
stick-table type string size 100k expire 15s store http_req_rate(5s)
http-request set-var(req.rate_limit) int(1),map_int(/usr/local/etc/haproxy/capacity.map,100)
http-request set-var(req.request_rate) str(carriers),table_http_req_rate()
acl rate_abuse var(req.rate_limit),sub(req.request_rate) le 0
http-request track-sc0 str(carriers) unless rate_abuse
http-request deny deny_status 429 if rate_abuse
```

A **stick table** is HAProxy's in-memory state table. Here, every request uses the same string key, `carriers`, so all traffic through this frontend shares one budget. It is not a separate allowance per client IP or SDG. `size 100k` is the maximum table entry count, not a TPS limit; `expire 15s` is the entry inactivity expiry, not the measurement window.

`http_req_rate(5s)` maintains a request-frequency counter. Its value is expressed as requests over the configured five-second period; it is not already normalized to requests per second. `table_http_req_rate()` reads that value for `carriers` and returns zero when the entry does not exist. The [HAProxy configuration manual](https://docs.haproxy.org/3.2/configuration.html) specifies these units under `http_req_rate` and `table_http_req_rate`.

For each HTTP attempt, the rules read the allowance and current counter, then check whether `allowance - observed_count <= 0`. If the check is true, HAProxy returns **429 Too Many Requests** before forwarding to an SDG. Otherwise, `track-sc0` tracks the request and processing continues to the backend. Tracking is conditional so attempts rejected by this rate rule do not consume more of the admission budget. A zero allowance rejects even the first request, when the table is empty.

Here, “admitted” means passed the proxy's rate check. An admitted request can still fail downstream, including with a 503; this counter does not prove carrier acceptance. It counts HTTP attempts, not unique message IDs, MIME parts, bytes, or handset deliveries. The demo submits one recipient per message; a production SOAP request containing multiple recipients would still be one HTTP request.

### How the allowance changes when capacity changes

The control API computes:

```text
available TPS = sum(configured TPS for binds marked healthy)
HAProxy threshold = available TPS × 5 seconds
```

| Configured healthy capacity | Aggregate TPS budget | Map threshold, in requests per 5 seconds |
| --- | ---: | ---: |
| SDG1: 300, SDG2: 300 | 600 | 3,000 |
| SDG1: 300, SDG2: down | 300 | 1,500 |
| Both down | 0 | 0 |

[The API's capacity synchronization](mms_queue/api.py) updates map key `1` using HAProxy's Runtime API. For example, it sends this command for a 600 TPS budget:

```text
set map /usr/local/etc/haproxy/capacity.map 1 3000
```

The map key `1` selects the configured limit; the stick-table key `carriers` identifies the tracked traffic. They are different keys in different data structures. Runtime updates take effect without reloading HAProxy; see the official [`set map` reference](https://www.haproxy.com/documentation/haproxy-runtime-api/reference/set-map/). The control endpoints synchronize immediately, and a background loop reapplies the database value every 10 seconds by default. The checked-in map starts at `1 100` until synchronization; runtime edits do not rewrite that file. The API's five-second multiplier must remain aligned with `http_req_rate(5s)`.

**Backend health checks and budget calculation are separate.** HAProxy probes `/health` to choose eligible servers. This lab's control page also changes the stored healthy flag, mock endpoint health, and aggregate map allowance. An outage outside the control page does not automatically recalculate the database-derived allowance: HAProxy can exclude a failed backend while the allowance remains unchanged. If no backend is available and a request passes the rate check, it can receive **503 Service Unavailable**. If the allowance is zero, the earlier rate rule returns 429 instead.

### How rejection becomes a retry

```text
Mbuni sends a SOAP attempt → HAProxy checks the shared request budget
  Budget available → forward to an SDG → return its response to Mbuni
  Budget exhausted → return HTTP 429 → Mbuni retains the message and schedules a retry
  Later retry      → a new HTTP attempt → the same budget check runs again
```

The public Mbuni build's [SOAP sender and queue runner](https://github.com/fredounnet/mbuni/blob/b8054f9ddfc48a8f2ec911adabd5309472bcf9f4/mmsbox/bearerbox.c) handle failed HTTP responses and update the native queue. The lab's integration scenarios exercise both 429 and 503 followed by recovery. With the default two-second backoff, successive retry delays grow approximately as 2, 4, 6 seconds, and so on, subject to queue polling and processing time. Mbuni also enforces its maximum attempts and message expiry.

HAProxy does not store the rejected MMS for later or resubmit it on a timer. Its own backend connection retry features are distinct from Mbuni's persistent message retry mechanism. This configuration supplies no `Retry-After` header and does not require Mbuni to interpret one. The application-level backpressure signal is the failed HTTP attempt. Mbuni can continue offering attempts while HAProxy protects the backend; it is not receiving a command to change its configured TPS.

For the private Skycore build, the vendor needs to confirm which HTTP/MM7 failures preserve a message for retry and what retry settings apply. If that build treats a 429 as terminal, HAProxy can still reject excess traffic, but the required retain-and-retry behavior would not follow. A timeout after forwarding also cannot establish whether an SDG accepted the message; this mechanism does not provide exactly-once delivery.

### What this demonstrates, and its boundaries

- **Rate policing, not evenly spaced sending.** The five-second frequency counter permits bursts. It is not a guarantee of precisely 600 requests in every individual second. Concurrent requests can also race between reading the counter and updating it; this is not a strict atomic quota.
- **One aggregate budget.** Round-robin routing does not independently enforce each SDG's configured TPS. Unequal backend capacities need a separate routing/limiting policy. Setting a bind's capacity to zero only changes the sum; use its health control to take it out of service.
- **One HAProxy instance.** This deployment has no shared quota across multiple proxies. Independent instances would maintain independent counters. Table state is in memory; restart/failover behavior would need to be addressed for a production-wide limit.
- **A limit does not generate load.** If Mbuni offers only 160 attempts/sec against a 600 TPS budget, HAProxy does not raise it to 600. Mbuni's ready queue, retry timing, SOAP serialization, and HTTP/SQL latency affect the offered rate.

The dashboard separates these observations: **Completed send attempts/sec** is the sum of rates from Mbuni's native sent/error counters; it includes repeated attempts and other send failures. **SDG accepted/sec** measures successful mock submissions. **HAProxy HTTP Errors** shows response classes (4xx includes 429; 5xx includes 503). Native active queue depth shows messages still waiting. None of those values alone is the configured allowance.

### Inspect the counting directly

The following read-only command shows the actual map and stick table in the running lab. It does not change capacity or submit traffic:

```sh
docker compose exec -T mms-api python - <<'PY'
import socket
for command in ("show map /usr/local/etc/haproxy/capacity.map", "show table fe_http"):
    print(command)
    with socket.create_connection(("haproxy", 9999), timeout=5) as connection:
        connection.sendall((command + "\n").encode())
        connection.shutdown(socket.SHUT_WR)
        while data := connection.recv(65536):
            print(data.decode(), end="")
PY
```

During traffic, the table contains `key=carriers` and a field such as `http_req_rate(5000)=...`; the map shows key `1` and the current allowance. After inactivity, the table entry can expire, which is normal. This is a direct view of HAProxy's counting, independent of Grafana. The official [`show table` reference](https://www.haproxy.com/documentation/haproxy-runtime-api/reference/show-table/) describes the output. The TCP admin socket used here is a lab convenience, not a production exposure recommendation.

To demonstrate the full mechanism, use the [backpressure exercise above](#show-backpressure): offer traffic, lower capacity, observe 429 responses and a retained native queue, then restore capacity and observe Mbuni retrying successfully. This proves the combination of HAProxy admission control and Mbuni retries; it does not imply HAProxy owns the MMS queue.

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

Single messages, bursts, blasts, and the control page automatically use the bundled demo image when `media_url` is omitted. No URL entry or Internet download is needed. This bundled 1×1 GIF is reachable by Mbuni inside Docker; the same file can be viewed from your Mac at `http://localhost:8000/demo/media/pixel.gif`. Set `media_url` to `null` explicitly for text-only MMS, or provide your own reachable URL to override the image. `https://example.com/image.jpg` was an illustrative placeholder, not a supplied image.

A `failed to fetch content from url` error occurs before SOAP egress. Mbuni 1.6.0 can misleadingly print `Queued [failed to fetch ...]` and return HTTP 200; the adapter requires an explicit `Accepted:` response. SSL write errors during an external fetch are separate from HAProxy carrier backpressure; using the local fixture does not establish or fix external HTTPS compatibility.

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
