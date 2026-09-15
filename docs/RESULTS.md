# Results: naive vs. Since

Output of `python scripts/compare_naive.py`, run against the same seeded feed
(`SEED = 20260907`) through both baselines — the naive one in `app/naive/`
and the real one (`app/corpactions`, `app/session`):

```
SCENARIO    SYSTEM  VERDICT                 DETAIL
split_day   naive   ✗ FALSE ALARM           RELIANCE -90.0% (screaming, wrong)
split_day   since   ✓ CORRECT               RELIANCE +0.2% adjusted, 1:10 split detected, no meaningful change
weekend     naive   ✗ FALSE ALARM           RELIANCE STALE (last tick 20h ago, >30s global timeout)
weekend     since   ✓ CORRECT               RELIANCE CLOSED (market closed, not stale)
```

## split_day — the price lie

RELIANCE goes through a 1:10 split mid-session. Its raw traded price drops
from ~₹2455 to ~₹245 — a real, correct number; nothing is wrong with the
feed. The naive baseline diffs that raw price against the raw watermark and
reports **-90.0%**, the single largest move of the day. To a user, that
reads as a catastrophic crash, and a "smart" naive scorer would rank it
first precisely because a 20-sigma move looks the most important. It is the
opposite: the shareholder's position is worth exactly what it was worth
before, just denominated in ten times as many shares. Since carries a
cumulative adjustment factor per ISIN (`corpactions.pct_change`) and reports
**+0.2%**, the real (tiny) intraday move, with the split named. A user who
trusts the naive number might panic-sell into a split with no actual loss —
this is the exact failure `docs/CLAUDE.md` calls "the cleverer the scoring, the
louder the lie."

## weekend — the staleness lie

The last tick landed at Friday's close. The naive baseline checks one global
30-second timeout regardless of session state, so by Saturday noon — nearly
a full day of silence — it reports **STALE**, a warning that fires every
single weekend, forever. A staleness warning a user learns to expect and
ignore on schedule is a warning that has already stopped working: the first
time it fires on a real feed outage, it looks exactly like every other
weekend and gets ignored too. Since judges liveness per instrument against
that instrument's own expected tick interval, but first asks what session
the market is even in — a closed market isn't stale, it's closed. Since
reports **CLOSED**, correctly, and would only escalate to **DEGRADED** if
the market were open and that specific instrument's own tick cadence broke
down. The naive system can't tell "nothing is wrong" from "everything is
wrong"; Since can, because it never conflates the two questions in the first
place.
