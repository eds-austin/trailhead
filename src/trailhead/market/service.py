"""MarketService: refresh quotes, history, profiles and dividends off the UI thread (PLAN §8)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..core.models import Dividend, Instrument, Quote
from ..core.positions import build_lots, build_positions
from ..data.repo import Repos
from .provider import MarketDataProvider, ProviderError, ProviderUnavailableError, SymbolMatch, symbol_variants

log = logging.getLogger(__name__)

HISTORY_BACK_DAYS = 365
DIVIDEND_BACK_DAYS = 400  # a year of payments: trailing income and the payment schedule
CLOSED_WITHIN_DAYS = 30


@dataclass
class RefreshResult:
    started: datetime
    source: str
    symbols: list[str] = field(default_factory=list)
    quotes: int = 0
    pinned_skipped: int = 0
    bars: int = 0
    profiles: int = 0
    dividends: int = 0
    splits: int = 0
    errors: list[str] = field(default_factory=list)
    finished: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.quotes > 0 or (not self.symbols and not self.errors)


def with_retry(fn: Callable, *args, retries: int = 1):
    last: Exception | None = None
    for _ in range(retries + 1):
        try:
            return fn(*args)
        except ProviderError as e:
            last = e
    assert last is not None
    raise last


def refresh_targets(repos: Repos, portfolio_ids: list[int] | None, today: date) -> dict[str, date]:
    """Symbols of open positions, positions closed within 30 days, and any traded symbol that still has no
    sector → earliest entry date to cover."""
    pids = portfolio_ids if portfolio_ids is not None else [p.id for p in repos.portfolios.list()]
    out: dict[str, date] = {}
    cutoff = today - timedelta(days=CLOSED_WITHIN_DAYS)
    known = {s for s, i in repos.market.get_instruments().items() if i.sector}
    for pid in pids:
        lots = build_lots(repos.trades.list(pid))
        for pos in build_positions(lots):
            cd = pos.close_date
            if pos.is_closed and (cd is None or cd < cutoff) and pos.symbol in known:
                continue
            d = pos.open_date
            if pos.symbol not in out or d < out[pos.symbol]:
                out[pos.symbol] = d
    for s in repos.cash_accounts.symbols():  # money market funds: their yield comes with the profile
        out.setdefault(s, today)
    return out


def run_refresh(repos: Repos, provider: MarketDataProvider, targets: dict[str, date], today: date,
                force_daily: bool = False) -> RefreshResult:
    """Fetch and store market data. Quotes every time; history incremental; profile/dividends once a day."""
    res = RefreshResult(datetime.now().replace(microsecond=0), provider.name, sorted(targets))
    symbols = res.symbols
    if not symbols:
        res.finished = datetime.now().replace(microsecond=0)
        return res
    try:
        quotes = with_retry(provider.quotes, symbols)
    except ProviderError as e:
        res.errors.append(str(e))
        quotes = {}
    for s, q in quotes.items():
        if repos.market.upsert_quote(Quote(s, q.last, q.prev_close, q.as_of, provider.name)):
            res.quotes += 1
        else:
            res.pinned_skipped += 1
    missing = [s for s in symbols if s not in quotes]
    if quotes and missing:
        res.errors.append(f"No quote for {', '.join(missing)}")

    instruments = repos.market.get_instruments(symbols)
    div_checked: dict[str, str] = repos.settings.get("market.div_checked", {}) or {}
    split_checked: dict[str, str] = repos.settings.get("market.split_checked", {}) or {}
    for s in symbols:
        last = repos.market.last_bar_date(s)
        start = (last + timedelta(days=1)) if last else min(targets[s], today - timedelta(days=HISTORY_BACK_DAYS))
        if last and last >= today:
            start = today  # refresh today's partial bar
        if start <= today:
            try:
                bars = with_retry(provider.history, s, start, today)
                res.bars += repos.market.upsert_bars(s, bars)
            except ProviderError as e:
                res.errors.append(str(e))
        inst = instruments.get(s)
        if force_daily or inst is None or inst.updated_at is None or inst.updated_at.date() < today:
            try:
                p = with_retry(provider.profile, s)
                repos.market.upsert_instrument(Instrument(s, p.name, p.sector, p.industry, p.next_earnings,
                                                          p.div_yield, p.annual_dividend,
                                                          datetime.now().replace(microsecond=0),
                                                          ex_div_date=p.ex_div_date, div_pay_date=p.div_pay_date))
                res.profiles += 1
            except ProviderError as e:
                res.errors.append(str(e))
        if force_daily or div_checked.get(s) != today.isoformat():
            try:
                since = min(targets[s] - timedelta(days=7), today - timedelta(days=DIVIDEND_BACK_DAYS))
                divs = with_retry(provider.dividends, s, since)
                res.dividends += repos.market.upsert_dividends(
                    Dividend(d.symbol, d.ex_date, d.amount_per_share, provider.name, d.pay_date) for d in divs)
                div_checked[s] = today.isoformat()
            except ProviderError as e:
                res.errors.append(str(e))
        if hasattr(provider, "splits") and (force_daily or split_checked.get(s) != today.isoformat()):
            try:
                res.splits += repos.splits.upsert_market(
                    (sp.symbol, sp.ex_date, sp.ratio, provider.name) for sp in with_retry(provider.splits, s))
                split_checked[s] = today.isoformat()
            except ProviderError as e:
                res.errors.append(str(e))
    repos.settings.set("market.div_checked", div_checked)
    repos.settings.set("market.split_checked", split_checked)
    from ..portfolio_types.cash.service import sync_fund_yields

    sync_fund_yields(repos, provider.name, today)
    res.finished = datetime.now().replace(microsecond=0)
    status = repos.settings.get("market.status", {}) or {}
    status.update({"attempt": res.finished.isoformat(), "ok": res.ok, "source": provider.name,
                   "errors": res.errors[:20], "quotes": res.quotes})
    if res.ok and res.quotes:
        status["success"] = res.finished.isoformat()
    repos.settings.set("market.status", status)
    return res


def business_days_between(a: date, b: date) -> int:
    """Weekdays strictly after a up to and including b."""
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def clock(dt: datetime) -> str:
    """12-hour time with AM/PM, no leading zero: 8:59 PM."""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"


def price_status(status: dict[str, Any] | None, now: datetime, has_symbols: bool = True) -> tuple[str, str, str]:
    """(state, text, tooltip) for the header chip. state ∈ none | fresh | stale | error."""
    status = status or {}
    success = datetime.fromisoformat(status["success"]) if status.get("success") else None
    attempt = datetime.fromisoformat(status["attempt"]) if status.get("attempt") else None
    src = status.get("source", "")
    if success is None:
        if attempt is not None and not status.get("ok", True):
            return "error", "Prices failed", "Last refresh failed:\n" + "\n".join(status.get("errors", [])[:5])
        return "none", "Prices —", "No prices fetched yet. Press F5 to refresh." if has_symbols else "No positions"
    when = clock(success) if success.date() == now.date() else f"{success:%b} {success.day}, {clock(success)}"
    label = f"Prices updated {when}"
    tip = f"Source: {src}\nLast success: {success:%Y-%m-%d} {clock(success)}"
    if attempt and attempt >= success and not status.get("ok", True):
        errs = "\n".join(status.get("errors", [])[:5])
        return "error", label, f"{tip}\nLast attempt {attempt:%Y-%m-%d} {clock(attempt)} failed:\n{errs}"
    if business_days_between(success.date(), now.date()) > 1:
        return "stale", label, f"{tip}\nPrices are more than one trading day old."
    if status.get("errors"):
        tip += "\nSome requests failed:\n" + "\n".join(status["errors"][:5])
    return "fresh", label, tip


def in_market_hours(now_utc: datetime) -> bool:
    """Weekdays 09:30–16:00 America/New_York."""
    try:
        from zoneinfo import ZoneInfo

        ny = now_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        ny = now_utc - timedelta(hours=5)
    return ny.weekday() < 5 and time(9, 30) <= ny.time() <= time(16, 0)


class _Emitter(QObject):
    done = Signal(object)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)


class _Job(QRunnable):
    def __init__(self, fn: Callable[[], Any], emitter: _Emitter):
        super().__init__()
        self.fn = fn
        self.emitter = emitter
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn()
        except ProviderError as e:  # expected (offline, unknown symbol): no traceback in the log
            log.info("Market data: %s", e)
            result = e
        except Exception as e:
            log.exception("Background job failed")
            result = e
        try:
            self.emitter.done.emit(result)
        except RuntimeError:  # receiver went away (app shutting down)
            log.debug("Job finished after its emitter was deleted")


class MarketService(QObject):
    """Runs provider calls on the thread pool. Rule: worker code never touches the UI thread's SQLite connection
    (it opens its own via `repos_factory`); anything that reads settings is resolved before submitting."""

    started = Signal()
    refreshed = Signal(object)  # RefreshResult
    failed = Signal(list)  # list[str]
    busy_changed = Signal(bool)

    def __init__(self, db_path: Path, provider_factory: Callable[[], MarketDataProvider],
                 today_fn: Callable[[], date] = date.today, pool: QThreadPool | None = None,
                 repos_factory: Callable[[], Repos] | None = None, parent: QObject | None = None):
        super().__init__(parent)
        self.db_path = db_path
        self.provider_factory = provider_factory
        self.today_fn = today_fn
        self.pool = pool or QThreadPool.globalInstance()
        self.repos_factory = repos_factory or self._open_repos
        self._busy = False
        self._emitters: set[_Emitter] = set()

    def _open_repos(self) -> Repos:
        from ..data.db import connect

        return Repos.open(connect(self.db_path, migrate_db=False))

    @property
    def busy(self) -> bool:
        return self._busy

    def _submit(self, fn: Callable[[], Any], on_done: Callable[[Any], None]) -> None:
        em = _Emitter(self)
        self._emitters.add(em)

        def finish(result: Any) -> None:
            self._emitters.discard(em)
            em.deleteLater()
            on_done(result)

        em.done.connect(finish)
        self.pool.start(_Job(fn, em))

    def refresh(self, portfolio_ids: list[int] | None = None, force_daily: bool = False) -> bool:
        if self._busy:
            return False
        self._busy = True
        self.busy_changed.emit(True)
        self.started.emit()
        today = self.today_fn()
        provider = self.provider_factory()  # resolved on the UI thread (it may read settings)

        def work() -> RefreshResult:
            repos = self.repos_factory()
            try:
                targets = refresh_targets(repos, portfolio_ids, today)
                return run_refresh(repos, provider, targets, today, force_daily)
            finally:
                if repos is not None and self.repos_factory == self._open_repos:
                    repos.conn.close()

        self._submit(work, self._finished)
        return True

    def _finished(self, result: Any) -> None:
        self._busy = False
        self.busy_changed.emit(False)
        if isinstance(result, Exception):
            self.failed.emit([str(result)])
            return
        if not result.ok:
            self.failed.emit(result.errors or ["Refresh failed"])
        self.refreshed.emit(result)

    def validate_symbol(self, symbol: str, callback: Callable[[str, bool, str], None]) -> None:
        """Asynchronously ask the provider about a symbol; callback(symbol, ok, info_text) on the UI thread."""

        provider = self.provider_factory()

        def work():
            p = provider.profile(symbol)
            repos = self.repos_factory()  # keep what we learned (name, sector, earnings) for the table
            try:
                repos.market.upsert_instrument(Instrument(symbol, p.name, p.sector, p.industry, p.next_earnings,
                                                          p.div_yield, p.annual_dividend, ex_div_date=p.ex_div_date,
                                                          div_pay_date=p.div_pay_date))
            finally:
                if self.repos_factory == self._open_repos:
                    repos.conn.close()
            return " · ".join(x for x in (p.name, p.sector) if x)

        def done(result: Any) -> None:
            if isinstance(result, ProviderUnavailableError):
                callback(symbol, True, "")  # offline / manual mode: nothing to validate against
            elif isinstance(result, ProviderError):
                callback(symbol, False, str(result))
            elif isinstance(result, Exception):
                callback(symbol, True, "")  # network trouble: don't warn about the symbol
            else:
                callback(symbol, True, result)

        self._submit(work, done)

    def fetch_quote(self, symbol: str, callback: Callable[[Any], None]) -> None:
        """Fetch and store the current quote for one symbol (the trade dialog's price), callback(Quote | None |
        Exception) on the UI thread. A manual price pinned for the symbol is kept (the stored quote is returned)."""
        provider = self.provider_factory()

        def work():
            quotes = with_retry(provider.quotes, [symbol])
            repos = self.repos_factory()
            try:
                q = quotes.get(symbol)
                if q is not None:
                    repos.market.upsert_quote(Quote(symbol, q.last, q.prev_close, q.as_of, provider.name))
                return repos.market.get_quote(symbol)
            finally:
                if self.repos_factory == self._open_repos:
                    repos.conn.close()

        self._submit(work, callback)

    def find_symbols(self, query: str, callback: Callable[[Any], None]) -> None:
        """Candidate provider symbols for what the user typed: its share-class spellings that the provider knows
        (BRK.B → BRK-B, marked likely), then search results. callback(list[SymbolMatch] | Exception)."""
        provider = self.provider_factory()

        def work() -> list[SymbolMatch]:
            out: list[SymbolMatch] = []
            q = query.strip()
            for v in symbol_variants(q):
                try:
                    p = provider.profile(v)
                except ProviderUnavailableError:
                    raise
                except ProviderError:
                    continue
                out.append(SymbolMatch(v, p.name, None, None, likely=True))
            try:
                found = provider.search(q)
            except ProviderUnavailableError:
                if out:
                    return out
                raise
            seen = {m.symbol for m in out}
            for m in found:
                if m.symbol in seen:
                    out = [replace(x, exchange=m.exchange, kind=m.kind) if x.symbol == m.symbol else x for x in out]
                else:
                    seen.add(m.symbol)
                    out.append(m)
            return out

        self._submit(work, callback)

    def fetch_history(self, symbol: str, start: date, callback: Callable[[Any], None]) -> None:
        """Make sure price history from `start` is stored (for the candle chart); callback(n_bars | Exception)."""
        today = self.today_fn()
        provider = self.provider_factory()

        def work():
            repos = self.repos_factory()
            try:
                first = repos.market.bars(symbol, start, start + timedelta(days=10))
                last = repos.market.last_bar_date(symbol)
                n = 0
                if not first:
                    n += repos.market.upsert_bars(symbol, with_retry(provider.history, symbol, start,
                                                                     last or today))
                if last is None or last < today:
                    n += repos.market.upsert_bars(symbol, with_retry(provider.history, symbol,
                                                                     (last + timedelta(days=1)) if last else start,
                                                                     today))
                return n
            finally:
                if self.repos_factory == self._open_repos:
                    repos.conn.close()

        self._submit(work, callback)
