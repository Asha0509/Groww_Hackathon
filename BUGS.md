# Known limitations & open issues

Honest list. Nothing here is hidden from a live Q&A — if asked "what's not
built," the answer is this file.

## Deliberately out of scope for this build

| What | Time cut or design decision? | Why |
|---|---|---|
| I9 — `UNVERIFIED_CORPORATE_ACTION` detection | Time cut | Needs a clean-ratio-gap detector (1:2/1:5/1:10/1:20 heuristics) cross-checked against volume — real logic, not plumbing, and it wasn't in the priority list for this pass. `corpactions/` only applies *confirmed* actions; an unconfirmed gap today would currently be scored as a real move, which is exactly the lie I9 exists to prevent. This is the single most important gap to close next. |
| I10 — cooldown / hysteresis on repeated signals | Time cut | The budget cap (≤5 cards) is built; the part that stops the same instrument re-triggering every poll once it crosses the threshold is not. Without it, a card can flicker in and out of the digest as a price oscillates around `MOVE_THRESHOLD`. |
| VOLUME, LEVEL, EVENT scorers | Time cut | Only MOVE is implemented. A corporate action currently suppresses the *price* lie correctly but doesn't emit the EVENT card CLAUDE.md describes ("1:10 split, share count now 10x") — the watchlist row annotation added on request is UI-layer sugar reading the same `_ca_notes` cache, not a signal. |
| WebSocket streaming | Design decision | The demo replays a scenario's full tick history to a fixed point in time (`DEMO_NOW`) synchronously on `POST /api/scenario/{name}`; there's no live push. Given a single demo user and a reproducibility requirement ("the demo has to be repeatable under pressure" — CLAUDE.md §9), a deterministic batch replay is easier to defend live than a WebSocket that could desync mid-Q&A. Real streaming is a natural next step once the ingest path needs to run continuously rather than on-demand. |
| `feed_death`, `illiquid`, `late_ca` demo scenarios | Time cut | Only `normal` and `split_day` were requested and built. `session/` already has the per-instrument liveness math these scenarios would exercise (`DEGRADED_MULTIPLIER`, per-tier `EXPECTED_INTERVAL_MS`), and `feed.INSTRUMENTS` already includes illiquid names — but no scenario currently drives an instrument into `DEGRADED`, and there's no late/missing corporate-action feed to trigger I9. `weekend` (CLOSED-not-stale) is exercised only by `scripts/compare_naive.py`, not by a `/api/scenario/weekend` endpoint. |

## Architectural shortcuts specific to this build

- **No persistence.** `app/main.py` holds `_states`, `_watermarks`, `_ca_notes`
  as module-level globals, rebuilt from scratch on every `POST
  /api/scenario/{name}` call and lost on process restart. `CLAUDE.md`'s
  SQLite schema (`ARCHITECTURE.md`) was never wired up. Fine for a stateless
  demo; wrong for anything a second process or a restart needs to see.
- **Not concurrency-safe.** Those same globals are mutated with no lock. Two
  simultaneous `POST /api/scenario/...` calls, or a scenario reload racing a
  `GET /api/digest`, can interleave. SQLite in WAL mode (the intended design)
  would serialize writes for free; the in-memory stand-in does not.
- **One hardcoded user.** `USER = "demo"` throughout — there's no multi-user
  watchlist, so the O(instruments) vs. O(users × instruments) claim in
  `ARCHITECTURE.md` is a shape the code supports, not a behavior the demo
  exercises.
- **`symbol_aliases` (I1) doesn't exist.** Instruments are keyed by ISIN
  everywhere in the code, which is the invariant that matters, but there's no
  `valid_from`/`valid_to` table backing a ticker rename — a claim CLAUDE.md
  makes (§4, I1) that this build doesn't actually test.
- **`DEMO_NOW` is a fixed timestamp**, not a live clock. Session state and
  tick age ("As of" column) are computed against `2026-09-07 11:00:00`
  regardless of when the server actually started. Correct for a repeatable
  demo, wrong for anything meant to run past that one scripted moment.
- **No `test_main.py`.** Every invariant-bearing module (`corpactions`,
  `digest`, `session`, `feed`, `naive`) has unit tests; the FastAPI wiring in
  `main.py` (scenario load ordering, the CA-note snapshot timing) is only
  checked by manual `curl` during development, not by an automated test.
- **`setup.sh` and `Makefile` still describe a Vite/React frontend** (`make
  dev`, `npm install`) that this build replaced with a single static HTML
  page served at `/`. They're stale from the original 9-hour scaffold and
  weren't touched, per the instruction to leave everything not on the
  priority list alone — running `setup.sh` as written will try to scaffold a
  `web/` app that nothing here uses.
- **Dockerfile is untested.** It was scaffolded before this build and expects
  a `web/dist` build stage that no longer applies.
