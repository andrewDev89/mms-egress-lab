import json
from unittest.mock import Mock

from mms_queue import event_log


def test_event_is_one_json_line_with_searchable_fields(monkeypatch):
    logger = Mock()
    monkeypatch.setattr(event_log, 'logger', logger)
    event_log.log_event('sender', 'retry_scheduled', level='warning', message_id=12, http_status=429)
    level, line = logger.log.call_args.args
    record = json.loads(line)
    assert level == 30
    assert record['component'] == 'sender'
    assert record['event'] == 'retry_scheduled'
    assert record['message_id'] == 12
    assert record['http_status'] == 429
    assert record['timestamp'].endswith('+00:00')
    assert '\n' not in line
