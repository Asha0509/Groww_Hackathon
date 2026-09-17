"""Two vendor adapters behind one shape: (isin, seq, exchange_ts, price_raw,
volume). `SCENARIOS` below is the deterministic, seeded rehearsal tool —
every ordinary tick and every rare event (a split, a feed going dark) that a
demo needs to show working on command, reproducibly, not whenever the real
market happens to feel like cooperating. `fetch_live_quote` is the other
adapter: a real vendor, Yahoo Finance's public chart endpoint, for actual
current prices. Both hand the rest of the system the exact same `Tick`
shape — `ingest`, `corpactions`, `session`, and `digest` never know which
one produced it.
"""
import random

import httpx

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


def generate_big_move(now_epoch: float) -> tuple[list[Tick], list[CorporateAction]]:
    """INFY makes a genuine +3.5% move on its final tick. `normal`'s random
    walk stays inside ±1% for every instrument, which is correct — most days
    are quiet, and silence is the product — but it leaves the digest's own
    MOVE card, and the "I looked" flow that clears it, with no scenario that
    actually fires them. This is that scenario.

    An ordinary price move, so no `CorporateAction` is emitted: the jump is
    ~1.04x, nowhere near `corpactions.CLEAN_SPLIT_RATIOS` (2x, 5x, 10x, 20x
    or their inverses, ±3%), so the unconfirmed-corporate-action detector
    (I9) correctly leaves it alone and it scores as the plain move it is.
    """
    ticks = _walk(now_epoch)
    mover_isin = "INE009A01021"  # INFY
    move_pct = 0.035

    own = [t for t in ticks if t.isin == mover_isin]
    # Sized from the seq-1 price, because that's the tick _load_scenario
    # seeds the watermark at — so the card reports exactly move_pct instead
    # of whatever the random walk happened to drift to by its last tick.
    target = round(own[0].price_raw * (1 + move_pct), 2)
    final_seq = own[-1].seq

    moved = []
    for t in ticks:
        if t.isin == mover_isin and t.seq == final_seq:
            moved.append(Tick(t.isin, t.seq, t.exchange_ts, target, t.volume))
        else:
            moved.append(t)
    return moved, []


SCENARIOS = {
    "normal": generate_normal,
    "split_day": generate_split_day,
    "feed_death": generate_feed_death,
    "big_move": generate_big_move,
}

# Real vendor adapter — no key required, but also no uptime or rate-limit
# guarantee, which is exactly why it isn't what the deterministic scenarios
# above run against. See docs/ARCHITECTURE.md for why this endpoint and
# docs/BUGS.md for its actual observed limitations.
LIVE_SYMBOLS: dict[str, str] = {
    "INE002A01018": "RELIANCE.NS",
    "INE009A01021": "INFY.NS",
    "INE040A01034": "HDFCBANK.NS",
    "INE030A01027": "HINDUNILVR.NS",
    "INE062A01020": "SBIN.NS",
    "INE070A01015": "SHREECEM.NS",
    "INE522D01027": "MANAPPURAM.NS",
    "INE761H01022": "IEX.NS",
}

_YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_LIVE_TIMEOUT_SEC = 5.0


def fetch_live_quote(yahoo_symbol: str) -> tuple[float, float, int] | None:
    """Returns (price_raw, exchange_ts, volume) for one symbol, or None on
    any failure — a bad network, a timeout, a malformed response, a symbol
    Yahoo doesn't recognize. Never raises: an unreliable dependency going
    down is an expected, handled outcome here, not an exception the caller
    has to guard against separately.
    """
    try:
        resp = httpx.get(
            _YAHOO_CHART_URL.format(symbol=yahoo_symbol),
            params={"interval": "1m", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=_LIVE_TIMEOUT_SEC,
        )
        resp.raise_for_status()
        meta = resp.json()["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        exchange_ts = float(meta["regularMarketTime"])
        volume = int(meta.get("regularMarketVolume") or 0)
        return price, exchange_ts, volume
    except Exception:
        return None


def fetch_live_ticks(next_seq: dict[str, int], symbols: dict[str, str] | None = None) -> tuple[dict[str, Tick], list[str]]:
    """Polls every instrument once. `next_seq` maps isin -> the seq to use
    if this poll succeeds (the caller owns sequencing, same as it would for
    any other vendor). `symbols` maps isin -> Yahoo ticker; defaults to
    `LIVE_SYMBOLS` (the curated 8) so every existing caller is unaffected —
    a caller managing a personal watchlist (curated instruments plus their
    own additions) passes its own map instead. Returns (ticks_by_isin,
    failed_isins) — a partial result on a partial outage, never an
    all-or-nothing failure for seven healthy symbols because one is down.
    """
    symbols = LIVE_SYMBOLS if symbols is None else symbols
    ticks: dict[str, Tick] = {}
    failed: list[str] = []
    for isin, symbol in symbols.items():
        quote = fetch_live_quote(symbol)
        if quote is None:
            failed.append(isin)
            continue
        price, exchange_ts, volume = quote
        ticks[isin] = Tick(isin, next_seq[isin], exchange_ts, round(price, 2), volume)
    return ticks, failed
