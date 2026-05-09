from mms_queue import config
from mms_queue.repository import is_capacity_available, retry_delay_for_attempt


def test_capacity_available_requires_healthy_positive_capacity():
    assert is_capacity_available({"healthy": True, "tps_capacity": 1})
    assert not is_capacity_available({"healthy": True, "tps_capacity": 0})
    assert not is_capacity_available({"healthy": False, "tps_capacity": 10})
    assert not is_capacity_available(None)


def test_retry_backoff_is_demo_friendly_and_capped():
    assert retry_delay_for_attempt(1) == 2
    assert retry_delay_for_attempt(2) == 5
    assert retry_delay_for_attempt(3) == 10
    assert retry_delay_for_attempt(9) == 10


def test_demo_max_attempts_accepts_large_retry_count():
    assert config.MAX_ATTEMPTS_LIMIT >= 100
