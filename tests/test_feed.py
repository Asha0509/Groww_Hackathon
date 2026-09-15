import httpx

from app.feed import LIVE_SYMBOLS, fetch_live_quote, fetch_live_ticks, generate_feed_death, generate_normal, generate_split_day


def test_normal_scenario_is_deterministic():
    ticks_a, actions_a = generate_normal(1_700_000_000)
    ticks_b, actions_b = generate_normal(1_700_000_000)
    assert ticks_a == ticks_b
    assert actions_a == actions_b == []


def test_split_day_ticks_ordered_by_seq_per_instrument():
    ticks, actions = generate_split_day(1_700_000_000)
    assert len(actions) == 1 and actions[0].kind == "SPLIT"
    by_isin: dict[str, list[int]] = {}
    for t in ticks:
        by_isin.setdefault(t.isin, []).append(t.seq)
    for seqs in by_isin.values():
        assert seqs == sorted(seqs)


def test_split_day_price_drops_tenfold_at_split_seq():
    ticks, _ = generate_split_day(1_700_000_000)
    reliance = [t for t in ticks if t.isin == "INE002A01018"]
    before = next(t for t in reliance if t.seq == 5)
    after = next(t for t in reliance if t.seq == 6)
    assert after.price_raw < before.price_raw / 8  # roughly a 10x drop


def test_feed_death_only_silences_one_instrument():
    now_epoch = 1_700_000_000
    ticks, actions = generate_feed_death(now_epoch)
    assert actions == []

    last_tick_by_isin: dict[str, float] = {}
    for t in ticks:
        last_tick_by_isin[t.isin] = max(last_tick_by_isin.get(t.isin, 0), t.exchange_ts)

    dead = last_tick_by_isin.pop("INE040A01034")  # HDFCBANK
    assert now_epoch - dead >= 60  # well past the liquid-tier DEGRADED threshold

    for isin, last_ts in last_tick_by_isin.items():
        assert now_epoch - last_ts < 12, f"{isin} should still be ticking right up to now_epoch"


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _fake_chart_payload(price, ts, volume=1000):
    return {"chart": {"result": [{"meta": {"regularMarketPrice": price, "regularMarketTime": ts, "regularMarketVolume": volume}}]}}


def test_fetch_live_quote_parses_a_healthy_response(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse(_fake_chart_payload(1235.3, 1789465500)))
    quote = fetch_live_quote("RELIANCE.NS")
    assert quote == (1235.3, 1789465500.0, 1000)


def test_fetch_live_quote_returns_none_on_network_failure(monkeypatch):
    def raise_error(*a, **kw):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(httpx, "get", raise_error)
    assert fetch_live_quote("RELIANCE.NS") is None


def test_fetch_live_quote_returns_none_on_malformed_response(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: _FakeResponse({"chart": {"result": None}}))
    assert fetch_live_quote("RELIANCE.NS") is None


def test_fetch_live_ticks_is_a_partial_result_on_a_partial_outage(monkeypatch):
    """INVARIANT: one dead symbol must never take down the other seven —
    the live feed is exactly the 'unreliable dependency' this project
    exists to handle gracefully, including in its own vendor adapter.
    """

    def flaky_get(url, **kw):
        if "HDFCBANK" in url:
            raise httpx.ConnectError("simulated outage for this one symbol")
        return _FakeResponse(_fake_chart_payload(100.0, 1_700_000_000.0))

    monkeypatch.setattr(httpx, "get", flaky_get)
    next_seq = {isin: 1 for isin in LIVE_SYMBOLS}
    ticks, failed = fetch_live_ticks(next_seq)

    assert failed == ["INE040A01034"]  # HDFCBANK, and only HDFCBANK
    assert len(ticks) == len(LIVE_SYMBOLS) - 1
    assert "INE040A01034" not in ticks
