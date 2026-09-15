import datetime as dt
import os
import threading
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app import db
from app.corpactions import CorporateAction, apply_corporate_action, detect_clean_ratio_gap
from app.digest import Watermark, WatermarkStore, build_digest
from app.feed import INSTRUMENTS, LIVE_SYMBOLS, SCENARIOS, fetch_live_ticks
from app.ingest import apply_tick
from app.models import InstrumentState, Tick
from app.session import SessionState, instrument_session_state

app = FastAPI(title="Since")

USER = "demo"
IST = ZoneInfo("Asia/Kolkata")
DEMO_NOW = dt.datetime(2026, 9, 7, 11, 0, 0)
DEMO_NOW_EPOCH = DEMO_NOW.timestamp()

DB_PATH = os.environ.get("SINCE_DB_PATH", "data/since.db")
_db = None  # set by _startup(); a sqlite3.Connection in WAL mode (app.db)

_states: dict[str, InstrumentState] = {}
_watermarks = WatermarkStore()
_scenario = "normal"
_ca_notes: dict[str, dict] = {}
_unverified: dict[str, str] = {}
_live_next_seq: dict[str, int] = {isin: 1 for isin in LIVE_SYMBOLS}

# Guards every read and every mutation of the five globals above. FastAPI
# runs each sync endpoint in its own OS thread, so a scenario reload racing
# a digest read is a real interleaving, not a theoretical one — without this
# lock, a reader can observe _states already swapped to the new scenario
# while _watermarks (or _scenario itself) is still the old one, because the
# five-name reassignment at the end of _load_scenario is not atomic across
# threads on its own. The lock is held only around the swap itself (the tick
# replay that builds the new state happens on fresh local objects, untouched
# by other requests) and around each read, so it stays a short critical
# section rather than serializing unrelated work like `/` or `/healthz`.
_lock = threading.Lock()

_CA_LABEL = {"SPLIT": "split", "BONUS": "bonus", "DIVIDEND": "dividend", "RIGHTS": "rights issue"}


def _ca_note_text(action) -> str:
    ratio = f"{int(action.ratio_from)}:{int(action.ratio_to)}"
    label = _CA_LABEL.get(action.kind, action.kind.lower())
    return f"{ratio} {label}, ex-date today — adjusted, no meaningful change"


def _unverified_note_text(ratio: float) -> str:
    shape = f"1:{int(ratio)}" if ratio >= 1 else f"{round(1 / ratio)}:1"
    return f"unconfirmed — price shape looks like a {shape} corporate action but there's no confirmed record. Treat with caution."


def _apply_one_tick(
    st: InstrumentState,
    t: Tick,
    actions: list[CorporateAction],
    ca_notes: dict[str, dict],
    unverified: dict[str, str],
) -> None:
    """The one piece of ingest logic every tick goes through, whether it came
    from a deterministic scenario replay or a live vendor poll: confirmed
    corporate actions adjust cum_factor (I3); an unconfirmed clean-ratio gap
    is flagged, never silently scored (I9); ordering is enforced last, by
    apply_tick itself (I4). Mutates st/ca_notes/unverified in place.
    """
    confirmed = [a for a in actions if a.isin == t.isin and a.ex_seq == t.seq]
    if confirmed:
        # pre-tick ltp is the last raw price before the ratio changed — the
        # reference a user needs to see the split isn't a crash.
        ca_notes[t.isin] = {"text": _ca_note_text(confirmed[0]), "pre_price": st.ltp_raw}
    else:
        gap_ratio = detect_clean_ratio_gap(st.ltp_raw, t.price_raw)
        if gap_ratio is not None:
            unverified[t.isin] = _unverified_note_text(gap_ratio)
    apply_tick(st, t)
    for a in confirmed:
        apply_corporate_action(st, a)


def _now() -> tuple[dt.datetime, float]:
    """Real wall-clock time in live mode — specifically real IST, the
    timezone session.calendar_state's market-hour constants assume. Found
    by actually running live mode on this machine: a naive dt.datetime.now()
    returns the SERVER's local time, which on most deployments (this one
    included) is UTC, not IST — comparing UTC's hour/minute against IST
    market hours silently misclassifies LIVE-vs-CLOSED by the 5.5-hour
    offset. session.instrument_session_state never calls .timestamp() on
    the datetime it's given, so an explicitly-IST-aware one works with zero
    changes to that module. The fixed DEMO_NOW everywhere else, so the
    seeded scenarios stay exactly as repeatable as they always were.
    """
    if _scenario == "live":
        now = dt.datetime.now(IST)
        return now, now.timestamp()
    return DEMO_NOW, DEMO_NOW_EPOCH


