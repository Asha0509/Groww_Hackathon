"""The naive baseline — deliberately wrong. Kept building forever as a
deliverable: this is what a watchlist looks like without I3/I5/I6.
Raw price vs raw watermark. One global staleness timeout. See docs/CLAUDE.md
'The three lies'.
"""

GLOBAL_STALE_MS = 30_000


def naive_pct_change(price_now_raw: float, last_seen_price_raw: float) -> float:
    if last_seen_price_raw == 0:
        return 0.0
    return (price_now_raw - last_seen_price_raw) / last_seen_price_raw


def naive_is_stale(now_epoch: float, last_exchange_ts: float) -> bool:
    return (now_epoch - last_exchange_ts) * 1000 > GLOBAL_STALE_MS
