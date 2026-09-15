# Architecture

This describes the system as it actually runs today. It's a single-process
FastAPI application: the same module boundaries below hold the full set of
invariants, backed by real SQLite persistence for the two tables that
actually need to survive a restart. See `BUGS.md` for exactly what's still
in-memory-only and why. For the exact fields, every distinct outcome, and
the test that pins each one down, see `LLD.md` — this document is the
shape of the system; that one is the detail.

## Module map

```
app/
├─ models.py       Instrument, Tick, InstrumentState — shared plain dataclasses
├─ feed/           seeded deterministic tick simulator (normal, split_day, feed_death)
├─ ingest/         ordering guard — applies a tick only if seq > last_seq (I4)
├─ corpactions/    cumulative adjustment factor, ISIN-keyed baseline math (I3);
│                  clean-ratio-gap detection for unconfirmed corporate actions (I9)
├─ session/        four-state session model + per-instrument liveness (I5, I6)
├─ digest/         watermark store + diff + budget cap (I2, I10)
├─ db.py           SQLite (WAL) persistence for instrument_state and watermarks
├─ naive/          deliberately naive baseline, kept wrong on purpose
├─ static/         single HTML page, plain CSS, no build step
└─ main.py         FastAPI app: wires the above into scenario/watchlist/digest endpoints
```

Nothing here imports from `main.py`. Every package is importable and unit-testable
with no server running — that's what makes `tests/` exercise the invariants
directly instead of through HTTP.

## Data model

Two tables are real, live SQLite (`app/db.py`), in WAL mode:

```
instrument_state   isin PK, last_seq, last_exchange_ts, ltp_raw,
                   cum_factor, volume_today, halted                -- I3, I4
watermarks         user_id, isin, last_seen_seq, last_seen_price_raw,
                   last_seen_cum_factor                             -- I2, I3
```

The rest of the originally-envisioned schema — `instruments`, `symbol_aliases`
(I1), `corporate_actions` with a `CONFIRMED`/`UNVERIFIED` status column,
`signals`, `suppressions` (I10) — stays as in-memory shapes only; see
`docs/BUGS.md` for exactly what each of those still doesn't do.

In the running process, every module keeps working against the same plain
dataclasses it always has (`app/models.py`) — persistence is a side effect
`app/main.py` triggers, not a change to the invariant math itself:

- `_states: dict[isin, InstrumentState]` — one row per instrument in memory;
  write-through persisted to `instrument_state` on every scenario load.
- `WatermarkStore._store: dict[(user_id, isin), Watermark]` — one row per
  user+instrument in memory; write-through persisted to `watermarks` on
  every `POST /api/watermark/ack`, and reloaded once at process startup
  before the first scenario replay, so a user's "I looked" position
  survives a restart. See `docs/BUGS.md` for the precise boundary: a
  scenario switch *within* a running process never consults the database —
  only the one-time startup load does, to keep the demo deterministic.
- `_ca_notes: dict[isin, {text, pre_price}]` and `_unverified: dict[isin,
  str]` — display-only caches for the watchlist/digest, not persisted; they
  regenerate identically from a scenario's deterministic replay every time.

Corporate actions are not persisted as rows; the feed simulator emits them
alongside the ticks for the scenario in progress (`app/feed.SCENARIOS`).

## Request flow: `GET /api/digest`

1. Client calls `GET /api/digest` (no body — the user is implicit, hardcoded
   to `"demo"`; see `BUGS.md` on auth scope).
2. `main.digest()` reads the current in-memory `_states` (already caught up
   to every tick applied by the active scenario) and `_watermarks`.
3. `digest.build_digest` iterates every instrument on the watchlist once:
   - looks up that instrument's watermark (skip + seed it if this is the
     first time the instrument's been seen — no card, since there's no prior
     baseline to diff against);
   - computes `corpactions.pct_change(ltp_raw, last_seen_price_raw,
     cum_factor_now, last_seen_cum_factor)` — the I3 adjusted diff, not a raw
     subtraction;
   - keeps the instrument only if `abs(pct_change) >= MOVE_THRESHOLD`.
