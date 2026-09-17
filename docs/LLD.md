# Low-level design

This is the detailed reference: exact fields, every distinct outcome each
subsystem can produce, what happens at the edges, and the test that pins
each claim down. For the shape of the system instead — module map, request
flow, why these architectural choices — see `docs/ARCHITECTURE.md`.

Scope is deliberately narrow: the three subsystems that answer the three
lies (`app/corpactions`, `app/session`, `app/digest`), plus the
unconfirmed-corporate-action check, which lives inside `app/corpactions`
rather than being a fourth subsystem. Nothing here is invented to make the
document feel more complete — every outcome below corresponds to a real
branch in the code as of this pass.

---

## `app/corpactions` — the baseline decays

### State and data structures

```python
@dataclass
class CorporateAction:
    isin: str
    kind: str            # SPLIT | BONUS | DIVIDEND | RIGHTS
    ex_seq: int           # the per-instrument seq at which it takes effect
    ratio_from: float
    ratio_to: float
    adjustment_factor: float   # ratio_from / ratio_to
```

`ex_seq`, not `ex_date`: this build's feed simulator numbers ticks by
per-instrument sequence, not wall-clock time, so a corporate action is
pinned to the exact tick it takes effect on, not a calendar date. A real
feed would use a date; the join key would change, the rest of the math
wouldn't.

`InstrumentState.cum_factor` (in `app/models.py`) is the only piece of
corporate-action state that outlives a single tick. It starts at `1.0` and
is multiplied, never replaced, by every confirmed action's
`adjustment_factor` as it's applied (`apply_corporate_action`). It is
**multiplicative and per-instrument** because a stock can go through more
than one corporate action over its lifetime (a split, then years later a
bonus issue), and each one compounds onto whatever adjustment already
applied — replacing it instead of multiplying would erase the earlier
event's correction.

### Every distinct outcome

| Outcome | When | Reason |
|---|---|---|
| `plain_diff` | No corporate action involved; `cum_factor_now == cum_factor_then`. `pct_change` reduces to an ordinary percentage difference. | The common case — most ticks aren't a corporate action. |
| `corporate_action_confirmed` | A `CorporateAction` record's `ex_seq` matches the tick being applied. `cum_factor` is multiplied by `adjustment_factor`. | The event is on record; the baseline self-corrects with zero backfill. |
| `clean_ratio_gap_detected` | `detect_clean_ratio_gap(price_before, price_after)` finds the ratio between two consecutive ticks within 3% of 2, 5, 10, 20, or one of their inverses. | The *shape* of a split/bonus/reverse-split, independent of whether it's confirmed. |
| `clean_ratio_gap_absent` | The ratio doesn't land near any clean value. | The overwhelming majority of real price moves — this is what keeps the detector low-noise. |
| `unverified_corporate_action` | `clean_ratio_gap_detected` **and** no `CorporateAction` record covers that tick (checked in `app.main._load_scenario`). | INVARIANT I9: fail toward silence, not toward a lie — flagged instead of scored. |

### Failure and edge behavior

- **A tick whose seq equals (not exceeds) the last one seen.** Not this
  module's concern directly — `app.ingest.apply_tick` drops it before
  `corpactions` ever sees it (`tick.seq <= state.last_seq` is rejected,
  same treatment as a genuinely stale tick). A retried, at-least-once
  redelivery of the same tick is a no-op, not a double-application.
- **A malformed tick** (negative, zero, or NaN `price_raw`) is not
  rejected by anything in this codebase — `apply_tick` only checks
  ordering. `adjusted_baseline`/`pct_change` guard division by zero
  (`cum_factor_then == 0` or `baseline == 0` short-circuit to a safe
  default), so this doesn't crash, but a negative price would silently
  produce a nonsensical percentage in a MOVE card. Named, not fixed — see
  `docs/BUGS.md` ("No tick sanity validation").
- **A corporate action whose ratio doesn't match any clean pattern**
  (a buyback, an odd-ratio rights issue, a data glitch that isn't a round
  number) — `detect_clean_ratio_gap` returns `None`, and the tick is
  treated as `plain_diff`: an ordinary move, scored normally. This is a
  known, named gap, not a silent one — see `docs/BUGS.md` ("Volume
  cross-check for the unconfirmed-CA detector") for the next layer that
  would catch more of these.
- **`cum_factor_then == 0`.** Can't happen in practice (`cum_factor`
  starts at `1.0` and is only ever multiplied by nonzero
  `adjustment_factor`s), but `adjusted_baseline` guards it anyway rather
  than trusting the invariant to hold forever.

### Tests

