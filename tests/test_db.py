"""Proof that state survives a process restart, against the real schema
already promised in docs/ARCHITECTURE.md. This is the concrete evidence for
'no persistence' moving from open to closed in docs/BUGS.md.
"""
from app import db
from app.digest import Watermark
from app.models import InstrumentState


def test_instrument_state_survives_a_reconnect(tmp_path):
    path = str(tmp_path / "since.db")

    conn = db.connect(path)
    db.save_instrument_state(conn, InstrumentState(isin="X", last_seq=7, last_exchange_ts=123.0, ltp_raw=245.42, cum_factor=0.1, volume_today=500, halted=False))
    conn.close()  # simulate the process restarting

    reopened = db.connect(path)
    states = db.load_instrument_states(reopened)
    assert states["X"] == InstrumentState(isin="X", last_seq=7, last_exchange_ts=123.0, ltp_raw=245.42, cum_factor=0.1, volume_today=500, halted=False)


def test_watermark_survives_a_reconnect(tmp_path):
    path = str(tmp_path / "since.db")

    conn = db.connect(path)
    db.save_watermark(conn, Watermark(user_id="demo", isin="X", last_seen_seq=3, last_seen_price_raw=2450.0, last_seen_cum_factor=1.0))
    conn.close()

    reopened = db.connect(path)
    watermarks = db.load_watermarks(reopened)
    assert watermarks[("demo", "X")] == Watermark(user_id="demo", isin="X", last_seen_seq=3, last_seen_price_raw=2450.0, last_seen_cum_factor=1.0)


def test_save_is_an_upsert_not_a_duplicate_row(tmp_path):
    path = str(tmp_path / "since.db")
    conn = db.connect(path)

    db.save_watermark(conn, Watermark("demo", "X", 3, 2450.0, 1.0))
    db.save_watermark(conn, Watermark("demo", "X", 9, 2500.0, 1.0))  # same key, advanced

    watermarks = db.load_watermarks(conn)
    assert len(watermarks) == 1
    assert watermarks[("demo", "X")].last_seen_seq == 9
