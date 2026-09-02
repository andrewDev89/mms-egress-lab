from contextlib import contextmanager
from io import BytesIO
from unittest.mock import Mock
import urllib.error

import pytest
from fastapi import Response

from mms_queue import api, config, repository, worker


@contextmanager
def fake_connection():
    yield Mock()


@pytest.mark.parametrize('code,label', [(429, '429'), (503, '503'), (400, 'other')])
def test_http_rejection_is_counted_and_retryable(monkeypatch, code, label):
    # Mbuni's inspected SOAP path retries non-2xx, including HTTP 4xx.
    error = urllib.error.HTTPError('http://proxy/submit', code, 'rejected', {}, BytesIO())
    monkeypatch.setattr(worker.urllib.request, 'urlopen', Mock(side_effect=error))
    counter = worker.egress_rejections.labels(label)
    before = counter._value.get()
    result = worker.process_message({'id': 1, 'sender': 'a', 'recipient': 'b', 'media_url': None, 'text': 'test'})
    assert result['status'] == 'error'
    assert str(code) in result['error']
    assert counter._value.get() == before + 1


def test_network_failure_has_separate_counter(monkeypatch):
    monkeypatch.setattr(worker, 'submit_to_haproxy', Mock(side_effect=urllib.error.URLError('offline')))
    before = worker.transport_errors._value.get()
    assert worker.process_message({'id': 1})['status'] == 'error'
    assert worker.transport_errors._value.get() == before + 1


@pytest.mark.parametrize('retry_state', [{'status': 'retry'}, None])
def test_sender_claims_at_configured_rate_without_querying_bind_health(monkeypatch, retry_state):
    monkeypatch.setattr(config, 'WORKER_SEND_TPS', 7)
    monkeypatch.setattr(config, 'WORKER_BATCH_SIZE', 10)
    conn = Mock()
    # Any carrier-capacity SQL in the worker fails this test.
    conn.execute.side_effect = AssertionError('Worker must not query carrier state')
    @contextmanager
    def connection():
        yield conn
    monkeypatch.setattr(worker, 'connect', connection)
    claim = Mock(return_value=[{'id': 1}])
    monkeypatch.setattr(worker, 'claim_messages', claim)
    monkeypatch.setattr(worker, 'process_message', lambda message: {'status': 'error', 'message': message, 'error': 'HTTP 429'})
    retry = Mock(return_value=retry_state)
    monkeypatch.setattr(worker, 'mark_delivery_error', retry)
    with worker.ThreadPoolExecutor(max_workers=1) as executor:
        assert worker.run_batch('test', executor) == ('sent', 1)
    claim.assert_called_once_with(conn, 'test', 7)
    retry.assert_called_once_with(conn, {'id': 1}, 'HTTP 429')


@pytest.mark.parametrize('attempts,expected', [(1, 'retry'), (3, 'failed')])
def test_retry_persistence_respects_attempt_limit(attempts, expected):
    conn = Mock()
    conn.execute.return_value.fetchone.return_value = {'status': expected}
    result = repository.mark_delivery_error(conn, {'id': 1, 'attempts': attempts, 'max_attempts': 3}, 'HTTP 429')
    assert result['status'] == expected
    sql, params = conn.execute.call_args.args
    assert params[0] == expected
    if expected == 'retry':
        assert params[2] == repository.retry_delay_for_attempt(attempts)
        assert 'interval' in sql


@pytest.mark.parametrize('burst', [False, True])
def test_submission_is_accepted_without_carrier_capacity_query(monkeypatch, burst):
    monkeypatch.setattr(api, 'connect', fake_connection)
    monkeypatch.setattr(api, 'create_message', Mock(return_value={'id': 1, 'status': 'queued', 'carrier': None}))
    response = Response()
    if burst:
        result = api.enqueue_message_burst(api.BurstCreate(count=1), response)
        assert result['enqueued'] == 1
    else:
        result = api.enqueue_message(api.MessageCreate(sender='a', recipient='b'), response)
        assert result['message_id'] == 1
    assert response.status_code == 202
    assert 'queued_due_to_carrier_backpressure' not in result