- `tests/test_corpactions.py::test_no_corporate_action_is_a_plain_diff` — `plain_diff`
- `tests/test_corpactions.py::test_split_leaves_pct_change_near_zero` — `corporate_action_confirmed`
- `tests/test_corpactions.py::test_detect_clean_ratio_gap_finds_a_ten_to_one_drop`, `test_detect_clean_ratio_gap_finds_a_reverse_split_shape` — `clean_ratio_gap_detected`
- `tests/test_corpactions.py::test_detect_clean_ratio_gap_ignores_an_ordinary_move` — `clean_ratio_gap_absent`
- `tests/test_corpactions.py::test_unconfirmed_clean_ratio_gap_is_never_reported_as_a_price_move` — `unverified_corporate_action`, exercised against `build_digest` directly
- `tests/test_main.py::test_load_scenario_detects_an_unconfirmed_clean_ratio_gap` — the same outcome, exercised through the actual `_load_scenario` detection loop, not a hand-built dict
- `tests/test_feed.py::test_normal_scenario_is_deterministic`,
  `test_split_day_ticks_ordered_by_seq_per_instrument`, and
  `test_split_day_price_drops_tenfold_at_split_seq` establish the scenario
  data `detect_clean_ratio_gap` runs against; a direct check in this pass
  (not itself a committed test) confirmed zero false positives across every
  tick in both `normal` and `split_day` before the detector was wired in

### Non-goals

- **Volume cross-checking.** A real split usually has a volume signature; this module doesn't look at volume at all. Owned by: `docs/BUGS.md`, "Volume cross-check for the unconfirmed-CA detector."
- **Non-clean-ratio anomalies** (buybacks, odd rights-issue ratios). Owned by: `docs/BUGS.md`, same entry, "Unconfirmed-corporate-action detection" closed-this-pass note.
- **Tick sanity validation.** Not this module's job even conceptually — it trusts `app.ingest` to hand it a tick worth reasoning about at all. Owned by: `docs/BUGS.md`, "No tick sanity validation."
- **Symbol-to-ISIN resolution / ticker rename history (I1).** This module and everything downstream of it already keys everything by ISIN, which is the invariant that matters, but there's no `symbol_aliases` table. Owned by: `docs/BUGS.md`, "A ticker-rename table doesn't exist."

---

## `app/session` — the baseline expires

### State and data structures

```python
class SessionState(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    LIVE = "LIVE"
    CLOSED = "CLOSED"
    HALTED = "HALTED"
    DEGRADED = "DEGRADED"
```

All five states can reach a user: `instrument_session_state` returns the
calendar state verbatim whenever the market isn't in continuous trading,
so `PRE_OPEN` surfaces in `GET /api/watchlist` and has its own badge in
the UI legend. It is the least interesting of the five — the calendar
window between midnight and market open, distinct from `CLOSED` (the rest
of the day and weekends) — because it behaves identically to `CLOSED` in
the way that matters: neither is ever reported as stale. It's kept separate in the
enum because "the market hasn't opened yet today" and "the market is shut"
are different facts, and the UI names both rather than collapsing them.

No state is stored — `instrument_session_state` is a pure function computed
fresh on every read from two inputs: a `datetime` (`docs/ARCHITECTURE.md`'s
`DEMO_NOW` in this build) and an `InstrumentState`. This is deliberate:
there is no `session_state` column to fall out of sync with reality.

`EXPECTED_INTERVAL_MS = {"liquid": 2_000, "illiquid": 240_000}` and
`DEGRADED_MULTIPLIER = 6` together define "how long is too long," per
liquidity tier — a liquid name silent for 12 seconds (2,000ms × 6) is
`DEGRADED`; an illiquid one gets 24 minutes (240,000ms × 6) before the same
judgment applies. One global number would either flag illiquid names
constantly or miss a real outage on a liquid one.

### Every distinct outcome

| Outcome | When | Reason |
|---|---|---|
| `HALTED` | `state.halted` is true. | Checked first, unconditionally — a real trading halt is not our fault and not a data problem, so nothing else matters once it's true. |
| `CLOSED` (weekend) | `now.weekday() >= 5`. | The exchange doesn't open on a weekend regardless of the time of day. |
| `CLOSED` (before pre-open) | Before 09:00 local. | Same treatment as any other closed period. |
| `PRE_OPEN` | 09:00–09:15 local, a weekday. | Market is open for the pre-open session, not for continuous trading yet. |
| `LIVE` | 09:15–15:30 local, a weekday, **and** the instrument's last tick is within `EXPECTED_INTERVAL_MS × DEGRADED_MULTIPLIER` of now. | The common case during trading hours. |
| `CLOSED` (after close) | After 15:30 local. | Same treatment as before pre-open. |
| `DEGRADED` | Market session is `LIVE` by the calendar, but this specific instrument's last tick is older than its own tier's threshold. | INVARIANT I5/I6 — the only state that's this system's own fault, judged per instrument, never by a global timeout. |

