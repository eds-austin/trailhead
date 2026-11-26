"""New / Edit Trade dialog (PLAN §7.6) with live risk panel, suggested shares and FIFO lot selection."""

from __future__ import annotations

import itertools
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QDate, QEvent, QObject, QStringListModel, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...context import AppContext
from ...core.models import STRATEGIES, TIMEFRAMES, Portfolio, Side, Trade
from ...core.money import ZERO, cents
from ...core.positions import LedgerError, Lot, fifo_allocate, open_lots_fifo, remaining_excluding
from ...core.r_engine import EXIT_PLAN_LABELS, ActionKind, ExitPlan, Ladder, as_plan
from ...core.sizing import check_buy, suggested_shares
from ...core.snapshot import PortfolioSnapshot
from ...data.repo import RepoError
from .. import format as fmt
from .. import theme
from ..theme import tokens
from ..widgets.common import label
from ..widgets.number_edit import NumberEdit
from ..widgets.segmented import Segmented
from .base import AppDialog, FieldGrid, inset_panel
from .symbol_picker_dialog import UnknownSymbolMixin


def low_cash_warning(ctx, portfolio, edit_trade, cost) -> str | None:
    """'Ally would go to −$1,200.00' when a buy costs more than the linked cash account holds (a warning only)."""
    from ...portfolio_types.cash.service import cash_after_buy

    if not cost or portfolio.id is None:
        return None
    link = ctx.repos.cash_accounts.link_for(portfolio.id)
    if link is None:
        return None
    replacing = None
    if edit_trade is not None and edit_trade.side == Side.BUY and not edit_trade.drip \
            and edit_trade.trade_date >= link.since:
        replacing = edit_trade.shares * edit_trade.price
    got = cash_after_buy(ctx.repos, portfolio.id, ctx.today(), cost, replacing)
    if got is None or got[1] >= 0:
        return None
    v, after = got
    return (f"{v.account.name} would go to {fmt.money(after)} (balance {fmt.money(v.balance)}). "
            "Record a deposit or transfer first, or save anyway.")


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


