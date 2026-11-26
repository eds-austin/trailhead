import json
from datetime import date, datetime
from decimal import Decimal as D

import pytest

from trailhead.core.models import (
    CashEvent,
    CashKind,
    Instrument,
    Portfolio,
    Side,
    StopChange,
    StopReason,
    Trade,
)
from trailhead.data.backup import (
    BackupError,
    backup_now,
    export_json,
    import_json,
    list_backups,
    restore,
    validate_db,
)
from trailhead.data.db import connect
from trailhead.data.importers.spreadsheet_csv import import_csv
from trailhead.data.repo import Repos


def _fill(repos):
    p = repos.portfolios.create(Portfolio(None, "Swing", D(100000), D("0.01")))
    repos.cash.add(CashEvent(None, p.id, date(2016, 7, 29), CashKind.DEPOSIT, D(100000), note="seed"))
    b1 = repos.trades.add(Trade(None, p.id, "W", Side.BUY, date(2016, 7, 29), D(50), D("43.18"),
                                initial_stop=D("40.26"), timeframe="Weekly/Daily", notes="first"))
    b2 = repos.trades.add(Trade(None, p.id, "W", Side.BUY, date(2016, 7, 29), D(50), D("43.18"),
                                initial_stop=D("40.26")))
    repos.trades.add(Trade(None, p.id, "W", Side.SELL, date(2016, 8, 2), D(50), D("46.24"), lot_id=b1.id))
    repos.trades.add(Trade(None, p.id, "W", Side.SELL, date(2016, 8, 4), D(25), D("49.01"), lot_id=b2.id))
    repos.stops.add(StopChange(None, b2.id, date(2016, 8, 3), D("41.72"), StopReason.LADDER_CONFIRMED))
    repos.market.upsert_instrument(Instrument("W", "Wayfair", "Consumer Discretionary"))
    return p


def test_backup_rotation(tmp_path):
    repos = Repos.open(connect(tmp_path / "a.db"))
    _fill(repos)
    folder = tmp_path / "bk"
    for i in range(5):
        backup_now(repos.conn, folder, keep=3, now=datetime(2026, 11, 20, 10, 0, i))
    names = [p.name for p in list_backups(folder)]
    assert names == ["portfolio-20261120-100004.db", "portfolio-20261120-100003.db", "portfolio-20261120-100002.db"]
    v = validate_db(folder / names[0])
    assert v.ok and v.portfolios == 1 and v.trades == 4


def test_unavailable_folder(tmp_path):
    repos = Repos.open(connect(tmp_path / "a.db"))
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(BackupError):
        backup_now(repos.conn, blocker / "sub", keep=3)


