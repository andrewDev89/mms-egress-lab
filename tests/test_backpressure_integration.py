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


def metric(name):
    _, body = request('/metrics')
    return sum(float(line.split()[-1]) for line in body.splitlines()
               if line.startswith(name + '{') or line.startswith(name + ' '))


def native_rows():
    return request('/messages?limit=200')[1]


def test_native_soap_backpressure_and_recovery():
    eventually(lambda: request('/health')[0] == 200)
    assert metric('mbuni_up') == 1
    assert metric('mbuni_configured_throughput') == 20
    # Former per-message retry controls must not silently pretend to affect Mbuni.
    assert request('/messages', {'sender':'12065550100','recipient':'12065550199','text':'test','max_attempts':2})[0] == 422
    for bind in ('tmobile-sdg1','tmobile-sdg2'):
        set_bind(bind, True, 0)
    sent_before = metric('mbuni_mt_sent_total')
    errors_before = metric('mbuni_mt_errors_total')
    code, result = request('/demo/messages/burst', {'count':8,'text':'native Mbuni outage test'})
    assert code == 202 and result['enqueued'] == 8
    first_id = result['first_message_id']
    assert first_id.startswith('Mbuni-')
    def retried():
        rows = [r for r in native_rows() if r['status'] == 'retry']
        return rows if len(rows) == 8 and min(r['attempts'] for r in rows) >= 2 else None
    rows = eventually(retried)
    assert metric('mbuni_mt_sent_total') == sent_before
    assert metric('mbuni_mt_errors_total') >= errors_before + 16
    assert all(r['next_attempt_at'] for r in rows)
    code, row = request('/messages/' + first_id)
    assert code == 200 and row['status'] == 'retry'
    # Restore a single bind; native retries must recover without another submission.
    set_bind('tmobile-sdg1', True, 20)
    eventually(lambda: metric('mbuni_mt_sent_total') >= sent_before + 8)
    eventually(lambda: request('/messages/' + first_id)[1]['status'] == 'archived')
    _, carrier_metrics = request('/metrics', base='http://tmobile-sdg1:8080')
    assert 'result="accepted"' in carrier_metrics
    assert not any(r['status'] in ('queued','retry') for r in native_rows())

    # Positive configured allowance with both real mock endpoints unhealthy => 503.
    # This deliberately bypasses the control page's configured capacity calculation.
    for bind in ('tmobile-sdg1','tmobile-sdg2'):
        set_bind(bind, True, 20)
        request('/health', {'healthy':False}, base=f'http://{bind}:8080')
    eventually(lambda: request('/submit', {}, base='http://haproxy:8080')[0] == 503)
    before = metric('mbuni_mt_sent_total')
    code, accepted = request('/messages', {'sender':'12065550100','recipient':'12065550199','text':'503 recovery'})
    assert code == 202
    eventually(lambda: request('/messages/' + accepted['message_id'])[1]['status'] == 'retry')
    for bind in ('tmobile-sdg1','tmobile-sdg2'):
        request('/health', {'healthy':True}, base=f'http://{bind}:8080')
    eventually(lambda: metric('mbuni_mt_sent_total') >= before + 1)

    # Native retry exhaustion is reported in Mbuni logs/CDR, not guessed from archives.
    for bind in ('tmobile-sdg1','tmobile-sdg2'):
        set_bind(bind, True, 0)
    code, accepted = request('/messages', {'sender':'12065550100','recipient':'12065550200','text':'native retry exhaustion'})
    assert code == 202
    eventually(lambda: request('/messages/' + accepted['message_id'])[1]['status'] == 'archived', timeout=40)
    assert metric('mbuni_mt_sent_total') == before + 1
    for bind in ('tmobile-sdg1','tmobile-sdg2'):
        set_bind(bind, True, 20)


def test_native_media_url_submission():
    import base64
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    pixel = base64.b64decode('R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==')

    class Media(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'image/gif')
            self.send_header('Content-Length', str(len(pixel)))
            self.end_headers()
            self.wfile.write(pixel)

    server = ThreadingHTTPServer(('0.0.0.0', 8099), Media)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        before = metric('mbuni_mt_sent_total')
        code, result = request('/messages', {
            'sender':'12065550100', 'recipient':'12065550199',
            'media_url':'http://tests:8099/pixel.gif', 'text':'Native media test',
        })
        assert code == 202, result
        eventually(lambda: metric('mbuni_mt_sent_total') >= before + 1)
        eventually(lambda: request('/messages/' + result['message_id'])[1]['status'] == 'archived')
    finally:
        server.shutdown()
        server.server_close()


def test_native_blast_and_queue_clear_preserve_archives():
    archived_before = sum(r['status'] == 'archived' for r in native_rows())
    for bind in ('tmobile-sdg1','tmobile-sdg2'):
        set_bind(bind, True, 0)
    code, job = request('/demo/messages/blast', {'count':3,'rate_per_second':10,'text':'native clear test'})
    assert code == 202
    eventually(lambda: request('/demo/messages/blast/' + job['job_id'])[1]['status'] == 'completed')
    eventually(lambda: sum(r['status'] == 'retry' for r in native_rows()) == 3)
    code, result = request('/demo/messages/clear', {})
    assert code == 200 and result['deleted_messages'] == 3
    assert not any(r['status'] in ('queued','retry') for r in native_rows())
    assert sum(r['status'] == 'archived' for r in native_rows()) == archived_before
    for bind in ('tmobile-sdg1','tmobile-sdg2'):
        set_bind(bind, True, 20)
