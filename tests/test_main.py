"""Tests for the FastAPI wiring in app.main — scenario load ordering, the
concurrency lock, and basic endpoint error handling. Previously undisclosed
gap in coverage per docs/BUGS.md; every invariant-bearing module had unit
tests, but the HTTP layer that ties them together didn't.
"""
import threading

from fastapi.testclient import TestClient

import app.main as m
from app import db
from app.digest import Watermark
from app.main import app

client = TestClient(app)


def test_unknown_scenario_returns_404_not_a_crash():
    resp = client.post("/api/scenario/does_not_exist")
    assert resp.status_code == 404


def test_unknown_isin_on_ack_returns_404_not_a_crash():
    """Found during the pre-panel input-validation pass: ack('isin=garbage')
    used to raise an unhandled KeyError (a raw 500) instead of a clean
    error, since nothing checked the isin was real before indexing _states
    with it.
    """
    client.post("/api/scenario/normal")
    resp = client.post("/api/watermark/ack", params={"isin": "NOT_A_REAL_ISIN"})
    assert resp.status_code == 404


def test_live_refresh_outside_live_mode_returns_409_not_a_crash():
    client.post("/api/scenario/normal")
    resp = client.post("/api/live/refresh")
    assert resp.status_code == 409


def test_normal_scenario_loads_and_digest_responds():
    resp = client.post("/api/scenario/normal")
    assert resp.status_code == 200
    resp = client.get("/api/digest")
    assert resp.status_code == 200
    assert resp.json()["scenario"] == "normal"


