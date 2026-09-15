"""Real persistence — SQLite in WAL mode, against the exact schema already
promised in docs/ARCHITECTURE.md. This module only knows how to shuttle the
existing dataclasses (app.models.InstrumentState, app.digest.Watermark) to
and from disk; it has no opinion on when that should happen — main.py
decides that, so the invariant math in corpactions/digest/session stays
completely unaware persistence exists at all.
"""
import sqlite3
from pathlib import Path

from app.digest import Watermark
from app.models import InstrumentState

SCHEMA = """
CREATE TABLE IF NOT EXISTS instrument_state (
    isin              TEXT PRIMARY KEY,
    last_seq          INTEGER NOT NULL,
    last_exchange_ts  REAL NOT NULL,
    ltp_raw           REAL NOT NULL,
    cum_factor        REAL NOT NULL,
    volume_today      INTEGER NOT NULL,
    halted            INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS watermarks (
    user_id              TEXT NOT NULL,
    isin                 TEXT NOT NULL,
    last_seen_seq        INTEGER NOT NULL,
    last_seen_price_raw  REAL NOT NULL,
    last_seen_cum_factor REAL NOT NULL,
    PRIMARY KEY (user_id, isin)
);
"""


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def save_instrument_state(conn: sqlite3.Connection, state: InstrumentState) -> None:
    conn.execute(
        """
        INSERT INTO instrument_state (isin, last_seq, last_exchange_ts, ltp_raw, cum_factor, volume_today, halted)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(isin) DO UPDATE SET
            last_seq=excluded.last_seq,
            last_exchange_ts=excluded.last_exchange_ts,
            ltp_raw=excluded.ltp_raw,
            cum_factor=excluded.cum_factor,
            volume_today=excluded.volume_today,
            halted=excluded.halted
        """,
        (state.isin, state.last_seq, state.last_exchange_ts, state.ltp_raw, state.cum_factor, state.volume_today, int(state.halted)),
    )
    conn.commit()


def load_instrument_states(conn: sqlite3.Connection) -> dict[str, InstrumentState]:
    rows = conn.execute(
        "SELECT isin, last_seq, last_exchange_ts, ltp_raw, cum_factor, volume_today, halted FROM instrument_state"
    ).fetchall()
    return {
        isin: InstrumentState(
            isin=isin,
            last_seq=last_seq,
            last_exchange_ts=last_exchange_ts,
            ltp_raw=ltp_raw,
            cum_factor=cum_factor,
            volume_today=volume_today,
            halted=bool(halted),
        )
        for isin, last_seq, last_exchange_ts, ltp_raw, cum_factor, volume_today, halted in rows
    }


def save_watermark(conn: sqlite3.Connection, wm: Watermark) -> None:
    conn.execute(
        """
        INSERT INTO watermarks (user_id, isin, last_seen_seq, last_seen_price_raw, last_seen_cum_factor)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, isin) DO UPDATE SET
            last_seen_seq=excluded.last_seen_seq,
            last_seen_price_raw=excluded.last_seen_price_raw,
            last_seen_cum_factor=excluded.last_seen_cum_factor
        """,
        (wm.user_id, wm.isin, wm.last_seen_seq, wm.last_seen_price_raw, wm.last_seen_cum_factor),
    )
    conn.commit()


def load_watermarks(conn: sqlite3.Connection) -> dict[tuple[str, str], Watermark]:
    rows = conn.execute(
        "SELECT user_id, isin, last_seen_seq, last_seen_price_raw, last_seen_cum_factor FROM watermarks"
    ).fetchall()
    return {(user_id, isin): Watermark(user_id, isin, seq, price, factor) for user_id, isin, seq, price, factor in rows}
