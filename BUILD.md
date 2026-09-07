# BUILD.md — how to actually build this

**~9 hours. Deadline 11:00 IST.** Read this once, then work top to bottom.

## Rules of engagement

1. **`CLAUDE.md` is the contract.** Start every Claude Code session by telling it to read
   `CLAUDE.md` and `PRD.md` first. It will drift within an hour otherwise.
2. **Commit at every hour gate.** Small commits. A clean history is read by the judges.
3. **Two hard gates, marked ⛔ below.** Do not pass a gate with it unmet.
4. **New idea mid-build?** It goes in `OPEN_QUESTIONS.md`, not the repo. Scope creep is the
   most likely way this build dies.

---

## Hour 0 → 0:30 · Scaffold and deploy ⛔

```bash
chmod +x setup.sh && ./setup.sh
make api        # confirm localhost:8000/healthz
```

Push to GitHub. Deploy the Dockerfile to Render or Railway. **Get a live URL now, with nothing
in it.** Deployment problems discovered at hour 8 are unrecoverable; discovered at hour 0 they
cost ten minutes.

> ⛔ **Gate 1: a live public URL returning `{"ok": true}` before any feature code exists.**

---

## Hour 0:30 → 2:00 · The domain core

This is the part that wins. Everything after it is plumbing.

**Prompt:**
> Read CLAUDE.md and PRD.md fully. Implement the data model from CLAUDE.md §6 with SQLAlchemy,
> plus three modules: `corpactions/` (cumulative adjustment factors, ISIN resolution via
> symbol_aliases, and `adjusted_baseline(watermark, state)` implementing invariant I3),
> `session/` (NSE market calendar in IST, the five-state machine from I5, and per-instrument
> liveness from I6), and `ingest/` (the ordering guard from I4 — drop out-of-order ticks by
> exchange timestamp and per-instrument seq, never rewind).
> Write failing tests first for I1, I3, I4, I5, I6. Add `# INVARIANT In:` comments where enforced.
> No API, no frontend yet.

Then verify by hand — do not trust it blindly:

```bash
make test
```

Read `corpactions/` yourself, line by line. If a judge asks one question you can't answer, it will
be about this file. Confirm the ratio form is `last_seen_price_raw × (cum_factor_now / cum_factor_then)`
and that **no historical row is ever rewritten**.

*Commit: `feat: corporate actions, session state, ordering guard`*

---

## Hour 2:00 → 3:00 · Feed simulator

**Prompt:**
> Build `feed/` — a deterministic, seeded tick simulator behind a `MarketFeed` protocol so a real
> vendor is one alternate implementation. Ship ~40 NSE instruments across liquidity tiers with a
> U-shaped intraday volume profile. Implement the six named scenarios from CLAUDE.md §9:
> `normal`, `split_day`, `feed_death`, `weekend`, `illiquid`, `late_ca`.
> Same seed must produce a byte-identical tick stream. Expose `POST /api/scenario/{name}` to
> switch scenarios at runtime without a restart.

*Commit: `feat: deterministic feed simulator with six scenarios`*

---

## Hour 3:00 → 4:00 · Signals and digest

**Prompt:**
> Implement `signals/` with four deterministic scorers — MOVE (sigma-normalised on **adjusted**
> price vs the user's watermark baseline), VOLUME (vs intraday profile bucket), LEVEL (52w break,
> circuit, open gap), EVENT (corporate action, results). Instruments with insufficient volatility
> history use a conservative tier default and are marked `low_confidence`.
> Implement `digest/`: watermark diff, budget of 5, cooldown and hysteresis per I10.
> **Critical:** a corporate action on the ex-date suppresses MOVE for that instrument and emits
> EVENT instead. Implement the I9 unverified-CA detector.
> Signals compute per instrument then fan out (I7) — no per-user scoring loop.

*Commit: `feat: deterministic signal scoring and budgeted digest`*

---

## Hour 4:00 → 4:30 · API, then SHIP ⛔

**Prompt:**
> Add `api/` — REST for watchlist CRUD and `GET /api/digest`, plus a WebSocket streaming tick and
> state updates. Watermark advance is a server-side endpoint that takes `seq`, never a client
> timestamp, and is monotonic and idempotent (I2).

Then: build the frontend to `dist`, deploy, and **submit on HackerEarth.**

> ⛔ **Gate 2: a real submission is in, with a working URL, before hour 5.**
> Only the first 1,000 of 2,942 registrations are evaluated. If resubmission is allowed you
> improve it later. If it isn't, you still have a valid entry instead of a perfect local repo.

*Commit: `feat: api + websocket; first submittable build`*

---

## Hour 4:30 → 6:00 · Frontend

Deliberately plain. No component library, no charts. The restraint is the point.

