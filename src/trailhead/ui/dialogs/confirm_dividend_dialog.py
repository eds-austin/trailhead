"""Confirm a dividend for a Long-term holding, optionally reinvesting it (DRIP): the shares it bought are added to
the holding as a BUY at the reinvestment price."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_DOWN, Decimal

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QAbstractSpinBox, QCheckBox, QDateEdit, QWidget

from ...context import AppContext
from ...core.dividends import DividendProposal
from ...core.holdings import HoldingView
from ...core.models import CashEvent, Portfolio, Trade
from ...core.splits import Split, clean_ratio, looks_like_spinoff, split_factor
from ...data.repo import RepoError
from ...portfolio_types.long_term.service import Reinvestment, record_dividend
from .. import format as fmt
from ..widgets.common import label
from ..widgets.number_edit import NumberEdit
from .base import AppDialog, FieldGrid, inset_panel


def _qdate(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _pydate(q: QDate) -> date:
    return date(q.year(), q.month(), q.day())


def _date_edit(d: date) -> QDateEdit:
    e = QDateEdit(_qdate(d))
    e.setCalendarPopup(True)
    e.setDisplayFormat("yyyy-MM-dd")
    e.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    return e


class ConfirmDividendDialog(AppDialog):
    """With a proposal: confirm that pending dividend. Without one: record a dividend by hand."""

    def __init__(self, ctx: AppContext, portfolio: Portfolio, holding: HoldingView,
                 proposal: DividendProposal | None = None, amount: Decimal | None = None,
                 parent: QWidget | None = None):
        super().__init__(f"{holding.symbol} dividend", parent, width=480, primary_text="Confirm dividend")
        self.ctx, self.portfolio, self.holding, self.proposal = ctx, portfolio, holding, proposal
        self.result: tuple[CashEvent, Trade | None] | None = None
        today = ctx.today()
        if proposal is not None:
            f = split_factor(holding.splits, holding.symbol, proposal.ex_date)  # in that day's shares, as paid
            intro = (f"{fmt.shares(proposal.shares / f)} sh × {fmt.number(proposal.amount_per_share * f, 4)} per share"
                     f" · ex-date {fmt.short_date(proposal.ex_date)}"
                     + (f" · pays {fmt.short_date(proposal.pay_date)}" if proposal.pay_date else ""))
            paid = proposal.pay_date if proposal.pay_date and proposal.pay_date <= today else min(proposal.ex_date,
                                                                                                 today)
        else:
            intro = f"Record a dividend received on {fmt.shares(holding.open_shares)} sh of {holding.symbol}."
            paid = today
        msg = label(intro, role="muted")
        msg.setWordWrap(True)
        self.body.addWidget(msg)

        g = FieldGrid()
        self.grid = g
        self.amount = NumberEdit(2, placeholder="0.00")
        self.amount.set_value(amount if amount is not None else proposal.amount if proposal else None)
        g.add("amount", "Amount", self.amount, 0)
        self.paid = _date_edit(paid)
        g.add("paid", "Received on", self.paid, 1)
        g.next_row()
        self.ex_date = _date_edit(proposal.ex_date if proposal else today)
        self.ex_date.setEnabled(proposal is None)
        g.add("ex", "Ex-date", self.ex_date, 0)
        self.body.addLayout(g)

        self.reinvest = QCheckBox("Reinvest this dividend (DRIP)")
        self.reinvest.setToolTip("Adds the shares the dividend bought to the holding as a BUY")
        self.reinvest.setChecked(any(lot.buy.drip for lot in holding.lots))  # on DRIP before → on DRIP now
        self.body.addWidget(self.reinvest)
        rg = FieldGrid()
        self.rgrid = rg
        self.r_shares = NumberEdit(6, placeholder="0.0000")
        self.r_price = NumberEdit(4, placeholder="0.00")
        rg.add("r_shares", "Shares received", self.r_shares, 0)
        rg.add("r_price", "Price per share", self.r_price, 1)
        self.r_box = QWidget()
        self.r_box.setLayout(rg)
        self.body.addWidget(self.r_box)
        panel, pv = inset_panel()
        self.line = label("", font_token="num")
        self.line.setWordWrap(True)
        pv.addWidget(self.line)
        self.body.addWidget(panel)

        self._auto_shares = True
        self.r_shares.textEdited.connect(lambda _t: setattr(self, "_auto_shares", False))
        self.reinvest.toggled.connect(lambda _b: self._on_reinvest())
        for w in (self.amount, self.r_price):
            w.textChanged.connect(lambda _t: self._suggest_shares())
        self.r_shares.textChanged.connect(lambda _t: self._update())
        self.paid.dateChanged.connect(lambda _d: self._prefill_price())
        self._prefill_price()
        self._on_reinvest()

    # ------------------------------------------------------------------ live
    def _prefill_price(self) -> None:
        """Close on the payment date (DRIP plans buy on the pay date), else the last price. Yahoo's closes are in
        today's (post-split) terms; the reinvestment is entered as it happened, so a close from before a split is
        put back into that day's price (×3 for a 1-for-3 consolidation is undone)."""
        if self.r_price.user_edited:
            return
        sym, day = self.holding.symbol, _pydate(self.paid.date())
        bars = [b for b in self.ctx.repos.market.bars(sym, end=day) if b.close is not None]
        if bars and (day - bars[-1].date).days <= 5:
            splits = [Split(m.symbol, m.ex_date, clean_ratio(m.ratio)) for m in self.ctx.repos.splits.market([sym])
                      if not looks_like_spinoff(m.ratio)]
            self.r_price.set_value(bars[-1].close * split_factor(splits, sym, day))
        else:
            self.r_price.set_value(self.holding.last)

    def _suggest_shares(self) -> None:
        amt, px = self.amount.value(), self.r_price.value()
        if self._auto_shares and amt and px:
            self.r_shares.set_value((amt / px).quantize(Decimal("0.0001"), ROUND_DOWN))
        self._update()

    def _on_reinvest(self) -> None:
        self.r_box.setVisible(self.reinvest.isChecked())
        self.primary_btn.setText("Confirm && reinvest" if self.reinvest.isChecked() else "Confirm dividend")
        self._suggest_shares()
        self.adjustSize()

    def _update(self) -> None:
        amt = self.amount.value()
        h = self.holding
        if not self.reinvest.isChecked():
            self.line.setText(f"Records {fmt.money(amt)} of dividend income" if amt else "Enter the amount")
            return
        n, px = self.r_shares.value(), self.r_price.value()
        if not (amt and n and px):
            self.line.setText("Enter the shares and price of the reinvestment")
            return
        cost = n * px
        text = (f"Buys {fmt.shares(n)} sh @ {fmt.price(px)} = {fmt.money(cost)}\n"
                f"{h.symbol}: {fmt.shares(h.open_shares)} → {fmt.shares(h.open_shares + n)} sh")
        left = amt - cost
        if abs(left) >= Decimal("0.01"):
            text += f"\n{'Cash left' if left > 0 else 'Over the dividend by'} {fmt.money(abs(left))}"
        self.line.setText(text)

    # ------------------------------------------------------------------ save
    def submit(self) -> bool:
        self.grid.clear_errors()
        self.rgrid.clear_errors()
        amt = self.amount.value()
        paid, ex = _pydate(self.paid.date()), _pydate(self.ex_date.date())
        if amt is None or amt <= 0:
            self.grid.set_error("amount", "Enter a positive amount")
            return False
        if paid > self.ctx.today():
            self.grid.set_error("paid", "Cannot be in the future")
            return False
        if paid < ex:
            self.grid.set_error("paid", "Paid before the ex-date?")
            return False
        reinvest = None
        if self.reinvest.isChecked():
            n, px = self.r_shares.value(), self.r_price.value()
            if not n or n <= 0:
                self.rgrid.set_error("r_shares", "Enter the shares received")
                return False
            if not px or px <= 0:
                self.rgrid.set_error("r_price", "Enter the price")
                return False
            reinvest = Reinvestment(n, px)
        p = self.proposal
        note = f"{fmt.shares(p.shares)} sh × {p.amount_per_share}" if p is not None else "Manual"
        try:
            self.result = record_dividend(self.ctx.repos, self.portfolio.id, self.holding.symbol, amt, paid,  # type: ignore[arg-type]
                                          ex, note, reinvest, self.holding)
        except RepoError as e:
            self.show_error(str(e))
            return False
        return True
