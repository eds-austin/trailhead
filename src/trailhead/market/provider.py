"""MarketDataProvider protocol and DTOs (PLAN §8). Providers raise ProviderError on any failure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ..core.models import Bar


class ProviderError(Exception):
    pass


class ProviderUnavailableError(ProviderError):
    """The provider can't answer this kind of question at all (e.g. manual / offline mode)."""


@dataclass(frozen=True)
class QuoteDTO:
    symbol: str
    last: Decimal
    prev_close: Decimal | None
    as_of: datetime


@dataclass(frozen=True)
class DividendDTO:
    symbol: str
    ex_date: date
    amount_per_share: Decimal
    pay_date: date | None = None


@dataclass(frozen=True)
class SplitDTO:
    symbol: str
    ex_date: date
    ratio: Decimal  # new shares per old share


@dataclass(frozen=True)
class ProfileDTO:
    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    next_earnings: date | None = None
    div_yield: Decimal | None = None  # fraction, 0.0042 = 0.42 %
    annual_dividend: Decimal | None = None
    ex_div_date: date | None = None  # next (or latest announced) ex-dividend date
    div_pay_date: date | None = None


@dataclass(frozen=True)
class SymbolMatch:
    symbol: str
    name: str | None = None
    exchange: str | None = None
    kind: str | None = None  # Equity, ETF, Mutual fund …
    likely: bool = False  # a spelling of the typed symbol that the provider knows (BRK.B → BRK-B)


def symbol_variants(symbol: str) -> list[str]:
    """Yahoo spellings of a share-class symbol: BRK.B, BRK/B, BRK B and BRKB → BRK-B.

    Only candidates: a dot is also Yahoo's exchange suffix (SHOP.TO), so each variant must be checked with the
    provider before it is offered."""
    s = symbol.strip().upper()
    out = []
    m = re.fullmatch(r"([A-Z]{1,5})[./ ]([A-Z]{1,2})", s)
    if m:
        out.append(f"{m[1]}-{m[2]}")
    else:
        m = re.fullmatch(r"([A-Z]{2,4})([A-C])", s)
        if m:
            out.append(f"{m[1]}-{m[2]}")
    return [v for v in out if v != s]


@runtime_checkable
class MarketDataProvider(Protocol):
    name: str

    def quotes(self, symbols: list[str]) -> dict[str, QuoteDTO]: ...

    def history(self, symbol: str, start: date, end: date) -> list[Bar]: ...

    def dividends(self, symbol: str, start: date) -> list[DividendDTO]: ...

    def profile(self, symbol: str) -> ProfileDTO: ...

    def search(self, query: str) -> list[SymbolMatch]: ...

    def splits(self, symbol: str) -> list[SplitDTO]: ...


# Yahoo's sector names → S&P (GICS) sector names.
GICS_SECTORS = {
    "Technology": "Information Technology",
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Financial Services": "Financials",
    "Healthcare": "Health Care",
    "Basic Materials": "Materials",
    "Communication Services": "Communication Services",
    "Energy": "Energy",
    "Industrials": "Industrials",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
}


def to_gics(sector: str | None) -> str | None:
    if not sector:
        return None
    return GICS_SECTORS.get(sector, sector)
