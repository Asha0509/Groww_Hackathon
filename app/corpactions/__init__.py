"""Corporate action adjustment (I3) — instruments are ISIN-keyed and the
baseline self-corrects via a cumulative adjustment factor, never a backfill
migration. See CLAUDE.md 'Lie 2 — the baseline decays'.
"""
from dataclasses import dataclass

from app.models import InstrumentState


@dataclass
class CorporateAction:
    isin: str
    kind: str  # SPLIT | BONUS | DIVIDEND | RIGHTS
    ex_seq: int  # per-instrument seq at which the action takes effect
    ratio_from: float
    ratio_to: float
    adjustment_factor: float  # ratio_from / ratio_to


def apply_corporate_action(state: InstrumentState, action: CorporateAction) -> None:
    # INVARIANT I3: cum_factor only ever moves forward, never rewritten.
    state.cum_factor *= action.adjustment_factor


def adjusted_baseline(last_seen_price_raw: float, cum_factor_now: float, cum_factor_then: float) -> float:
    # INVARIANT I3: baseline = last_seen_price_raw x (cum_factor_now / cum_factor_then)
    if cum_factor_then == 0:
        return last_seen_price_raw
    return last_seen_price_raw * (cum_factor_now / cum_factor_then)


def pct_change(price_now_raw: float, last_seen_price_raw: float, cum_factor_now: float, cum_factor_then: float) -> float:
    baseline = adjusted_baseline(last_seen_price_raw, cum_factor_now, cum_factor_then)
    if baseline == 0:
        return 0.0
    return (price_now_raw - baseline) / baseline


# Ratios a real split, bonus, or reverse-split typically produces (I9).
CLEAN_SPLIT_RATIOS = (2.0, 5.0, 10.0, 20.0)
RATIO_TOLERANCE = 0.03  # 3% — real ticks land near a clean ratio, not exactly on it


def detect_clean_ratio_gap(price_before: float, price_after: float) -> float | None:
    """Returns the clean ratio (10.0 for a ~1:10 drop, 0.1 for a ~10:1 rise) if two
    consecutive raw ticks match a known split/bonus/reverse-split shape within
    tolerance, else None. A genuine, ordinary price move essentially never lands
    within 3% of an exact 2x/5x/10x/20x ratio, so this is a narrow, low-noise
    signal — not a general anomaly detector.

    This only *detects the shape* of the gap. It does not confirm a corporate
    action happened; the caller decides what to do when the shape is clean but
    no confirmed CorporateAction record covers that tick (INVARIANT I9: fail
    toward silence, not toward a lie).
    """
    if price_before <= 0 or price_after <= 0:
        return None
    ratio = price_before / price_after
    for clean in CLEAN_SPLIT_RATIOS:
        if abs(ratio - clean) <= clean * RATIO_TOLERANCE:
            return clean  # e.g. price dropped ~10x -> looks like a 1:10 split
        inv = 1 / clean
        if abs(ratio - inv) <= inv * RATIO_TOLERANCE:
            return inv  # e.g. price rose ~10x -> looks like a 10:1 reverse split
    return None
