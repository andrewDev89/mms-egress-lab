"""Run only against the disposable stack in compose.integration.yml."""
import json
import os
import time
import urllib.error
import urllib.request

import pytest

BASE = os.getenv('MMS_INTEGRATION_URL')
pytestmark = pytest.mark.skipif(not BASE, reason='Requires isolated integration stack')


def request(path, payload=None, base=None):
    req = urllib.request.Request(
        (base or BASE) + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Content-Type': 'application/json'},
    )
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    with response:
        body = response.read().decode()
        try:
            body = json.loads(body)
        except ValueError:
            pass
        return response.code, body


def eventually(check, timeout=45):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(0.5)
    raise AssertionError(f'Condition not met in {timeout}s; last result={last}')


def set_bind(name, healthy, capacity):
    assert request(f'/carriers/{name}/capacity', {'tps_capacity': capacity})[0] == 200
    assert request(f'/carriers/{name}/health', {'healthy': healthy})[0] == 200


def worker_counter(name, label=''):
    _, body = request('/metrics', base='http://worker-tmobile-egress:9102')
    prefix = name + label + ' '
    return sum(float(line.split()[-1]) for line in body.splitlines() if line.startswith(prefix))


def messages():
    return request('/messages?limit=100')[1]


def test_outage_rejection_retry_recovery_and_exhaustion():
    eventually(lambda: request('/health')[0] == 200)
    set_bind('tmobile-sdg1', False, 10)
    set_bind('tmobile-sdg2', False, 10)

    # Zero capacity must deny even the first request in an empty rate window.
    for _ in range(3):
        assert request('/submit', {'message_id': 0}, base='http://haproxy:8080')[0] == 429

    # All intake paths accept into the durable queue even during total outage.
    code, job = request('/demo/messages/blast', {'count': 3, 'rate_per_second': 3})
    assert code == 202
    def blast_done():
        result = request('/demo/messages/blast/' + job['job_id'])[1]
        return result if result['status'] == 'completed' else None
    completed = eventually(blast_done)
    assert completed['accepted_for_delivery'] == 3
    assert 'queued_due_to_carrier_backpressure' not in completed
    assert request('/demo/messages/clear', {})[0] == 200

    code, burst = request('/demo/messages/burst', {'count': 40, 'max_attempts': 100})
    assert code == 202 and burst['accepted_for_delivery'] == 40
    eventually(lambda: any(m['attempts'] >= 2 and m['status'] == 'retry' for m in messages()))
    assert all(m['status'] != 'delivered' for m in messages())
    assert worker_counter('mms_egress_rejections_total', '{status_code="429"}') > 0
    assert worker_counter('mms_retry_total', '{carrier="haproxy"}') > 0
    assert worker_counter('mms_worker_send_tps') == 20

    # Reduced capacity delivers some traffic and continues rejecting excess.
    before = worker_counter('mms_egress_rejections_total', '{status_code="429"}')
    set_bind('tmobile-sdg1', True, 5)
    eventually(lambda: any(m['status'] == 'delivered' for m in messages()))
    eventually(lambda: worker_counter('mms_egress_rejections_total', '{status_code="429"}') > before)
    assert worker_counter('mms_worker_send_tps') == 20

    # Retry eligibility, not a capacity lookup by the sender, drives recovery.
    set_bind('tmobile-sdg1', True, 20)
    set_bind('tmobile-sdg2', True, 20)
    eventually(lambda: len(messages()) == 40 and all(m['status'] == 'delivered' for m in messages()))

    # Actual backend outage while configured capacity stays positive gives 503.
    for name in ('tmobile-sdg1', 'tmobile-sdg2'):
        assert request('/health', {'healthy': False}, base=f'http://{name}:8080')[0] == 200
    before = worker_counter('mms_egress_rejections_total', '{status_code="503"}')
    code, message = request('/messages', {'sender': 'a', 'recipient': 'b', 'max_attempts': 100})
    assert code == 202
    eventually(lambda: worker_counter('mms_egress_rejections_total', '{status_code="503"}') > before)
    for name in ('tmobile-sdg1', 'tmobile-sdg2'):
        set_bind(name, True, 20)
    eventually(lambda: request('/messages/' + str(message['message_id']))[1]['status'] == 'delivered')

    # A finite retry budget still applies during a prolonged capacity outage.
    set_bind('tmobile-sdg1', False, 20)
    set_bind('tmobile-sdg2', False, 20)
    code, message = request('/messages', {'sender': 'a', 'recipient': 'b', 'max_attempts': 2})
    assert code == 202
    def failed():
        row = request('/messages/' + str(message['message_id']))[1]
        return row if row['status'] == 'failed' else None
    row = eventually(failed)
    assert row['attempts'] == 2
    assert '429' in row['last_error']