class TradeDialog(UnknownSymbolMixin, AppDialog):
    saved = Signal(list)  # list[Trade]

    def __init__(self, ctx: AppContext, portfolio: Portfolio, snapshot: PortfolioSnapshot,
                 trade: Trade | None = None, prefill: dict[str, Any] | None = None, parent: QWidget | None = None):
        self.edit_trade = trade
        super().__init__("Edit trade" if trade else "New trade", parent, width=580, primary_text="Save trade")
        self.ctx = ctx
        self.portfolio = portfolio
        self.snap = snapshot
        self.prefill = prefill or {}
        self.result_trades: list[Trade] = []
        self._symbol_info_cache: dict[str, str] = {}
        self._build()
        self._load()
        self._update_all()

    # ------------------------------------------------------------------ build
    def _build(self) -> None:
        self.side = Segmented([("BUY", "▲  BUY", "positive"), ("SELL", "▼  SELL", "negative")])
        self.side.setProperty("scale", "large")
        self.side.setToolTip("Keyboard: B / S")
        for key in ("BUY", "SELL"):
            self.side.button(key).setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        for w in (self.side, self.side.button("BUY"), self.side.button("SELL")):
            w.style().unpolish(w)
            w.style().polish(w)
        self.body.addWidget(self.side)  # full width so the active side can't be missed

        grid = FieldGrid()
        self.grid = grid
        self.symbol = QLineEdit()
        self.symbol.setPlaceholderText("Ticker")
        self.symbol.setMaxLength(12)
        self.symbol.setFont(theme.font("body_strong"))
        known = sorted(set(self.ctx.repos.trades.symbols()) | set(self.ctx.repos.market.get_instruments()))
        comp = QCompleter(QStringListModel(known, self), self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.symbol.setCompleter(comp)
        self.symbol_info = label("", role="muted")
        self.symbol_info.setFont(theme.font("caption"))
        grid.add("symbol", "Symbol", self.symbol, span=2)
        grid.next_row()
        self.date = QDateEdit()
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd")
        self.date.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        grid.add("date", "Date", self.date)
        grid.next_row()
        self.body.addLayout(grid)
        info_row = QHBoxLayout()
        info_row.addWidget(self.symbol_info, 1)
        self._init_symbol_check(info_row)
        self.body.addLayout(info_row)

        # BUY section
        self.buy_box = QWidget()
        bv = QVBoxLayout(self.buy_box)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(tokens.SPACE[3])
        bg = FieldGrid()
        self.buy_grid = bg
        # Order (and tab order): Shares → Price → Initial stop, each on its own row.
        self.shares = NumberEdit(0, placeholder="0")
        self.use_suggested = _LinkButton()
        self.use_suggested.clicked.connect(self._apply_suggested)
        bg.add("shares", "Shares", self.shares, 0, extra=self.use_suggested, span=2)
        bg.next_row()
        self.price = NumberEdit(4, placeholder="0.00")
        bg.add("price", "Price", self.price, 0, span=2)
        bg.next_row()
        self.stop = NumberEdit(4, placeholder="0.00")
        bg.add("stop", "Initial stop *", self.stop, 0, span=2)
        bg.next_row()
        self.strategy = QComboBox()
        self.strategy.addItems(["—", *STRATEGIES])
        self.timeframe = QComboBox()
        self.timeframe.addItems(["—", *TIMEFRAMES])
        bg.add("strategy", "Strategy", self.strategy, 0)
        bg.add("timeframe", "Timeframe", self.timeframe, 1)
        bg.next_row()
        self.exit_plan = QComboBox()
        for plan in ExitPlan:
            self.exit_plan.addItem(EXIT_PLAN_LABELS[plan], plan.value)
        self.exit_plan.setToolTip("Where to take profits (¼ each) and how far below to trail the last quarter.\n"
                                  "1-3-6: 1R stop → −0.5R, 2R → break even, 6R → 3R, then every 3R.\n"
                                  "1-2-3: 1R stop → break even, 2R → 1R, 3R → 2R, then 1R below each new R.")
        bg.add("exit_plan", "Exit plan", self.exit_plan, 0, span=2)
        bv.addLayout(bg)
        panel, pv = inset_panel()
        self.risk_line = label("", font_token="num")
        self.levels_line = label("", role="muted", font_token="num")
        pv.addWidget(self.risk_line)
        pv.addWidget(self.levels_line)
        self.warn_box = QVBoxLayout()
        self.warn_box.setSpacing(tokens.SPACE[1])
        pv.addLayout(self.warn_box)
        bv.addWidget(panel)
        self.body.addWidget(self.buy_box)

        # SELL section
        self.sell_box = QWidget()
        sv = QVBoxLayout(self.sell_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(tokens.SPACE[3])
        sg = FieldGrid()
        self.sell_grid = sg
        self.lot = QComboBox()
        self.lot.setFont(theme.font("num"))
        sg.add("lot", "Lot", self.lot, 0, span=2)
        sg.next_row()
        self.sell_shares = NumberEdit(0, placeholder="0")
        self.use_tranche = _LinkButton()
        self.use_tranche.clicked.connect(self._apply_tranche)
        self.sell_price = NumberEdit(4, placeholder="0.00")
        sg.add("sell_shares", "Shares", self.sell_shares, 0, extra=self.use_tranche)
        sg.add("sell_price", "Price", self.sell_price, 1)
        sv.addLayout(sg)
        spanel, spv = inset_panel()
        self.realized_line = label("", font_token="num")
        spv.addWidget(self.realized_line)
        self.stop_out = QCheckBox("Stop-out — sold because the stop was hit")
        self.stop_out.toggled.connect(lambda _b: setattr(self, "_stop_out_touched", True))
        self._stop_out_touched = False
        spv.addWidget(self.stop_out)
        self.split_strip = QFrame()
        self.split_strip.setProperty("role", "warning-strip")
        sl = QVBoxLayout(self.split_strip)
        sl.setContentsMargins(tokens.SPACE[2], tokens.SPACE[1], tokens.SPACE[2], tokens.SPACE[1])
        self.split_label = QLabel("")
        self.split_label.setWordWrap(True)
        self.split_check = QCheckBox("Split across lots in FIFO order")
        self.split_check.setChecked(True)
        sl.addWidget(self.split_label)
        sl.addWidget(self.split_check)
        spv.addWidget(self.split_strip)
        sv.addWidget(spanel)
        self.body.addWidget(self.sell_box)

        ng = FieldGrid()
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(56)
        self.notes.setPlaceholderText("Optional")
        ng.add("notes", "Notes", self.notes, span=2)
        self.body.addLayout(ng)

        # signals
        self.side.changed.connect(lambda _k: self._update_all())
        self.symbol.textEdited.connect(self._on_symbol_edited)
        self.symbol.editingFinished.connect(self._on_symbol_done)
        for w in (self.price, self.stop, self.shares, self.sell_shares, self.sell_price):
            w.textChanged.connect(lambda _t: self._update_live())
        self.lot.currentIndexChanged.connect(lambda _i: self._on_lot_changed())
        self.split_check.toggled.connect(lambda _b: self._update_live())
        self.date.dateChanged.connect(lambda _d: self._update_live())
        self.exit_plan.currentIndexChanged.connect(lambda _i: self._update_live())
        chain = [self.symbol, self.date, self.shares, self.price, self.stop, self.strategy, self.timeframe,
                 self.exit_plan, self.lot, self.sell_shares, self.sell_price, self.notes, self.primary_btn]
        for a, b in itertools.pairwise(chain):
            QWidget.setTabOrder(a, b)
        self.installEventFilter(self)
        for w in self.findChildren(QWidget):
            w.installEventFilter(self)

    # ------------------------------------------------------------------ load
    def _load(self) -> None:
        t = self.edit_trade
        pre = self.prefill
        if t is not None:
            self.side.set_value(t.side.value, emit=False)
            self.side.setEnabled(False)
            self.symbol.setText(t.symbol)
            self.date.setDate(_qdate(t.trade_date))
            self.notes.setPlainText(t.notes or "")
            if t.side == Side.BUY:
                self.price.set_value(t.price)
                self.stop.set_value(t.initial_stop)
                self.shares.set_value(t.shares, 0)
                self.strategy.setCurrentText(t.strategy or "—")
                self.timeframe.setCurrentText(t.timeframe or "—")
                self.exit_plan.setCurrentIndex(max(0, self.exit_plan.findData(as_plan(t.exit_plan).value)))
                if self.snap.lots and any(lot.id == t.id and lot.sells for lot in self.snap.lots):
                    self.symbol.setReadOnly(True)
            else:
                self.symbol.setReadOnly(True)
                self.sell_price.set_value(t.price)
                self.sell_shares.set_value(t.shares, 0)
                self.stop_out.blockSignals(True)
                self.stop_out.setChecked(t.is_stop_out)
                self.stop_out.blockSignals(False)
                self._stop_out_touched = True  # keep the stored choice
            self.price.user_edited = self.sell_price.user_edited = True
        else:
            self.date.setDate(_qdate(self.ctx.today()))
            side = pre.get("side", "BUY")
            self.side.set_value(side, emit=False)
            if pre.get("symbol"):
                self.symbol.setText(pre["symbol"])
            if pre.get("shares") is not None:
                (self.sell_shares if side == "SELL" else self.shares).set_value(pre["shares"], 0)
            if pre.get("price") is not None:
                (self.sell_price if side == "SELL" else self.price).set_value(pre["price"])
                self.sell_price.user_edited = self.price.user_edited = True
        self._refresh_symbol_info()
        self._reload_lots(select=(t.lot_id if t is not None and t.side == Side.SELL else pre.get("lot_id")))
        self._prefill_price()

    # ------------------------------------------------------------------ helpers
    def is_buy(self) -> bool:
        return self.side.value() == "BUY"

    def sym(self) -> str:
        return self.symbol.text().strip().upper()

    def _exclude_sell_id(self) -> int | None:
        t = self.edit_trade
        return t.id if t is not None and t.side == Side.SELL else None

    def _open_lots(self) -> list[Lot]:
        return open_lots_fifo(self.snap.lots, self.sym(), exclude_sell_id=self._exclude_sell_id())

    def _selected_lot(self) -> Lot | None:
        lot_id = self.lot.currentData()
        for lot in self.snap.lots:
            if lot.id == lot_id:
                return lot
        return None

    def _reload_lots(self, select: int | None = None) -> None:
        self.lot.blockSignals(True)
        self.lot.clear()
        excl = self._exclude_sell_id()
        for i, lot in enumerate(self._open_lots()):
            rem = remaining_excluding(lot, excl)
            text = (f"#{lot.no} · {fmt.short_date(lot.entry_date)} · {fmt.shares(rem)} rem · @ {fmt.price(lot.entry)}"
                    f" · R {fmt.price(lot.r)}" + ("  (FIFO)" if i == 0 else ""))
            self.lot.addItem(text, lot.id)
        if select is not None:
            i = self.lot.findData(select)
            if i >= 0:
                self.lot.setCurrentIndex(i)
        self.lot.blockSignals(False)
        self._on_lot_changed()

    def _on_symbol_edited(self, text: str) -> None:
        pos = self.symbol.cursorPosition()
        self.symbol.setText(text.upper())
        self.symbol.setCursorPosition(pos)

    def _on_symbol_done(self) -> None:
        self._refresh_symbol_info()
        if not self.is_buy() or self.edit_trade is None:
            self._reload_lots()
        self._prefill_price()
        self._update_live()
        if self.ctx.market is not None and self.sym():
            self.ctx.market.validate_symbol(self.sym(), self._on_symbol_validated)

    def _on_symbol_validated(self, symbol: str, ok: bool, info: str) -> None:
        if symbol != self.sym():
            return
        self._symbol_checked(symbol, ok)
        if ok:
            self._symbol_info_cache[symbol] = info
            self._refresh_symbol_info()
        else:
            self.symbol_info.setText(f"⚠ {symbol} was not found by the data provider — saving still works offline")
            self.symbol_info.setProperty("tone", "warning")
            self.symbol_info.style().polish(self.symbol_info)

    def _refresh_symbol_info(self) -> None:
        s = self.sym()
        self.symbol_info.setProperty("tone", None)
        self.symbol_info.style().polish(self.symbol_info)
        if not s:
            self.symbol_info.setText("")
            return
        inst = self.ctx.repos.market.get_instruments([s]).get(s)
        q = self.ctx.repos.market.get_quote(s)
        parts = []
        if inst and inst.name:
            parts.append(inst.name)
        if inst and inst.sector:
            parts.append(inst.sector)
        if q is not None:
            parts.append(f"last {fmt.price(q.last)}" + (" (manual)" if q.is_manual else ""))
        if not parts and s in self._symbol_info_cache:
            parts.append(self._symbol_info_cache[s])
        self.symbol_info.setText(" · ".join(parts) if parts else "No market data yet")

    def _prefill_price(self) -> None:
        q = self.ctx.repos.market.get_quote(self.sym()) if self.sym() else None
        if q is None:
            return
        target = self.price if self.is_buy() else self.sell_price
        if not target.user_edited and target.value() is None:
            target.set_value(q.last)

    def _apply_suggested(self) -> None:
        n = self._suggested()
        if n:
            self.shares.set_value(n, 0)

    def _apply_tranche(self) -> None:
        n = self._tranche()
        if n:
            self.sell_shares.set_value(n, 0)

    def _suggested(self) -> int:
        p, s = self.price.value(), self.stop.value()
        if p is None or s is None:
            return 0
        return suggested_shares(self.portfolio.risk_per_trade, p, s)

    def _tranche(self) -> Decimal | None:
        lot = self._selected_lot()
        if lot is None:
            return None
        rem = remaining_excluding(lot, self._exclude_sell_id())
        lv = self.snap.lot_views.get(lot.id)
        if lv is not None and any(a.kind == ActionKind.STOP_HIT for a in lv.state.actions):
            return rem  # stopped out: sell everything left
        return lot.ladder.next_tranche(lot.shares - rem)  # one tranche at a time, even if several are due

    def _on_lot_changed(self) -> None:
        t = self._tranche()
        self.use_tranche.setText(f"Suggested tranche: {fmt.shares(t)}" if t else "")
        self.use_tranche.setVisible(bool(t))
        self._update_live()

    # ------------------------------------------------------------------ live update
    def _update_all(self) -> None:
        buy = self.is_buy()
        verb = "Edit" if self.edit_trade is not None else "New"
        self.title_label.setText(f"{verb} {'buy' if buy else 'sell'}")
        self.title_label.setProperty("role", "title")
        self.title_label.setProperty("tone", "positive" if buy else "negative")
        self.title_label.style().unpolish(self.title_label)
        self.title_label.style().polish(self.title_label)
        self.setWindowTitle(f"{verb} {'buy' if buy else 'sell'}")
        self.buy_box.setVisible(buy)
        self.sell_box.setVisible(not buy)
        if not buy:
            self._reload_lots(select=self.lot.currentData())
        self._prefill_price()
        self._update_live()
        self.adjustSize()

    def _clear_warnings(self) -> None:
        while self.warn_box.count():
            item = self.warn_box.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _add_warning(self, text: str, blocking: bool = False) -> None:
        f = QFrame()
        f.setProperty("role", "error-strip" if blocking else "warning-strip")
        h = QHBoxLayout(f)
        h.setContentsMargins(tokens.SPACE[2], tokens.SPACE[1], tokens.SPACE[2], tokens.SPACE[1])
        lb = QLabel(f"⚠  {text}")
        lb.setWordWrap(True)
        h.addWidget(lb)
        self.warn_box.addWidget(f)

    def _deployed_excluding_edit(self) -> Decimal:
        """Deployed cost basis, excluding the lot being edited."""
        deployed = self.snap.deployed
        t = self.edit_trade
        if t is not None and t.side == Side.BUY:
            lot = next((lot for lot in self.snap.lots if lot.id == t.id), None)
            if lot is not None:
                deployed -= lot.cost_basis_open
        return deployed

    def _update_live(self) -> None:
        if self.is_buy():
            self._update_buy_panel()
        else:
            self._update_sell_panel()
        self._update_primary()

    def _update_buy_panel(self) -> None:
        p, s, n = self.price.value(), self.stop.value(), self.shares.value()
        sug = self._suggested()
        self.use_suggested.setText(f"Use suggested: {sug:,}" if sug else "")
        self.use_suggested.setVisible(bool(sug))
        self._clear_warnings()
        self._warnings = []
        if p is None or p <= 0:
            self.risk_line.setText(f"Risk per trade {fmt.money(self.portfolio.risk_per_trade)}"
                                   f" ({fmt.pct(self.portfolio.risk_pct, 2)} of max)")
            self.levels_line.setText("Enter price and initial stop to see R levels")
            return
        deployed = self._deployed_excluding_edit()
        chk = check_buy(price=p, stop=s, shares=n or ZERO, deployed=deployed, max_amount=self.portfolio.max_amount,
                        risk_per_trade=self.portfolio.risk_per_trade)
        parts = []
        if chk.r_per_share is not None and chk.r_per_share > 0:
            parts.append(f"R/share {fmt.price(chk.r_per_share)}")
            if n:
                parts.append(f"Risk {fmt.money(chk.risk_dollars)} ({fmt.pct(chk.risk_pct_of_max, 2)})")
        if n:
            parts.append(f"Cost {fmt.money(chk.cost)}")
        self.risk_line.setText(" · ".join(parts) or "—")
        if s is not None and s < p:
            plan = as_plan(self.exit_plan.currentData())
            lad = Ladder(p, s, n or Decimal(1), plan)
            ks = (1, 2, 3) if plan == ExitPlan.P123 else (1, 2, 3, 6)
            self.levels_line.setText(" · ".join(f"{k}R {fmt.price(cents(lad.level(k)))}" for k in ks))
        else:
            self.levels_line.setText("")
        for w in chk.warnings:
            if w.blocking:
                continue
            self._add_warning(w.message)
        self._warnings = [w for w in chk.warnings if not w.blocking]
        low = low_cash_warning(self.ctx, self.portfolio, self.edit_trade, chk.cost if n else ZERO)
        if low:
            self._add_warning(low)
            self._warnings.append(low)

    def _sell_plan(self) -> tuple[list[tuple[int, Decimal]] | None, str | None]:
        lot = self._selected_lot()
        n = self.sell_shares.value()
        if lot is None or n is None or n <= 0:
            return None, None
        excl = self._exclude_sell_id()
        rem = remaining_excluding(lot, excl)
        if n <= rem:
            return [(lot.id, n)], None
        if self.edit_trade is not None:
            return None, f"Lot #{lot.no} has only {fmt.shares(rem)} shares open"
        try:
            plan = fifo_allocate(self._open_lots(), n, first_lot_id=lot.id, exclude_sell_id=excl)
        except LedgerError:
            total = sum((remaining_excluding(x, excl) for x in self._open_lots()), ZERO)
            return None, f"Only {fmt.shares(total)} shares of {self.sym()} are open"
        return plan, None

    def _update_sell_panel(self) -> None:
        plan, err = self._sell_plan()
        price = self.sell_price.value()
        lot = self._selected_lot()
        if not self._stop_out_touched and lot is not None:
            self.stop_out.blockSignals(True)
            self.stop_out.setChecked(price is not None and price <= lot.current_stop)
            self.stop_out.blockSignals(False)
        lots = {lot.id: lot for lot in self.snap.lots}
        self.split_strip.setVisible(bool(plan and len(plan) > 1))
        if plan and len(plan) > 1:
            self.split_label.setText(
                "This sell is larger than the selected lot. Split: "
                + ", ".join(f"#{lots[lid].no} × {fmt.shares(q)}" for lid, q in plan))
        if err:
            self.realized_line.setText(err)
            return
        if not self.lot.count():
            self.realized_line.setText(f"No open lots for {self.sym() or 'this symbol'}")
            return
        if plan is None or price is None:
            self.realized_line.setText("Realized: —")
            return
        pl = sum((q * (price - lots[lid].entry) for lid, q in plan), ZERO)
        risk = sum((lots[lid].shares * lots[lid].r for lid, _ in plan), ZERO)
        r = pl / risk if risk else None
        self.realized_line.setText(f"Realized: {fmt.signed_money(pl)} · {fmt.r_mult(r)}")
        tone = "positive" if pl > 0 else "negative" if pl < 0 else None
        self.realized_line.setProperty("tone", tone)
        self.realized_line.style().polish(self.realized_line)

    def _update_primary(self) -> None:
        warn = self.is_buy() and bool(getattr(self, "_warnings", []))
        self.primary_btn.setText("Save anyway" if warn else "Save trade")

    # ------------------------------------------------------------------ keyboard
    def eventFilter(self, obj: QObject, e: QEvent) -> bool:  # noqa: N802
        if e.type() == QEvent.Type.KeyPress and self.side.isEnabled():
            fw = self.focusWidget()
            typing = isinstance(fw, (QLineEdit, QPlainTextEdit, QAbstractSpinBox, QComboBox))
            if not typing and e.key() in (Qt.Key.Key_B, Qt.Key.Key_S) and not e.modifiers():
                self.side.set_value("BUY" if e.key() == Qt.Key.Key_B else "SELL")
                return True
        return super().eventFilter(obj, e)

    # ------------------------------------------------------------------ submit
    def _validate_common(self) -> bool:
        ok = True
        self.grid.clear_errors()
        if not self.sym():
            self.grid.set_error("symbol", "Enter a symbol")
            ok = False
        if _pydate(self.date.date()) > self.ctx.today():
            self.grid.set_error("date", "Date cannot be in the future")
            ok = False
        return ok

    def submit(self) -> bool:
        self._check_symbol_before_save()
        ok = self._validate_common()
        notes = self.notes.toPlainText().strip() or None
        d = _pydate(self.date.date())
        pid = self.portfolio.id
        assert pid is not None
        try:
            if self.is_buy():
                g = self.buy_grid
                g.clear_errors()
                p, s, n = self.price.value(), self.stop.value(), self.shares.value()
                if p is None or p <= 0:
                    g.set_error("price", "Enter a price")
                    ok = False
                if s is None:
                    g.set_error("stop", "Initial stop is required")
                    ok = False
                elif p is not None and s >= p:
                    g.set_error("stop", "Initial stop must be below the price")
                    ok = False
                elif s <= 0:
                    g.set_error("stop", "Initial stop must be positive")
                    ok = False
                if n is None or n <= 0:
                    g.set_error("shares", "Enter shares")
                    ok = False
                if not ok:
                    return False
                strategy = self.strategy.currentText() if self.strategy.currentIndex() > 0 else None
                timeframe = self.timeframe.currentText() if self.timeframe.currentIndex() > 0 else None
                if self.edit_trade is not None:
                    t = replace(self.edit_trade, symbol=self.sym(), trade_date=d, price=p, initial_stop=s, shares=n,
                                strategy=strategy, timeframe=timeframe, notes=notes,
                                exit_plan=self.exit_plan.currentData())
                    self.result_trades = [self.ctx.repos.trades.update(t)]
                else:
                    t = Trade(None, pid, self.sym(), Side.BUY, d, n, p, initial_stop=s,  # type: ignore[arg-type]
                              strategy=strategy, timeframe=timeframe, notes=notes,
                              exit_plan=self.exit_plan.currentData())
                    self.result_trades = [self.ctx.repos.trades.add(t)]
            else:
                g = self.sell_grid
                g.clear_errors()
                price, n = self.sell_price.value(), self.sell_shares.value()
                lot = self._selected_lot()
                if lot is None:
                    g.set_error("lot", "No open lot to sell from")
                    ok = False
                if n is None or n <= 0:
                    g.set_error("sell_shares", "Enter shares")
                    ok = False
                if price is None or price <= 0:
                    g.set_error("sell_price", "Enter a price")
                    ok = False
                if not ok:
                    return False
                plan, err = self._sell_plan()
                if err or plan is None:
                    g.set_error("sell_shares", err or "Invalid quantity")
                    return False
                reason = "STOP" if self.stop_out.isChecked() else None
                if len(plan) > 1 and not self.split_check.isChecked():
                    g.set_error("sell_shares", f"Lot #{lot.no} has only {fmt.shares(plan[0][1])} shares open")
                    return False
                if self.edit_trade is not None:
                    t = replace(self.edit_trade, trade_date=d, price=price, shares=n, lot_id=lot.id, notes=notes,
                                exit_reason=reason)
                    self.result_trades = [self.ctx.repos.trades.update(t)]
                else:
                    trades = [Trade(None, pid, self.sym(), Side.SELL, d, q, price, lot_id=lid, notes=notes,
                                    exit_reason=reason)
                              for lid, q in plan]
                    self.result_trades = self.ctx.repos.trades.add_many(trades)
        except RepoError as e:
            self.show_error(str(e))
            return False
        self.saved.emit(self.result_trades)
        return True
