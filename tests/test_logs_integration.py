"""Optional end-to-end Alloy/Loki/Grafana test in the disposable log stack."""
import json
import os
from pathlib import Path
import urllib.parse

import pytest
from test_backpressure_integration import eventually, request

pytestmark = pytest.mark.skipif(not os.getenv('MMS_LOG_TEST'), reason='Requires log stack')


def query_logs(query):
    status, data = request('/loki/api/v1/query_range?' + urllib.parse.urlencode({
        'query': query, 'since': '1h', 'limit': 1000,
    }), base='http://loki:3100')
    assert status == 200, data
    return [line for stream in data['data']['result'] for _, line in stream['values']]


def test_lab_outcomes_and_live_host_files_reach_grafana():
    eventually(lambda: request('/ready', base='http://loki:3100')[0] == 200, timeout=90)
    # The backpressure integration scenario runs first and generates both outcomes.
    rejected = eventually(lambda: query_logs('{job="mmsc",source="lab",event="retry_scheduled"}'))
    delivered = eventually(lambda: query_logs('{job="mmsc",source="lab",event="delivered"}'))
    assert any(json.loads(line).get('http_status') == 429 for line in rejected)
    assert all('message_id' in json.loads(line) for line in delivered)
    assert all('sender' not in json.loads(line) and 'recipient' not in json.loads(line) for line in delivered)

    # Exercise the actual host configuration against all four user-specified files.
    names = ('mmsbox.log', 'mmsc.log', 'access-mmsbox.log', 'access-mmsc.log')
    for name in names:
        Path('/var/log/mbuni', name).write_text('historical-before-collector-start\n')
    def watching_all_files():
        _, metrics = request('/metrics', base='http://alloy-host:12345')
        return any(line.startswith('loki_source_file_files_active_total{') and line.endswith(' 4') for line in metrics.splitlines())
    eventually(watching_all_files)
    for name in names:
        with Path('/var/log/mbuni', name).open('a') as f:
            f.write(f'synthetic-live-mbuni-test file={name}\n')
    def collected_all_files():
        lines = query_logs('{job="mmsc",source="mbuni_file",instance="mbuni-test-host"}')
        return lines if len(lines) >= 4 else None
    lines = eventually(collected_all_files)
    assert not any('historical-before-collector-start' in line for line in lines)
    assert all(any(f'file={name}' in line for line in lines) for name in names)

    path = Path('/var/log/mbuni/mmsbox.log')
    path.rename(path.with_suffix('.log.1'))
    path.write_text('synthetic-mbuni-after-rotation\n')
    eventually(lambda: query_logs('{job="mmsc",source="mbuni_file"} |= "synthetic-mbuni-after-rotation"'))

    # Grafana's provisioned Loki datasource is the path used by the dashboard.
    eventually(lambda: request('/api/health', base='http://grafana:3000')[0] == 200)
    status, data = request('/api/datasources/uid/loki', base='http://grafana:3000')
    assert status == 200 and data['url'] == 'http://loki:3100'
    status, data = request('/api/dashboards/uid/mms-egress-tmobile', base='http://grafana:3000')
    assert status == 200
    panel = next(p for p in data['dashboard']['panels'] if p['title'] == 'MMSC-side Logs')
    assert panel['datasource']['uid'] == 'loki'
    query = '/api/datasources/proxy/uid/loki/loki/api/v1/query_range?' + urllib.parse.urlencode({
        'query': '{job="mmsc", source=~"lab"} |= "retry_scheduled"', 'since': '1h',
    })
    status, data = request(query, base='http://grafana:3000')
    assert status == 200 and data['data']['result']
