from app.corpactions import CorporateAction, apply_corporate_action, pct_change
from app.models import InstrumentState


def test_split_leaves_pct_change_near_zero():
    state = InstrumentState(isin="X", cum_factor=1.0)
    last_seen_price_raw = 2000.0
    last_seen_cum_factor = 1.0

    split = CorporateAction("X", "SPLIT", ex_seq=5, ratio_from=1, ratio_to=10, adjustment_factor=1 / 10)
    apply_corporate_action(state, split)  # cum_factor now 0.1

    price_now_raw = 202.0  # 200 post-split + a genuine tiny move
    change = pct_change(price_now_raw, last_seen_price_raw, state.cum_factor, last_seen_cum_factor)
    assert abs(change) < 0.02  # not the naive -90%


def test_no_corporate_action_is_a_plain_diff():
    change = pct_change(price_now_raw=110.0, last_seen_price_raw=100.0, cum_factor_now=1.0, cum_factor_then=1.0)
    assert change == 0.1
