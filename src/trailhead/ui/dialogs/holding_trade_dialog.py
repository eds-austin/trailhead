"""New / Edit trade for a Long-term Holdings portfolio: investment type, optional stop and target, FIFO sells."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QDate, QStringListModel, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ...context import AppContext
from ...core.holdings import HoldingsSnapshot, HoldingView
from ...core.metals import BY_NAME, METALS, display_symbol, is_bullion, quantity_unit
from ...core.models import ASSET_TYPES, Portfolio, Side, Trade
from ...core.money import ZERO, dsum
from ...core.positions import LedgerError, fifo_allocate, open_lots_fifo, remaining_excluding
from ...core.splits import split_factor
from ...data.repo import RepoError
from .. import format as fmt
from .. import theme
from ..widgets.common import label
from ..widgets.number_edit import NumberEdit
from ..widgets.segmented import Segmented
from .base import AppDialog, FieldGrid, inset_panel
from .symbol_picker_dialog import UnknownSymbolMixin
from .trade_dialog import low_cash_warning

ASSET_TYPE_HINT = ("Equity: individual stocks · ETF: index and sector funds · Income: REITs, bond funds, BDCs, "
                   "preferreds · Precious metals: bullion, coins, metal ETFs")


def _qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> date:
    return date(q.year(), q.month(), q.day())


class _LinkButton(QPushButton):
    def __init__(self, text: str = ""):
        super().__init__(text)
        self.setProperty("variant", "ghost")
        self.setProperty("scale", "small")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"color: {theme.palette().accent};")
        self.setAutoDefault(False)


class HoldingTradeDialog(UnknownSymbolMixin, AppDialog):
    def __init__(self, ctx: AppContext, portfolio: Portfolio, snapshot: HoldingsSnapshot,
                 trade: Trade | None = None, prefill: dict[str, Any] | None = None, parent: QWidget | None = None):
        self.edit_trade = trade
        super().__init__("Edit trade" if trade else "New trade", parent, width=540, primary_text="Save trade")
        self.ctx = ctx
        self.portfolio = portfolio
        self.snap = snapshot
        self.prefill = prefill or {}
        self.result_trades: list[Trade] = []
        self._levels_touched = False
        self._build()
        self._load()
        self._update()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self.side = Segmented([("BUY", "▲  BUY", "positive"), ("SELL", "▼  SELL", "negative")])
        self.side.setProperty("scale", "large")
        for key in ("BUY", "SELL"):
            self.side.button(key).setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for w in (self.side, self.side.button("BUY"), self.side.button("SELL")):
            w.style().unpolish(w)
            w.style().polish(w)
        self.body.addWidget(self.side)
        self.kind = Segmented([("security", "Ticker symbol"), ("bullion", "Bullion (oz)")])
        self.kind.setToolTip("Ticker: a metal ETF or fund such as GLD · Bullion: physical gold, silver, platinum or "
                             "palladium in troy ounces, valued at the metal's price")

        g = FieldGrid()
        self.grid = g
        self.symbol = QLineEdit()
        self.symbol.setPlaceholderText("Ticker")
        self.symbol.setMaxLength(12)
        self.symbol.setFont(theme.font("body_strong"))
        known = sorted(set(self.ctx.repos.trades.symbols()) | set(self.ctx.repos.market.get_instruments()))
        comp = QCompleter(QStringListModel(known, self), self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.symbol.setCompleter(comp)
        self.metal = QComboBox()
        self.metal.addItems(list(METALS.values()))
        self.metal.setToolTip("Valued at the metal's front-month futures price (close to spot), per troy ounce")
        sym_box = QWidget()
        sb = QHBoxLayout(sym_box)
        sb.setContentsMargins(0, 0, 0, 0)
        sb.addWidget(self.symbol)
        sb.addWidget(self.metal)
        self.symbol_label = g.add("symbol", "Symbol", sym_box, 0)
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        g.add("date", "Date", self.date, 1)
        g.next_row()
        self.asset_type = QComboBox()
        self.asset_type.addItems(list(ASSET_TYPES))
        self.asset_type.setToolTip(ASSET_TYPE_HINT)
        self.type_label = g.add("asset_type", "Investment type", self.asset_type, 0, span=2)
        g.next_row()
        self.kind_label = g.add("kind", "Held as", self.kind, 0, span=2)  # only for Precious metals
        g.next_row()
        self.shares = NumberEdit(4, placeholder="0")
        self.all_btn = _LinkButton()
        self.all_btn.clicked.connect(self._sell_all)
        self.shares_label = g.add("shares", "Shares", self.shares, 0, extra=self.all_btn)
        self.price = NumberEdit(4, placeholder="0.00")
        self.price_label = g.add("price", "Price", self.price, 1)
        g.next_row()
        self.stop = NumberEdit(4, placeholder="Optional")
        self.target = NumberEdit(4, placeholder="Optional")
        self.stop_label = g.add("stop", "Stop", self.stop, 0)
        self.target_label = g.add("target", "Target", self.target, 1)
        self.body.addLayout(g)
        self.symbol_info = label("", role="muted")
        self.symbol_info.setFont(theme.font("caption"))
        self.symbol_info.setWordWrap(True)
        info_row = QHBoxLayout()
        info_row.addWidget(self.symbol_info, 1)
        self._init_symbol_check(info_row)
        self.body.addLayout(info_row)

        panel, pv = inset_panel()
        self.summary = label("", font_token="num")
        self.summary.setWordWrap(True)
        self.detail = label("", role="muted", font_token="num")
        self.detail.setWordWrap(True)
        pv.addWidget(self.summary)
        pv.addWidget(self.detail)
        self.body.addWidget(panel)

        ng = FieldGrid()
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(56)
        self.notes.setPlaceholderText("Optional")
        ng.add("notes", "Notes", self.notes, span=2)
        self.body.addLayout(ng)

        self.side.changed.connect(lambda _k: self._on_side())
        self.kind.changed.connect(lambda _k: self._on_kind())
        self.metal.currentIndexChanged.connect(lambda _i: self._on_symbol_done())
        self.asset_type.currentTextChanged.connect(lambda _t: self._sync_kind())
        self.symbol.textEdited.connect(self._on_symbol_edited)
        self.symbol.editingFinished.connect(self._on_symbol_done)
        for w in (self.shares, self.price, self.stop, self.target):
            w.textChanged.connect(lambda _t: self._update())
        for w in (self.stop, self.target):
            w.textEdited.connect(lambda _t: setattr(self, "_levels_touched", True))
        self.date.dateChanged.connect(lambda _d: self._update())
        chain = [self.symbol, self.metal, self.date, self.asset_type, self.shares, self.price, self.stop, self.target,
                 self.notes, self.primary_btn]
        for a, b in itertools.pairwise(chain):
            QWidget.setTabOrder(a, b)

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        t = self.edit_trade
        pre = self.prefill
        if t is not None:
            self.side.set_value(t.side.value, emit=False)
            self.side.setEnabled(False)
            self._set_metal(t.symbol)
            self.kind.setEnabled(False)
            self.symbol.setText(t.symbol)
            self.symbol.setReadOnly(t.side == Side.SELL or any(lot.id == t.id and lot.sells for lot in self.snap.lots))
            self.date.setDate(_qdate(t.trade_date))
            self.shares.set_value(t.shares)
            self.price.set_value(t.price)
            self.price.user_edited = True
            self.notes.setPlainText(t.notes or "")
            if t.side == Side.BUY:
                self.asset_type.setCurrentIndex(max(0, self.asset_type.findText(t.asset_type or "Equity")))
                self.stop.set_value(t.initial_stop)
                self.target.set_value(t.target_price)
        else:
            self.date.setDate(_qdate(self.ctx.today()))
            self.side.set_value(pre.get("side", "BUY"), emit=False)
            if pre.get("symbol"):
                self._set_metal(pre["symbol"])
                self.symbol.setText(pre["symbol"])
            if pre.get("shares") is not None:
                self.shares.set_value(pre["shares"])
            if pre.get("price") is not None:
                self.price.set_value(pre["price"])
                self.price.user_edited = True
        self._on_kind(update=False)
        self._on_side()
        self._apply_holding_defaults()
        self._refresh_symbol_info()

    # ------------------------------------------------------------------ helpers
    def is_buy(self) -> bool:
        return self.side.value() == "BUY"

    def is_bullion(self) -> bool:
        return self.kind.value() == "bullion"

    def sym(self) -> str:
        if self.is_bullion():
            return BY_NAME[self.metal.currentText()]
        return self.symbol.text().strip().upper()

    def _set_metal(self, symbol: str) -> None:
        if is_bullion(symbol):
            self.kind.set_value("bullion", emit=False)
            self.metal.setCurrentText(METALS[symbol])

    def _sync_kind(self) -> None:
        """Held as (ticker / bullion) shows for a Precious metals buy, for a sell when bullion is held, and when
        editing a bullion trade; otherwise the trade is a ticker."""
        if self.edit_trade is not None:
            show = is_bullion(self.edit_trade.symbol)
        elif self.is_buy():
            show = self.asset_type.currentText() == "Precious metals"
        else:
            show = any(is_bullion(h.symbol) and not h.is_closed for h in self.snap.holdings)
        self.kind.setVisible(show)
        self.kind_label.setVisible(show)
        if not show and self.is_bullion():
            self.kind.set_value("security", emit=False)
            self._on_kind()
        QTimer.singleShot(0, self.adjustSize)

    def _on_kind(self, update: bool = True) -> None:
        b = self.is_bullion()
        self.symbol.setVisible(not b)
        self.metal.setVisible(b)
        self.symbol_label.setText("Metal" if b else "Symbol")
        self.shares_label.setText("Ounces" if b else "Shares")
        self.price_label.setText("Price / oz" if b else "Price")
        self.shares.setPlaceholderText("Troy oz of pure metal" if b else "0")
        if b:
            self.asset_type.setCurrentText("Precious metals")
            self.find_symbol_btn.hide()
        if update:
            self.price.user_edited = False
            self._on_symbol_done()
            QTimer.singleShot(0, self.adjustSize)

    def _holding(self) -> HoldingView | None:
        s = self.sym()
        return next((h for h in self.snap.holdings if h.symbol == s and not h.is_closed), None)

    def _exclude_sell_id(self) -> int | None:
        t = self.edit_trade
        return t.id if t is not None and t.side == Side.SELL else None

    def _factor(self) -> Decimal:
        """Shares on the trade date × factor = today's (post-split) shares, the terms the lots are in."""
        return split_factor(getattr(self.snap, "splits", []), self.sym(), _pydate(self.date.date()))

    def _available(self) -> Decimal:
        """Shares that can be sold on the trade date (for an edited SELL: its lot only, with this sell put back)."""
        t = self.edit_trade
        if t is not None and t.side == Side.SELL:
            lot = next((lot for lot in self.snap.lots if lot.id == t.lot_id), None)
            avail = remaining_excluding(lot, t.id) if lot else ZERO
        else:
            avail = dsum(lot.remaining for lot in open_lots_fifo(self.snap.lots, self.sym()))
        return avail / self._factor()

    def _on_side(self) -> None:
        buy = self.is_buy()
        for w in (self.stop, self.stop_label, self.target, self.target_label):
            w.setVisible(buy)
        for w in (self.asset_type, self.type_label):
            w.setVisible(buy)
        self._sync_kind()
        self.all_btn.setVisible(not buy)
        self.primary_btn.setText("Save trade" if self.edit_trade else ("Buy" if buy else "Sell"))
        self._prefill_price()
        self._update()

    def _on_symbol_edited(self, text: str) -> None:
        pos = self.symbol.cursorPosition()
        self.symbol.setText(text.upper())
        self.symbol.setCursorPosition(pos)

    def _on_symbol_done(self) -> None:
        self._apply_holding_defaults()
        self._refresh_symbol_info()
        self._prefill_price()
        self._update()
        if self.ctx.market is not None and self.sym() and not self.is_bullion():
            self.ctx.market.validate_symbol(self.sym(), self._on_symbol_validated)

    def _on_symbol_validated(self, symbol: str, ok: bool, info: str) -> None:
        if symbol != self.sym():
            return
        self._symbol_checked(symbol, ok)
        if ok:
            self._refresh_symbol_info(info)
        else:
            self.symbol_info.setText(f"⚠ {symbol} was not found by the data provider — saving still works offline")

    def _apply_holding_defaults(self) -> None:
        """A buy that adds to a holding keeps its investment type, stop and target."""
        if self.edit_trade is not None:
            return
        h = self._holding()
        if h is None:
            prev = [t for t in self.snap.trades if t.symbol == self.sym() and t.asset_type]
            if prev:
                self.asset_type.setCurrentText(prev[-1].asset_type)  # type: ignore[arg-type]
            return
        if h.asset_type and h.asset_type in ASSET_TYPES:
            self.asset_type.setCurrentText(h.asset_type)
        if not self._levels_touched:
            self.stop.set_value(h.stop)
            self.target.set_value(h.target)

    def _refresh_symbol_info(self, fetched: str = "") -> None:
        s = self.sym()
        if not s:
            self.symbol_info.setText("")
            return
        inst = self.ctx.repos.market.get_instruments([s]).get(s)
        q = self.ctx.repos.market.get_quote(s)
        if is_bullion(s):
            price = f"{fmt.price(q.last)} / oz" + (" (manual)" if q.is_manual else "") if q else "no price yet"
            self.symbol_info.setText(f"{METALS[s]} · futures price ≈ spot · {price}. Enter the ounces of pure "
                                     "metal and what you paid per ounce, premium included.")
            return
        parts = [x for x in ((inst.name if inst else None) or fetched,) if x]
        if inst and inst.div_yield:
            parts.append(f"yield {fmt.pct(inst.div_yield, 2)}")
        if q is not None:
            parts.append(f"last {fmt.price(q.last)}" + (" (manual)" if q.is_manual else ""))
        self.symbol_info.setText(" · ".join(parts) if parts else "No market data yet")

    def _prefill_price(self) -> None:
        q = self.ctx.repos.market.get_quote(self.sym()) if self.sym() else None
        if q is not None and not self.price.user_edited:
            self.price.set_value(q.last)

    def _sell_all(self) -> None:
        self.shares.set_value(self._available())
        self._update()

    # ------------------------------------------------------------------ live summary
    def _update(self) -> None:
        n, px = self.shares.value(), self.price.value()
        h = self._holding()
        u = quantity_unit(self.sym())
        if self.is_buy():
            self.summary.setText(f"Cost {fmt.money(n * px)}" if n and px else
                                 "Enter ounces and price per ounce" if self.is_bullion() else "Enter shares and price")
            if h is not None and n and px and self.edit_trade is None:
                new_shares = h.open_shares + n
                new_avg = (h.cost_basis + n * px) / new_shares
                self.detail.setText(f"Adds to {display_symbol(h.symbol)}: {fmt.shares(h.open_shares)} → "
                                    f"{fmt.shares(new_shares)} {u}"
                                    f" · avg cost {fmt.price(h.avg_cost)} → {fmt.price(new_avg)}")
            elif self.edit_trade is None and self.sym():
                self.detail.setText(f"New holding in {self.sym()}")
            else:
                self.detail.setText("")
            stop, target = self.stop.value(), self.target.value()
            if stop is not None and target is not None and stop >= target:
                self.detail.setText("Stop should be below the target")
            low = low_cash_warning(self.ctx, self.portfolio, self.edit_trade, n * px) if n and px else None
            if low:
                self.detail.setText(f"⚠  {low}")
            return
        avail = self._available()
        self.all_btn.setText(f"All {fmt.shares(avail)}" if avail else "")
        self.all_btn.setVisible(bool(avail))
        if not n or not px:
            what = display_symbol(self.sym()) or "shares"
            self.summary.setText(f"{fmt.shares(avail)} {u} held" if avail else f"No open {what}")
            self.detail.setText("")
            return
        try:
            alloc = self._allocation(n)
        except LedgerError:
            self.summary.setText(f"Only {fmt.shares(avail)} {u} held")
            self.detail.setText("")
            return
        lots = {lot.id: lot for lot in self.snap.lots}
        f = self._factor()  # lot entries are in post-split terms; the sell is in the trade date's shares
        pl = dsum(sh * (px - lots[lid].entry * f) for lid, sh in alloc)
        self.summary.setText(f"Proceeds {fmt.money(n * px)} · realized {fmt.signed_money(pl)}")
        self.detail.setText("FIFO: " + ", ".join(f"{fmt.shares(sh)} {u} from lot #{lots[lid].no} "
                                                f"@ {fmt.price(lots[lid].entry * f)}" for lid, sh in alloc)
                            if len(alloc) > 1 or self.edit_trade is None else "")

    def _allocation(self, shares: Decimal) -> list[tuple[int, Decimal]]:
        t = self.edit_trade
        if t is not None and t.side == Side.SELL:
            if shares > self._available():
                raise LedgerError("oversold")
            return [(t.lot_id, shares)]  # type: ignore[list-item]
        f = self._factor()
        return [(lid, sh / f) for lid, sh in fifo_allocate(open_lots_fifo(self.snap.lots, self.sym()), shares * f)]

    # ------------------------------------------------------------------ save
    def submit(self) -> bool:
        self._check_symbol_before_save()
        g = self.grid
        g.clear_errors()
        sym, n, px = self.sym(), self.shares.value(), self.price.value()
        ok = True
        if not sym:
            g.set_error("symbol", "Enter a symbol")
            ok = False
        if n is None or n <= 0:
            g.set_error("shares", "Enter the ounces" if self.is_bullion() else "Enter the number of shares")
            ok = False
        if px is None or px <= 0:
            g.set_error("price", "Enter the price")
            ok = False
        when = _pydate(self.date.date())
        if when > self.ctx.today():
            g.set_error("date", "The date cannot be in the future")
            ok = False
        if not ok:
            return False
        notes = self.notes.toPlainText().strip() or None
        try:
            if self.is_buy():
                stop, target = self.stop.value(), self.target.value()
                if stop is not None and target is not None and stop >= target:
                    g.set_error("stop", "Stop must be below the target")
                    return False
                asset_type = "Precious metals" if self.is_bullion() else self.asset_type.currentText()
                trade = Trade(None, self.portfolio.id, sym, Side.BUY, when, n, px, initial_stop=stop,  # type: ignore[arg-type]
                              notes=notes, asset_type=asset_type, target_price=target)
                h = self._holding()
                if self.edit_trade is not None:
                    trade = replace(trade, id=self.edit_trade.id, drip=self.edit_trade.drip)
                    self.result_trades = [self.ctx.repos.trades.update(trade)]
                else:
                    self.result_trades = [self.ctx.repos.trades.add(trade)]
                if h is not None and self._levels_touched:  # a holding's stop / target apply to all its open lots
                    self.ctx.repos.trades.set_levels([lot.id for lot in h.open_lots], stop, target)
                return True
            try:
                alloc = self._allocation(n)  # type: ignore[arg-type]
            except LedgerError:
                g.set_error("shares", f"Only {fmt.shares(self._available())} {quantity_unit(sym)} held")
                return False
            lots = {lot.id: lot for lot in self.snap.lots}
            for lid, _sh in alloc:
                lot = lots[lid]
                if when < lot.entry_date:
                    g.set_error("date", f"Before the buy of lot #{lot.no} ({fmt.short_date(lot.entry_date)})")
                    return False
            if self.edit_trade is not None:
                t = replace(self.edit_trade, trade_date=when, shares=n, price=px, notes=notes)  # type: ignore[arg-type]
                self.result_trades = [self.ctx.repos.trades.update(t)]
            else:
                self.result_trades = self.ctx.repos.trades.add_many(
                    [Trade(None, self.portfolio.id, sym, Side.SELL, when, sh, px, lot_id=lid, notes=notes)  # type: ignore[arg-type]
                     for lid, sh in alloc])
        except RepoError as e:
            self.show_error(str(e))
            return False
        return True

