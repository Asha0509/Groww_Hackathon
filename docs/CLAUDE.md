# CLAUDE.md — Since

> Read this file completely before writing any code. It is the contract.
> When a request in chat conflicts with this file, follow this file and say so.

**Status note, added after a later hardening pass**: this is the original
design contract, kept as written. One line below (§8, "do not integrate a
real market data vendor") has since been deliberately superseded — a real
live feed was added on top of the deterministic simulator this file
describes, for the reasoning recorded in `README.md` and
`docs/ARCHITECTURE.md`'s "The live feed" section. Everything else here
still holds. See `docs/BUGS.md` for the honest, current gap list and
`docs/LLD.md` for the detailed design as it actually stands today.

---

## 1. The one-sentence thesis

**A watchlist is not a dashboard. It is a diff. A diff is only as good as its baseline — so the entire engineering problem is whether the baseline can be trusted.**

Every design decision below is a consequence of that sentence. If a proposed feature does not
make the baseline more trustworthy or the diff more honest, it does not belong in this project.

## 2. What this is

`Since` is a market watchlist that answers **"what did I miss?"** rather than **"what is this worth?"**

The user opens the app. It tells her what meaningfully changed **since she last actually looked**,
and stays silent about everything else.

Built for a 72-hour solo engineering challenge (Groww). Judged on: engineering depth,
problem interpretation, edge cases & resilience, code quality & simplicity, originality.
Judged in a live Q&A where the author must defend every choice.

## 3. The three lies (the whole product)

A naive watchlist lies three ways. Each lie is one subsystem.

### Lie 1 — the baseline is not personal
Naive systems diff against *yesterday's close*. The correct baseline is **whenever this user last
actually looked**, held as a per-user, per-instrument **watermark**.

### Lie 2 — the baseline decays
A 1:10 split takes a stock ₹2000 → ₹200. A naive watchlist reports **−90%**. A *clever* naive
watchlist ranks it as the biggest event of the day, because −90% is a ~20-sigma move.
**The cleverer the scoring, the louder the lie.** Nothing happened.
Bonus issues do the same at −50%. Ex-dividend dates do it weekly.
Fix: corporate actions as cumulative adjustment factors, instruments keyed by **ISIN, not ticker**.

### Lie 3 — the baseline expires
Naive systems ship one `stale` flag on a global timeout. That flag is wrong three ways.
There are **four** states, and liveness is judged **per symbol**, not globally.

---

## 4. Hard invariants

These are non-negotiable. Every one of them is a Q&A answer. Violating one is a bug even if tests pass.

| # | Invariant | Why (the "why" answer) |
|---|-----------|------------------------|
| I1 | Instruments are keyed by **ISIN**. `symbol` is a display alias with validity dates. | Tickers get renamed and reused. A watermark keyed on ticker silently starts diffing against a different company and nothing notices. |
| I2 | Watermarks advance **monotonically**, server-side, by **sequence number**. Never by client timestamp. | Client clocks are skewed and untrusted. Two devices race. `seq` is the only total order we control. |
| I3 | A watermark stores `last_seen_price_raw` **and** `last_seen_cum_factor`. Comparison is always `price_now_raw` vs `last_seen_price_raw × (cum_factor_now / cum_factor_then)`. | Lets a corporate action land at any time with **zero backfill migration**. The baseline self-corrects. |
| I4 | Tick ordering is by **exchange timestamp + per-instrument seq**, never arrival time. Out-of-order ticks are **dropped**, never applied. | Network reordering must not rewind state. |
| I5 | Session state is one of `PRE_OPEN | LIVE | CLOSED | HALTED | DEGRADED`. `CLOSED` is **not** stale. `HALTED` is **not** stale. | Only `DEGRADED` is our fault. Warning during a weekend is a false alarm and destroys trust in the warning. |
| I6 | `DEGRADED` is computed against **that instrument's own expected tick interval** for that time-of-day bucket. Never a global timeout. | An illiquid smallcap trading every 4 min is not stale at 30s. RELIANCE silent for 10s is. |
| I7 | Signals are computed **per instrument**, then fanned out to subscribers. Per-user work is only the cheap watermark diff. | O(instruments), not O(users × instruments). Few thousand instruments, millions of users. |
| I8 | Scoring is **deterministic and unit-testable**. No LLM in the ranking path, ever. | A scorer you cannot unit-test is a scorer you cannot defend. |
| I9 | An unexplained clean-ratio gap (1:2, 1:5, 1:10, 1:20) at session open with **no matching volume spike** is flagged `UNVERIFIED_CORPORATE_ACTION`, **suppressed**, and surfaced as "unverified" — never reported as a price move. | The CA feed can be late. **Fail toward silence, not toward a lie.** |
| I10 | The digest has a **budget** (default 5 cards) with per-signal cooldowns and hysteresis. | Silence is a feature. FINRA warns push notifications drive overtrading; SEBI FY26 found 87.7% of individual F&O traders lost money. A watchlist that shouts trains bad behaviour. |

---

## 5. Architecture

**A modular monolith.** One deployable unit. This is a deliberate choice, not a shortcut — the
rubric explicitly rewards "maintainability without unnecessary over-engineering." Seams are clean
enough to split later; splitting now would be cosplay.

```
FastAPI app (single process)
├─ feed/          deterministic tick simulator + scenario replay
│                 (vendor adapter interface — real feed is one impl)
├─ ingest/        ordering guard (I4), per-instrument state, seq assignment
├─ corpactions/   cumulative adjustment factors, ISIN resolution, anomaly detector (I9)
├─ session/       market calendar + 5-state machine (I5) + per-symbol liveness (I6)
├─ signals/       deterministic scorers: MOVE, VOLUME, LEVEL, EVENT  → per-instrument (I7)
├─ digest/        watermark diff + budget + cooldown/hysteresis (I10)
├─ api/           REST + WebSocket
└─ web/           React SPA, served static by the same app
```

**Stack (fixed — do not renegotiate):**
Python 3.11 · FastAPI · SQLAlchemy · SQLite (WAL) · pytest ·
React + Vite + TypeScript · plain CSS (no UI framework) · single Dockerfile.

**Why SQLite:** single-writer is fine at this scale, zero ops, the schema is the interesting part.
Say this out loud in Q&A; it reads as judgement, not laziness.

---

## 6. Data model

```
instruments        isin PK, name, liquidity_tier, listed_on
symbol_aliases     symbol, isin FK, valid_from, valid_to        -- I1
corporate_actions  id, isin FK, kind(SPLIT|BONUS|DIVIDEND|RIGHTS),
                   ex_date, ratio_from, ratio_to, dividend_amount,
                   adjustment_factor, status(CONFIRMED|UNVERIFIED)  -- I9
instrument_state   isin PK, last_seq, last_exchange_ts, ltp_raw,
                   cum_factor, session_state, expected_interval_ms  -- I3,I5,I6
users              id, handle
watchlists         id, user_id FK, name
watchlist_items    watchlist_id FK, isin FK, added_at
watermarks         user_id, isin, last_seen_seq, last_seen_price_raw,
                   last_seen_cum_factor, last_seen_at              -- I2,I3
signals            id, isin FK, seq, kind, score, payload_json, computed_at  -- I7
suppressions       user_id, isin, signal_kind, cooldown_until      -- I10
```

`cum_factor` is the product of all confirmed adjustment factors to date.
It only ever moves forward. Never rewrite history; change the factor.

---

## 7. Signals

Four kinds. All deterministic. All return a `score` in a comparable range.

- **MOVE** — adjusted-price move since watermark, normalised by that instrument's own realised
  volatility (sigma-move). Instruments with insufficient history fall back to a conservative
  tier default and are marked `low_confidence` — never silently scored as if they had history.
- **VOLUME** — volume vs the intraday volume profile bucket. Volume is U-shaped through the
  session; "high volume" at 15:20 means something different from "high volume" at 11:00.
- **LEVEL** — 52-week high/low break, circuit hit, gap-up/gap-down at open.
- **EVENT** — corporate action ex-date, results date.

A corporate action **suppresses** the MOVE signal for that instrument on the ex-date and emits an
EVENT card instead. This is the single most important line of business logic in the repo.

---

## 8. Rules for you, Claude Code

**Do:**
- Write the failing test first for anything touching invariants I1–I10.
- Keep every module importable and testable without a running server.
- Keep the `naive/` baseline implementation building and *deliberately wrong*. It is a deliverable.
- Put a one-line `# INVARIANT In:` comment above code that enforces an invariant.
- Prefer boring, readable code. This will be read by a senior engineer, not a linter.

**Do not:**
- Add an LLM anywhere in the scoring or ranking path (I8).
- Add news integration, charts, price alerts, portfolio tracking, social features, or auth beyond
  a hardcoded user switcher. **All are explicitly out of scope.** Breadth is the enemy here.
- Introduce Kafka, Redis, microservices, Celery, or a message broker. The monolith is the answer.
- Integrate a real market data vendor. The simulator is the deliverable; the vendor adapter
  interface is the proof we thought about it.
- Refactor toward abstraction "for later." There is no later.
- Silently widen scope. If something seems missing, add it to `docs/BUGS.md` instead (in practice, this is where every open question actually ended up).

**When stuck:** choose the option that is easier to *explain*, not the one that is more impressive.

---

## 9. The demo is a deliverable

The build must support these five scenarios on demand via `/api/scenario/{name}`:

| Scenario | Must show |
|---|---|
| `normal` | 40 instruments, 3 cards. Silence as a feature. |
| `split_day` | naive says −90%; Since says "1:10 split, no meaningful change, share count now 10×" |
| `feed_death` | flips to `DEGRADED` mid-session with per-symbol reasoning visible |
| `weekend` | `CLOSED`, **no** stale warning |
| `illiquid` | sparse ticks, **not** flagged degraded |
| `late_ca` | clean-ratio gap, no CA record → `UNVERIFIED`, suppressed |

Scenarios must be **seeded and deterministic**. The demo has to be repeatable under pressure.

## 10. Definition of done

- [ ] `./setup.sh` then `make dev` works on a clean machine
- [ ] `make test` green; `make demo-tests` prints the naive-vs-Since red/green table
- [ ] README with setup, architecture diagram, and a **Decisions & Trade-offs** section
      that lists what was deliberately *not* built and why
- [ ] 100-word pitch (later folded directly into `README.md`'s opening rather than kept as a separate file)
- [ ] All five demo scenarios run end-to-end without a restart
