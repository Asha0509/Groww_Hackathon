"""Shared plain data structures. No behavior, no persistence — every
package below imports these instead of redefining its own shape.
"""
from dataclasses import dataclass


@dataclass
class Instrument:
    isin: str
    symbol: str
    name: str
    liquidity_tier: str = "liquid"  # liquid | illiquid — drives I6 expected interval


@dataclass
class Tick:
    isin: str
    seq: int
    exchange_ts: float  # exchange-assigned epoch seconds, not arrival time (I4)
    price_raw: float
    volume: int


@dataclass
class InstrumentState:
    isin: str
    last_seq: int = 0
    last_exchange_ts: float = 0.0
    ltp_raw: float = 0.0
    cum_factor: float = 1.0
    volume_today: int = 0
    halted: bool = False
