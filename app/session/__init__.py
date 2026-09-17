"""Five-state session model (I5) with per-instrument liveness (I6). The
states are PRE_OPEN, LIVE, CLOSED, HALTED, and DEGRADED, and all five can
reach a user — instrument_session_state returns the calendar state
verbatim when the market isn't in continuous trading. PRE_OPEN is the one
that matters least: it's the 09:00–09:15 window, distinct from CLOSED, but
it behaves identically to CLOSED in the only way that counts — neither is
ever reported as stale. See docs/LLD.md for exactly how it behaves.
See docs/CLAUDE.md 'Lie 3 — the baseline expires'.
"""
import datetime as dt
from enum import Enum

from app.models import Instrument, InstrumentState


class SessionState(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    LIVE = "LIVE"
    CLOSED = "CLOSED"
    HALTED = "HALTED"
    DEGRADED = "DEGRADED"


PRE_OPEN_START_SEC = 9 * 3600
MARKET_OPEN_SEC = 9 * 3600 + 15 * 60
MARKET_CLOSE_SEC = 15 * 3600 + 30 * 60

# INVARIANT I6: expected tick interval is per liquidity tier, not a global timeout.
EXPECTED_INTERVAL_MS = {"liquid": 2_000, "illiquid": 240_000}
DEGRADED_MULTIPLIER = 6


def calendar_state(now: dt.datetime) -> SessionState:
    if now.weekday() >= 5:
        return SessionState.CLOSED
    sec = now.hour * 3600 + now.minute * 60 + now.second
    if sec < PRE_OPEN_START_SEC:
        return SessionState.CLOSED
    if sec < MARKET_OPEN_SEC:
        return SessionState.PRE_OPEN
    if sec <= MARKET_CLOSE_SEC:
        return SessionState.LIVE
    return SessionState.CLOSED


def instrument_session_state(now: dt.datetime, now_epoch: float, instrument: Instrument, state: InstrumentState) -> SessionState:
    if state.halted:
        # INVARIANT I5: HALTED is not stale — it is not our fault.
        return SessionState.HALTED

    base = calendar_state(now)
    if base != SessionState.LIVE:
        # INVARIANT I5: CLOSED/PRE_OPEN are never reported as stale.
        return base

    expected_ms = EXPECTED_INTERVAL_MS.get(instrument.liquidity_tier, EXPECTED_INTERVAL_MS["liquid"])
    age_ms = (now_epoch - state.last_exchange_ts) * 1000
    # INVARIANT I6: liveness judged against this instrument's own expected interval.
    if age_ms > expected_ms * DEGRADED_MULTIPLIER:
        return SessionState.DEGRADED
    return SessionState.LIVE
