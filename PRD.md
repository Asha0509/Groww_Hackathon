# PRD — Since

**A watchlist that tells you what you missed, and never lies about it.**

Version 1.0 · Solo build · ~9 hour window · Submission: Groww "Code" 2026

---

## 1. Problem

The brief: *"help users not just track stocks, but quickly understand what has meaningfully
changed since they last checked, and what deserves their attention now."*

The obvious reading produces a live price grid with red and green tiles. That answers
*"what is this stock worth?"* — a question nobody opens a watchlist to ask.

The actual question is **"what did I miss?"** That is a diff, not a display. And a diff is
only as good as its baseline. So the real engineering problem, which the brief does not state,
is: **can the baseline be trusted?**

Three ways it breaks:

**The baseline is personal.** Not yesterday's close. Whenever *this user* last actually looked.

**The baseline decays.** A 1:10 split moves a stock ₹2000 → ₹200. Naive systems report −90%.
Volatility-normalised systems rank it as the day's biggest event, because −90% is a ~20-sigma move.
Nothing happened. This is not hypothetical: Zerodha's Kite adjusts historical data for splits only,
not other corporate actions, and mostly over the following weekend.

**The baseline expires.** One `stale` flag on a global timeout is wrong three separate ways:
it warns all weekend when the price is correct, it can't distinguish a circuit halt from an outage,
and it false-alarms on illiquid instruments that legitimately trade every few minutes.

## 2. User

One user type: a retail investor with 20–60 instruments on a watchlist who opens the app a few
times a day, on more than one device. Not a professional trader. Groww's base skews first-time
investors, 98%+ pincode coverage, majority non-metro.

## 3. What ships

### P0 — must exist for a valid submission
- Create / rename / delete watchlists; add and remove instruments
- Live prices over WebSocket
- **The digest**: on return, what changed since this user last looked
- Per-user, per-instrument watermarks that survive sessions and devices
- Corporate-action adjustment so splits/bonuses/dividends never fire false alerts
- Five-state session model with per-instrument liveness
- Deterministic, replayable feed simulator with named scenarios
- Test suite proving a naive baseline fails where Since passes

### P1 — ships if time allows
- Digest budget tuning UI (cards-per-visit slider)
- "Why am I seeing this?" expander on each card showing the score breakdown
- Mark-as-read via viewport intersection rather than page load

### P2 — explicitly cut
Charts · news feed · LLM narration · price alerts · portfolio/holdings · real vendor feed ·
auth beyond a user switcher · mobile app · social features · backtesting · search across all NSE

**Cutting these is a scored decision, not a shortcut.** The README must say so.

## 4. Behaviour spec

### Digest
On load, for each watched instrument, diff current adjusted state against the user's watermark.
Score each candidate signal. Return at most **5** cards, ranked. Everything else is silence.

Card kinds: `MOVE` · `VOLUME` · `LEVEL` · `EVENT` · `UNVERIFIED`

A corporate action on the ex-date **suppresses** MOVE for that instrument and emits `EVENT` instead.

### Watermark
Advances server-side, monotonically, by `max(seq)`. Client timestamps are never trusted.
Reading on device A advances the watermark for device B. It can never regress.

### Adjustment
`baseline_adjusted = last_seen_price_raw × (cum_factor_now / cum_factor_then)`.
A corporate action landing late requires **no backfill** — the ratio self-corrects.

### Session states
| State | Meaning | Warn user? |
|---|---|---|
| `PRE_OPEN` | 09:00–09:15 IST | No |
| `LIVE` | 09:15–15:30 IST, weekday, non-holiday | No |
| `CLOSED` | outside hours / weekend / holiday | **No** — price is correct |
| `HALTED` | circuit hit; price frozen legitimately | No — frozen is the signal |
| `DEGRADED` | `LIVE` but no tick within `k × expected_interval(isin, bucket)` | **Yes** |

### Unverified corporate action
Clean-ratio gap (1:2, 1:5, 1:10, 1:20 within tolerance) at open, no volume spike, no CA record
→ mark `UNVERIFIED`, suppress the move, surface as "unverified — not treated as a price change."

## 5. Acceptance criteria, mapped to the rubric

| Rubric line | How this build earns it | Verified by |
|---|---|---|
| **Engineering Depth** — architecture, correctness, reliability, scalability | ISIN keying, monotonic watermarks, ordering guard, per-instrument fan-out (O(instruments) not O(users×instruments)) | `test_ordering.py`, `test_watermark.py`, README scaling section |
| **Product & Problem Interpretation** — beyond the obvious brief | Reframed display → diff; identified that the baseline, not the UI, is the problem | Pitch, README opening, demo beat 1 |
| **Edge Cases & Resilience** — failures, races, integrity, unreliable deps | Corporate actions, 5-state session model, per-symbol liveness, out-of-order ticks, late CA feed, two-device race | `make demo-tests` red/green table |
| **Code Quality & Simplicity** — no over-engineering | Modular monolith, SQLite, no broker, no LLM, four scorers | Repo structure, `Decisions & Trade-offs` |
| **Originality & Thoughtfulness** — independent choices | The three-lies framing; deliberate non-goals; "fail toward silence, not toward a lie" | README, Q&A |

## 6. Non-functional

- Cold start to first meaningful paint under 2s on the deployed URL
- Digest computed for a 60-instrument watchlist in under 100ms
- Deterministic: same scenario + same seed = byte-identical tick stream
- Runs on a clean machine via `./setup.sh && make dev`

## 7. Risks

| Risk | Mitigation |
|---|---|
| Only the **first 1,000** of 2,942 submissions are evaluated | Ship a thin working version at hour 4.5 and submit it. Improve after. |
| Deployment fails late | Deploy hello-world in the first 30 minutes, before any features |
| Scope creep into charts/news | P2 list is binding. New ideas go to `OPEN_QUESTIONS.md`, not the repo |
| Demo breaks live | Scenarios are seeded and deterministic; rehearse the 5-minute run twice |
| Can't defend it in Q&A | Every invariant in CLAUDE.md is written as a "why" answer. Read them aloud. |

## 8. Submission checklist

- [ ] Git repo, public, clean history
- [ ] README: setup instructions, architecture, **Decisions & Trade-offs**
- [ ] `PITCH.md` — exactly ~100 words
- [ ] Working deployed URL
- [ ] `make test` green, `make demo-tests` prints the comparison table
- [ ] Submitted **early**