def test_load_scenario_detects_an_unconfirmed_clean_ratio_gap(monkeypatch):
    """Exercises _load_scenario's own detection wiring end-to-end — not just
    build_digest's handling of a hand-built dict (tests/test_corpactions.py
    covers that half). A tick sequence with a clean ~1:10 gap and no
    CorporateAction record must populate _unverified for that instrument.
    """
    from app.models import Tick

    def fake_scenario(now_epoch):
        isin = "INE002A01018"
        ticks = [
            Tick(isin, 1, now_epoch - 2, 2450.0, 1000),
            Tick(isin, 2, now_epoch, 245.0, 1000),  # clean ~10x drop, no CA record
        ]
        return ticks, []  # no confirmed CorporateAction

    monkeypatch.setitem(m.SCENARIOS, "unconfirmed_gap_test", fake_scenario)
    m._load_scenario("unconfirmed_gap_test")

    assert "INE002A01018" in m._unverified
    assert "1:10" in m._unverified["INE002A01018"]

    digest = client.get("/api/digest").json()
    card = next(c for c in digest["cards"] if c["isin"] == "INE002A01018")
    assert card["kind"] == "UNVERIFIED_CORPORATE_ACTION"

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_feed_death_scenario_flags_the_dead_instrument_degraded_not_move():
    """INVARIANT I5/I6: an instrument whose feed has gone dark mid-session is
    DEGRADED, not silently shown with a stale price as if it were current —
    and every other instrument, still ticking normally, stays LIVE.
    """
    client.post("/api/scenario/feed_death")

    wl = client.get("/api/watchlist").json()
    rows = {r["isin"]: r for r in wl["watchlist"]}
    hdfc = rows["INE040A01034"]  # HDFCBANK
    assert hdfc["session_state"] == "DEGRADED"
    assert hdfc["age_seconds"] >= 60

    live_others = [r for isin, r in rows.items() if isin != "INE040A01034"]
    assert all(r["session_state"] == "LIVE" for r in live_others)

    digest = client.get("/api/digest").json()
    degraded_cards = [c for c in digest["cards"] if c["isin"] == "INE040A01034"]
    assert len(degraded_cards) == 1
    assert degraded_cards[0]["kind"] == "DEGRADED_FEED"
    assert "pct_change" not in degraded_cards[0]  # never scored as a move

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_concurrent_scenario_reload_and_reads_stay_internally_consistent():
    """The lock in app.main guards exactly this: without it, a reader can
    observe _states already swapped to the new scenario while _scenario (or
    _watermarks) is still the old one, because the five-name reassignment at
    the end of _load_scenario is not atomic across threads on its own. This
    hammers a scenario reload against concurrent reads and asserts every
    observed response is self-consistent — RELIANCE's raw price always
    matches the scenario tag reported in that same response.

    This is a stress test, not a proof: it demonstrates the absence of the
    specific torn read many times under real thread interleaving, backed by
    a structural fix (the lock), not by the test run alone. See docs/LLD.md.
    """
    errors = []

    def reload_loop():
        for i in range(25):
            name = "split_day" if i % 2 == 0 else "normal"
            client.post(f"/api/scenario/{name}")

    def read_loop():
        for _ in range(25):
            wl = client.get("/api/watchlist").json()
            reliance = next(r for r in wl["watchlist"] if r["isin"] == "INE002A01018")
            scenario = wl["scenario"]
            if scenario == "split_day" and not (200 <= reliance["ltp"] <= 260):
                errors.append(("split_day", reliance["ltp"]))
            elif scenario == "normal" and not (2400 <= reliance["ltp"] <= 2500):
                errors.append(("normal", reliance["ltp"]))

    threads = [threading.Thread(target=reload_loop) for _ in range(2)]
    threads += [threading.Thread(target=read_loop) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"torn reads observed: {errors}"

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_persisted_watermark_survives_the_startup_replay(tmp_path):
    """This is what _startup() does on a real process restart: load whatever
    was durably saved, then replay a scenario. INVARIANT I2 (WatermarkStore's
    monotonic guard) must mean the persisted seq blocks the replay's own
    fresh seq=1 baseline-seed — otherwise 'persistence' would be pointless,
    since every restart would immediately overwrite it back to seq=1.
    """
    path = str(tmp_path / "since.db")
    conn = db.connect(path)
    db.save_watermark(conn, Watermark("demo", "INE002A01018", last_seen_seq=5, last_seen_price_raw=2450.0, last_seen_cum_factor=1.0))
    persisted = db.load_watermarks(conn)

    # Exercises the exact seeding path _startup() uses, without touching the
    # app's own global _db/DB_PATH (keeps this test isolated from the others).
    m._load_scenario("normal", seed_watermarks=persisted)
    wm = m._watermarks.get("demo", "INE002A01018")
    assert wm.last_seen_seq == 5  # not rewound to the fresh seq=1 reseed

    client.post("/api/scenario/normal")  # leave state clean for other tests


def _fake_quote(_symbol):
    return 100.0, 1757222400.0, 1234


def _enter_live(monkeypatch):
    """Puts the app in live mode without touching the network — the same path
    POST /api/scenario/live takes, with the vendor adapter stubbed out."""
    monkeypatch.setattr(m, "fetch_live_quote", _fake_quote)
    monkeypatch.setattr(m, "fetch_live_ticks", lambda next_seq, symbols=None: (
        {isin: m.Tick(isin, next_seq[isin], 1757222400.0, 100.0, 1234) for isin in (symbols or {})},
        [],
    ))
    client.post("/api/scenario/live")


def test_watchlist_management_is_rejected_outside_live_mode():
    """The scripted scenarios are a pinned rehearsal set — letting a
    user add or remove instruments mid-replay would break exactly the
    determinism they exist to provide."""
    client.post("/api/scenario/split_day")
    add = client.post("/api/watchlist/add", params={"symbol": "TCS"})
    remove = client.post("/api/watchlist/remove", params={"isin": "INE002A01018"})
    assert add.status_code == 409
    assert remove.status_code == 409
    assert "switch to Live" in add.json()["detail"]

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_add_rejects_a_symbol_the_vendor_does_not_recognise(monkeypatch):
    _enter_live(monkeypatch)
    monkeypatch.setattr(m, "fetch_live_quote", lambda _s: None)
    resp = client.post("/api/watchlist/add", params={"symbol": "NOTAREALTICKER"})
    assert resp.status_code == 400
    assert "no live quote" in resp.json()["detail"]

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_add_then_remove_a_custom_instrument_in_live_mode(monkeypatch):
    _enter_live(monkeypatch)
    added = client.post("/api/watchlist/add", params={"symbol": "tcs"})
    assert added.status_code == 200
    assert added.json()["added"] == "TCS"  # symbol-as-isin, see app/db.py schema note
    assert m._custom_live_symbols["TCS"] == "TCS.NS"

    isins = [r["isin"] for r in client.get("/api/watchlist").json()["watchlist"]]
    assert "TCS" in isins

    assert client.post("/api/watchlist/remove", params={"isin": "TCS"}).status_code == 200
    isins = [r["isin"] for r in client.get("/api/watchlist").json()["watchlist"]]
    assert "TCS" not in isins

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_removing_a_curated_instrument_never_affects_a_scripted_scenario(monkeypatch):
    """The curated 8 are excluded, not deleted — app.feed.INSTRUMENTS is what
    the deterministic replays walk, so it must come back intact."""
    _enter_live(monkeypatch)
    assert client.post("/api/watchlist/remove", params={"isin": "INE040A01034"}).status_code == 200
    live_isins = [r["isin"] for r in client.get("/api/watchlist").json()["watchlist"]]
    assert "INE040A01034" not in live_isins

    client.post("/api/scenario/feed_death")
    replay_isins = [r["isin"] for r in client.get("/api/watchlist").json()["watchlist"]]
    assert len(replay_isins) == 8
    assert "INE040A01034" in replay_isins

    m._excluded_isins.clear()
    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_remove_rejects_an_unknown_instrument(monkeypatch):
    _enter_live(monkeypatch)
    assert client.post("/api/watchlist/remove", params={"isin": "NOPE"}).status_code == 404

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_watchlist_exposes_the_watermark_itself(monkeypatch):
    """The row has to be able to show "your baseline: seq X, price Y" — that
    diff is the whole product, so the numbers behind it aren't hidden."""
    client.post("/api/scenario/normal")
    client.post("/api/watermark/ack")
    row = next(r for r in client.get("/api/watchlist").json()["watchlist"] if r["isin"] == "INE002A01018")
    assert row["last_seen_seq"] == row["last_seq"]
    assert row["last_seen_price_raw"] == row["ltp"]
    assert row["last_seen_cum_factor"] == row["cum_factor"]

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_big_move_fires_exactly_one_move_card():
    """`normal`'s walk stays inside ±1%, so nothing in it clears
    MOVE_THRESHOLD and the digest is correctly silent — which left the MOVE
    card itself with no scenario that exercises it end-to-end. This is that
    scenario: one real move, one card, everything else still quiet.
    """
    client.post("/api/scenario/big_move")
    cards = client.get("/api/digest").json()["cards"]
    assert len(cards) == 1, cards

    card = cards[0]
    assert card["symbol"] == "INFY"
    assert card["kind"] == "MOVE"  # an ordinary move, not UNVERIFIED_CORPORATE_ACTION
    assert card["pct_change"] == 3.5

    row = next(r for r in client.get("/api/watchlist").json()["watchlist"] if r["symbol"] == "INFY")
    assert row["last_seen_price_raw"] == 1476.51  # the seq-1 baseline the move is sized from
    assert row["ltp"] == 1528.19
    assert row["ca_note"] is None  # no corporate action involved

    client.post("/api/scenario/normal")  # leave state clean for other tests


def test_big_move_leaves_every_other_instrument_silent():
    client.post("/api/scenario/big_move")
    movers = {c["symbol"] for c in client.get("/api/digest").json()["cards"]}
    assert movers == {"INFY"}

    client.post("/api/scenario/normal")  # leave state clean for other tests
