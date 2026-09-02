import pytest
from mms_queue.carrier_mock import parse_mm7


def mm7_request(href='cid:payload'):
    soap = f'''<env:Envelope xmlns:env="http://schemas.xmlsoap.org/soap/envelope/" xmlns:m="urn:mm7">
    <env:Header><m:TransactionID>native-123</m:TransactionID></env:Header>
    <env:Body><m:SubmitReq><m:MM7Version>5.3.0</m:MM7Version><m:Recipients><m:To>123</m:To></m:Recipients>
    <m:Content href="{href}"/></m:SubmitReq></env:Body></env:Envelope>'''
    raw = f'--demo\r\nContent-Type: text/xml\r\nContent-ID: <soap>\r\n\r\n{soap}\r\n--demo\r\nContent-Type: text/plain\r\nContent-ID: <payload>\r\n\r\nactual MMS content\r\n--demo--\r\n'.encode()
    return 'multipart/related; boundary="demo"; start="<soap>"', raw


def test_mm7_extracts_correlation_and_version():
    assert parse_mm7(*mm7_request()) == {'transaction':'native-123','version':'5.3.0','namespace':'urn:mm7'}


def test_mm7_rejects_missing_content_reference():
    with pytest.raises(ValueError, match='does not resolve'):
        parse_mm7(*mm7_request('cid:missing'))


def test_carrier_does_not_accept_old_json_protocol():
    with pytest.raises(ValueError):
        parse_mm7('application/json', b'{"message_id":1}')
