"""Free Yahoo Finance data via the unofficial `yfinance` package. Every call maps exceptions to ProviderError."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from ..core.models import Bar
from .provider import DividendDTO, ProfileDTO, ProviderError, QuoteDTO, SplitDTO, SymbolMatch, to_gics

log = logging.getLogger(__name__)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
TIMEOUT = 10
# Instruments you can hold; options, futures and crypto pairs are left out of symbol search results.
SEARCH_KINDS = {"EQUITY": "Equity", "ETF": "ETF", "MUTUALFUND": "Mutual fund", "MONEYMARKET": "Money market"}


def _dec(v: Any) -> Decimal | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return Decimal(repr(round(f, 4)))


def _to_date(v: Any) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if hasattr(v, "date"):
        try:
            return v.date()
        except (TypeError, ValueError):
            return None
    if isinstance(v, (int, float)) and v > 0:
        return datetime.fromtimestamp(v).date()
    return None


class YFinanceProvider:
    name = "yfinance"

    def __init__(self, timeout: int = TIMEOUT):
        self.timeout = timeout

    def _yf(self):
        try:
            import yfinance as yf
        except ImportError as e:  # pragma: no cover
            raise ProviderError("yfinance is not installed") from e
        return yf

    def quotes(self, symbols: list[str]) -> dict[str, QuoteDTO]:
        if not symbols:
            return {}
        yf = self._yf()
        try:
            df = yf.download(symbols, period="5d", group_by="ticker", progress=False, auto_adjust=False,
                             threads=True, timeout=self.timeout)
        except Exception as e:
            raise ProviderError(f"Quote download failed: {e}") from e
        if df is None or df.empty:
            raise ProviderError("No quote data returned (offline or rate-limited?)")
        out: dict[str, QuoteDTO] = {}
        now = datetime.now().replace(microsecond=0)
        for s in symbols:
            try:
                sub = df[s] if s in df.columns.get_level_values(0) else df
                closes = sub["Close"].dropna()
            except (KeyError, AttributeError):
                continue
            if closes.empty:
                continue
            last = _dec(closes.iloc[-1])
            prev = _dec(closes.iloc[-2]) if len(closes) > 1 else None
            if last is None or last <= 0:
                continue
            out[s] = QuoteDTO(s, last, prev, now)
        return out

    def history(self, symbol: str, start: date, end: date) -> list[Bar]:
        yf = self._yf()
        try:
            df = yf.Ticker(symbol).history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                           auto_adjust=False, timeout=self.timeout)
        except Exception as e:
            raise ProviderError(f"{symbol}: history failed: {e}") from e
        bars: list[Bar] = []
        if df is None or df.empty:
            return bars
        for idx, row in df.iterrows():
            d = _to_date(idx)
            if d is None:
                continue
            vol = row.get("Volume")
            bars.append(Bar(d, _dec(row.get("Open")), _dec(row.get("High")), _dec(row.get("Low")),
                            _dec(row.get("Close")), int(vol) if vol is not None and not math.isnan(vol) else None))
        return bars

    def dividends(self, symbol: str, start: date) -> list[DividendDTO]:
        yf = self._yf()
        try:
            series = yf.Ticker(symbol).dividends
        except Exception as e:
            raise ProviderError(f"{symbol}: dividends failed: {e}") from e
        out = []
        if series is None or len(series) == 0:
            return out
        for idx, amt in series.items():
            d = _to_date(idx)
            a = _dec(amt)
            if d is None or a is None or d < start:
                continue
            out.append(DividendDTO(symbol, d, a))
        return out

    def splits(self, symbol: str) -> list[SplitDTO]:
        yf = self._yf()
        try:
            series = yf.Ticker(symbol).splits
        except Exception as e:
            raise ProviderError(f"{symbol}: splits failed: {e}") from e
        out = []
        for idx, ratio in (series.items() if series is not None else ()):
            d, r = _to_date(idx), _dec(ratio)
            if d is not None and r is not None and r > 0:
                out.append(SplitDTO(symbol, d, r))
        return out

    def search(self, query: str) -> list[SymbolMatch]:
        yf = self._yf()
        try:
            quotes = yf.Search(query, max_results=12, news_count=0, timeout=self.timeout).quotes
        except Exception as e:
            raise ProviderError(f"Symbol search failed: {e}") from e
        return [SymbolMatch(q["symbol"], q.get("longname") or q.get("shortname"), q.get("exchDisp"),
                            SEARCH_KINDS[q.get("quoteType")])
                for q in quotes or [] if q.get("symbol") and q.get("quoteType") in SEARCH_KINDS]

    def profile(self, symbol: str) -> ProfileDTO:
        yf = self._yf()
        try:
            t = yf.Ticker(symbol)
            info = t.info or {}
        except Exception as e:
            raise ProviderError(f"{symbol}: profile failed: {e}") from e
        if not info or (info.get("regularMarketPrice") is None and not info.get("longName")
                        and not info.get("shortName")):
            raise ProviderError(f"{symbol}: unknown symbol")
        earnings = None
        ex_div = pay = None
        try:
            cal = t.calendar
            if isinstance(cal, dict):
                ex_div = _to_date(cal.get("Ex-Dividend Date"))
                pay = _to_date(cal.get("Dividend Date"))
                dates = cal.get("Earnings Date") or []
                dates = [_to_date(x) for x in (dates if isinstance(dates, list) else [dates])]
                future = sorted(d for d in dates if d and d >= date.today())
                earnings = future[0] if future else None
        except Exception:
            log.debug("calendar failed for %s", symbol, exc_info=True)
        if earnings is None:
            ts = info.get("earningsTimestampStart") or info.get("earningsTimestamp")
            d = _to_date(ts)
            earnings = d if d and d >= date.today() else None
        ex_div = ex_div or _to_date(info.get("exDividendDate"))
        pay = pay or _to_date(info.get("dividendDate"))
        if pay is not None and ex_div is not None and pay < ex_div:
            pay = None  # a stale pay date from the previous dividend
        rate = _dec(info.get("dividendRate")) or _dec(info.get("trailingAnnualDividendRate"))
        price = _dec(info.get("currentPrice") or info.get("regularMarketPrice") or info.get("navPrice"))
        dy = None
        if rate and price:
            dy = rate / price
        elif info.get("yield") is not None:  # ETFs and funds report a distribution yield instead of a rate
            dy = _dec(info.get("yield"))
            rate = dy * price if dy is not None and price else None
        elif info.get("trailingAnnualDividendYield") is not None:
            dy = _dec(info.get("trailingAnnualDividendYield"))
        return ProfileDTO(symbol, info.get("longName") or info.get("shortName"), to_gics(info.get("sector")),
                          info.get("industry"), earnings, dy, rate, ex_div, pay)
