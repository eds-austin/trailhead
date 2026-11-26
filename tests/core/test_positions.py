from datetime import date
from decimal import Decimal as D

import pytest

from trailhead.core.positions import (
    LedgerError,
    build_lots,
    build_positions,
    fifo_allocate,
    open_lots_fifo,
    validate,
)

from .conftest import buy, sell


def test_golden_positions(golden):
    _, trades, _ = golden
    lots = build_lots(trades)
    assert [lot.realized_pl for lot in lots] == [D("153.00"), D("291.50")]
    assert round(lots[0].realized_r, 2) == D("1.05")
    assert round(lots[1].realized_r, 2) == D("2.00")
    positions = build_positions(lots)
    assert len(positions) == 1
    p = positions[0]
    assert p.is_closed
    assert p.close_date == date(2016, 8, 4)
    assert p.realized_pl == D("444.50")
    assert round(p.realized_r, 2) == D("1.52")
    assert p.timeframe == "Weekly/Daily"
    assert p.strategy is None


def test_fifo_several_lots():
    trades = [
        buy(1, "NVDA", date(2024, 1, 2), 100, 50, 45),
        buy(2, "NVDA", date(2024, 1, 5), 60, 55, 50),
        buy(3, "NVDA", date(2024, 1, 3), 40, 52, 48),
        buy(4, "AAPL", date(2024, 1, 1), 10, 100, 90),
    ]
    lots = build_lots(trades)
    fifo = open_lots_fifo(lots, "NVDA")
    assert [lot.id for lot in fifo] == [1, 3, 2]
    assert fifo_allocate(fifo, D(30)) == [(1, D(30))]


def test_sell_larger_than_lot_is_split():
    trades = [
        buy(1, "NVDA", date(2024, 1, 2), 100, 50, 45),
        buy(2, "NVDA", date(2024, 1, 3), 40, 52, 48),
        buy(3, "NVDA", date(2024, 1, 5), 60, 55, 50),
        sell(4, "NVDA", date(2024, 1, 6), 30, 56, 1),
    ]
    lots = build_lots(trades)
    fifo = open_lots_fifo(lots, "NVDA")
    assert fifo_allocate(fifo, D(120)) == [(1, D(70)), (2, D(40)), (3, D(10))]
    # user picks lot 3 first; the rest follows FIFO
    assert fifo_allocate(fifo, D(80), first_lot_id=3) == [(3, D(60)), (1, D(20))]
    with pytest.raises(LedgerError):
        fifo_allocate(fifo, D(171))


def test_edit_sell_excludes_itself():
    trades = [buy(1, "X", date(2024, 1, 2), 10, 50, 45), sell(2, "X", date(2024, 1, 3), 10, 51, 1)]
    lots = build_lots(trades)
    assert open_lots_fifo(lots, "X") == []
    assert [lot.id for lot in open_lots_fifo(lots, "X", exclude_sell_id=2)] == [1]
    assert fifo_allocate(lots, D(10), exclude_sell_id=2) == [(1, D(10))]


def test_close_and_reopen_creates_new_position():
    trades = [
        buy(1, "AMD", date(2024, 1, 2), 10, 100, 95),
        sell(2, "AMD", date(2024, 1, 10), 10, 110, 1),
        buy(3, "AMD", date(2024, 2, 1), 20, 120, 110),
        buy(4, "AMD", date(2024, 2, 5), 5, 125, 118),
        sell(5, "AMD", date(2024, 2, 6), 20, 130, 3),
    ]
    positions = build_positions(build_lots(trades))
    assert [p.key for p in positions] == ["AMD#0", "AMD#1"]
    first, second = positions
    assert first.is_closed and first.realized_pl == D(100)
    assert not second.is_closed
    assert second.open_shares == D(5)
    assert second.original_shares == D(25)
    assert second.avg_entry == D(125)
    assert [lot.id for lot in second.lots] == [3, 4]


def test_add_on_while_open_stays_in_position():
    trades = [
        buy(1, "AMD", date(2024, 1, 2), 10, 100, 95, strategy="Pullback"),
        buy(2, "AMD", date(2024, 1, 4), 10, 104, 99, strategy="Breakout"),
        sell(3, "AMD", date(2024, 1, 5), 10, 106, 1),
    ]
    positions = build_positions(build_lots(trades))
    assert len(positions) == 1
    assert positions[0].strategy == "Mixed"
    assert positions[0].avg_entry == D(104)


def test_sell_without_lot_errors():
    with pytest.raises(LedgerError):
        build_lots([sell(1, "X", date(2024, 1, 1), 1, 1, 99)])


def test_validate():
    trades = [buy(1, "X", date(2024, 1, 2), 10, 50, 45), sell(2, "X", date(2024, 1, 1), 11, 51, 1)]
    problems = validate(trades)
    assert any("oversold" in p for p in problems)
    assert any("before" in p for p in problems)


def test_per_symbol_trade_numbers():
    trades = [
        buy(1, "XNCR", date(2026, 10, 22), 100, "25.63", "20.63"),
        sell(2, "XNCR", date(2026, 10, 26), 25, "31.63", 1),
        buy(3, "CVX", date(2026, 11, 12), 24, "217.37", 210),
        buy(4, "EOG", date(2026, 8, 20), 32, "152.17", 145),
        buy(5, "XNCR", date(2026, 11, 2), 10, 30, 28),
        sell(6, "XNCR", date(2026, 11, 3), 5, 31, 5),
        buy(7, "XNCR", date(2026, 10, 1), 10, 24, 22),  # entered later but dated earliest → BUY #1
    ]
    lots = {lot.id: lot for lot in build_lots(trades)}
    assert [lots[i].no for i in (7, 1, 5)] == [1, 2, 3]
    assert lots[3].no == 1 and lots[4].no == 1
    assert lots[1].sell_number(2) == 1 and lots[5].sell_number(6) == 2
