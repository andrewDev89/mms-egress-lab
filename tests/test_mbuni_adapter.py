from unittest.mock import patch
import urllib.error
import pytest
from fastapi import HTTPException
from mms_queue import mbuni
from mms_queue.api import BurstCreate, enqueue_message_burst
from starlette.responses import Response


def test_ambiguous_intake_is_not_retried():
    with patch.object(mbuni.urllib.request, 'urlopen', side_effect=urllib.error.URLError('connection lost')) as send:
        with pytest.raises(HTTPException, match='acceptance may be unknown'):
            mbuni.submit({'sender':'123','recipient':'456','text':'demo'})
        assert send.call_count == 1


def test_partial_burst_reports_confirmed_acceptances():
    with patch('mms_queue.api.create_message', side_effect=[
        {'id':'native-1'}, HTTPException(502, 'acceptance may be unknown'),
    ]):
        with pytest.raises(HTTPException) as result:
            enqueue_message_burst(BurstCreate(count=3), Response())
        assert result.value.detail['accepted_for_delivery'] == 1
        assert result.value.detail['last_message_id'] == 'native-1'
