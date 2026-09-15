# Known limitations & open issues

Honest list. Nothing here is hidden from a live Q&A — if asked "what's not
built," the answer is this file.

## Closed this pass

| What | How it was closed |
|---|---|
| No lock around shared mutable state | `app/main.py` now guards every read (`watchlist`, `digest`, `ack`) and the state-swap at the end of `_load_scenario` with a single `threading.Lock`. The critical section is kept short on purpose: the expensive tick-replay loop in `_load_scenario` builds entirely fresh local objects untouched by any other request, and only the final five-name reassignment happens under the lock — so this doesn't serialize `/` or `/healthz`, and barely serializes anything else in practice. `tests/test_main.py::test_concurrent_scenario_reload_and_reads_stay_internally_consistent` hammers a scenario reload against concurrent reads (2 writer threads × 25 reloads, 4 reader threads × 25 reads) and asserts every observed response is self-consistent. **Honest caveat on the test itself**: I also ran the identical stress test with the lock replaced by a no-op, to confirm the test would actually catch the bug it's meant to catch — it didn't reproduce a single torn read even unlocked, across 3×60 reloads and 6×60 reads. The race is real (a Python tuple-unpack across five global names is not a language-guaranteed atomic operation across threads — nothing stops a thread switch between two of the five `STORE_GLOBAL`s), but the window is small enough relative to HTTP request overhead that black-box stress testing didn't reproduce it either way. The lock is correct and cheap regardless of whether this particular test can prove the bug existed; correctness shouldn't depend on a race being lucky. This is a stress test that the *fixed* code holds up, not a proof the *unfixed* code was observably broken. **What this doesn't cover**: it's an in-process lock — it says nothing about two separate processes (e.g. a second uvicorn worker) sharing state, which real persistence (see below) would handle via the database's own write-serialization instead. |
| `feed_death` demo scenario, and the digest's degraded-feed behavior | `app/feed.generate_feed_death` ticks every instrument normally except HDFCBANK, whose feed is shifted 60 seconds into the past and never resumes — every other instrument keeps ticking right up to the scenario's "now", so the contrast is one silent feed next to seven live ones, not a market-wide outage. `session.instrument_session_state` already correctly classifies this as `DEGRADED` (not `CLOSED` — the market's open, only this feed is dark). New this pass: `app/digest.build_digest` takes a `degraded` mapping and, for any instrument in it, emits an explicit `DEGRADED_FEED` card instead of a `MOVE` card — the last known price is never silently scored as if it were current. Wired end-to-end via a third scenario-toggle button in the UI. Verified by `tests/test_feed.py::test_feed_death_only_silences_one_instrument` (the scenario data itself) and `tests/test_main.py::test_feed_death_scenario_flags_the_dead_instrument_degraded_not_move` (the full API response, including the digest card). **What this doesn't cover**: only one demo scenario exercises `DEGRADED`; a partial degradation (some ticks arriving late but not stopped entirely, a flapping feed) isn't modeled. |
| Unconfirmed-corporate-action detection | `app/corpactions.detect_clean_ratio_gap` compares each tick's raw price to the previous tick for that instrument; if the ratio lands within 3% of a known clean split/bonus/reverse-split shape (1:2, 1:5, 1:10, 1:20, or their inverses) **and** no confirmed `CorporateAction` covers that tick, the instrument is flagged `UNVERIFIED_CORPORATE_ACTION` in `app/main._load_scenario` and `app/digest.build_digest` reports it as an explicit, labeled card instead of a raw price move — the digest never silently scores an unconfirmed gap as if it were a real change. See `docs/LLD.md` for the exact outcomes and `tests/test_corpactions.py::test_unconfirmed_clean_ratio_gap_is_never_reported_as_a_price_move` for the pinned behavior. **What this does not close:** it only catches gaps that happen to land near a clean ratio. A buyback, an odd-ratio rights issue, or a data glitch that doesn't produce a round number passes through untouched — this is a narrow, low-noise shape-matcher, not a general anomaly detector. It also does not cross-check against trading volume (a real split usually has a volume signature; this build doesn't verify that) — a stated, deliberate limitation, not an oversight; see the entry below. |

## Deliberately out of scope for this build

| What | Time cut or design decision? | Why |
|---|---|---|
| Volume cross-check for the unconfirmed-CA detector | Time cut | A real corporate action usually has a volume signature a data glitch doesn't. The ratio-shape check above doesn't look at volume at all, so a coincidental clean-ratio price glitch (rare, but possible with synthetic or noisy data) would still be flagged the same as a real unconfirmed split. Next layer, not built. |
| I10 — cooldown / hysteresis on repeated signals | Time cut | The budget cap (≤5 cards) is built; the part that stops the same instrument re-triggering every poll once it crosses the threshold is not. Without it, a card can flicker in and out of the digest as a price oscillates around `MOVE_THRESHOLD`. |
| VOLUME, LEVEL, EVENT scorers | Time cut | Only MOVE and the new UNVERIFIED_CORPORATE_ACTION card are implemented. A confirmed corporate action suppresses the *price* lie correctly but doesn't emit a dedicated EVENT card ("1:10 split, share count now 10x") — the watchlist row annotation is UI-layer sugar reading the `_ca_notes` cache, not a signal in the digest's own scoring path. |
| WebSocket streaming | Design decision | The demo replays a scenario's full tick history to a fixed point in time (`DEMO_NOW`) synchronously on `POST /api/scenario/{name}`; there's no live push. Given a reproducibility requirement — the demo has to be repeatable under pressure — a deterministic batch replay is easier to defend live than a WebSocket that could desync mid-Q&A. Real streaming is a natural next step once the ingest path needs to run continuously rather than on-demand. |
| `illiquid` as its own demo scenario | Time cut | `feed.INSTRUMENTS` already includes illiquid names and `session/` already has the per-tier liveness math (`EXPECTED_INTERVAL_MS`) that would exercise it, but there's no scenario that isolates an illiquid instrument's sparse-but-healthy ticking as its own demo moment. It's implicitly exercised (illiquid names sit in `normal`/`split_day` too, correctly reported `LIVE`), just not called out as a dedicated scenario. |
| `late_ca` as a dedicated demo scenario | Design decision | The unconfirmed-corporate-action *detection* is built and tested (see "Closed this pass" above) — what's deliberately not built is a `/api/scenario/late_ca` endpoint to demo it live, to keep this pass's demo surface deliberately small. The behavior is verified by `tests/test_corpactions.py`, not by a clickable demo button. |

## Architectural shortcuts specific to this build

- **No persistence.** `app/main.py` holds `_states`, `_watermarks`, `_ca_notes`
  as module-level globals, rebuilt from scratch on every `POST
  /api/scenario/{name}` call and lost on process restart. `CLAUDE.md`'s
  SQLite schema (`ARCHITECTURE.md`) was never wired up. Fine for a stateless
  demo; wrong for anything a second process or a restart needs to see.
- **One hardcoded user.** `USER = "demo"` throughout — there's no multi-user
  watchlist, so the O(instruments) vs. O(users × instruments) claim in
  `ARCHITECTURE.md` is a shape the code supports, not a behavior the demo
  exercises.
- **A ticker-rename table doesn't exist.** Instruments are keyed by ISIN
  everywhere in the code, which is the invariant that matters, but there's no
  `valid_from`/`valid_to` table backing a symbol rename — the design this
  build follows (`docs/CLAUDE.md §4`, invariant I1) describes one, but this
  build doesn't actually implement or test it.
- **`DEMO_NOW` is a fixed timestamp**, not a live clock. Session state and
  tick age ("As of" column) are computed against `2026-09-07 11:00:00`
  regardless of when the server actually started. Correct for a repeatable
  demo, wrong for anything meant to run past that one scripted moment.
- **`setup.sh` still describes a Vite/React frontend** (`npm install`,
  scaffolding a `web/` directory) that this build replaced with a single
  static HTML page served at `/`. It's stale from the original scaffold and
  wasn't touched, per the instruction to leave everything not on the
  priority list alone — running `setup.sh` as written will try to scaffold a
  `web/` app that nothing here uses. (There is no `Makefile` or `Dockerfile`
  committed to this repo — `setup.sh` generates them itself on a first run
  it was never actually run to completion here, so neither exists yet.)
