# Architecture

This describes the system as built for the reduced-scope (75-minute) cut. The
target design in `CLAUDE.md` calls for SQLite-backed persistence and a fuller
module split; this build keeps the same module boundaries but holds state
in-memory, in a single process, reset per scenario load. See `BUGS.md` for
what that costs.

## Module map

```
app/
├─ models.py       Instrument, Tick, InstrumentState — shared plain dataclasses
├─ feed/           seeded deterministic tick simulator (normal, split_day)
├─ ingest/         ordering guard — applies a tick only if seq > last_seq (I4)
├─ corpactions/    cumulative adjustment factor, ISIN-keyed baseline math (I3)
├─ session/        five-state session model + per-instrument liveness (I5, I6)
├─ digest/         watermark store + diff + budget cap (I2, I10)
├─ naive/          deliberately naive baseline, kept wrong on purpose
├─ static/         single HTML page, plain CSS, no build step
└─ main.py         FastAPI app: wires the above into scenario/watchlist/digest endpoints
```

Nothing here imports from `main.py`. Every package is importable and unit-testable
with no server running — that's what makes `tests/` exercise the invariants
directly instead of through HTTP.

## Data model

The target schema (`CLAUDE.md §6`) is the one to build against if this
persists past the demo:

```
instruments        isin PK, name, liquidity_tier, listed_on
symbol_aliases     symbol, isin FK, valid_from, valid_to        -- I1
corporate_actions  id, isin FK, kind, ex_date, ratio_from, ratio_to,
                   adjustment_factor, status(CONFIRMED|UNVERIFIED)
instrument_state   isin PK, last_seq, last_exchange_ts, ltp_raw,
                   cum_factor, session_state, expected_interval_ms
watermarks         user_id, isin, last_seen_seq, last_seen_price_raw,
                   last_seen_cum_factor, last_seen_at              -- I2, I3
signals            id, isin FK, seq, kind, score, payload_json
suppressions       user_id, isin, signal_kind, cooldown_until      -- I10
```

What's actually running is the same shapes as plain dataclasses
(`app/models.py`), keyed by ISIN in module-level dicts:

- `_states: dict[isin, InstrumentState]` — one row per instrument, the
  in-memory equivalent of `instrument_state`.
- `WatermarkStore._store: dict[(user_id, isin), Watermark]` — the in-memory
  equivalent of `watermarks`.
- `_ca_notes: dict[isin, {text, pre_price}]` — a display-only cache for the
  watchlist row annotation, not part of the invariant math.

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

A watchlist with a few thousand instruments and millions of users must not
redo the adjusted-price math per user — that's the difference between
`O(instruments)` and `O(users × instruments)`. The per-user step here is a
dict lookup and a float subtraction; it stays cheap at any user count. This
build has one user, so the distinction isn't exercised by the demo, but the
digest function signature — instrument state passed in once, watermark
looked up per user — is shaped so that swapping in a real per-user fan-out
loop (e.g. iterating `users_watching(isin)` instead of a single hardcoded
`USER`) requires no change to the pricing math it calls.

## Why a modular monolith, not microservices

One process, one deploy, one thing to keep up during a live Q&A. The seams
(`feed` / `ingest` / `corpactions` / `session` / `digest`) are clean package
boundaries already — splitting them into services later is a deployment
change, not a rewrite. Splitting *now*, for a few thousand instruments and a
single demo user, would add network calls and partial-failure modes to a
problem that doesn't have them yet. Complexity introduced before it's needed
is not a sign of engineering maturity here — it's cosplay.

## Why SQLite (and why this build skips even that)

SQLite in WAL mode is single-writer, which is exactly the write pattern here:
one ingest path serializing ticks, many cheap reads. There is no
horizontally-scaled write load to justify Postgres, and no operational
surface (connection pools, a separate DB host) to justify running one. The
interesting engineering problem is the schema and the invariants it encodes,
not the storage engine.

This particular build goes a step further and skips persistence entirely —
state lives in module-level dicts, reset on scenario load or process
restart. That's a scope cut for the demo window, not a claim that SQLite is
unnecessary: the schema above is what `instrument_state` and `watermarks`
would look like on disk, and the invariant math (`corpactions.pct_change`,
`WatermarkStore.ack`) doesn't change at all when a real `INSERT`/`UPDATE`
replaces a dict write.
