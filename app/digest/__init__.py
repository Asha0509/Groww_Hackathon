"""Watermark diff + digest budget (I2, I10). The watermark is the personal
baseline: whenever this user last actually looked. See docs/CLAUDE.md
'Lie 1 — the baseline is not personal'.
"""
from dataclasses import dataclass
from typing import Optional

from app.corpactions import pct_change
from app.models import Instrument, InstrumentState

MOVE_THRESHOLD = 0.02  # 2% adjusted move is worth a card
DEFAULT_BUDGET = 5


@dataclass
class Watermark:
    user_id: str
    isin: str
    last_seen_seq: int
    last_seen_price_raw: float
    last_seen_cum_factor: float


class WatermarkStore:
    def __init__(self) -> None:
        self._store: dict[tuple[str, str], Watermark] = {}

    def get(self, user_id: str, isin: str) -> Optional[Watermark]:
        return self._store.get((user_id, isin))

    def ack(self, user_id: str, isin: str, seq: int, price_raw: float, cum_factor: float) -> Watermark:
        current = self._store.get((user_id, isin))
        # INVARIANT I2: watermark advances monotonically by seq, never rewinds.
        if current is not None and seq <= current.last_seen_seq:
            return current
        wm = Watermark(user_id, isin, seq, price_raw, cum_factor)
        self._store[(user_id, isin)] = wm
        return wm

    def drop(self, user_id: str, isin: str) -> None:
        """Forget this user's baseline for an instrument that no longer exists
        for them. The monotonic guard in ack is about a stale write losing to a
        newer one, not about a baseline outliving the thing it measured: an
        instrument dropped from a watchlist and added again starts from seq 1
        with no history, and a surviving watermark would sit above that and
        refuse to move, leaving a fresh row diffed against a price from a
        watchlist the user already deleted.
        """
        self._store.pop((user_id, isin), None)


def build_digest(
    user_id: str,
    instruments: list[Instrument],
    states: dict[str, InstrumentState],
    watermarks: WatermarkStore,
    budget: int = DEFAULT_BUDGET,
    unverified: dict[str, str] | None = None,
    degraded: dict[str, str] | None = None,
) -> list[dict]:
    unverified = unverified or {}
    degraded = degraded or {}
    cards = []
    for inst in instruments:
        st = states[inst.isin]
        wm = watermarks.get(user_id, inst.isin)
        if wm is None:
            # first time seeing this instrument: baseline is now, no card
            watermarks.ack(user_id, inst.isin, st.last_seq, st.ltp_raw, st.cum_factor)
            continue
        if inst.isin in degraded:
            # INVARIANT I5/I6: a degraded feed is never reported as a price
            # move — the last known price can't be trusted while the feed is
            # dark, so it's flagged instead of silently scored either way.
            cards.append(
                {
                    "isin": inst.isin,
                    "symbol": inst.symbol,
                    "kind": "DEGRADED_FEED",
                    "message": degraded[inst.isin],
                    "score": 1.0,
                }
            )
            continue
        if inst.isin in unverified:
            # INVARIANT I9: a clean-ratio gap with no confirmed corporate action
            # is never reported as a price move — flag it and stop, don't score it.
            cards.append(
                {
                    "isin": inst.isin,
                    "symbol": inst.symbol,
                    "kind": "UNVERIFIED_CORPORATE_ACTION",
                    "message": unverified[inst.isin],
                    "score": 1.0,
                }
            )
            continue
        change = pct_change(st.ltp_raw, wm.last_seen_price_raw, st.cum_factor, wm.last_seen_cum_factor)
        if abs(change) >= MOVE_THRESHOLD:
            cards.append(
                {
                    "isin": inst.isin,
                    "symbol": inst.symbol,
                    "kind": "MOVE",
                    "pct_change": round(change * 100, 2),
                    "score": abs(change),
                }
            )
    cards.sort(key=lambda c: c["score"], reverse=True)
    # INVARIANT I10: digest has a budget — silence is a feature.
    return cards[:budget]
