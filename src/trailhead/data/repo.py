"""Repositories: the only place that runs SQL. They speak core dataclasses."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..core.models import (
    AlertRecord,
    Bar,
    CashAccount,
    CashAccountKind,
    CashEvent,
    CashKind,
    CashLink,
    CashRate,
    CashTxn,
    CashTxnKind,
    Dividend,
    Instrument,
    Portfolio,
    Quote,
    Side,
    StopChange,
    StopReason,
    Trade,
)
from ..core.money import D_or_none
from ..core.spinoffs import Spinoff
from ..core.splits import APPLIED, IGNORED, Split


class RepoError(ValueError):
    pass


def now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _d(v) -> Decimal | None:
    return D_or_none(v)


def _date(v) -> date | None:
    return date.fromisoformat(v[:10]) if v else None


def _dt(v) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


class _Repo:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn


# ---------------------------------------------------------------- portfolios
def _portfolio(r: sqlite3.Row) -> Portfolio:
    return Portfolio(
        id=r["id"], name=r["name"], type=r["type"], max_amount=_d(r["max_amount"]),  # type: ignore[arg-type]
        risk_pct=_d(r["risk_pct"]),  # type: ignore[arg-type]
        settings=json.loads(r["settings_json"] or "{}"), archived=bool(r["archived"]),
        sort_order=r["sort_order"], created_at=_dt(r["created_at"]),
    )


class PortfolioRepo(_Repo):
    def create(self, p: Portfolio) -> Portfolio:
        created = p.created_at or now()
        with self.conn:
            order = self.conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM portfolios").fetchone()[0]
            cur = self.conn.execute(
                "INSERT INTO portfolios (name, type, max_amount, risk_pct, settings_json, archived, sort_order,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)",
                (p.name, p.type, p.max_amount, p.risk_pct, json.dumps(p.settings), int(p.archived), order, created),
            )
        return replace(p, id=cur.lastrowid, sort_order=order, created_at=created)

    def get(self, pid: int) -> Portfolio | None:
        r = self.conn.execute("SELECT * FROM portfolios WHERE id=?", (pid,)).fetchone()
        return _portfolio(r) if r else None

    def list(self, include_archived: bool = False) -> list[Portfolio]:
        sql = "SELECT * FROM portfolios"
        if not include_archived:
            sql += " WHERE archived=0"
        return [_portfolio(r) for r in self.conn.execute(sql + " ORDER BY sort_order, id")]

    def update(self, p: Portfolio) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE portfolios SET name=?, type=?, max_amount=?, risk_pct=?, settings_json=?, archived=?,"
                " sort_order=? WHERE id=?",
                (p.name, p.type, p.max_amount, p.risk_pct, json.dumps(p.settings), int(p.archived), p.sort_order,
                 p.id),
            )

    def names(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT name FROM portfolios")}

    def delete(self, pid: int) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM stop_changes WHERE lot_id IN (SELECT id FROM trades WHERE portfolio_id=?)", (pid,))
            self.conn.execute("DELETE FROM trades WHERE portfolio_id=? AND side='SELL'", (pid,))
            self.conn.execute("DELETE FROM trades WHERE portfolio_id=?", (pid,))
            self.conn.execute("DELETE FROM cash_events WHERE portfolio_id=?", (pid,))
            self.conn.execute("DELETE FROM splits WHERE portfolio_id=?", (pid,))
            self.conn.execute("DELETE FROM spinoffs WHERE portfolio_id=?", (pid,))
            self.conn.execute("DELETE FROM alerts_log WHERE portfolio_id=?", (pid,))
            self.conn.execute("DELETE FROM portfolios WHERE id=?", (pid,))


# ---------------------------------------------------------------- trades
def _trade(r: sqlite3.Row) -> Trade:
    return Trade(
        id=r["id"], portfolio_id=r["portfolio_id"], symbol=r["symbol"], side=Side(r["side"]),
        trade_date=_date(r["trade_date"]), shares=_d(r["shares"]), price=_d(r["price"]),  # type: ignore[arg-type]
        initial_stop=_d(r["initial_stop"]), strategy=r["strategy"], timeframe=r["timeframe"], lot_id=r["lot_id"],
        notes=r["notes"], created_at=_dt(r["created_at"]), updated_at=_dt(r["updated_at"]),
        exit_plan=r["exit_plan"], exit_reason=r["exit_reason"], asset_type=r["asset_type"],
        target_price=_d(r["target_price"]), drip=bool(r["drip"]), spinoff_of=r["spinoff_of"],
    )


# Portfolio types whose BUYs must carry an initial stop below the price (R-multiple trading).
STOP_REQUIRED_TYPES = {"stock_trading"}


class TradeRepo(_Repo):
    def _portfolio_type(self, portfolio_id: int) -> str | None:
        r = self.conn.execute("SELECT type FROM portfolios WHERE id=?", (portfolio_id,)).fetchone()
        return r[0] if r else None

    def _validate(self, t: Trade) -> None:
        if t.shares <= 0 or t.price <= 0:
            raise RepoError("Shares and price must be positive")
        if t.side == Side.BUY:
            if self._portfolio_type(t.portfolio_id) in STOP_REQUIRED_TYPES:
                if t.initial_stop is None:
                    raise RepoError("A BUY needs an initial stop")
                if t.initial_stop >= t.price:
                    raise RepoError("Initial stop must be below the price")
            elif t.initial_stop is not None and t.initial_stop <= 0:
                raise RepoError("A stop must be positive")
            if t.target_price is not None and t.target_price <= 0:
                raise RepoError("A target must be positive")
            if t.lot_id is not None:
                raise RepoError("A BUY cannot reference a lot")
        else:
            if t.lot_id is None:
                raise RepoError("A SELL must reference a lot")
            lot = self.get(t.lot_id)
            if lot is None or lot.side != Side.BUY:
                raise RepoError(f"Lot {t.lot_id} does not exist")
            if lot.symbol != t.symbol or lot.portfolio_id != t.portfolio_id:
                raise RepoError("SELL symbol/portfolio does not match its lot")
            # compare in today's shares: a lot bought before a recorded split holds more shares after it
            f = self._factor(t.portfolio_id, t.symbol)
            sold = self._sold(t.lot_id, f, exclude=t.id)
            open_now = lot.shares * f(lot.trade_date) - sold
            if t.shares * f(t.trade_date) > open_now:
                raise RepoError(f"That {lot.symbol} lot has only {open_now / f(t.trade_date):,f} shares open"
                                f" on {t.trade_date.isoformat()}")
            if t.trade_date < lot.trade_date:
                raise RepoError("A SELL cannot be dated before its lot's BUY")

    def _insert(self, t: Trade) -> Trade:
        self._validate(t)
        ts = now()
        cur = self.conn.execute(
            "INSERT INTO trades (portfolio_id, symbol, side, trade_date, shares, price, initial_stop, strategy,"
            " timeframe, lot_id, notes, created_at, updated_at, exit_plan, exit_reason, asset_type, target_price,"
            " drip, spinoff_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (t.portfolio_id, t.symbol.upper(), t.side.value, t.trade_date, t.shares, t.price, t.initial_stop,
             t.strategy, t.timeframe, t.lot_id, t.notes, ts, ts, t.exit_plan if t.side == Side.BUY else None,
             t.exit_reason if t.side == Side.SELL else None, *self._buy_extras(t)),
        )
        return replace(t, id=cur.lastrowid, symbol=t.symbol.upper(), created_at=ts, updated_at=ts)

    def _factor(self, portfolio_id: int, symbol: str):
        """d → factor that turns shares on day d into today's shares (applied splits only)."""
        rows = [(_date(r[0]), _d(r[1])) for r in self.conn.execute(
            "SELECT ex_date, ratio FROM splits WHERE portfolio_id=? AND symbol=? AND status='APPLIED'",
            (portfolio_id, symbol.upper()))]

        def f(d: date) -> Decimal:
            out = Decimal(1)
            for ex, ratio in rows:
                if ex > d:  # type: ignore[operator]
                    out *= ratio  # type: ignore[operator]
            return out
        return f

    def _sold(self, lot_id: int, f, exclude: int | None = None) -> Decimal:
        """Shares sold from a lot, in today's shares."""
        rows = self.conn.execute("SELECT shares, trade_date FROM trades WHERE lot_id=? AND id IS NOT ?",
                                 (lot_id, exclude)).fetchall()
        return sum((_d(r[0]) * f(_date(r[1])) for r in rows), Decimal(0))  # type: ignore[operator]

    @staticmethod
    def _buy_extras(t: Trade) -> tuple:
        if t.side != Side.BUY:
            return None, None, 0, None
        return t.asset_type, t.target_price, int(t.drip), t.spinoff_of

    def add(self, t: Trade) -> Trade:
        with self.conn:
            return self._insert(t)

    def add_many(self, trades: Sequence[Trade]) -> list[Trade]:
        """Insert several trades atomically (e.g. a sell split across lots)."""
        with self.conn:
            return [self._insert(t) for t in trades]

    def update(self, t: Trade) -> Trade:
        if t.id is None:
            raise RepoError("Trade has no id")
        old = self.get(t.id)
        if old is None:
            raise RepoError(f"Trade {t.id} not found")
        if old.side != t.side:
            raise RepoError("Cannot change a trade's side; delete it and add a new one")
        if t.side == Side.BUY:
            f = self._factor(t.portfolio_id, t.symbol)
            sold = self._sold(t.id, f)  # type: ignore[arg-type]
            if sold > t.shares * f(t.trade_date):
                raise RepoError(f"{sold / f(t.trade_date):,f} shares were already sold from this lot")
            if old.symbol != t.symbol.upper() and sold:
                raise RepoError("Cannot change the symbol of a lot that has sells")
        self._validate(t)
        ts = now()
        with self.conn:
            self.conn.execute(
                "UPDATE trades SET symbol=?, trade_date=?, shares=?, price=?, initial_stop=?, strategy=?, timeframe=?,"
                " lot_id=?, notes=?, updated_at=?, exit_plan=?, exit_reason=?, asset_type=?, target_price=?, drip=?,"
                " spinoff_of=? WHERE id=?",
                (t.symbol.upper(), t.trade_date, t.shares, t.price, t.initial_stop, t.strategy, t.timeframe,
                 t.lot_id, t.notes, ts, t.exit_plan if t.side == Side.BUY else None,
                 t.exit_reason if t.side == Side.SELL else None, *self._buy_extras(t), t.id),
            )
        return replace(t, symbol=t.symbol.upper(), updated_at=ts)

    def set_levels(self, lot_ids: Sequence[int], stop: Decimal | None, target: Decimal | None) -> None:
        """Long-term Holdings: set (or clear, with None) the stop and profit target on BUY lots."""
        if (stop is not None and stop <= 0) or (target is not None and target <= 0):
            raise RepoError("Stop and target must be positive")
        ts = now()
        with self.conn:
            self.conn.executemany(
                "UPDATE trades SET initial_stop=?, target_price=?, updated_at=? WHERE id=? AND side='BUY'",
                [(stop, target, ts, i) for i in lot_ids])

    def delete(self, tid: int) -> None:
        n = self.conn.execute("SELECT COUNT(*) FROM trades WHERE lot_id=?", (tid,)).fetchone()[0]
        if n:
            raise RepoError("This BUY has sells. Delete its SELL trades first.")
        with self.conn:
            self.conn.execute("DELETE FROM trades WHERE id=?", (tid,))

    def get(self, tid: int) -> Trade | None:
        r = self.conn.execute("SELECT * FROM trades WHERE id=?", (tid,)).fetchone()
        return _trade(r) if r else None

    def list(self, portfolio_id: int) -> list[Trade]:
        return [_trade(r) for r in self.conn.execute(
            "SELECT * FROM trades WHERE portfolio_id=? ORDER BY trade_date, id", (portfolio_id,))]

    def rename_symbol(self, old: str, new: str) -> int:
        """Change a symbol everywhere it is used (trades, dividends, reviews, alerts) in every portfolio, e.g. to
        the data provider's spelling (BRK.B → BRK-B). A sector entered by hand moves with it. Returns trades
        changed."""
        old, new = old.strip().upper(), new.strip().upper()
        if not new or old == new:
            return 0
        with self.conn:
            n = self.conn.execute("UPDATE trades SET symbol=?, updated_at=? WHERE symbol=?", (new, now(), old)).rowcount
            self.conn.execute("UPDATE cash_events SET symbol=? WHERE symbol=?", (new, old))
            self.conn.execute("UPDATE OR IGNORE dividend_reviews SET symbol=? WHERE symbol=?", (new, old))
            self.conn.execute("UPDATE alerts_log SET symbol=? WHERE symbol=?", (new, old))
            self.conn.execute("UPDATE OR IGNORE splits SET symbol=? WHERE symbol=?", (new, old))
            self.conn.execute("UPDATE trades SET spinoff_of=? WHERE spinoff_of=?", (new, old))
            self.conn.execute("UPDATE OR IGNORE spinoffs SET parent=? WHERE parent=?", (new, old))
            self.conn.execute("UPDATE spinoffs SET child=? WHERE child=?", (new, old))
            r = self.conn.execute("SELECT sector FROM instruments WHERE symbol=? AND sector_manual=1",
                                  (old,)).fetchone()
            if r is not None:
                self.conn.execute(
                    "INSERT INTO instruments (symbol, sector, sector_manual, updated_at) VALUES (?,?,1,NULL)"
                    " ON CONFLICT(symbol) DO UPDATE SET sector=excluded.sector, sector_manual=1", (new, r[0]))
        return n

    def symbols(self, portfolio_id: int | None = None) -> list[str]:
        if portfolio_id is None:
            rows = self.conn.execute("SELECT DISTINCT symbol FROM trades ORDER BY symbol")
        else:
            rows = self.conn.execute(
                "SELECT DISTINCT symbol FROM trades WHERE portfolio_id=? ORDER BY symbol", (portfolio_id,))
        return [r[0] for r in rows]


