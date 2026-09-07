import datetime as dt

from app.models import Instrument, InstrumentState
from app.session import SessionState, instrument_session_state

LIQUID = Instrument("X", "X", "X", liquidity_tier="liquid")
ILLIQUID = Instrument("Y", "Y", "Y", liquidity_tier="illiquid")


def test_weekend_is_closed_not_stale():
    saturday = dt.datetime(2026, 9, 5, 12, 0, 0)  # a Saturday
    epoch = saturday.timestamp()
    state = InstrumentState(isin="X", last_exchange_ts=epoch - 3 * 24 * 3600)  # last tick 3 days ago
    assert instrument_session_state(saturday, epoch, LIQUID, state) == SessionState.CLOSED


def test_halted_beats_everything():
    weekday_live = dt.datetime(2026, 9, 7, 11, 0, 0)
    epoch = weekday_live.timestamp()
    state = InstrumentState(isin="X", last_exchange_ts=epoch, halted=True)
    assert instrument_session_state(weekday_live, epoch, LIQUID, state) == SessionState.HALTED


def test_illiquid_sparse_ticks_are_not_degraded():
    weekday_live = dt.datetime(2026, 9, 7, 11, 0, 0)
    epoch = weekday_live.timestamp()
    state = InstrumentState(isin="Y", last_exchange_ts=epoch - 200)  # 200s gap, fine for illiquid
    assert instrument_session_state(weekday_live, epoch, ILLIQUID, state) == SessionState.LIVE


def test_liquid_instrument_silent_10s_is_degraded():
    weekday_live = dt.datetime(2026, 9, 7, 11, 0, 0)
    epoch = weekday_live.timestamp()
    state = InstrumentState(isin="X", last_exchange_ts=epoch - 15)  # 15s silence, liquid expects 2s
    assert instrument_session_state(weekday_live, epoch, LIQUID, state) == SessionState.DEGRADED
