from sales_agent.services.memory.profile_worker import compute_profile_backoff_seconds


def test_profile_backoff_is_bounded():
    assert compute_profile_backoff_seconds(1) == 2
    assert compute_profile_backoff_seconds(9) == 300
