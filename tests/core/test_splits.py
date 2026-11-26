from datetime import date
from decimal import Decimal as D

import pytest

from trailhead.core.models import Portfolio, Side, StopChange, Trade
from trailhead.core.splits import (
    APPLIED,
    IGNORED,
    Split,
    adjust_stops,
    adjust_trades,
    clean_ratio,
    looks_like_spinoff,
    pending_splits,
    ratio_label,
    split_factor,
)
from trailhead.data.db import connect
from trailhead.data.repo import RepoError, Repos

BRK_SPLIT = Split("BRK-B", date(2010, 1, 21), D(50))


def test_ratio_label():
    assert ratio_label(D(50)) == "50-for-1"
    assert ratio_label(D("0.1")) == "1-for-10"
    assert ratio_label(D("1.5")) == "3-for-2"
    assert ratio_label(D("1.253")) == "1.253-for-1"


def test_adjust_trades_keeps_cost_and_marks_factor():
    buy = Trade(1, 1, "BRK-B", Side.BUY, date(1998, 5, 4), D(6), D("2034.50"), initial_stop=D(1800))
    after = Trade(2, 1, "BRK-B", Side.BUY, date(2010, 1, 21), D(10), D(70))  # on the ex-date: post-split
    a, b = adjust_trades([buy, after], [BRK_SPLIT])
    assert (a.shares, a.price, a.initial_stop, a.split_factor) == (D(300), D("40.69"), D(36), D(50))
    assert a.amount == buy.amount
    assert b == after
    assert adjust_trades([buy], [Split("BRK-B", date(2010, 1, 21), D(50), IGNORED)]) == [buy]
    assert split_factor([BRK_SPLIT, Split("BRK-B", date(2020, 1, 2), D(2))], "BRK-B", date(2000, 1, 1)) == 100
    (s,) = adjust_stops([StopChange(1, 1, date(2005, 1, 1), D(2000))], [buy], [BRK_SPLIT])
    assert s.stop == D(40)


def test_pending_splits_only_when_held_and_undecided():
    buy = Trade(1, 1, "BRK-B", Side.BUY, date(1998, 5, 4), D(6), D("2034.50"))
    market = [BRK_SPLIT, Split("BRK-B", date(1990, 1, 1), D(2))]  # before the first buy: ignored
    (p,) = pending_splits([buy], market, [], date(2026, 11, 24))
    assert (p.symbol, p.ex_date, p.shares_before, p.label) == ("BRK-B", date(2010, 1, 21), D(6), "50-for-1")
    assert pending_splits([buy], market, [Split("BRK-B", date(2010, 1, 21), D(50), IGNORED)], date(2026, 11, 24)) == []
    sold = Trade(2, 1, "BRK-B", Side.SELL, date(2005, 1, 1), D(6), D(3000), lot_id=1)
    assert pending_splits([buy, sold], market, [], date(2026, 11, 24)) == []


def test_repo_validates_sells_in_post_split_shares(tmp_path):
    repos = Repos.open(connect(tmp_path / "s.db"))
    p = repos.portfolios.create(Portfolio(None, "Hold", D(0), type="long_term"))
    lot = repos.trades.add(Trade(None, p.id, "BRK-B", Side.BUY, date(1998, 5, 4), D(6), D("2034.50")))
    with pytest.raises(RepoError):  # 100 of 6 shares before the split is recorded
        repos.trades.add(Trade(None, p.id, "BRK-B", Side.SELL, date(2026, 11, 2), D(100), D(500), lot_id=lot.id))
    repos.splits.record(Split("BRK-B", date(2010, 1, 21), D(50), APPLIED, p.id))
    repos.trades.add(Trade(None, p.id, "BRK-B", Side.SELL, date(2026, 11, 2), D(100), D(500), lot_id=lot.id))
    with pytest.raises(RepoError):  # 201 + 100 > 300
        repos.trades.add(Trade(None, p.id, "BRK-B", Side.SELL, date(2026, 11, 3), D(201), D(500), lot_id=lot.id))
    # undoing the split would leave 100 sold from a 6-share lot
    from trailhead.portfolio_types.splits import undo_split

    with pytest.raises(RepoError):
        undo_split(repos, p.id, repos.splits.list(p.id)[0])


def test_odd_ratios_look_like_spinoffs():
    from trailhead.core.splits import looks_like_spinoff

    assert not any(looks_like_spinoff(D(r)) for r in ("2", "50", "1.5", "1.25", "0.1", "0.5", "10"))
    assert looks_like_spinoff(D("1.241")) and looks_like_spinoff(D("1.253")) and looks_like_spinoff(D("1.04"))


def test_rounded_reverse_split_is_a_consolidation_not_a_spinoff():
    """Yahoo reports AWP's 1-for-3 consolidation (2026-02-09) as 0.3333."""
    assert not looks_like_spinoff(D("0.3333"))
    assert clean_ratio(D("0.3333")) == D(1) / D(3)
    assert ratio_label(clean_ratio(D("0.3333"))) == "1-for-3"
    assert looks_like_spinoff(D("1.241")) and looks_like_spinoff(D("1.253"))  # FDX, GE spin-offs
    assert not looks_like_spinoff(D("0.8058"))  # a ratio below 1 is never a spin-off
    buy = Trade(1, 1, "AWP", Side.BUY, date(2025, 2, 28), D(2493), D("4.015"))
    (p,) = pending_splits([buy], [Split("AWP", date(2026, 2, 9), D("0.3333"))], [], date(2026, 11, 25))
    assert (p.ratio, p.label, p.shares_after) == (D(1) / D(3), "1-for-3", D(831))
    (a,) = adjust_trades([buy], [Split("AWP", date(2026, 2, 9), p.ratio)])
    assert a.shares == D(831)  # exactly: selling 831 closes the lot