# ---------------------------------------------------------------- stops
def _stop(r: sqlite3.Row) -> StopChange:
    return StopChange(id=r["id"], lot_id=r["lot_id"], effective_date=_date(r["effective_date"]),  # type: ignore[arg-type]
                      stop=_d(r["stop"]), reason=StopReason(r["reason"]),  # type: ignore[arg-type]
                      created_at=_dt(r["created_at"]))


class StopRepo(_Repo):
    def add(self, s: StopChange) -> StopChange:
        ts = s.created_at or now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO stop_changes (lot_id, effective_date, stop, reason, created_at) VALUES (?,?,?,?,?)",
                (s.lot_id, s.effective_date, s.stop, s.reason.value, ts))
        return replace(s, id=cur.lastrowid, created_at=ts)

    def delete(self, sid: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM stop_changes WHERE id=?", (sid,))

    def list_for_portfolio(self, portfolio_id: int) -> list[StopChange]:
        return [_stop(r) for r in self.conn.execute(
            "SELECT s.* FROM stop_changes s JOIN trades t ON t.id = s.lot_id WHERE t.portfolio_id=?"
            " ORDER BY s.effective_date, s.id", (portfolio_id,))]

    def list_for_lot(self, lot_id: int) -> list[StopChange]:
        return [_stop(r) for r in self.conn.execute(
            "SELECT * FROM stop_changes WHERE lot_id=? ORDER BY effective_date, id", (lot_id,))]


# ---------------------------------------------------------------- cash
def _cash(r: sqlite3.Row) -> CashEvent:
    return CashEvent(id=r["id"], portfolio_id=r["portfolio_id"], event_date=_date(r["event_date"]),  # type: ignore[arg-type]
                     kind=CashKind(r["kind"]), amount=_d(r["amount"]), symbol=r["symbol"],  # type: ignore[arg-type]
                     dividend_ex_date=_date(r["dividend_ex_date"]), note=r["note"])


class CashRepo(_Repo):
    def add(self, e: CashEvent) -> CashEvent:
        amount = e.amount
        if e.kind == CashKind.WITHDRAWAL and amount > 0:
            amount = -amount
        if e.kind in (CashKind.DEPOSIT, CashKind.DIVIDEND) and amount < 0:
            raise RepoError(f"A {e.kind.value.lower()} must be positive")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO cash_events (portfolio_id, event_date, kind, amount, symbol, dividend_ex_date, note)"
                " VALUES (?,?,?,?,?,?,?)",
                (e.portfolio_id, e.event_date, e.kind.value, amount, e.symbol, e.dividend_ex_date, e.note))
        return replace(e, id=cur.lastrowid, amount=amount)

    def update(self, e: CashEvent) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE cash_events SET event_date=?, kind=?, amount=?, symbol=?, dividend_ex_date=?, note=?"
                " WHERE id=?", (e.event_date, e.kind.value, e.amount, e.symbol, e.dividend_ex_date, e.note, e.id))

    def delete(self, cid: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM cash_events WHERE id=?", (cid,))

    def list(self, portfolio_id: int) -> list[CashEvent]:
        return [_cash(r) for r in self.conn.execute(
            "SELECT * FROM cash_events WHERE portfolio_id=? ORDER BY event_date, id", (portfolio_id,))]


# ---------------------------------------------------------------- cash accounts (Cash portfolio type)
def _account(r: sqlite3.Row) -> CashAccount:
    return CashAccount(id=r["id"], portfolio_id=r["portfolio_id"], name=r["name"], kind=CashAccountKind(r["kind"]),
                       opened_on=_date(r["opened_on"]), institution=r["institution"],  # type: ignore[arg-type]
                       symbol=r["symbol"], manual_rate=bool(r["manual_rate"]), payout_day=r["payout_day"],
                       closed=bool(r["closed"]), sort_order=r["sort_order"], notes=r["notes"],
                       created_at=_dt(r["created_at"]))


def _txn(r: sqlite3.Row) -> CashTxn:
    return CashTxn(id=r["id"], account_id=r["account_id"], txn_date=_date(r["txn_date"]),  # type: ignore[arg-type]
                   kind=CashTxnKind(r["kind"]), amount=_d(r["amount"]),  # type: ignore[arg-type]
                   transfer_id=r["transfer_id"], period_end=_date(r["period_end"]),
                   true_up_balance=_d(r["true_up_balance"]), note=r["note"], created_at=_dt(r["created_at"]))


_POSITIVE = (CashTxnKind.OPENING, CashTxnKind.DEPOSIT)


class CashAccountRepo(_Repo):
    # accounts
    def create(self, a: CashAccount, opening_balance: Decimal, apy: Decimal | None = None) -> CashAccount:
        """A new account with its opening balance (dated opened_on) and, for savings, its APY from that date."""
        if not a.name.strip():
            raise RepoError("Enter a name")
        if opening_balance < 0:
            raise RepoError("The opening balance can't be negative")
        created = now()
        with self.conn:
            order = self.conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM cash_accounts WHERE"
                                      " portfolio_id=?", (a.portfolio_id,)).fetchone()[0]
            cur = self.conn.execute(
                "INSERT INTO cash_accounts (portfolio_id, name, kind, institution, symbol, manual_rate, payout_day,"
                " opened_on, closed, sort_order, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (a.portfolio_id, a.name.strip(), a.kind.value, a.institution, a.symbol, int(a.manual_rate),
                 a.payout_day, a.opened_on,
                 int(a.closed), order, a.notes, created))
            aid = cur.lastrowid
            self.conn.execute("INSERT INTO cash_txns (account_id, txn_date, kind, amount, created_at)"
                              " VALUES (?,?,?,?,?)", (aid, a.opened_on, CashTxnKind.OPENING.value, opening_balance,
                                                      created))
            if apy is not None:
                self.conn.execute("INSERT INTO cash_rates (account_id, effective_date, apy, source) VALUES"
                                  " (?,?,?,'MANUAL')", (aid, a.opened_on, apy))
        return replace(a, id=aid, sort_order=order, created_at=created)

    def update(self, a: CashAccount) -> None:
        if not a.name.strip():
            raise RepoError("Enter a name")
        with self.conn:
            self.conn.execute(
                "UPDATE cash_accounts SET name=?, kind=?, institution=?, symbol=?, manual_rate=?, payout_day=?,"
                " opened_on=?, closed=?, sort_order=?, notes=? WHERE id=?",
                (a.name.strip(), a.kind.value, a.institution, a.symbol, int(a.manual_rate), a.payout_day, a.opened_on,
                 int(a.closed), a.sort_order, a.notes, a.id))
            self.conn.execute("UPDATE cash_txns SET txn_date=? WHERE account_id=? AND kind='OPENING'",
                              (a.opened_on, a.id))

    def delete(self, account_id: int) -> None:
        with self.conn:
            # the other half of a transfer into or out of this account becomes a plain deposit / withdrawal
            self.conn.execute(
                "UPDATE cash_txns SET kind=CASE WHEN amount >= 0 THEN 'DEPOSIT' ELSE 'WITHDRAWAL' END,"
                " transfer_id=NULL WHERE transfer_id IN (SELECT id FROM cash_txns WHERE account_id=?)",
                (account_id,))
            self.conn.execute("DELETE FROM cash_accounts WHERE id=?", (account_id,))

    def get(self, account_id: int) -> CashAccount | None:
        r = self.conn.execute("SELECT * FROM cash_accounts WHERE id=?", (account_id,)).fetchone()
        return _account(r) if r else None

    def list(self, portfolio_id: int | None = None) -> list[CashAccount]:
        if portfolio_id is None:
            rows = self.conn.execute(
                "SELECT a.* FROM cash_accounts a JOIN portfolios p ON p.id = a.portfolio_id WHERE p.archived = 0"
                " ORDER BY p.sort_order, a.sort_order, a.id")
        else:
            rows = self.conn.execute("SELECT * FROM cash_accounts WHERE portfolio_id=? ORDER BY sort_order, id",
                                     (portfolio_id,))
        return [_account(r) for r in rows]

    def symbols(self) -> list[str]:
        """Money market fund symbols of open accounts in active portfolios (refreshed with prices)."""
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT a.symbol FROM cash_accounts a JOIN portfolios p ON p.id = a.portfolio_id"
            " WHERE a.kind='MONEY_MARKET' AND a.symbol IS NOT NULL AND a.closed=0 AND p.archived=0")]

    # rates
    def rates(self, account_ids: Iterable[int]) -> dict[int, list[CashRate]]:
        ids = list(account_ids)
        out: dict[int, list[CashRate]] = {i: [] for i in ids}
        if not ids:
            return out
        for r in self.conn.execute(f"SELECT * FROM cash_rates WHERE account_id IN ({_ph(len(ids))})"
                                   " ORDER BY effective_date", ids):
            out[r["account_id"]].append(CashRate(r["account_id"], _date(r["effective_date"]),  # type: ignore[arg-type]
                                                 _d(r["apy"]), r["source"]))  # type: ignore[arg-type]
        return out

    def set_rate(self, rate: CashRate) -> None:
        if rate.apy < 0:
            raise RepoError("The rate can't be negative")
        with self.conn:
            self.conn.execute("INSERT INTO cash_rates (account_id, effective_date, apy, source) VALUES (?,?,?,?)"
                              " ON CONFLICT(account_id, effective_date) DO UPDATE SET apy=excluded.apy,"
                              " source=excluded.source", (rate.account_id, rate.effective_date, rate.apy,
                                                          rate.source))

    def delete_rate(self, account_id: int, effective_date: date) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM cash_rates WHERE account_id=? AND effective_date=?",
                              (account_id, effective_date))

    # transactions
    @staticmethod
    def _signed(t: CashTxn) -> Decimal:
        amount = t.amount
        if t.kind == CashTxnKind.WITHDRAWAL:
            return -abs(amount)
        if t.kind in _POSITIVE and amount < 0:
            raise RepoError(f"A{'n' if t.kind == CashTxnKind.OPENING else ''} {t.kind.value.lower()} amount "
                            "must be positive")
        if t.kind == CashTxnKind.INTEREST and amount < 0 and not t.is_true_up:
            raise RepoError("Interest can't be negative")
        return amount

    def add(self, t: CashTxn) -> CashTxn:
        if t.kind == CashTxnKind.TRANSFER:
            raise RepoError("Use transfer() for transfers")
        amount = self._signed(t)
        created = now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO cash_txns (account_id, txn_date, kind, amount, period_end, true_up_balance, note,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)",
                (t.account_id, t.txn_date, t.kind.value, amount, t.period_end, t.true_up_balance, t.note, created))
        return replace(t, id=cur.lastrowid, amount=amount, created_at=created)

    def transfer(self, from_id: int, to_id: int, on: date, amount: Decimal, note: str | None = None
                 ) -> tuple[CashTxn, CashTxn]:
        if from_id == to_id:
            raise RepoError("Choose two different accounts")
        if amount <= 0:
            raise RepoError("Enter an amount above zero")
        created = now()
        with self.conn:
            out_id = self.conn.execute(
                "INSERT INTO cash_txns (account_id, txn_date, kind, amount, note, created_at) VALUES"
                " (?,?,'TRANSFER',?,?,?)", (from_id, on, -amount, note, created)).lastrowid
            in_id = self.conn.execute(
                "INSERT INTO cash_txns (account_id, txn_date, kind, amount, transfer_id, note, created_at) VALUES"
                " (?,?,'TRANSFER',?,?,?,?)", (to_id, on, amount, out_id, note, created)).lastrowid
            self.conn.execute("UPDATE cash_txns SET transfer_id=? WHERE id=?", (in_id, out_id))
        return (CashTxn(out_id, from_id, on, CashTxnKind.TRANSFER, -amount, in_id, note=note, created_at=created),
                CashTxn(in_id, to_id, on, CashTxnKind.TRANSFER, amount, out_id, note=note, created_at=created))

    def update_txn(self, t: CashTxn) -> CashTxn:
        """Date, amount and note of a transaction. A transfer's other half follows (opposite amount)."""
        amount = t.amount if t.kind == CashTxnKind.TRANSFER else self._signed(t)
        with self.conn:
            self.conn.execute("UPDATE cash_txns SET txn_date=?, kind=?, amount=?, period_end=?, true_up_balance=?,"
                              " note=? WHERE id=?", (t.txn_date, t.kind.value, amount, t.period_end,
                                                    t.true_up_balance, t.note, t.id))
            if t.kind == CashTxnKind.TRANSFER and t.transfer_id:
                self.conn.execute("UPDATE cash_txns SET txn_date=?, amount=?, note=? WHERE id=?",
                                  (t.txn_date, -amount, t.note, t.transfer_id))
        return replace(t, amount=amount)

    def delete_txn(self, txn_id: int) -> None:
        with self.conn:
            r = self.conn.execute("SELECT kind, transfer_id FROM cash_txns WHERE id=?", (txn_id,)).fetchone()
            if r is None:
                return
            if r["kind"] == CashTxnKind.OPENING.value:
                raise RepoError("The opening balance can't be deleted; edit it or delete the account")
            if r["transfer_id"]:
                self.conn.execute("DELETE FROM cash_txns WHERE id=?", (r["transfer_id"],))
            self.conn.execute("DELETE FROM cash_txns WHERE id=?", (txn_id,))

    def get_txn(self, txn_id: int) -> CashTxn | None:
        r = self.conn.execute("SELECT * FROM cash_txns WHERE id=?", (txn_id,)).fetchone()
        return _txn(r) if r else None

    def txns(self, account_ids: Iterable[int]) -> dict[int, list[CashTxn]]:
        ids = list(account_ids)
        out: dict[int, list[CashTxn]] = {i: [] for i in ids}
        if not ids:
            return out
        for r in self.conn.execute(f"SELECT * FROM cash_txns WHERE account_id IN ({_ph(len(ids))})"
                                   " ORDER BY txn_date, id", ids):
            out[r["account_id"]].append(_txn(r))
        return out

    # links to Stock Trading / Long-term portfolios
    def link(self, link: CashLink) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO cash_links (portfolio_id, account_id, since) VALUES (?,?,?)"
                              " ON CONFLICT(portfolio_id) DO UPDATE SET account_id=excluded.account_id,"
                              " since=excluded.since", (link.portfolio_id, link.account_id, link.since))

    def unlink(self, portfolio_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM cash_links WHERE portfolio_id=?", (portfolio_id,))

    def link_for(self, portfolio_id: int) -> CashLink | None:
        r = self.conn.execute("SELECT * FROM cash_links WHERE portfolio_id=?", (portfolio_id,)).fetchone()
        return CashLink(r["portfolio_id"], r["account_id"], _date(r["since"])) if r else None  # type: ignore[arg-type]

    def links(self, account_ids: Iterable[int] | None = None) -> list[CashLink]:
        if account_ids is None:
            rows = self.conn.execute("SELECT * FROM cash_links")
        else:
            ids = list(account_ids)
            if not ids:
                return []
            rows = self.conn.execute(f"SELECT * FROM cash_links WHERE account_id IN ({_ph(len(ids))})", ids)
        return [CashLink(r["portfolio_id"], r["account_id"], _date(r["since"])) for r in rows]  # type: ignore[arg-type]


# ---------------------------------------------------------------- market
def _quote(r: sqlite3.Row) -> Quote:
    keys = r.keys()
    return Quote(symbol=r["symbol"], last=_d(r["last"]), prev_close=_d(r["prev_close"]),  # type: ignore[arg-type]
                 as_of=_dt(r["as_of"]), source=r["source"], is_manual=bool(r["is_manual"]),  # type: ignore[arg-type]
                 pinned=bool(r["pinned"]) if "pinned" in keys else False)


def _instrument(r: sqlite3.Row) -> Instrument:
    return Instrument(symbol=r["symbol"], name=r["name"], sector=r["sector"], industry=r["industry"],
                      next_earnings_date=_date(r["next_earnings_date"]), div_yield=_d(r["div_yield"]),
                      annual_dividend=_d(r["annual_dividend"]), updated_at=_dt(r["updated_at"]),
                      sector_manual=bool(r["sector_manual"]), ex_div_date=_date(r["ex_div_date"]),
                      div_pay_date=_date(r["div_pay_date"]))


def _ph(n: int) -> str:
    return ",".join("?" * n)


class MarketRepo(_Repo):
    # quotes
    def get_quotes(self, symbols: Iterable[str] | None = None) -> dict[str, Quote]:
        if symbols is None:
            rows = self.conn.execute("SELECT * FROM quotes")
        else:
            syms = list(symbols)
            if not syms:
                return {}
            rows = self.conn.execute(f"SELECT * FROM quotes WHERE symbol IN ({_ph(len(syms))})", syms)
        return {r["symbol"]: _quote(r) for r in rows}

    def get_quote(self, symbol: str) -> Quote | None:
        return self.get_quotes([symbol]).get(symbol)

    def upsert_quote(self, q: Quote, *, respect_pinned: bool = True) -> bool:
        """Write a quote. A fetched quote never replaces a pinned manual override. Returns True if written."""
        if respect_pinned and not q.is_manual:
            cur = self.get_quote(q.symbol)
            if cur is not None and cur.is_manual and cur.pinned:
                return False
        with self.conn:
            self.conn.execute(
                "INSERT INTO quotes (symbol, last, prev_close, as_of, source, is_manual, pinned)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET last=excluded.last,"
                " prev_close=excluded.prev_close, as_of=excluded.as_of, source=excluded.source,"
                " is_manual=excluded.is_manual, pinned=excluded.pinned",
                (q.symbol, q.last, q.prev_close, q.as_of, q.source, int(q.is_manual), int(q.pinned)))
        return True

    def clear_manual(self, symbol: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE quotes SET is_manual=0, pinned=0 WHERE symbol=?", (symbol,))

    # instruments
    def get_instruments(self, symbols: Iterable[str] | None = None) -> dict[str, Instrument]:
        if symbols is None:
            rows = self.conn.execute("SELECT * FROM instruments")
        else:
            syms = list(symbols)
            if not syms:
                return {}
            rows = self.conn.execute(f"SELECT * FROM instruments WHERE symbol IN ({_ph(len(syms))})", syms)
        return {r["symbol"]: _instrument(r) for r in rows}

    def upsert_instrument(self, i: Instrument) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO instruments (symbol, name, sector, industry, next_earnings_date, div_yield,"
                " annual_dividend, updated_at, ex_div_date, div_pay_date) VALUES (?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(symbol) DO UPDATE SET"
                " name=COALESCE(excluded.name, name),"
                " sector=CASE WHEN sector_manual=1 THEN sector ELSE COALESCE(excluded.sector, sector) END,"
                " industry=COALESCE(excluded.industry, industry), next_earnings_date=excluded.next_earnings_date,"
                " div_yield=excluded.div_yield, annual_dividend=excluded.annual_dividend,"
                " updated_at=excluded.updated_at, ex_div_date=excluded.ex_div_date,"
                " div_pay_date=excluded.div_pay_date",
                (i.symbol, i.name, i.sector, i.industry, i.next_earnings_date, i.div_yield, i.annual_dividend,
                 i.updated_at or now(), i.ex_div_date, i.div_pay_date))

    def set_sector(self, symbol: str, sector: str | None) -> None:
        """Set the sector by hand (locked against refreshes), or clear it (None) so the provider fills it again."""
        manual = 1 if sector else 0
        with self.conn:
            self.conn.execute(
                "INSERT INTO instruments (symbol, sector, sector_manual, updated_at) VALUES (?,?,?,NULL)"
                " ON CONFLICT(symbol) DO UPDATE SET sector=excluded.sector, sector_manual=excluded.sector_manual",
                (symbol, sector or None, manual))
            if not sector:  # let the next refresh fetch the profile again
                self.conn.execute("UPDATE instruments SET updated_at=NULL WHERE symbol=?", (symbol,))

    # price history
    def upsert_bars(self, symbol: str, bars: Iterable[Bar]) -> int:
        rows = [(symbol, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars]
        with self.conn:
            self.conn.executemany(
                "INSERT INTO price_history (symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(symbol, date) DO UPDATE SET open=excluded.open, high=excluded.high,"
                " low=excluded.low, close=excluded.close, volume=excluded.volume", rows)
        return len(rows)

    def last_bar_date(self, symbol: str) -> date | None:
        v = self.conn.execute("SELECT MAX(date) FROM price_history WHERE symbol=?", (symbol,)).fetchone()[0]
        return _date(v)

    def bars(self, symbol: str, start: date | None = None, end: date | None = None) -> list[Bar]:
        sql = "SELECT * FROM price_history WHERE symbol=?"
        args: list[Any] = [symbol]
        if start:
            sql += " AND date>=?"
            args.append(start)
        if end:
            sql += " AND date<=?"
            args.append(end)
        return [Bar(_date(r["date"]), _d(r["open"]), _d(r["high"]), _d(r["low"]), _d(r["close"]),  # type: ignore[arg-type]
                    r["volume"]) for r in self.conn.execute(sql + " ORDER BY date", args)]

    def highs(self, symbols: Iterable[str], since: date | None = None) -> dict[str, list[tuple[date, Decimal | None]]]:
        out: dict[str, list[tuple[date, Decimal | None]]] = {}
        syms = list(symbols)
        if not syms:
            return out
        sql = f"SELECT symbol, date, high FROM price_history WHERE symbol IN ({_ph(len(syms))})"
        args: list[Any] = list(syms)
        if since:
            sql += " AND date>=?"
            args.append(since)
        for r in self.conn.execute(sql + " ORDER BY date", args):
            out.setdefault(r["symbol"], []).append((_date(r["date"]), _d(r["high"])))  # type: ignore[arg-type]
        return out

    def closes(self, symbols: Iterable[str], since: date | None = None) -> dict[str, dict[date, Decimal]]:
        out: dict[str, dict[date, Decimal]] = {}
        syms = list(symbols)
        if not syms:
            return out
        sql = f"SELECT symbol, date, close FROM price_history WHERE symbol IN ({_ph(len(syms))})"
        args: list[Any] = list(syms)
        if since:
            sql += " AND date>=?"
            args.append(since)
        for r in self.conn.execute(sql + " ORDER BY date", args):
            c = _d(r["close"])
            if c is not None:
                out.setdefault(r["symbol"], {})[_date(r["date"])] = c  # type: ignore[index]
        return out

    # dividends
    def upsert_dividends(self, divs: Iterable[Dividend]) -> int:
        rows = [(d.symbol, d.ex_date, d.pay_date, d.amount_per_share, d.source) for d in divs]
        with self.conn:
            self.conn.executemany(
                "INSERT INTO dividends (symbol, ex_date, pay_date, amount_per_share, source) VALUES (?,?,?,?,?)"
                " ON CONFLICT(symbol, ex_date) DO UPDATE SET pay_date=COALESCE(excluded.pay_date, pay_date),"
                " amount_per_share=excluded.amount_per_share, source=excluded.source", rows)
        return len(rows)

    def dividends(self, symbols: Iterable[str] | None = None, since: date | None = None) -> list[Dividend]:
        sql = "SELECT * FROM dividends WHERE 1=1"
        args: list[Any] = []
        if symbols is not None:
            syms = list(symbols)
            if not syms:
                return []
            sql += f" AND symbol IN ({_ph(len(syms))})"
            args += syms
        if since:
            sql += " AND ex_date>=?"
            args.append(since)
        return [Dividend(r["symbol"], _date(r["ex_date"]), _d(r["amount_per_share"]), r["source"],  # type: ignore[arg-type]
                         _date(r["pay_date"])) for r in self.conn.execute(sql + " ORDER BY ex_date", args)]


# ---------------------------------------------------------------- splits
class SplitRepo(_Repo):
    def market(self, symbols: Iterable[str] | None = None) -> list[Split]:
        """Splits reported by the data provider."""
        sql, args = "SELECT * FROM market_splits", []
        if symbols is not None:
            syms = list(symbols)
            if not syms:
                return []
            sql += f" WHERE symbol IN ({_ph(len(syms))})"
            args = syms
        return [Split(r["symbol"], _date(r["ex_date"]), _d(r["ratio"])) for r in  # type: ignore[arg-type]
                self.conn.execute(sql + " ORDER BY ex_date", args)]

    def upsert_market(self, splits: Iterable[tuple[str, date, Decimal, str]]) -> int:
        rows = list(splits)
        with self.conn:
            self.conn.executemany(
                "INSERT INTO market_splits (symbol, ex_date, ratio, source) VALUES (?,?,?,?) ON CONFLICT(symbol,"
                " ex_date) DO UPDATE SET ratio=excluded.ratio, source=excluded.source", rows)
        return len(rows)

    def record(self, s: Split) -> Split:
        if s.ratio <= 0:
            raise RepoError("A split ratio must be positive")
        if s.status not in (APPLIED, IGNORED):
            raise RepoError(f"Unknown split status {s.status}")
        with self.conn:
            self.conn.execute(
                "INSERT INTO splits (portfolio_id, symbol, ex_date, ratio, status, created_at) VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(portfolio_id, symbol, ex_date) DO UPDATE SET ratio=excluded.ratio,"
                " status=excluded.status", (s.portfolio_id, s.symbol.upper(), s.ex_date, s.ratio, s.status, now()))
        return replace(s, symbol=s.symbol.upper())

    def delete(self, portfolio_id: int, symbol: str, ex_date: date) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM splits WHERE portfolio_id=? AND symbol=? AND ex_date=?",
                              (portfolio_id, symbol, ex_date))

    def list(self, portfolio_id: int) -> list[Split]:
        return [Split(r["symbol"], _date(r["ex_date"]), _d(r["ratio"]), r["status"], r["portfolio_id"])  # type: ignore[arg-type]
                for r in self.conn.execute("SELECT * FROM splits WHERE portfolio_id=? ORDER BY ex_date, symbol",
                                           (portfolio_id,))]


# ---------------------------------------------------------------- spin-offs
def _spinoff(r: sqlite3.Row) -> Spinoff:
    return Spinoff(r["parent"], r["child"], _date(r["ex_date"]), _d(r["cost_pct"]), _d(r["price_factor"]),  # type: ignore[arg-type]
                   r["child_trade_id"], r["portfolio_id"])


class SpinoffRepo(_Repo):
    def add(self, s: Spinoff) -> Spinoff:
        if not (0 < s.cost_pct < 1):
            raise RepoError("The share of cost moved must be between 0 and 100%")
        with self.conn:
            self.conn.execute(
                "INSERT INTO spinoffs (portfolio_id, parent, child, ex_date, cost_pct, price_factor, child_trade_id,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)", (s.portfolio_id, s.parent, s.child, s.ex_date, s.cost_pct,
                                                         s.price_factor, s.child_trade_id, now()))
        return s

    def delete(self, portfolio_id: int, parent: str, ex_date: date) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM spinoffs WHERE portfolio_id=? AND parent=? AND ex_date=?",
                              (portfolio_id, parent, ex_date))

    def list(self, portfolio_id: int) -> list[Spinoff]:
        return [_spinoff(r) for r in self.conn.execute(
            "SELECT * FROM spinoffs WHERE portfolio_id=? ORDER BY ex_date, parent", (portfolio_id,))]


# ---------------------------------------------------------------- alerts
def _alert(r: sqlite3.Row) -> AlertRecord:
    return AlertRecord(id=r["id"], portfolio_id=r["portfolio_id"], symbol=r["symbol"], lot_id=r["lot_id"],
                       kind=r["kind"], level=_d(r["level"]), message=r["message"],
                       triggered_at=_dt(r["triggered_at"]), acknowledged=bool(r["acknowledged"]))  # type: ignore[arg-type]


class AlertRepo(_Repo):
    def insert_if_new(self, a: AlertRecord) -> AlertRecord | None:
        """Insert unless an alert with the same (portfolio, symbol, lot, kind, level) exists. NULL-safe dedup."""
        exists = self.conn.execute(
            "SELECT 1 FROM alerts_log WHERE portfolio_id=? AND symbol=? AND lot_id IS ? AND kind=? AND level IS ?",
            (a.portfolio_id, a.symbol, a.lot_id, a.kind, a.level)).fetchone()
        if exists:
            return None
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO alerts_log (portfolio_id, symbol, lot_id, kind, level, message, triggered_at,"
                " acknowledged) VALUES (?,?,?,?,?,?,?,0)",
                (a.portfolio_id, a.symbol, a.lot_id, a.kind, a.level, a.message, a.triggered_at))
        if cur.rowcount == 0:
            return None
        return replace(a, id=cur.lastrowid)

    def list(self, portfolio_id: int | None = None, unacked_only: bool = False) -> list[AlertRecord]:
        sql = "SELECT * FROM alerts_log WHERE 1=1"
        args: list[Any] = []
        if portfolio_id is not None:
            sql += " AND portfolio_id=?"
            args.append(portfolio_id)
        if unacked_only:
            sql += " AND acknowledged=0"
        return [_alert(r) for r in self.conn.execute(sql + " ORDER BY triggered_at DESC, id DESC", args)]

    def acknowledge(self, aid: int) -> None:
        with self.conn:
            self.conn.execute("UPDATE alerts_log SET acknowledged=1 WHERE id=?", (aid,))

    def acknowledge_all(self, portfolio_id: int | None = None) -> None:
        with self.conn:
            if portfolio_id is None:
                self.conn.execute("UPDATE alerts_log SET acknowledged=1")
            else:
                self.conn.execute("UPDATE alerts_log SET acknowledged=1 WHERE portfolio_id=?", (portfolio_id,))

    def count_unacked(self, portfolio_id: int | None = None) -> int:
        if portfolio_id is None:
            return self.conn.execute("SELECT COUNT(*) FROM alerts_log WHERE acknowledged=0").fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM alerts_log WHERE acknowledged=0 AND portfolio_id=?", (portfolio_id,)).fetchone()[0]


# ---------------------------------------------------------------- dividend reviews
class DividendReviewRepo(_Repo):
    def dismiss(self, portfolio_id: int, symbol: str, ex_date: date) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO dividend_reviews (portfolio_id, symbol, ex_date, status, reviewed_at)"
                " VALUES (?,?,?,'DISMISSED',?)", (portfolio_id, symbol, ex_date, now()))

    def dismissed(self, portfolio_id: int) -> set[tuple[str, date]]:
        return {(r[0], _date(r[1])) for r in self.conn.execute(  # type: ignore[misc]
            "SELECT symbol, ex_date FROM dividend_reviews WHERE portfolio_id=?", (portfolio_id,))}


