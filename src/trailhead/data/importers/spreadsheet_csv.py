"""Import the legacy trading spreadsheet exported as CSV (PLAN §10).

Columns (1-based letters): B date, C symbol, E shares, F price, I stop, K sell date, L exit price.
Each usable row becomes a BUY (+ a SELL of all shares when a sell date and exit price are present).
Blank rows, header rows and rows containing #DIV/0! (or other spreadsheet errors) are skipped.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ...core.models import Side, Trade
from ..repo import RepoError, Repos

COL = {"date": 1, "symbol": 2, "shares": 4, "price": 5, "stop": 8, "sell_date": 10, "exit": 11}
DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d.%m.%Y", "%Y/%m/%d", "%d-%b-%Y", "%d-%b-%y")


@dataclass
class ImportReport:
    buys: int = 0
    sells: int = 0
    skipped: list[str] = field(default_factory=list)


def parse_date(v: str) -> date | None:
    v = (v or "").strip()
    if not v:
        return None
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(v, f).date()
        except ValueError:
            continue
    return None


def parse_num(v: str) -> Decimal | None:
    v = (v or "").strip().replace("$", "").replace(",", "")
    if not v or v.startswith("#"):
        return None
    neg = v.startswith("(") and v.endswith(")")
    try:
        d = Decimal(v.strip("()"))
    except InvalidOperation:
        return None
    return -d if neg else d


def _cell(row: list[str], key: str) -> str:
    i = COL[key]
    return row[i] if i < len(row) else ""


def parse_rows(path: Path) -> tuple[list[dict], list[str]]:
    rows, skipped = [], []
    with path.open(newline="", encoding="utf-8-sig") as f:
        for n, row in enumerate(csv.reader(f), start=1):
            if not any(c.strip() for c in row):
                continue
            if any("#DIV/0!" in c or c.strip().startswith("#N/A") for c in row[:12]):
                skipped.append(f"Row {n}: spreadsheet error")
                continue
            d = parse_date(_cell(row, "date"))
            sym = _cell(row, "symbol").strip().upper()
            shares, price, stop = parse_num(_cell(row, "shares")), parse_num(_cell(row, "price")), parse_num(
                _cell(row, "stop"))
            if d is None or not sym:
                if n > 1 and any(c.strip() for c in row[:6]):
                    skipped.append(f"Row {n}: no date/symbol")
                continue
            if not shares or shares <= 0 or not price or price <= 0:
                skipped.append(f"Row {n} {sym}: missing shares or price")
                continue
            if stop is None or stop >= price or stop <= 0:
                skipped.append(f"Row {n} {sym}: missing or invalid stop")
                continue
            rows.append({"row": n, "date": d, "symbol": sym, "shares": shares, "price": price, "stop": stop,
                         "sell_date": parse_date(_cell(row, "sell_date")), "exit": parse_num(_cell(row, "exit"))})
    return rows, skipped


def import_csv(repos: Repos, portfolio_id: int, path: Path) -> ImportReport:
    rows, skipped = parse_rows(path)
    rep = ImportReport(skipped=skipped)
    for r in rows:
        try:
            buy = repos.trades.add(Trade(None, portfolio_id, r["symbol"], Side.BUY, r["date"], r["shares"], r["price"],
                                         initial_stop=r["stop"], notes=f"Imported (row {r['row']})"))
            rep.buys += 1
            if r["sell_date"] and r["exit"] and r["exit"] > 0:
                repos.trades.add(Trade(None, portfolio_id, r["symbol"], Side.SELL, r["sell_date"], r["shares"],
                                       r["exit"], lot_id=buy.id))
                rep.sells += 1
        except RepoError as e:
            rep.skipped.append(f"Row {r['row']} {r['symbol']}: {e}")
    return rep