def _load_scenario(name: str, seed_watermarks: dict[tuple[str, str], Watermark] | None = None) -> None:
    """seed_watermarks, when given, is only ever used once — from _startup(),
    loaded from disk. A scenario switch via the API always starts from a
    fresh session-start baseline (unchanged behavior), so the demo stays
    seeded and deterministic: a persisted watermark could otherwise make the
    very next `POST /api/scenario/...` during a live demo depend on
    whatever state a previous run happened to leave behind.
    """
    global _states, _watermarks, _scenario, _ca_notes, _unverified
    if name not in SCENARIOS:
        raise HTTPException(404, f"unknown scenario: {name}")

    ticks, actions = SCENARIOS[name](DEMO_NOW_EPOCH)
    states = {inst.isin: InstrumentState(isin=inst.isin) for inst in INSTRUMENTS}
    watermarks = WatermarkStore()
    ca_notes: dict[str, dict] = {}
    unverified: dict[str, str] = {}

    for (uid, isin), wm in (seed_watermarks or {}).items():
        # INVARIANT I2: this only ever advances the fresh store, never rewinds
        # it — WatermarkStore.ack's own monotonic guard does the work.
        watermarks.ack(uid, isin, wm.last_seen_seq, wm.last_seen_price_raw, wm.last_seen_cum_factor)

    for t in ticks:
        st = states[t.isin]
        _apply_one_tick(st, t, actions, ca_notes, unverified)
        if t.seq == 1:
            # baseline: "whenever this user last actually looked" — here, session start.
            watermarks.ack(USER, t.isin, seq=1, price_raw=st.ltp_raw, cum_factor=st.cum_factor)

    with _lock:
        _states, _watermarks, _scenario, _ca_notes, _unverified = states, watermarks, name, ca_notes, unverified

    if _db is not None:
        # Write-through: durably record the market data this scenario replay
        # produced. Not read back at startup (see _startup) — each scenario
        # load stays a fresh, deterministic replay by design; this only means
        # the last known state is on disk, not that it's resumed from disk.
        for st in states.values():
            db.save_instrument_state(_db, st)


def _load_live_baseline() -> None:
    """Activates live mode: one real poll of every instrument, seq=1, and a
    fresh watermark baseline seeded at that poll — same shape as any
    deterministic scenario's first tick, just sourced from a real vendor
    instead of a replay. If every single fetch fails (the vendor itself is
    down), this raises rather than silently presenting an all-zero
    watchlist as if it were real data.
    """
    global _states, _watermarks, _scenario, _ca_notes, _unverified, _live_next_seq
    next_seq = {isin: 1 for isin in LIVE_SYMBOLS}
    ticks, failed = fetch_live_ticks(next_seq)
    if not ticks:
        raise HTTPException(503, "live feed unavailable right now — every symbol failed to fetch")

    states = {inst.isin: InstrumentState(isin=inst.isin) for inst in INSTRUMENTS}
    watermarks = WatermarkStore()
    ca_notes: dict[str, dict] = {}
    unverified: dict[str, str] = {}
    for isin, t in ticks.items():
        st = states[isin]
        _apply_one_tick(st, t, [], ca_notes, unverified)  # no CA feed exists for live data
        watermarks.ack(USER, isin, seq=1, price_raw=st.ltp_raw, cum_factor=st.cum_factor)

    with _lock:
        _states, _watermarks, _scenario, _ca_notes, _unverified = states, watermarks, "live", ca_notes, unverified
        _live_next_seq = {isin: (2 if isin in ticks else 1) for isin in LIVE_SYMBOLS}

    if _db is not None:
        for st in states.values():
            db.save_instrument_state(_db, st)


