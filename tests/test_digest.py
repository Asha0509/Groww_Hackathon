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


def test_first_view_seeds_a_baseline_and_says_nothing():
    """An instrument with no watermark has no "since" to report yet. It gets
    one, set to where the instrument is right now, and stays out of the digest
    until something happens after that point.
    """
    inst = Instrument("X", "X", "X")
    state = InstrumentState(isin="X", last_seq=7, ltp_raw=150.0, cum_factor=1.0)
    watermarks = WatermarkStore()

    assert build_digest("u1", [inst], {"X": state}, watermarks) == []

    seeded = watermarks.get("u1", "X")
    assert (seeded.last_seen_seq, seeded.last_seen_price_raw) == (7, 150.0)


def test_dropping_a_watermark_lets_a_re_added_instrument_start_clean():
    """A watchlist removal takes the baseline with it. Without that, the
    monotonic guard in ack keeps the old watermark, outranks the seq=1 reseed
    a re-added instrument comes back with, and the first digest after the
    re-add reports a move the user never sat through.
    """
    inst = Instrument("X", "X", "X")
    watermarks = WatermarkStore()
    watermarks.ack("u1", "X", seq=1, price_raw=100.0, cum_factor=1.0)

    watermarks.drop("u1", "X")
    assert watermarks.get("u1", "X") is None

    re_added = InstrumentState(isin="X", last_seq=1, ltp_raw=150.0, cum_factor=1.0)
    assert build_digest("u1", [inst], {"X": re_added}, watermarks) == []
    assert watermarks.get("u1", "X").last_seen_price_raw == 150.0


def test_small_move_is_silent():
    inst = Instrument("X", "X", "X")
    state = InstrumentState(isin="X", last_seq=2, ltp_raw=100.5, cum_factor=1.0)
    watermarks = WatermarkStore()
    watermarks.ack("u1", "X", seq=1, price_raw=100.0, cum_factor=1.0)
    cards = build_digest("u1", [inst], {"X": state}, watermarks)
    assert cards == []
