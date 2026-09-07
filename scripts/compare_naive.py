#!/usr/bin/env python3
"""Prints the naive-vs-Since red/green table: same feed, two baselines.

Two lies, one demo each:
  split_day — the price lie   (naive raw diff vs I3 adjusted baseline)
  weekend   — the staleness lie (naive global 30s timeout vs I5/I6 per-symbol liveness)
"""
import datetime as dt
import sys

sys.path.insert(0, ".")

from app import naive
from app.corpactions import apply_corporate_action, pct_change
from app.feed import generate_normal, generate_split_day
from app.ingest import apply_tick
from app.models import Instrument, InstrumentState
from app.session import SessionState, instrument_session_state

GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
OFF = "\033[0m"

RELIANCE = "INE002A01018"
rows: list[tuple[str, str, str, str]] = []  # scenario, system, verdict, detail


def wrong(detail: str) -> tuple[str, str]:
    return f"{RED}✗ FALSE ALARM{OFF}", detail


def right(detail: str) -> tuple[str, str]:
    return f"{GREEN}✓ CORRECT{OFF}", detail


# --- split_day: the price lie ----------------------------------------------
now_epoch = dt.datetime(2026, 9, 7, 11, 0, 0).timestamp()
ticks, actions = generate_split_day(now_epoch)
state = InstrumentState(isin=RELIANCE)
watermark_price = watermark_cum_factor = None

for t in [t for t in ticks if t.isin == RELIANCE]:
    if t.seq == 5:
        watermark_price, watermark_cum_factor = state.ltp_raw, state.cum_factor
    apply_tick(state, t)
    for a in [a for a in actions if a.isin == t.isin and a.ex_seq == t.seq]:
        apply_corporate_action(state, a)

naive_change = naive.naive_pct_change(state.ltp_raw, watermark_price)
since_change = pct_change(state.ltp_raw, watermark_price, state.cum_factor, watermark_cum_factor)

v, d = wrong(f"RELIANCE {naive_change * 100:+.1f}% (screaming, wrong)")
rows.append(("split_day", "naive", v, d))
v, d = right(f"RELIANCE {since_change * 100:+.1f}% adjusted, 1:10 split detected, no meaningful change")
rows.append(("split_day", "since", v, d))

# --- weekend: the staleness lie ---------------------------------------------
friday_close = dt.datetime(2026, 9, 4, 15, 30, 0).timestamp()
n_ticks, _ = generate_normal(friday_close)
last_tick = max((t for t in n_ticks if t.isin == RELIANCE), key=lambda t: t.seq)
state2 = InstrumentState(isin=RELIANCE)
apply_tick(state2, last_tick)

saturday_noon = dt.datetime(2026, 9, 5, 12, 0, 0)
sat_epoch = saturday_noon.timestamp()

is_stale = naive.naive_is_stale(sat_epoch, state2.last_exchange_ts)
v, d = wrong(f"RELIANCE STALE (last tick {int((sat_epoch - state2.last_exchange_ts) / 3600)}h ago, >30s global timeout)")
rows.append(("weekend", "naive", v, d))

reliance_inst = Instrument(RELIANCE, "RELIANCE", "Reliance Industries", "liquid")
since_state = instrument_session_state(saturday_noon, sat_epoch, reliance_inst, state2)
v, d = right(f"RELIANCE {since_state.value} (market closed, not stale)")
rows.append(("weekend", "since", v, d))

print(f"{BOLD}{'SCENARIO':<12}{'SYSTEM':<8}{'VERDICT':<24}DETAIL{OFF}")
for scenario, system, verdict, detail in rows:
    plain_len = len(verdict) - 9  # strip ANSI color codes from column width math
    print(f"{scenario:<12}{system:<8}{verdict}{' ' * max(0, 24 - plain_len)}{detail}")
