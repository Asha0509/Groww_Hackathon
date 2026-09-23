"""Tests for the ingest guard — what apply_tick refuses to let through."""
import math

from app.ingest import apply_tick
from app.models import InstrumentState, Tick


def _state():
    return InstrumentState(isin="X", last_seq=4, last_exchange_ts=100.0, ltp_raw=250.0, volume_today=900)


def test_out_of_order_tick_is_dropped():
    state = _state()
    assert apply_tick(state, Tick("X", 3, 200.0, 260.0, 50)) is False
    assert (state.last_seq, state.ltp_raw) == (4, 250.0)  # INVARIANT I4: never rewound


def test_a_price_that_is_not_a_price_is_dropped():
    """Negative, zero, and NaN all reach pct_change as a number, and none of
    them crash it — a wrong percentage would be reported in full confidence
    instead. The guard is here, before any of that.
    """
    for bad in (-250.0, 0.0, float("nan"), math.inf):
        state = _state()
        assert apply_tick(state, Tick("X", 5, 200.0, bad, 50)) is False
        assert state.ltp_raw == 250.0
        assert state.last_seq == 4  # seq untouched, so a good tick 5 still lands
        assert state.volume_today == 900


def test_a_good_tick_after_a_dropped_one_still_applies():
    state = _state()
    apply_tick(state, Tick("X", 5, 200.0, -1.0, 50))
    assert apply_tick(state, Tick("X", 5, 200.0, 262.5, 50)) is True
    assert (state.last_seq, state.ltp_raw, state.volume_today) == (5, 262.5, 950)