### Failure and edge behavior

- **`state.halted` and a stale feed at the same time.** `HALTED` wins —
  checked before the liveness math even runs, so a halted, silent
  instrument still reports `HALTED`, not `DEGRADED`. A halt is a market
  fact; a degraded feed is an infrastructure fact, and conflating them
  would blame the feed for something the exchange did.
  `tests/test_session.py::test_halted_beats_everything` pins this exact
  ordering — the test constructs a state that's both halted and long
  silent and asserts `HALTED`.
- **Exactly at a boundary** (`sec == MARKET_OPEN_SEC`, `sec ==
  MARKET_CLOSE_SEC`). The comparisons are `<` / `<=` chosen so 09:15:00
  sharp is already `LIVE` and 15:30:00 sharp is still `LIVE` — the close
  boundary is inclusive, the open boundary makes `PRE_OPEN` exclusive of
  the instant trading starts. Not separately pinned by a test in this
  pass; a boundary-second test is a reasonable next addition, named here
  rather than silently assumed correct.
- **An instrument with no liquidity tier match.** `EXPECTED_INTERVAL_MS.get(tier,
  EXPECTED_INTERVAL_MS["liquid"])` falls back to the liquid threshold — the
  *stricter* default — rather than silently treating an unrecognized tier
  as infinitely patient.

### Tests

- `tests/test_session.py::test_weekend_is_closed_not_stale` — `CLOSED` (weekend), proving age is irrelevant
- `tests/test_session.py::test_halted_beats_everything` — `HALTED` precedence
- `tests/test_session.py::test_illiquid_sparse_ticks_are_not_degraded` — `LIVE` for an illiquid name within its own threshold
- `tests/test_session.py::test_liquid_instrument_silent_10s_is_degraded` — `DEGRADED` for a liquid name past its threshold
- `tests/test_main.py::test_feed_death_scenario_flags_the_dead_instrument_degraded_not_move` — `DEGRADED` end-to-end through the API, alongside seven `LIVE` instruments in the same response

### Non-goals

- **Partial or flapping degradation.** A feed that's late but not fully
  stopped, or one that degrades and recovers within a session, isn't
  modeled by any current scenario. Owned by: `docs/BUGS.md`,
  `feed_death`'s closed-this-pass note, "what this doesn't cover."
- **A real market calendar** (holidays, special sessions). `calendar_state`
  only knows weekday-vs-weekend and a fixed daily window — a market
  holiday on a Tuesday would be reported `LIVE`. Not currently disclosed
  elsewhere; naming it here.

---

## `app/digest` — the baseline is not personal

### State and data structures

```python
@dataclass
class Watermark:
    user_id: str
    isin: str
    last_seen_seq: int
    last_seen_price_raw: float
    last_seen_cum_factor: float
```

`last_seen_cum_factor` travels with the watermark, not just
`last_seen_price_raw`, because a price alone can't be compared honestly
across a corporate action — INVARIANT I3 needs both numbers to compute
`last_seen_price_raw × (cum_factor_now / cum_factor_then)` as the real
baseline. Storing only the raw price would make the watermark exactly the
kind of naive baseline this whole project exists to not be.

```python
class WatermarkStore:
    def ack(self, user_id, isin, seq, price_raw, cum_factor) -> Watermark:
        current = self._store.get((user_id, isin))
        if current is not None and seq <= current.last_seen_seq:
            return current            # rejected, not merged
        ...
```

