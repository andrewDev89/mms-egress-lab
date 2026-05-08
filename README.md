# MMS Egress Queue Lab

Production-flavored MMS egress demo with HAProxy, PostgreSQL, FastAPI, per-carrier workers, mock carriers, Prometheus, and Grafana.

## Run

```bash
docker compose up -d --build
```

Useful local URLs:

- MMS API: http://localhost:8000/docs
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

Restore capacity and watch the worker drain the queue:

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

Mark one T-Mobile SDG bind unhealthy, submit a message without choosing a bind, and watch the other bind deliver it:

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
