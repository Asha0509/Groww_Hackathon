# Known limitations & open issues

Honest list of what this build does not do, and why. For what *is* built,
in detail — exact outcomes, edge behavior, the test pinning each one — see
`LLD.md`; several entries below point back to a specific non-goal named
there.

## Real, current limitations

| What | Detail |
|---|---|
| Live mode has no automated test against the real network | `app.feed.fetch_live_quote` is mocked in `tests/test_feed.py` so the suite stays deterministic and doesn't depend on Yahoo being reachable to run — it's been run and read by hand instead. See `docs/ARCHITECTURE.md`'s "The live feed" for what running it for real actually caught (an IST-timezone bug). |
| The concurrency lock is in-process only | `app/main.py`'s single `threading.Lock` guards every read (`watchlist`, `digest`, `ack`) and the state-swap at the end of `_load_scenario`. `tests/test_main.py::test_concurrent_scenario_reload_and_reads_stay_internally_consistent` stress-tests it (2 writer threads × 25 reloads against 4 reader threads × 25 reads, zero torn reads observed). **Honest caveat on the test itself**: the identical stress test with the lock replaced by a no-op also produced zero torn reads, across 3×60 reloads and 6×60 reads — the race is real (a five-name tuple-unpack across globals is not a language-guaranteed atomic operation across threads) but the window is small enough that black-box stress testing doesn't reliably reproduce it either way. **What this doesn't cover**: it's an in-process lock — a second uvicorn worker sharing state would need the database's own write-serialization instead. |
| Persistence covers watermarks, instrument state, and a user's own watchlist choices, not the full schema `docs/CLAUDE.md` originally describes | `app/db.py` round-trips `InstrumentState`, `Watermark`, and the `custom_instruments`/`excluded_instruments` tables (see `docs/ARCHITECTURE.md`'s Data model). A `POST /api/scenario/{name}` from the running UI never consults the database — only the one-time process startup load does, so a scenario replay stays deterministic regardless of what a previous run left on disk. The `Watermark` dataclass has no `last_seen_at` timestamp, so persistence doesn't track one either. It's still a single SQLite file — no migration tooling, no multi-process write coordination beyond what WAL mode gives for free. |
| Unconfirmed-corporate-action detection only catches clean-ratio shapes | `app.corpactions.detect_clean_ratio_gap` flags a tick-to-tick ratio within 3% of 1:2, 1:5, 1:10, 1:20 or their inverses with no confirmed `CorporateAction` covering it. A buyback, an odd-ratio rights issue, or a data glitch that doesn't land near a clean ratio passes through untouched. It also doesn't cross-check trading volume — a real split usually has one; see the "Volume cross-check" row below. |
| Only one scenario exercises `DEGRADED` | `feed_death` ticks every instrument normally except HDFCBANK, whose feed goes dark 60 seconds before the others do — the contrast is one silent feed next to seven live ones. A partial degradation (ticks arriving late but not stopped entirely, a flapping feed) isn't modeled. |
| The market calendar is weekday-vs-weekend only | `app.session.calendar_state` knows a fixed daily window (09:00 pre-open, 09:15–15:30 continuous) and that Saturday and Sunday are shut. It has no holiday list and no notion of a special session, so a market holiday falling on a Tuesday is reported `LIVE`, and every instrument on it goes `DEGRADED` as its last tick ages out. Fixing it properly needs a real exchange calendar as a data source, which this build doesn't have; a hardcoded list of dates would be wrong the moment it aged. |

## Deliberately out of scope for this build