The comparison is `seq <= current.last_seen_seq`, strictly `<=` and not
`<`: a re-`ack()` at the *same* seq the watermark is already at is treated
identically to a stale one — rejected, returning the existing watermark
unchanged. This matters because `ack()` gets called unconditionally on
every scenario load's first tick and on every `POST
/api/watermark/ack` — if equal-seq acks silently "succeeded" by
overwriting with identical values, that would be harmless today, but the
strict `<=` is what makes the *general* rule ("a watermark only ever moves
forward") hold for a client that legitimately double-submits the exact
same acknowledgment, not just for a client that's behind.

### Every distinct outcome

| Outcome | When | Reason |
|---|---|---|
| `watermark_seeded_first_view` | `watermarks.get(user_id, isin)` returns `None`. | No prior baseline exists — there's nothing to diff against, so the current state *becomes* the baseline and no card is produced. |
| `degraded_feed` | The instrument is in the caller-supplied `degraded` mapping (from `app.session`, via `app.main`). Checked **before** `unverified` and before scoring a move. | INVARIANT I5/I6: the last known price can't be trusted while the feed is dark — never scored as a move, whether it looks like one or not. |
| `unverified_corporate_action` | The instrument is in the caller-supplied `unverified` mapping. Checked after `degraded_feed`, before scoring a move. | INVARIANT I9: a clean-ratio gap with no confirmed record is flagged, never scored. |
| `move_below_threshold` | `abs(pct_change) < MOVE_THRESHOLD` (2%). | INVARIANT I10 — silence is the default; most ticks don't produce a card. |
| `move_card` | `abs(pct_change) >= MOVE_THRESHOLD`. | The adjusted move is large enough to be worth telling the user about. |
| `budget_truncated` | More than `DEFAULT_BUDGET` (5) cards survive all of the above across the whole watchlist. | INVARIANT I10 — the digest has a hard cap regardless of how many instruments genuinely moved. |

The ordering of the first three checks (`first_view` → `degraded_feed` →
`unverified_corporate_action` → scored move) is itself a real design
decision, not an accident of code layout: an instrument can't be more than
one of these at once for a given digest read, and "can we trust this price
at all" (degraded) is resolved before "is this price shape suspicious"
(unverified), which is resolved before "is this a normal move."

### Failure and edge behavior

- **`build_digest` is a pure function — no lock inside it.** It only ever
  reads the `states`/`watermarks`/`unverified`/`degraded` it's handed as
  arguments; it never touches any of `app.main`'s module-level globals
  directly. That purity is exactly why the lock in `app.main` (guarding
  the *caller's* read of those globals) is sufficient on its own — a
  digest read that lands mid-scenario-reload is `app.main`'s concern, not
  this module's, and is covered by
  `tests/test_main.py::test_concurrent_scenario_reload_and_reads_stay_internally_consistent`.
- **An instrument present in both `unverified` and `degraded` at once**
  (a feed that goes dark right as an unconfirmed gap appears). `degraded`
  is checked first, so `DEGRADED_FEED` wins and the unverified-ratio flag
  is silently dropped for that read. Not exercised by any current
  scenario (the two conditions don't currently co-occur in
  `normal`/`split_day`/`feed_death`) — named here as the deliberate
  precedence rule, not verified by a test.
- **`cards.sort(key=lambda c: c["score"], reverse=True)` with a tie.**
  Python's sort is stable, so tied scores keep their original
  watchlist-iteration order (`app.feed.INSTRUMENTS`'s order) — not
  separately guaranteed by this module, just an accepted property of
  `list.sort`.

### Tests

- `tests/test_digest.py::test_small_move_is_silent` — `move_below_threshold`
- `tests/test_digest.py::test_digest_is_capped_at_budget` — `move_card` and `budget_truncated` together
- `tests/test_digest.py::test_watermark_never_rewinds` — the `WatermarkStore.ack` monotonic guard directly
- `tests/test_corpactions.py::test_unconfirmed_clean_ratio_gap_is_never_reported_as_a_price_move` — `unverified_corporate_action`
- `tests/test_main.py::test_feed_death_scenario_flags_the_dead_instrument_degraded_not_move` — `degraded_feed`
- `watermark_seeded_first_view` is exercised implicitly by every scenario load (every instrument's first tick goes through this path in `app.main._load_scenario`) but has no isolated unit test naming it directly — worth adding, not currently there.

### Non-goals

- **Cooldown / hysteresis (I10).** The budget cap is enforced; nothing
  stops the same instrument re-triggering `move_card` on the very next
  poll once it crosses `MOVE_THRESHOLD`, so a price oscillating near the
  boundary can flicker in and out of the digest. Owned by: `docs/BUGS.md`,
  "I10 — cooldown / hysteresis on repeated signals."
- **VOLUME, LEVEL, EVENT signal kinds.** Only `MOVE`,
  `UNVERIFIED_CORPORATE_ACTION`, and `DEGRADED_FEED` exist. Owned by:
  `docs/BUGS.md`, "VOLUME, LEVEL, EVENT scorers."
- **Multi-user fan-out.** `build_digest` takes one `user_id` per call;
  `app.main` only ever calls it with the single hardcoded `"demo"` user.
  The function shape supports a real per-user loop without changing the
  pricing math it calls (see `docs/ARCHITECTURE.md`'s fan-out section),
  but no such loop exists yet. Owned by: `docs/BUGS.md`, "One hardcoded
  user."
