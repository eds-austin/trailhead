"""Backups, restore, JSON export/import (PLAN §10)."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import platformdirs

from ..core.models import CashEvent, CashKind, Instrument, Portfolio, Side, StopChange, StopReason, Trade
from .db import EXPECTED_TABLES, LATEST_VERSION, connect, schema_version, table_names
from .repo import Repos

BACKUP_PREFIX = "portfolio-"
JSON_FORMAT = "trailhead/portfolio"
LEGACY_JSON_FORMATS = ("portfolio-tracker/portfolio",)  # exports made before the rename
JSON_VERSION = 1
TRADE_FIELDS = ("id", "symbol", "side", "trade_date", "shares", "price", "initial_stop", "strategy", "timeframe",
                "lot_id", "notes", "exit_plan", "exit_reason", "asset_type", "target_price", "drip", "spinoff_of")


class BackupError(Exception):
    pass


def default_backup_dir() -> Path:
    return Path(platformdirs.user_documents_dir()) / "Trailhead Backups"


def backup_dir(repos: Repos) -> Path:
    d = repos.settings.get("backup.dir")
    return Path(d).expanduser() if d else default_backup_dir()


# ---------------------------------------------------------------- backup / rotate
def backup_to(conn: sqlite3.Connection, dest: Path) -> Path:
    """Hot copy using the SQLite backup API."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    out = sqlite3.connect(str(tmp))
    try:
        conn.backup(out)
    finally:
        out.close()
    tmp.replace(dest)
    return dest


def backup_now(conn: sqlite3.Connection, folder: Path, keep: int = 30, now: datetime | None = None) -> Path:
    """Write portfolio-YYYYMMDD-HHMMSS.db into folder and keep the newest `keep` copies."""
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise BackupError(f"Backup folder is unavailable: {folder} ({e})") from e
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    dest = folder / f"{BACKUP_PREFIX}{stamp}.db"
    n = 1
    while dest.exists():
        dest = folder / f"{BACKUP_PREFIX}{stamp}-{n}.db"
        n += 1
    try:
        backup_to(conn, dest)
    except (OSError, sqlite3.Error) as e:
        raise BackupError(f"Backup failed: {e}") from e
    rotate(folder, keep)
    return dest