| What | Not built or by design? | Why |
|---|---|---|
| Volume cross-check for the unconfirmed-CA detector | Not built | A real corporate action usually has a volume signature a data glitch doesn't. The ratio-shape check above doesn't look at volume at all, so a coincidental clean-ratio price glitch (rare, but possible with synthetic or noisy data) would still be flagged the same as a real unconfirmed split. Next layer, not built. |
| I10 — cooldown / hysteresis on repeated signals | Not built | The budget cap (≤5 cards) is built; the part that stops the same instrument re-triggering every poll once it crosses the threshold is not. Without it, a card can flicker in and out of the digest as a price oscillates around `MOVE_THRESHOLD`. |
| VOLUME, LEVEL, EVENT scorers | Not built | Only three card kinds exist in `digest.build_digest`: `MOVE`, `UNVERIFIED_CORPORATE_ACTION`, and `DEGRADED_FEED`. A confirmed corporate action suppresses the *price* lie correctly but doesn't emit a dedicated EVENT card ("1:10 split, share count now 10x") — the watchlist row annotation is UI-layer sugar reading the `_ca_notes` cache, not a signal in the digest's own scoring path. |
| WebSocket streaming | By design | The deterministic scenarios still replay a full tick history to a fixed point in time (`DEMO_NOW`) synchronously on `POST /api/scenario/{name}` — a reproducibility requirement the app's core walkthrough still leans on. Live mode (`POST /api/live/refresh`) does update prices over time now, but by client-driven polling every 10s, not a server push — no WebSocket, no persistent connection. A poll that fails is just a poll that fails; a desynced WebSocket would be a harder failure to recover from than a failed poll. Real streaming is a natural next step once the ingest path needs to push rather than be asked. |
| Retry/backoff and caching for the live feed | Not built | `app.feed.fetch_live_quote` makes one attempt per symbol per poll with a 5s timeout and gives up — no retry, no exponential backoff, no short-lived cache to avoid re-hitting Yahoo for a value that hasn't changed. A transient failure on one poll is handled gracefully (that instrument just doesn't advance this round), but there's no attempt to recover faster than "wait for the next scheduled poll." |
| `illiquid` as its own demo scenario | Not built | `feed.INSTRUMENTS` already includes illiquid names and `session/` already has the per-tier liveness math (`EXPECTED_INTERVAL_MS`) that would exercise it, but there's no scenario that isolates an illiquid instrument's sparse-but-healthy ticking as its own demo moment. It's implicitly exercised (illiquid names sit in `normal`/`split_day` too, correctly reported `LIVE`), just not called out as a dedicated scenario. |
| `late_ca` as a dedicated demo scenario | By design | The unconfirmed-corporate-action *detection* is built and tested (see `docs/LLD.md`'s corpactions section) — what's deliberately not built is a `/api/scenario/late_ca` endpoint to demo it live, to keep the demo surface deliberately small. The behavior is verified by `tests/test_corpactions.py`, not by a clickable demo button. |

## Architectural shortcuts specific to this build

- **One hardcoded user.** `USER = "demo"` throughout — there's no multi-user
  watchlist, so the O(instruments) vs. O(users × instruments) claim in
  `ARCHITECTURE.md` is a shape the code supports, not a behavior the demo
  exercises.
- **A ticker-rename table doesn't exist.** The curated eight are keyed by
  ISIN everywhere in the code, which is the invariant that matters, but
  there's no `valid_from`/`valid_to` table backing a symbol rename — the
  design this build follows (`docs/CLAUDE.md §4`, invariant I1) describes
  one, but this build doesn't actually implement or test it.
- **A watchlist instrument a user adds is keyed by its ticker symbol, not a
  real ISIN.** `POST /api/watchlist/add` takes a symbol, and there's no ISIN
  lookup available for an arbitrary user-typed ticker, so the symbol itself
  becomes the key in `custom_instruments` and in `_states`. The consequences
  are real and worth naming: a custom addition would not survive the very
  ticker rename that invariant I1 exists to handle, and if a user adds a
  symbol that is *already* one of the curated eight, they get a second row
  keyed by symbol rather than a match against the existing ISIN. The curated
  eight themselves are unaffected. This is a disclosed simplification, noted
  in the `app/db.py` schema comment and in `README.md`, not an oversight —
  fixing it properly needs a symbol→ISIN reference source this build doesn't
  have.
- **`DEMO_NOW` is a fixed timestamp**, not a live clock — but only for the
  four deterministic scenarios (`normal`, `split_day`, `feed_death`,
  `big_move`).
  Session state and tick age ("As of" column) for those are computed
  against `2026-09-07 11:00:00` regardless of when the server actually
  started, correct for a repeatable demo. Live mode is the deliberate
  exception: `main._now()` switches to a real `zoneinfo`-aware IST clock
  whenever `_scenario == "live"`, exactly because a live tick's timestamp
  is a real current moment and comparing it against a fixed 2026 date
  would be meaningless (see `docs/ARCHITECTURE.md`'s "The live feed").
