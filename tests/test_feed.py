from app.feed import generate_normal, generate_split_day


def test_normal_scenario_is_deterministic():
    ticks_a, actions_a = generate_normal(1_700_000_000)
    ticks_b, actions_b = generate_normal(1_700_000_000)
    assert ticks_a == ticks_b
    assert actions_a == actions_b == []


def test_split_day_ticks_ordered_by_seq_per_instrument():
    ticks, actions = generate_split_day(1_700_000_000)
    assert len(actions) == 1 and actions[0].kind == "SPLIT"
    by_isin: dict[str, list[int]] = {}
    for t in ticks:
        by_isin.setdefault(t.isin, []).append(t.seq)
    for seqs in by_isin.values():
        assert seqs == sorted(seqs)


def test_split_day_price_drops_tenfold_at_split_seq():
    ticks, _ = generate_split_day(1_700_000_000)
    reliance = [t for t in ticks if t.isin == "INE002A01018"]
    before = next(t for t in reliance if t.seq == 5)
    after = next(t for t in reliance if t.seq == 6)
    assert after.price_raw < before.price_raw / 8  # roughly a 10x drop
