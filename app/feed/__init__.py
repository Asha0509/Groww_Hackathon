"""Deterministic seeded tick simulator. Two scenarios: normal, split_day.

This is the vendor adapter's only implementation — a real feed would satisfy
the same (isin, seq, exchange_ts, price_raw, volume) shape.
"""
import random

from app.corpactions import CorporateAction
from app.models import Instrument, Tick

SEED = 20260907

INSTRUMENTS: list[Instrument] = [
    Instrument("INE002A01018", "RELIANCE", "Reliance Industries", "liquid"),
    Instrument("INE009A01021", "INFY", "Infosys", "liquid"),
    Instrument("INE040A01034", "HDFCBANK", "HDFC Bank", "liquid"),
    Instrument("INE030A01027", "HINDUNILVR", "Hindustan Unilever", "liquid"),
    Instrument("INE062A01020", "SBIN", "State Bank of India", "liquid"),
    Instrument("INE070A01015", "SHREECEM", "Shree Cement", "liquid"),
    Instrument("INE522D01027", "MANAPPURAM", "Manappuram Finance", "illiquid"),
    Instrument("INE761H01022", "IEX", "Indian Energy Exchange", "illiquid"),
]

BASE_PRICES = {
    "INE002A01018": 2450.0,
    "INE009A01021": 1480.0,
    "INE040A01034": 1620.0,
    "INE030A01027": 2510.0,
    "INE062A01020": 610.0,
    "INE070A01015": 24800.0,
    "INE522D01027": 180.0,
    "INE761H01022": 155.0,
}


def _walk(now_epoch: float) -> list[Tick]:
    rng = random.Random(SEED)
    ticks = []
    for inst in INSTRUMENTS:
        price = BASE_PRICES[inst.isin]
        n = 10 if inst.liquidity_tier == "liquid" else 3
        step_sec = 2.0 if inst.liquidity_tier == "liquid" else 60.0
        for seq in range(1, n + 1):
            price *= 1 + rng.uniform(-0.003, 0.003)
            ts = now_epoch - (n - seq) * step_sec
            ticks.append(Tick(inst.isin, seq, ts, round(price, 2), rng.randint(100, 5000)))
    return ticks


def generate_normal(now_epoch: float) -> tuple[list[Tick], list[CorporateAction]]:
    return _walk(now_epoch), []


def generate_split_day(now_epoch: float) -> tuple[list[Tick], list[CorporateAction]]:
    """RELIANCE undergoes a 1:10 split partway through the session."""
    ticks = _walk(now_epoch)
    split_isin = "INE002A01018"
    split_seq = 6

    adjusted = []
    for t in ticks:
        if t.isin == split_isin and t.seq >= split_seq:
            adjusted.append(Tick(t.isin, t.seq, t.exchange_ts, round(t.price_raw / 10, 2), t.volume))
        else:
            adjusted.append(t)

    action = CorporateAction(split_isin, "SPLIT", ex_seq=split_seq, ratio_from=1, ratio_to=10, adjustment_factor=1 / 10)
    return adjusted, [action]


def generate_feed_death(now_epoch: float) -> tuple[list[Tick], list[CorporateAction]]:
    """HDFCBANK ticks normally, then its feed simply goes quiet for the rest
    of the session — no more ticks arrive, ever. Every other instrument keeps
    ticking right up to `now_epoch`, so the contrast is visible: one silent
    instrument next to seven live ones, not a market-wide outage.

    This is not the same failure as a `CLOSED` market: the exchange is open,
    other instruments are still trading, and only this one feed has gone
    dark — the exact distinction `session.instrument_session_state` exists
    to draw (DEGRADED, not CLOSED).
    """
    ticks = _walk(now_epoch)
    dead_isin = "INE040A01034"  # HDFCBANK
    silence_sec = 60.0  # far past the liquid-tier DEGRADED threshold (12s)

    shifted = []
    for t in ticks:
        if t.isin == dead_isin:
            shifted.append(Tick(t.isin, t.seq, t.exchange_ts - silence_sec, t.price_raw, t.volume))
        else:
            shifted.append(t)
    return shifted, []


SCENARIOS = {
    "normal": generate_normal,
    "split_day": generate_split_day,
    "feed_death": generate_feed_death,
}