@app.post("/api/live/refresh")
def live_refresh():
    """Polls every instrument again and applies whatever comes back as a new
    tick on top of the CURRENT state — incremental, not a replay from
    scratch, so prices actually move and the digest reflects a real diff
    against the baseline set when live mode was activated. A symbol that
    fails this round just doesn't advance; it isn't reset, hidden, or
    guessed at.

    The network fetch (up to 8 sequential HTTP calls, each with its own
    timeout) deliberately happens OUTSIDE the lock — holding a global lock
    across blocking I/O would freeze every other endpoint for as long as the
    slowest vendor call takes. Only the brief in-memory apply is locked.
    """
    with _lock:
        if _scenario != "live":
            raise HTTPException(409, "not in live mode — POST /api/scenario/live first")
        next_seq = dict(_live_next_seq)

    ticks, failed = fetch_live_ticks(next_seq)

    with _lock:
        if _scenario != "live":
            # Scenario changed while this fetch was in flight — discard
            # rather than apply stale live ticks onto whatever's active now.
            raise HTTPException(409, "no longer in live mode")
        for isin, t in ticks.items():
            st = _states[isin]
            _apply_one_tick(st, t, [], _ca_notes, _unverified)
            _live_next_seq[isin] = t.seq + 1
            if _db is not None:
                db.save_instrument_state(_db, st)
        return {"updated": list(ticks.keys()), "failed": failed}


@app.on_event("startup")
def _startup() -> None:
    global _db
    _db = db.connect(DB_PATH)
    # INVARIANT: "how state persists across sessions/devices" — a user's own
    # watermark (never the market data) is what should survive a restart.
    # Loading it before the very first scenario replay means _load_scenario's
    # own seq=1 baseline-seed can't rewind it (WatermarkStore.ack is
    # monotonic), so a prior "I looked" durably outlives this process.
    persisted = db.load_watermarks(_db)
    _load_scenario("normal", seed_watermarks=persisted)


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "since"}


@app.post("/api/scenario/{name}")
def set_scenario(name: str):
    if name == "live":
        _load_live_baseline()
    else:
        _load_scenario(name)
    return {"scenario": name, "instruments": len(INSTRUMENTS)}


@app.get("/api/watchlist")
def watchlist():
    with _lock:
        now, now_epoch = _now()
        out = []
        for inst in INSTRUMENTS:
            st = _states[inst.isin]
            state = instrument_session_state(now, now_epoch, inst, st)
            note = _ca_notes.get(inst.isin)
            out.append(
                {
                    "isin": inst.isin,
                    "symbol": inst.symbol,
                    "name": inst.name,
                    "ltp": st.ltp_raw,
                    "age_seconds": round(now_epoch - st.last_exchange_ts),
                    "session_state": state.value,
                    "ca_note": note["text"] if note else None,
                    "ca_pre_price": note["pre_price"] if note else None,
                }
            )
        return {"scenario": _scenario, "watchlist": out}


def _degraded_notes() -> dict[str, str]:
    """Instruments currently DEGRADED, with a plain-language reason. Called
    only while holding _lock."""
    now, now_epoch = _now()
    notes = {}
    for inst in INSTRUMENTS:
        st = _states[inst.isin]
        state = instrument_session_state(now, now_epoch, inst, st)
        if state == SessionState.DEGRADED:
            age_s = round(now_epoch - st.last_exchange_ts)
            notes[inst.isin] = f"feed has gone quiet — no new ticks in {age_s}s. The price shown is the last one received, not a current price."
    return notes


@app.get("/api/digest")
def digest():
    with _lock:
        cards = build_digest(
            USER, INSTRUMENTS, _states, _watermarks, unverified=_unverified, degraded=_degraded_notes()
        )
        return {"scenario": _scenario, "cards": cards}


@app.post("/api/watermark/ack")
def ack(isin: str | None = None):
    # INVARIANT I2: "I looked" advances the watermark by seq, never by client clock.
    with _lock:
        if isin is not None and isin not in _states:
            raise HTTPException(404, f"unknown instrument: {isin}")
        targets = [isin] if isin else [i.isin for i in INSTRUMENTS]
        for iso in targets:
            st = _states[iso]
            wm = _watermarks.ack(USER, iso, seq=st.last_seq, price_raw=st.ltp_raw, cum_factor=st.cum_factor)
            if _db is not None:
                db.save_watermark(_db, wm)
        return {"ok": True}


_INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/", response_class=HTMLResponse)
def root():
    return _INDEX_HTML
