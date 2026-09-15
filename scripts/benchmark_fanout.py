#!/usr/bin/env python3
"""Measures the O(instruments) vs O(users x instruments) fan-out claim in
docs/ARCHITECTURE.md with real numbers, not an argued-only claim.

Loads K synthetic instruments and applies one batch of ticks to all of them
-- the expensive, per-instrument work (ingest + adjustment), paid once no
matter how many users are watching. Then times digest computation for 1
user vs N users reading that same shared state -- the cheap, per-user work
(a watermark lookup and a subtraction per instrument), paid once per user.

Prints exactly what it measures. No numbers here are rounded to look tidy.
"""
import random
import sys
import time

sys.path.insert(0, ".")

from app.digest import WatermarkStore, build_digest  # noqa: E402
from app.ingest import apply_tick  # noqa: E402
from app.models import Instrument, InstrumentState, Tick  # noqa: E402

K_INSTRUMENTS = 3000
N_USERS = 1000
SEED = 20260907


def build_instruments(k: int) -> list[Instrument]:
    return [
        Instrument(f"ISIN{i:06d}", f"SYM{i}", f"Company {i}", "liquid" if i % 5 else "illiquid")
        for i in range(k)
    ]


def ingest_one_batch(instruments: list[Instrument]) -> tuple[dict[str, InstrumentState], float]:
    """The O(instruments) work: two ticks (a baseline, then a real move)
    applied to every instrument. This is timed once, independent of N_USERS.
    """
    rng = random.Random(SEED)
    states = {inst.isin: InstrumentState(isin=inst.isin) for inst in instruments}
    t0 = time.perf_counter()
    for seq in (1, 2):
        for inst in instruments:
            price = 100.0 if seq == 1 else round(100.0 * (1 + rng.uniform(-0.05, 0.05)), 2)
            apply_tick(states[inst.isin], Tick(inst.isin, seq, float(seq), price, rng.randint(100, 5000)))
    return states, time.perf_counter() - t0


def seed_watermarks_for_user(user_id: str, instruments: list[Instrument], watermarks: WatermarkStore) -> None:
    for inst in instruments:
        watermarks.ack(user_id, inst.isin, seq=1, price_raw=100.0, cum_factor=1.0)


def main() -> None:
    instruments = build_instruments(K_INSTRUMENTS)
    states, ingest_elapsed = ingest_one_batch(instruments)

    # -- 1 user --
    wm_one = WatermarkStore()
    seed_watermarks_for_user("user_0", instruments, wm_one)
    t0 = time.perf_counter()
    build_digest("user_0", instruments, states, wm_one, budget=5)
    one_user_elapsed = time.perf_counter() - t0

    # -- N users, all reading the SAME already-ingested instrument state --
    wm_many = WatermarkStore()
    for u in range(N_USERS):
        seed_watermarks_for_user(f"user_{u}", instruments, wm_many)

    t0 = time.perf_counter()
    for u in range(N_USERS):
        build_digest(f"user_{u}", instruments, states, wm_many, budget=5)
    n_users_elapsed = time.perf_counter() - t0

    per_user_at_n = n_users_elapsed / N_USERS
    ratio = per_user_at_n / one_user_elapsed if one_user_elapsed > 0 else float("nan")
    replay_cost_if_not_shared = ingest_elapsed * N_USERS

    print(f"instruments (K)                     : {K_INSTRUMENTS}")
    print(f"users (N)                            : {N_USERS}")
    print(f"ingest, one batch, all K instruments : {ingest_elapsed * 1000:.2f} ms   (paid once)")
    print(f"digest for 1 user                    : {one_user_elapsed * 1000:.3f} ms")
    print(f"digest for {N_USERS} users (total)         : {n_users_elapsed * 1000:.2f} ms")
    print(f"digest for {N_USERS} users (avg/user)      : {per_user_at_n * 1000:.4f} ms")
    print(f"per-user cost ratio (N-user avg / 1-user): {ratio:.2f}x")
    print()
    print("If the adjusted-price computation were redone per user instead of")
    print("shared, serving these N users would cost N x the ingest pass:")
    print(f"  {N_USERS} x {ingest_elapsed * 1000:.2f} ms = {replay_cost_if_not_shared * 1000:.0f} ms,")
    print(f"  vs. the {ingest_elapsed * 1000:.2f} ms actually paid, once, above.")


if __name__ == "__main__":
    main()
