from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, REGISTRY, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily
from .mbuni import native_status

messages_submitted = Counter("mms_messages_submitted_total", "Messages accepted by native Mbuni through the demo API.", ["result"])
queue_depth = Gauge("mms_queue_depth", "Native Mbuni PostgreSQL queue entries; archives are not delivery receipts.", ["carrier", "status"])
queue_oldest_age = Gauge("mms_queue_oldest_age_seconds", "Oldest active native Mbuni queue entry.", ["carrier", "status"])
queue_age_bucket = Gauge("mms_queue_age_bucket", "Native Mbuni active queue age buckets.", ["carrier", "bucket"])
carrier_health = Gauge("mms_carrier_healthy", "Configured mock bind health.", ["carrier"])
carrier_capacity = Gauge("mms_carrier_tps_capacity", "Configured mock bind capacity.", ["carrier"])


class MbuniCollector:
    def collect(self):
        up = GaugeMetricFamily("mbuni_up", "Native Mbuni admin endpoint reachable.")
        try:
            rows = native_status()
        except Exception:
            up.add_metric([], 0)
            yield up
            return
        up.add_metric([], 1)
        yield up
        for name, description, field, kind in (
            ("mbuni_mt_sent", "Mbuni successful outbound PDUs since process/connection restart; not handset delivery.", "sent", CounterMetricFamily),
            ("mbuni_mt_errors", "Mbuni outbound errors, transient and terminal, since process/connection restart.", "errors", CounterMetricFamily),
            ("mbuni_configured_throughput", "Native Mbuni configured throughput; actual rate depends on threads and latency.", "throughput", GaugeMetricFamily),
        ):
            metric = kind(name, description, labels=["mmsc"])
            for row in rows:
                metric.add_metric([row["id"]], row[field])
            yield metric


REGISTRY.register(MbuniCollector())


def render_metrics():
    return generate_latest(), CONTENT_TYPE_LATEST
