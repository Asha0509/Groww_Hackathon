"""Ordering guard. Ticks are applied by exchange seq, never arrival order,
and only when the price they carry is one a price could actually be.
"""
import math

from app.models import InstrumentState, Tick


def apply_tick(state: InstrumentState, tick: Tick) -> bool:
    # INVARIANT I4: out-of-order ticks are dropped, never applied — they must
    # never rewind state.
    if tick.seq <= state.last_seq:
        return False
    if not math.isfinite(tick.price_raw) or tick.price_raw <= 0:
        # A negative, zero, or NaN price is a broken feed, not a cheap stock.
        # Nothing downstream crashes on one — the corporate-action maths guards
        # its own division — so it would survive all the way to a user as a
        # confidently-worded percentage, which is the single outcome this
        # system exists to avoid. Dropping it leaves last_seq untouched, so the
        # next good tick for this instrument still applies normally.
        return False
    state.last_seq = tick.seq
    state.last_exchange_ts = tick.exchange_ts
    state.ltp_raw = tick.price_raw
    state.volume_today += tick.volume
    return True