def list_backups(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(folder.glob(f"{BACKUP_PREFIX}*.db"), key=lambda p: p.name, reverse=True)


def rotate(folder: Path, keep: int) -> list[Path]:
    removed = []
    for old in list_backups(folder)[max(1, keep):]:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------- restore
@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str
    version: int = 0
    portfolios: int = 0
    trades: int = 0


def validate_db(path: Path) -> ValidationResult:
    if not path.exists():
        return ValidationResult(False, "File not found")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return ValidationResult(False, f"Not a SQLite database: {e}")
    try:
        ic = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ic != "ok":
            return ValidationResult(False, f"Integrity check failed: {ic}")
        missing = EXPECTED_TABLES - table_names(conn)
        if missing:
            return ValidationResult(False, f"Not a Trailhead database (missing {', '.join(sorted(missing))})")
        v = schema_version(conn)
        if v > LATEST_VERSION:
            return ValidationResult(False, f"Backup is from a newer app version (schema v{v} > v{LATEST_VERSION})")
        np = conn.execute("SELECT COUNT(*) FROM portfolios").fetchone()[0]
        nt = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        return ValidationResult(True, "OK", v, np, nt)
    except sqlite3.Error as e:
        return ValidationResult(False, f"Could not read database: {e}")
    finally:
        conn.close()


def restore(source: Path, db_path: Path, current: sqlite3.Connection,
            safety_folder: Path) -> tuple[Path, sqlite3.Connection]:
    """Validate → safety backup of the current DB → swap files → migrate. Returns (safety copy, new connection).

    The caller must stop using `current` (it is closed here) and switch to the returned connection.
    """
    res = validate_db(source)
    if not res.ok:
        raise BackupError(res.message)
    safety = backup_now(current, safety_folder, keep=10_000)
    staged = db_path.with_suffix(".restore-tmp")
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(str(staged))
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    current.close()
    for suffix in ("-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    shutil.move(str(staged), str(db_path))
    conn = connect(db_path)  # runs migrations
    return safety, conn  # type: ignore[return-value]


# ---------------------------------------------------------------- JSON export / import
def _s(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    return v


def export_json(repos: Repos, portfolio_id: int) -> dict[str, Any]:
    p = repos.portfolios.get(portfolio_id)
    if p is None:
        raise BackupError(f"Portfolio {portfolio_id} not found")
    trades = repos.trades.list(portfolio_id)
    symbols = sorted({t.symbol for t in trades})
    return {
        "format": JSON_FORMAT,
        "version": JSON_VERSION,
        "exported_at": datetime.now().replace(microsecond=0).isoformat(),
        "portfolio": {"name": p.name, "type": p.type, "max_amount": str(p.max_amount), "risk_pct": str(p.risk_pct),
                      "settings": p.settings},
        "trades": [{k: _s(getattr(t, k)) for k in TRADE_FIELDS} for t in trades],
        "stop_changes": [{"lot_id": s.lot_id, "effective_date": _s(s.effective_date), "stop": _s(s.stop),
                          "reason": s.reason.value} for s in repos.stops.list_for_portfolio(portfolio_id)],
        "cash_events": [{"event_date": _s(e.event_date), "kind": e.kind.value, "amount": _s(e.amount),
                         "symbol": e.symbol, "dividend_ex_date": _s(e.dividend_ex_date), "note": e.note}
                        for e in repos.cash.list(portfolio_id)],
        "spinoffs": [{"parent": s.parent, "child": s.child, "ex_date": _s(s.ex_date), "cost_pct": _s(s.cost_pct),
                      "price_factor": _s(s.price_factor), "child_trade_id": s.child_trade_id}
                     for s in repos.spinoffs.list(portfolio_id)],
        "splits": [{"symbol": s.symbol, "ex_date": _s(s.ex_date), "ratio": _s(s.ratio), "status": s.status}
                   for s in repos.splits.list(portfolio_id)],
        "instruments": [{k: _s(getattr(i, k)) for k in ("symbol", "name", "sector", "industry",
                                                         "next_earnings_date", "div_yield", "annual_dividend")}
                        for i in repos.market.get_instruments(symbols).values()],
        "cash_accounts": _export_cash_accounts(repos, portfolio_id),
    }


def _export_cash_accounts(repos: Repos, portfolio_id: int) -> list[dict[str, Any]]:
    """Accounts of a Cash portfolio with their rates and ledger. A transfer to an account in another portfolio
    becomes a deposit / withdrawal (its other side isn't exported). Links to other portfolios aren't exported."""
    accounts = repos.cash_accounts.list(portfolio_id)
    ids = [a.id for a in accounts if a.id is not None]
    rates = repos.cash_accounts.rates(ids)
    txns = repos.cash_accounts.txns(ids)
    inside = {t.id for ts in txns.values() for t in ts}
    out = []
    for a in accounts:
        assert a.id is not None
        rows = []
        for t in txns[a.id]:
            kind, partner = t.kind.value, t.transfer_id
            if kind == "TRANSFER" and partner not in inside:
                kind, partner = ("DEPOSIT" if t.amount >= 0 else "WITHDRAWAL"), None
            rows.append({"id": t.id, "txn_date": _s(t.txn_date), "kind": kind, "amount": _s(t.amount),
                         "transfer_id": partner, "period_end": _s(t.period_end),
                         "true_up_balance": _s(t.true_up_balance), "note": t.note})
        out.append({"name": a.name, "kind": a.kind.value, "institution": a.institution, "symbol": a.symbol,
                    "manual_rate": a.manual_rate, "payout_day": a.payout_day, "opened_on": _s(a.opened_on),
                    "closed": a.closed, "notes": a.notes,
                    "rates": [{"effective_date": _s(r.effective_date), "apy": _s(r.apy), "source": r.source}
                              for r in rates[a.id]],
                    "txns": rows})
    return out


def write_json(data: dict[str, Any], path: Path) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _d(v) -> Decimal | None:
    return None if v in (None, "") else Decimal(str(v))


def _date(v) -> date | None:
    return date.fromisoformat(v[:10]) if v else None


def unique_name(existing: set[str], name: str) -> str:
    if name not in existing:
        return name
    candidate = f"{name} (imported)"
    n = 2
    while candidate in existing:
        candidate = f"{name} (imported {n})"
        n += 1
    return candidate


def import_json(repos: Repos, data: dict[str, Any]) -> Portfolio:
    """Always creates a NEW portfolio (renamed '(imported)' if needed) with all ids remapped. Atomic."""
    if data.get("format") not in (JSON_FORMAT, *LEGACY_JSON_FORMATS):
        raise BackupError("Not a Trailhead portfolio export")
    if int(data.get("version", 0)) > JSON_VERSION:
        raise BackupError("Export is from a newer app version")
    pd = data["portfolio"]
    conn = repos.conn
    if conn.in_transaction:
        conn.commit()
    conn.execute("BEGIN")
    try:
        name = unique_name(repos.portfolios.names(), pd["name"])
        order = conn.execute("SELECT COALESCE(MAX(sort_order), -1) + 1 FROM portfolios").fetchone()[0]
        created = datetime.now().replace(microsecond=0)
        cur = conn.execute(
            "INSERT INTO portfolios (name, type, max_amount, risk_pct, settings_json, archived, sort_order, created_at)"
            " VALUES (?,?,?,?,?,0,?,?)", (name, pd.get("type", "stock_trading"), str(Decimal(pd["max_amount"])),
                                          str(Decimal(pd["risk_pct"])), json.dumps(pd.get("settings") or {}), order,
                                          created.isoformat()))
        p = Portfolio(cur.lastrowid, name, Decimal(pd["max_amount"]), Decimal(pd["risk_pct"]),
                      type=pd.get("type", "stock_trading"), settings=pd.get("settings") or {}, sort_order=order,
                      created_at=created)
        assert p.id is not None
        # splits first: sells after a split are validated in post-split shares
        for s in data.get("splits", []):
            conn.execute("INSERT INTO splits (portfolio_id, symbol, ex_date, ratio, status, created_at)"
                         " VALUES (?,?,?,?,?,?)", (p.id, s["symbol"], s["ex_date"], s["ratio"], s["status"],
                                                  created.isoformat()))
        id_map: dict[int, int] = {}
        rows = sorted(data.get("trades", []), key=lambda t: (t["side"] != "BUY", t["trade_date"], t["id"]))
        for t in rows:
            side = Side(t["side"])
            lot = None
            if side == Side.SELL:
                if t.get("lot_id") not in id_map:
                    raise BackupError(f"SELL {t['id']} references unknown lot {t.get('lot_id')}")
                lot = id_map[t["lot_id"]]
            new = repos.trades._insert(Trade(None, p.id, t["symbol"], side, _date(t["trade_date"]),  # type: ignore[arg-type]
                                             _d(t["shares"]), _d(t["price"]), _d(t.get("initial_stop")),  # type: ignore[arg-type]
                                             t.get("strategy"), t.get("timeframe"), lot, t.get("notes"),
                                             exit_plan=t.get("exit_plan"), exit_reason=t.get("exit_reason"),
                                             asset_type=t.get("asset_type"), target_price=_d(t.get("target_price")),
                                             drip=bool(t.get("drip")), spinoff_of=t.get("spinoff_of")))
            id_map[t["id"]] = new.id  # type: ignore[assignment]
        for s in data.get("spinoffs", []):
            conn.execute("INSERT INTO spinoffs (portfolio_id, parent, child, ex_date, cost_pct, price_factor,"
                         " child_trade_id, created_at) VALUES (?,?,?,?,?,?,?,?)",
                         (p.id, s["parent"], s["child"], s["ex_date"], s["cost_pct"], s.get("price_factor"),
                          id_map.get(s.get("child_trade_id")), created.isoformat()))
        for s in data.get("stop_changes", []):
            if s["lot_id"] not in id_map:
                continue
            conn.execute("INSERT INTO stop_changes (lot_id, effective_date, stop, reason, created_at)"
                         " VALUES (?,?,?,?,?)", (id_map[s["lot_id"]], s["effective_date"], s["stop"],
                                                 StopReason(s["reason"]).value, datetime.now().isoformat()))
        for e in data.get("cash_events", []):
            conn.execute("INSERT INTO cash_events (portfolio_id, event_date, kind, amount, symbol, dividend_ex_date,"
                         " note) VALUES (?,?,?,?,?,?,?)", (p.id, e["event_date"], CashKind(e["kind"]).value,
                                                          e["amount"], e.get("symbol"), e.get("dividend_ex_date"),
                                                          e.get("note")))
        _import_cash_accounts(conn, p.id, data.get("cash_accounts", []), created)
        for i in data.get("instruments", []):
            existing = repos.market.get_instruments([i["symbol"]])
            if i["symbol"] not in existing:
                conn.execute("INSERT INTO instruments (symbol, name, sector, industry, next_earnings_date, div_yield,"
                             " annual_dividend, updated_at) VALUES (?,?,?,?,?,?,?,NULL)",
                             (i["symbol"], i.get("name"), i.get("sector"), i.get("industry"),
                              i.get("next_earnings_date"), i.get("div_yield"), i.get("annual_dividend")))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return p


def _import_cash_accounts(conn: sqlite3.Connection, pid: int, accounts: list[dict[str, Any]], created: datetime
                          ) -> None:
    txn_map: dict[int, int] = {}
    pairs: list[tuple[int, int]] = []
    for order, a in enumerate(accounts):
        aid = conn.execute(
            "INSERT INTO cash_accounts (portfolio_id, name, kind, institution, symbol, manual_rate, payout_day,"
            " opened_on, closed, sort_order, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, a["name"], a["kind"], a.get("institution"), a.get("symbol"), int(bool(a.get("manual_rate"))),
             int(a.get("payout_day") or 0), a["opened_on"], int(bool(a.get("closed"))), order, a.get("notes"),
             created.isoformat())).lastrowid
        for r in a.get("rates", []):
            conn.execute("INSERT INTO cash_rates (account_id, effective_date, apy, source) VALUES (?,?,?,?)",
                         (aid, r["effective_date"], str(Decimal(r["apy"])), r.get("source") or "MANUAL"))
        for t in a.get("txns", []):
            new = conn.execute(
                "INSERT INTO cash_txns (account_id, txn_date, kind, amount, period_end, true_up_balance, note,"
                " created_at) VALUES (?,?,?,?,?,?,?,?)",
                (aid, t["txn_date"], t["kind"], str(Decimal(t["amount"])), t.get("period_end"),
                 t.get("true_up_balance"), t.get("note"), created.isoformat())).lastrowid
            txn_map[t["id"]] = new
            if t.get("transfer_id") is not None:
                pairs.append((new, t["transfer_id"]))
    for new, old_partner in pairs:
        conn.execute("UPDATE cash_txns SET transfer_id=? WHERE id=?", (txn_map.get(old_partner), new))


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BackupError(f"Could not read {path.name}: {e}") from e


__all__ = ["BackupError", "CashEvent", "Instrument", "StopChange", "backup_dir", "backup_now", "backup_to",
           "default_backup_dir", "export_json", "import_json", "list_backups", "read_json", "restore", "rotate",
           "unique_name", "validate_db", "write_json"]
