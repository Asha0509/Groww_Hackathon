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
