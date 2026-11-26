"""Offline provider: never fetches. Prices come only from manual overrides."""

from __future__ import annotations

from datetime import date

from ..core.models import Bar
from .provider import DividendDTO, ProfileDTO, ProviderError, ProviderUnavailableError, QuoteDTO, SymbolMatch


class ManualProvider:
    name = "manual"

    def quotes(self, symbols: list[str]) -> dict[str, QuoteDTO]:
        raise ProviderError("Manual mode: prices are entered by hand (Data ▸ Manual price)")

    def history(self, symbol: str, start: date, end: date) -> list[Bar]:
        return []

    def dividends(self, symbol: str, start: date) -> list[DividendDTO]:
        return []

    def profile(self, symbol: str) -> ProfileDTO:
        raise ProviderUnavailableError("Manual mode: no profile data")

    def splits(self, symbol: str) -> list:
        return []

    def search(self, query: str) -> list[SymbolMatch]:
        raise ProviderUnavailableError("Manual mode: symbol search needs the Yahoo Finance data source (Settings)")


def create_provider(name: str):
    if name == "manual":
        return ManualProvider()
    from .yfinance_provider import YFinanceProvider

    return YFinanceProvider()
