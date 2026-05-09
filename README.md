# MMS Egress Queue Lab

Production-flavored MMS egress demo with HAProxy, PostgreSQL, FastAPI, one T-Mobile operator worker, mock SDG binds, Prometheus, and Grafana.

Message flow:

```text
MMSC API -> PostgreSQL queue -> T-Mobile egress worker -> HAProxy -> tmobile-sdg1 / tmobile-sdg2
```

The queue stores operator-level T-Mobile work. HAProxy owns the final SDG bind decision and failover.

The API periodically syncs the configured healthy bind TPS from PostgreSQL into HAProxy's runtime capacity map. This keeps HAProxy aligned after container restarts, even when PostgreSQL remembers a higher demo capacity than the static `capacity.map` file.

## Run

```bash
docker compose up -d --build
```

Useful local URLs:

- MMS API: http://localhost:8000/docs
- Demo control page: http://localhost:8000/demo/control
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- HAProxy stats: http://localhost:8404
- PostgreSQL: `localhost:15432`, database `psql_mms`, user/password `mms`/`mms`

## Backpressure Demo

Set both T-Mobile SDG bind capacities to zero:

```bash
curl -X POST http://localhost:8000/carriers/tmobile-sdg1/capacity \
  -H "Content-Type: application/json" \
  -d '{"tps_capacity":0}'

curl -X POST http://localhost:8000/carriers/tmobile-sdg2/capacity \
  -H "Content-Type: application/json" \
  -d '{"tps_capacity":0}'
```

Submit an MMS-like message. The API stores it in PostgreSQL and returns `429` with a `message_id` instead of dropping it:

```bash
curl -i -X POST http://localhost:8000/messages \
  -H "Content-Type: application/json" \
  -d '{
    "sender": "12065550100",
    "recipient": "12065550199",
    "media_url": "https://example.com/demo.jpg",
    "text": "queued demo"
  }'
```

Restore capacity and watch the worker drain the queue through HAProxy:

```bash
curl -X POST http://localhost:8000/carriers/tmobile-sdg1/capacity \
  -H "Content-Type: application/json" \
  -d '{"tps_capacity":5}'

curl -X POST http://localhost:8000/carriers/tmobile-sdg2/capacity \
  -H "Content-Type: application/json" \
  -d '{"tps_capacity":5}'
```

Check a message:

```bash
curl http://localhost:8000/messages/1
```

## Retry Demo

Mark one T-Mobile SDG bind unhealthy, submit a message without choosing a bind, and watch HAProxy send it through the remaining healthy bind:

```bash
curl -X POST http://localhost:8000/carriers/tmobile-sdg2/health \
  -H "Content-Type: application/json" \
  -d '{"healthy":false}'

curl -X POST http://localhost:8000/messages \
  -H "Content-Type: application/json" \
  -d '{"sender":"12065550100","recipient":"12065550200","text":"failover demo"}'

curl -X POST http://localhost:8000/carriers/tmobile-sdg2/health \
  -H "Content-Type: application/json" \
  -d '{"healthy":true}'
```

Metrics are available at `http://localhost:8000/metrics` and are scraped by Prometheus.

## Queue Age Alerts

Prometheus loads demo alert rules for the oldest active queue entry:

- `MmsQueueOldestAgeWarning`: fires when the oldest queued/sending/retry message is older than 60 seconds.
- `MmsQueueOldestAgeCritical`: fires when the oldest queued/sending/retry message is older than 5 minutes.

Open `http://localhost:9090/alerts` to see alert state during a backlog demo. Grafana also includes an `Oldest Queue Age` panel with matching green/yellow/red thresholds.

The dashboard also includes queue-age buckets and two ETA views. `Net Drain ETA Status` only shows a countdown when the current backlog is shrinking:

```text
active backlog / (recent delivered rate - recent submitted rate)
```

If submitted traffic is equal to or greater than delivered traffic, the queue is not draining and the panel reports that the queue is still growing. `Clear Time If Ingress Stops Now` answers the common operations question: "If new traffic stopped right now, how long would the current backlog take to clear at the observed delivery rate?"

The worker claims up to one second of configured healthy bind capacity per loop and sends those messages concurrently. This keeps high-TPS demos from being capped by one HTTP round trip at a time while still making HAProxy responsible for final SDG bind selection.

## Traffic Burst Demo

Use this endpoint when you want Grafana to show visible traffic:

```bash
curl -X POST http://localhost:8000/demo/messages/burst \
  -H "Content-Type: application/json" \
  -d '{
    "count": 1000,
    "sender": "12065550100",
    "recipient_prefix": "1206555",
    "text": "Grafana traffic demo",
    "max_attempts": 100
  }'
```

`max_attempts` is the retry limit for each message. It is not the number of messages to send.

Clear all demo messages and reset queue depth:

```bash
curl -X POST http://localhost:8000/demo/messages/clear
```

Use this before a clean one-message demo. Delivered rows remain visible until the queue is cleared, so a previous message through each SDG bind will show one terminal delivery on each bind even if the most recent test only sent one message.

## Sustained Message Blast Demo

Use this endpoint for a more traditional customer traffic blast. By default it injects 150,000 messages into the MMSC API path at 900 messages/second, which intentionally exceeds a `300 + 300 TPS` egress configuration and should create sustained backlog pressure.

```bash
curl -X POST http://localhost:8000/demo/messages/blast \
  -H "Content-Type: application/json" \
  -d '{
    "count": 150000,
    "rate_per_second": 900,
    "sender": "12065550100",
    "recipient_prefix": "120655",
    "text": "Customer traffic blast",
    "max_attempts": 100
  }'
```

The response includes a `job_id`. Check injection progress with:

```bash
curl http://localhost:8000/demo/messages/blast/<job_id>
```

Only one blast can run at a time. The default blast takes about 167 seconds to finish injecting; the queue can continue draining afterward.
