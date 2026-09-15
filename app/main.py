import datetime as dt
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse

from app.corpactions import apply_corporate_action, detect_clean_ratio_gap
from app.digest import WatermarkStore, build_digest
from app.feed import INSTRUMENTS, SCENARIOS
from app.ingest import apply_tick
from app.models import InstrumentState
from app.session import instrument_session_state

app = FastAPI(title="Since")

USER = "demo"
DEMO_NOW = dt.datetime(2026, 9, 7, 11, 0, 0)
DEMO_NOW_EPOCH = DEMO_NOW.timestamp()

_states: dict[str, InstrumentState] = {}
_watermarks = WatermarkStore()
_scenario = "normal"
_ca_notes: dict[str, dict] = {}
_unverified: dict[str, str] = {}

_CA_LABEL = {"SPLIT": "split", "BONUS": "bonus", "DIVIDEND": "dividend", "RIGHTS": "rights issue"}


def _ca_note_text(action) -> str:
    ratio = f"{int(action.ratio_from)}:{int(action.ratio_to)}"
    label = _CA_LABEL.get(action.kind, action.kind.lower())
    return f"{ratio} {label}, ex-date today — adjusted, no meaningful change"


def _unverified_note_text(ratio: float) -> str:
    shape = f"1:{int(ratio)}" if ratio >= 1 else f"{round(1 / ratio)}:1"
    return f"unconfirmed — price shape looks like a {shape} corporate action but there's no confirmed record. Treat with caution."


def _load_scenario(name: str) -> None:
    global _states, _watermarks, _scenario, _ca_notes, _unverified
    if name not in SCENARIOS:
        raise HTTPException(404, f"unknown scenario: {name}")

    ticks, actions = SCENARIOS[name](DEMO_NOW_EPOCH)
    states = {inst.isin: InstrumentState(isin=inst.isin) for inst in INSTRUMENTS}
    watermarks = WatermarkStore()
    ca_notes: dict[str, dict] = {}
    unverified: dict[str, str] = {}

    for t in ticks:
        st = states[t.isin]
        confirmed = [a for a in actions if a.isin == t.isin and a.ex_seq == t.seq]
        if confirmed:
            # pre-tick ltp is the last raw price before the ratio changed —
            # the reference a user needs to see the split isn't a crash.
            ca_notes[t.isin] = {"text": _ca_note_text(confirmed[0]), "pre_price": st.ltp_raw}
        else:
            # INVARIANT I9: a clean-ratio gap with no confirmed action behind it
            # is never silently scored as a real move — flag it instead.
            gap_ratio = detect_clean_ratio_gap(st.ltp_raw, t.price_raw)
            if gap_ratio is not None:
                unverified[t.isin] = _unverified_note_text(gap_ratio)
        apply_tick(st, t)
        for a in confirmed:
            apply_corporate_action(st, a)
        if t.seq == 1:
            # baseline: "whenever this user last actually looked" — here, session start.
            watermarks.ack(USER, t.isin, seq=1, price_raw=st.ltp_raw, cum_factor=st.cum_factor)

    _states, _watermarks, _scenario, _ca_notes, _unverified = states, watermarks, name, ca_notes, unverified


@app.on_event("startup")
def _startup() -> None:
    _load_scenario("normal")


@app.get("/healthz")
def healthz():
    return {"ok": True, "service": "since"}


@app.post("/api/scenario/{name}")
def set_scenario(name: str):
    _load_scenario(name)
    return {"scenario": name, "instruments": len(INSTRUMENTS)}


@app.get("/api/watchlist")
def watchlist():
    out = []
    for inst in INSTRUMENTS:
        st = _states[inst.isin]
        state = instrument_session_state(DEMO_NOW, DEMO_NOW_EPOCH, inst, st)
        note = _ca_notes.get(inst.isin)
        out.append(
            {
                "isin": inst.isin,
                "symbol": inst.symbol,
                "name": inst.name,
                "ltp": st.ltp_raw,
                "age_seconds": round(DEMO_NOW_EPOCH - st.last_exchange_ts),
                "session_state": state.value,
                "ca_note": note["text"] if note else None,
                "ca_pre_price": note["pre_price"] if note else None,
            }
        )
    return {"scenario": _scenario, "watchlist": out}


@app.get("/api/digest")
def digest():
    cards = build_digest(USER, INSTRUMENTS, _states, _watermarks, unverified=_unverified)
    return {"scenario": _scenario, "cards": cards}


@app.post("/api/watermark/ack")
def ack(isin: str | None = None):
    # INVARIANT I2: "I looked" advances the watermark by seq, never by client clock.
    targets = [isin] if isin else [i.isin for i in INSTRUMENTS]
    for iso in targets:
        st = _states[iso]
        _watermarks.ack(USER, iso, seq=st.last_seq, price_raw=st.ltp_raw, cum_factor=st.cum_factor)
    return {"ok": True}


_INDEX_HTML = (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/", response_class=HTMLResponse)
def root():
    return _INDEX_HTML