4. Surviving cards are sorted by `abs(pct_change)` descending and truncated
   to `DEFAULT_BUDGET = 5` (I10).
5. Response returns the capped list. No signal computation happened per
   request — it's a dict lookup and a subtraction. All the expensive work
   (tick ingestion, corporate-action application) already happened once, when
   the scenario was loaded, not once per user per request.

## Why per-instrument fan-out, not per-user computation (I7)

The adjusted price and the corporate-action-adjusted `cum_factor` are
properties of the **instrument**, not the viewer. They're computed exactly
once per tick, regardless of how many users watch that instrument:

```
O(instruments) work:  ingest tick → update InstrumentState → (maybe) apply CA
O(users) work:        look up watermark → subtract → compare to threshold
```

This build has one user in its live demo, so the distinction isn't
exercised end-to-end there — but `scripts/benchmark_fanout.py` measures it
directly against the real `build_digest` function, not a toy stand-in.
3,000 synthetic instruments, one ingest pass (two ticks each: a baseline,
then a real move), then digest computation for 1 user vs. 1,000 users
reading that *same already-ingested* state. Actual output from a run on
this machine:

```
instruments (K)                     : 3000
users (N)                            : 1000
ingest, one batch, all K instruments : 18.30 ms   (paid once)
digest for 1 user                    : 8.281 ms
digest for 1000 users (total)         : 7724.60 ms
digest for 1000 users (avg/user)      : 7.7246 ms
per-user cost ratio (N-user avg / 1-user): 0.93x
```

The number that matters is the last one: serving the 1,000th user costs
about the *same* per-user as serving the 1st (0.93x — noise, not a trend).
If the adjusted-price computation were redone per user instead of shared,
serving 1,000 users would cost 1,000× the 18.30ms ingest pass (~18.3
seconds) instead of the 18.30ms actually paid, once, above. The per-user
step itself is not free — 7–8ms to diff 3,000 instruments in pure Python
is a real cost, and it does scale linearly with instrument count (it is
`O(instruments)` per user, not `O(1)`) — the claim this defends is narrower
and still true: the *expensive* step (ingest and corporate-action
adjustment) is paid once, not once per user, which is the difference
between `O(instruments)` and `O(users × instruments)` at the part of the
system that would actually dominate cost at real scale. The digest function
signature — instrument state passed in once, watermark looked up per user
— is shaped so that swapping in a real per-user fan-out loop (iterating
`users_watching(isin)` instead of a single hardcoded `USER`) requires no
change to the pricing math it calls.

**Honest limits of this measurement**: this is 1,000 sequential Python
function calls in one process, not 1,000 real concurrent users with real
network latency, real per-request overhead, or real horizontal scaling
across machines. It proves the *shape* of the cost — shared work stays
shared as N grows — not a production capacity number.

## Why a modular monolith, not microservices

One process, one deploy, one thing to keep up during a live Q&A. The seams
(`feed` / `ingest` / `corpactions` / `session` / `digest`) are clean package
boundaries already — splitting them into services later is a deployment
change, not a rewrite. Splitting *now*, for a few thousand instruments and a
single demo user, would add network calls and partial-failure modes to a
problem that doesn't have them yet. Complexity introduced before it's needed
is not a sign of engineering maturity here — it's cosplay.

## Why SQLite

SQLite in WAL mode is single-writer, which is exactly the write pattern here:
one ingest path serializing ticks, many cheap reads. There is no
horizontally-scaled write load to justify Postgres, and no operational
surface (connection pools, a separate DB host) to justify running one. The
interesting engineering problem is the schema and the invariants it encodes,
not the storage engine — and unlike an argument made only in prose, this one
is backed by a running `app/db.py`: `instrument_state` and `watermarks` are
real tables, and `tests/test_db.py` proves a value written before a
connection closes is still there after a fresh one opens.

The invariant math never had to change to make this true. `corpactions.pct_change`,
`session.instrument_session_state`, and `WatermarkStore.ack`'s monotonic
guard are exactly the same functions whether the caller is a dict or a row —
persistence is a write-through at the edges (`app/main.py`), not a rewrite
of the logic in between.
