# MMS Egress Queue Lab

Production-flavored MMS egress demo with HAProxy, PostgreSQL, FastAPI, one T-Mobile operator worker, mock SDG binds, Prometheus, and Grafana.

Message flow:

```text
MMSC API -> PostgreSQL queue -> T-Mobile egress worker -> HAProxy -> tmobile-sdg1 / tmobile-sdg2
```

The queue stores operator-level T-Mobile work. HAProxy owns the final SDG bind decision and failover.

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