**Prompt:**
> Build the React SPA: watchlist management; the digest view at the top ("Since you last looked,
> 2:14 PM") showing at most 5 cards; below it the full watchlist, quiet. Each card states its kind
> and a one-line reason. Show session state per instrument with an honest badge — `CLOSED` and
> `HALTED` must **not** look like warnings; only `DEGRADED` does. Every price shows its as-of age.
> A scenario switcher and a user switcher in the header for the demo. Plain CSS. Keep it calm.

*Commit: `feat: digest UI with honest liveness badges`*

---

## Hour 6:00 → 7:00 · The proof

The highest-value hour in the build. Everyone else will *claim* resilience in a README.

**Prompt:**
> Implement `app/naive/` — a deliberately naive watchlist: diffs raw price against a raw watermark
> with no adjustment, one global 30-second stale timeout, no session awareness, applies ticks in
> arrival order. Keep it honest and readable; it represents what a reasonable person builds.
> Then write `scripts/compare_naive.py`: run both implementations across all six scenarios and
> print a red/green table. Add these tests:
> `test_split_day_fires_no_false_alert`, `test_market_closed_is_not_stale`,
> `test_illiquid_symbol_not_flagged_degraded`, `test_watermark_never_regresses_across_devices`,
> `test_out_of_order_tick_does_not_rewind`, `test_late_corporate_action_is_unverified_not_a_move`.
> Naive must visibly fail; Since must pass.

```bash
make demo-tests
```

That table is your closing argument. Screenshot it for the README.

*Commit: `test: naive baseline comparison — proof, not claims`*

---

## Hour 7:00 → 8:00 · README and pitch

The README is scored. Structure it exactly:

1. **What this is** — the one-sentence thesis, then the three lies
2. **The split-day screenshot**, side by side. Put it high. It sells the whole project.
3. **Architecture** — the ASCII diagram from CLAUDE.md §5, plus why a modular monolith
4. **Invariants** — the I1–I10 table verbatim. This is your Q&A cheatsheet made public.
5. **Scaling** — O(instruments) not O(users×instruments); note hot-symbol fan-out
   (fan-out-on-write for widely-held names, on-read for the long tail) as the next step
6. **Decisions & Trade-offs** ← *the section that scores originality*
7. **Setup** — `./setup.sh && make dev`

Write section 6 honestly:

> **Deliberately not built.** No LLM in the scoring path: a scorer that can't be unit-tested can't
> be defended, and ranking is exactly where a hallucination becomes a false financial signal.
> No news feed or charts: breadth where the brief rewards depth. No message broker or
> microservices: a modular monolith is correct at this scale and the seams are clean enough to
> split later. No real vendor feed: a deterministic simulator makes failure modes *reproducible*,
> which is the only way to test resilience honestly — the vendor adapter interface is there.
> SQLite: single-writer is sufficient here and the schema is the interesting part, not the ops.

Then `PITCH.md` (~100 words):

> Most watchlists are dashboards showing what a stock is worth. Since is a diff: it shows what
> changed while you were away. That makes the baseline the whole engineering problem. The baseline
> is personal, held as a monotonic server-side watermark so two devices can't corrupt it. It
> decays, so corporate actions apply as adjustment factors keyed by ISIN — a 1:10 split is not a
> 90% crash, and Since won't say it is. It expires, so liveness is judged per symbol across four
> states: live, closed, halted, degraded. Silence is the default, because a watchlist that shouts
> trains overtrading.

*Commit: `docs: readme, decisions and trade-offs, pitch`*

---

## Hour 8:00 → 9:00 · Rehearse and resubmit

Redeploy. Run all six scenarios on the **live URL**, not localhost.

Rehearse the demo twice, out loud, with a timer:

| Time | Beat |
|---|---|
| 0:00 | Normal grid. *"Tell me what I missed since this morning. You can't."* |
| 0:30 | Since: 40 instruments, 3 cards. *"The silence is the product."* |
| 1:30 | **Split day, side by side.** *"Every other watchlist just told this user her biggest holding crashed 90%. That's how you make someone panic-sell on a day nothing happened."* |
| 3:00 | Kill the feed → `DEGRADED` with per-symbol reasoning. Flip to weekend → `CLOSED`, no false alarm. |
| 4:00 | Two devices; watermark advances, never regresses. |
| 4:30 | `make demo-tests` — the red/green table. |

Then rehearse the three questions until the answers are reflexive:

**"Why ISIN, not ticker?"** — Tickers get renamed and reused. A watermark keyed on ticker silently
starts diffing against a different company and nothing in the system notices.

**"A stock hasn't ticked in two minutes. Broken?"** — Depends on that instrument's own baseline tick
rate and the session state. A global timeout false-alarms on illiquid names and misses real outages
on liquid ones.

**"What if your corporate-action feed is late?"** — Detect the anomaly: a clean-ratio gap at open
with no matching volume spike. Suppress it and mark it unverified rather than assert a crash.
**Fail toward silence, not toward a lie.**

**Final submit by 10:30.** Leave 30 minutes of buffer. Something will go wrong.

---

## If you fall behind

Cut in this order, without hesitation:
1. P1 items (score expander, viewport read receipts)
2. Frontend polish — plain is fine, plain is *on-brand*
3. VOLUME and LEVEL scorers — MOVE and EVENT alone carry the argument
4. Scenarios `illiquid` and `late_ca` — keep `split_day` and `feed_death`

**Never cut:** corporate-action adjustment, the five-state session model, the naive comparison
tests, or the Decisions & Trade-offs section. Those four *are* the submission.