def test_validate_rejects_garbage(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not a database at all" * 100)
    assert not validate_db(bad).ok
    import sqlite3

    other = tmp_path / "other.db"
    sqlite3.connect(other).execute("CREATE TABLE x (a)").connection.close()
    assert "missing" in validate_db(other).message


def test_restore_with_safety_backup(tmp_path):
    db = tmp_path / "live.db"
    repos = Repos.open(connect(db))
    _fill(repos)
    snap = backup_now(repos.conn, tmp_path / "bk", keep=5)
    repos.portfolios.create(Portfolio(None, "Later", D(1)))
    assert len(repos.portfolios.list()) == 2
    safety, conn = restore(snap, db, repos.conn, tmp_path / "safety")
    assert safety.exists() and validate_db(safety).portfolios == 2
    restored = Repos.open(conn)
    assert [p.name for p in restored.portfolios.list()] == ["Swing"]
    conn.close()


def test_json_roundtrip_remaps_ids(tmp_path):
    repos = Repos.open(connect(tmp_path / "a.db"))
    p = _fill(repos)
    data = json.loads(json.dumps(export_json(repos, p.id)))
    # import into the same DB → renamed + new ids
    q = import_json(repos, data)
    assert q.name == "Swing (imported)" and q.id != p.id
    t_old, t_new = repos.trades.list(p.id), repos.trades.list(q.id)
    assert len(t_new) == 4
    assert {t.id for t in t_old}.isdisjoint({t.id for t in t_new})
    new_ids = {t.id for t in t_new if t.side == Side.BUY}
    assert all(t.lot_id in new_ids for t in t_new if t.side == Side.SELL)
    assert [(s.stop, s.reason) for s in repos.stops.list_for_portfolio(q.id)] == [(D("41.72"),
                                                                                  StopReason.LADDER_CONFIRMED)]
    assert repos.cash.list(q.id)[0].note == "seed"
    strip = lambda d: {k: v for k, v in d.items() if k not in ("exported_at",)}  # noqa: E731
    again = export_json(repos, q.id)
    again["portfolio"]["name"] = "Swing"
    for a, b in zip(data["trades"], again["trades"], strict=True):
        assert {k: v for k, v in a.items() if k not in ("id", "lot_id")} == \
               {k: v for k, v in b.items() if k not in ("id", "lot_id")}
    assert strip(data)["cash_events"] == strip(again)["cash_events"]
    assert import_json(repos, data).name == "Swing (imported 2)"
    # into a fresh DB
    other = Repos.open(connect(tmp_path / "b.db"))
    assert import_json(other, data).name == "Swing"
    assert other.market.get_instruments(["W"])["W"].name == "Wayfair"
    # exports made under the old app name still import
    data["format"] = "portfolio-tracker/portfolio"
    assert import_json(other, data).name == "Swing (imported)"


def test_json_import_is_atomic(tmp_path):
    repos = Repos.open(connect(tmp_path / "a.db"))
    p = _fill(repos)
    data = export_json(repos, p.id)
    data["trades"][-1]["lot_id"] = 999
    with pytest.raises(BackupError):
        import_json(repos, data)
    assert len(repos.portfolios.list()) == 1
    with pytest.raises(BackupError):
        import_json(repos, {"format": "x"})


def test_legacy_csv(tmp_path):
    csv_path = tmp_path / "legacy.csv"
    csv_path.write_text(
        ",Date,Symbol,TF,Shares,Entry,x,x,Stop,R,Sell date,Exit\n"
        ",7/29/2016,W,W,50,43.18,,,40.26,2.92,8/2/2016,46.24\n"
        ",7/29/2016,W,W,50,43.18,,,40.26,2.92,8/4/2016,49.01\n"
        ",,,,,,,,,,,\n"
        ",#DIV/0!,,,,,,,,,,\n"
        ",8/10/2016,NVDA,D,10,60,,,58,2,,\n"
        ",8/11/2016,BAD,D,10,60,,,61,2,,\n", encoding="utf-8")
    repos = Repos.open(connect(tmp_path / "a.db"))
    p = repos.portfolios.create(Portfolio(None, "Legacy", D(100000)))
    rep = import_csv(repos, p.id, csv_path)
    assert (rep.buys, rep.sells) == (3, 2)
    assert len(rep.skipped) == 2
    from trailhead.core.positions import build_lots
    from trailhead.core.stats import closed_lot_units, trade_stats

    st = trade_stats(closed_lot_units(build_lots(repos.trades.list(p.id))))
    assert round(st.expectancy, 2) == D("1.52")


def test_json_roundtrip_long_term_fields(tmp_path):
    repos = Repos.open(connect(tmp_path / "a.db"))
    p = repos.portfolios.create(Portfolio(None, "Hold", D(0), type="long_term"))
    repos.trades.add(Trade(None, p.id, "GLD", Side.BUY, date(2026, 1, 5), D("2.5"), D(180),
                           asset_type="Precious metals", target_price=D(250)))
    repos.trades.add(Trade(None, p.id, "KO", Side.BUY, date(2026, 2, 5), D("0.75"), D(68), asset_type="Income",
                           drip=True))
    data = json.loads(json.dumps(export_json(repos, p.id)))
    other = Repos.open(connect(tmp_path / "b.db"))
    q = import_json(other, data)
    assert q.type == "long_term"
    got = [(t.symbol, t.initial_stop, t.asset_type, t.target_price, t.drip) for t in other.trades.list(q.id)]
    assert got == [("GLD", None, "Precious metals", D(250), False), ("KO", None, "Income", None, True)]


def test_json_roundtrip_splits_and_spinoffs(tmp_path):
    from trailhead.core.spinoffs import Spinoff
    from trailhead.core.splits import APPLIED, Split

    repos = Repos.open(connect(tmp_path / "a.db"))
    p = repos.portfolios.create(Portfolio(None, "Hold", D(0), type="long_term"))
    b = repos.trades.add(Trade(None, p.id, "BRK-B", Side.BUY, date(1998, 5, 4), D(6), D("2034.50")))
    repos.splits.record(Split("BRK-B", date(2010, 1, 21), D(50), APPLIED, p.id))
    repos.trades.add(Trade(None, p.id, "BRK-B", Side.SELL, date(2026, 2, 1), D(100), D(500), lot_id=b.id))
    c = repos.trades.add(Trade(None, p.id, "FDXF", Side.BUY, date(2026, 6, 1), D(40), D("97.1"), spinoff_of="FDX"))
    repos.spinoffs.add(Spinoff("FDX", "FDXF", date(2026, 6, 1), D("0.1942"), D("1.241"), c.id, p.id))
    data = json.loads(json.dumps(export_json(repos, p.id)))
    other = Repos.open(connect(tmp_path / "b.db"))
    q = import_json(other, data)  # the post-split sell imports because the split comes first
    (s,) = other.splits.list(q.id)
    assert (s.symbol, s.ratio, s.status) == ("BRK-B", D(50), APPLIED)
    (so,) = other.spinoffs.list(q.id)
    child = other.trades.get(so.child_trade_id)
    assert (child.symbol, child.spinoff_of, so.cost_pct) == ("FDXF", "FDX", D("0.1942"))


def test_json_roundtrip_cash_accounts(tmp_path):
    from trailhead.core.models import CashAccount, CashAccountKind, CashRate, CashTxn, CashTxnKind

    repos = Repos.open(connect(tmp_path / "a.db"))
    p = repos.portfolios.create(Portfolio(None, "Cash", D(0), type="cash"))
    elsewhere = repos.portfolios.create(Portfolio(None, "Cash 2", D(0), type="cash"))
    ca = repos.cash_accounts
    a = ca.create(CashAccount(None, p.id, "Ally", CashAccountKind.SAVINGS, date(2026, 1, 1), payout_day=15),
                  D(1000), D("0.04"))
    b = ca.create(CashAccount(None, p.id, "Fund", CashAccountKind.MONEY_MARKET, date(2026, 1, 1), symbol="SPAXX"),
                  D(50))
    c = ca.create(CashAccount(None, elsewhere.id, "Other", CashAccountKind.SAVINGS, date(2026, 1, 1)), D(0))
    ca.set_rate(CashRate(a.id, date(2026, 3, 1), D("0.035")))
    ca.transfer(a.id, b.id, date(2026, 2, 1), D(100))
    ca.transfer(a.id, c.id, date(2026, 2, 2), D(10))
    ca.add(CashTxn(None, a.id, date(2026, 1, 15), CashTxnKind.INTEREST, D("1.60"), period_end=date(2026, 1, 15)))
    ca.add(CashTxn(None, a.id, date(2026, 2, 20), CashTxnKind.ADJUSTMENT, D("-0.5"), true_up_balance=D("891.10")))
    data = json.loads(json.dumps(export_json(repos, p.id)))
    other = Repos.open(connect(tmp_path / "b.db"))
    q = import_json(other, data)
    accts = other.cash_accounts.list(q.id)
    assert [(x.name, x.kind, x.symbol, x.payout_day) for x in accts] == [
        ("Ally", CashAccountKind.SAVINGS, None, 15), ("Fund", CashAccountKind.MONEY_MARKET, "SPAXX", 0)]
    ally, fund = accts
    assert [r.apy for r in other.cash_accounts.rates([ally.id])[ally.id]] == [D("0.04"), D("0.035")]
    tx = other.cash_accounts.txns([ally.id, fund.id])
    kinds = [(t.kind, t.amount) for t in tx[ally.id]]
    assert kinds == [(CashTxnKind.OPENING, D(1000)), (CashTxnKind.INTEREST, D("1.60")),
                     (CashTxnKind.TRANSFER, D(-100)), (CashTxnKind.WITHDRAWAL, D(-10)),
                     (CashTxnKind.ADJUSTMENT, D("-0.5"))]
    out = tx[ally.id][2]
    inc = next(t for t in tx[fund.id] if t.kind == CashTxnKind.TRANSFER)
    assert out.transfer_id == inc.id and inc.transfer_id == out.id
    assert tx[ally.id][1].period_end == date(2026, 1, 15) and tx[ally.id][4].true_up_balance == D("891.10")
