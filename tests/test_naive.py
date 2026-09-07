from app.naive import naive_is_stale, naive_pct_change


def test_naive_reports_the_full_split_drop():
    change = naive_pct_change(200.0, 2000.0)
    assert change == -0.9  # this is the lie I3 exists to fix


def test_naive_global_timeout_ignores_session_state():
    assert naive_is_stale(now_epoch=100_000, last_exchange_ts=0) is True
