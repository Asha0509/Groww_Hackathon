from app.digest import WatermarkStore, build_digest
from app.models import Instrument, InstrumentState


def test_watermark_never_rewinds():
    wm = WatermarkStore()
    wm.ack("u1", "X", seq=5, price_raw=100, cum_factor=1.0)
    wm.ack("u1", "X", seq=3, price_raw=999, cum_factor=1.0)  # stale/out-of-order write
    current = wm.get("u1", "X")
    assert current.last_seen_seq == 5
    assert current.last_seen_price_raw == 100  # INVARIANT I2: not rewound


def test_digest_is_capped_at_budget():
    instruments = [Instrument(f"ISIN{i}", f"SYM{i}", f"Name {i}") for i in range(10)]
    states = {i.isin: InstrumentState(isin=i.isin, last_seq=2, ltp_raw=150.0, cum_factor=1.0) for i in instruments}
    watermarks = WatermarkStore()
    for i in instruments:
        watermarks.ack("u1", i.isin, seq=1, price_raw=100.0, cum_factor=1.0)  # every one moved +50%

    cards = build_digest("u1", instruments, states, watermarks, budget=5)
    assert len(cards) == 5


def test_small_move_is_silent():
    inst = Instrument("X", "X", "X")
    state = InstrumentState(isin="X", last_seq=2, ltp_raw=100.5, cum_factor=1.0)
    watermarks = WatermarkStore()
    watermarks.ack("u1", "X", seq=1, price_raw=100.0, cum_factor=1.0)
    cards = build_digest("u1", [inst], {"X": state}, watermarks)
    assert cards == []
