"""Ordering guard. Ticks are applied by exchange seq, never arrival order."""
from app.models import InstrumentState, Tick


def apply_tick(state: InstrumentState, tick: Tick) -> bool:
    # INVARIANT I4: out-of-order ticks are dropped, never applied — they must
    # never rewind state.
    if tick.seq <= state.last_seq:
        return False
    state.last_seq = tick.seq
    state.last_exchange_ts = tick.exchange_ts
    state.ltp_raw = tick.price_raw
    state.volume_today += tick.volume
    return True