# ---------------------------------------------------------------- settings
SETTINGS_DEFAULTS: dict[str, Any] = {
    "backup.dir": None,  # resolved at runtime to documents/Trailhead Backups
    "backup.keep": 30,
    "refresh.on_startup": True,
    "refresh.auto_minutes": 0,
    "alerts.near_stop_pct": 0.02,
    "alerts.earnings_days": 7,
    "alerts.desktop": True,
    "alerts.open_risk_warn_pct": 0.06,
    "ui.table_density": "compact",
    "market.provider": "yfinance",
    "market.api_key": "",
}


class SettingsRepo(_Repo):
    def get(self, key: str, default: Any = None) -> Any:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if r is None:
            return SETTINGS_DEFAULTS.get(key, default) if default is None else default
        try:
            return json.loads(r[0])
        except json.JSONDecodeError:
            return r[0]

    def set(self, key: str, value: Any) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)))

    def delete(self, key: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM settings WHERE key=?", (key,))

    def all(self) -> dict[str, Any]:
        out = dict(SETTINGS_DEFAULTS)
        for r in self.conn.execute("SELECT key, value FROM settings"):
            try:
                out[r[0]] = json.loads(r[1])
            except json.JSONDecodeError:
                out[r[0]] = r[1]
        return out


@dataclass
class Repos:
    conn: sqlite3.Connection
    portfolios: PortfolioRepo
    trades: TradeRepo
    stops: StopRepo
    cash: CashRepo
    market: MarketRepo
    alerts: AlertRepo
    settings: SettingsRepo
    dividend_reviews: DividendReviewRepo
    splits: SplitRepo
    spinoffs: SpinoffRepo
    cash_accounts: CashAccountRepo

    @classmethod
    def open(cls, conn: sqlite3.Connection) -> Repos:
        return cls(conn, PortfolioRepo(conn), TradeRepo(conn), StopRepo(conn), CashRepo(conn), MarketRepo(conn),
                   AlertRepo(conn), SettingsRepo(conn), DividendReviewRepo(conn), SplitRepo(conn),
                   SpinoffRepo(conn), CashAccountRepo(conn))
