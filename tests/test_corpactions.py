from app.corpactions import CorporateAction, apply_corporate_action, detect_clean_ratio_gap, pct_change
from app.digest import WatermarkStore, build_digest
from app.models import Instrument, InstrumentState


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


def test_detect_clean_ratio_gap_finds_a_ten_to_one_drop():
    assert detect_clean_ratio_gap(2450.0, 245.0) == 10.0


def test_detect_clean_ratio_gap_finds_a_reverse_split_shape():
    assert detect_clean_ratio_gap(50.0, 500.0) == 0.1


def test_detect_clean_ratio_gap_ignores_an_ordinary_move():
    # a real 3% intraday move must never be mistaken for a corporate action
    assert detect_clean_ratio_gap(100.0, 97.0) is None


def test_unconfirmed_clean_ratio_gap_is_never_reported_as_a_price_move():
    """INVARIANT I9: a clean 1:10 gap with no confirmed CorporateAction record
    must never surface as a raw -90% MOVE card — the exact lie this exists to
    prevent.
    """
    inst = Instrument("X", "RELIANCE", "Reliance Industries")
    watermarks = WatermarkStore()
    watermarks.ack("u1", "X", seq=1, price_raw=2450.0, cum_factor=1.0)

    # no confirmed CorporateAction anywhere — cum_factor never moved off 1.0
    state = InstrumentState(isin="X", last_seq=2, ltp_raw=245.0, cum_factor=1.0)

    unverified = {"X": "unconfirmed — price shape looks like a 1:10 corporate action"}
    cards = build_digest("u1", [inst], {"X": state}, watermarks, unverified=unverified)

    assert len(cards) == 1
    assert cards[0]["kind"] == "UNVERIFIED_CORPORATE_ACTION"
    assert "pct_change" not in cards[0]  # never scored as a move, per I9
