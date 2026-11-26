"""Cash portfolio dialogs: account, move money, true up, confirm interest, change rate."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QAbstractSpinBox, QCheckBox, QComboBox, QDateEdit, QHBoxLayout, QLineEdit, QWidget

from ...context import AppContext
from ...core.cash import AccountView, CashSnapshot, InterestProposal
from ...core.models import CashAccount, CashAccountKind, CashLink, CashRate, CashTxn, CashTxnKind, Portfolio
from ...data.repo import RepoError
from .. import format as fmt
from ..widgets.common import button, label
from ..widgets.number_edit import NumberEdit
from ..widgets.segmented import Segmented
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


def _payout_combo(day: int) -> QComboBox:
    c = QComboBox()
    c.addItem("Last day of the month", 0)
    for d in range(1, 29):
        c.addItem(f"Day {d}", d)
    c.setCurrentIndex(max(0, c.findData(day)))
    return c


def _pct_to_frac(v: Decimal | None) -> Decimal | None:
    return None if v is None else (v / 100).normalize()


# ---------------------------------------------------------------------------- account
class CashAccountDialog(AppDialog):
    """New account (opened with today's balance) or edit an existing one."""

    def __init__(self, ctx: AppContext, portfolio: Portfolio, view: AccountView | None = None,
                 parent: QWidget | None = None):
        acct = view.account if view else None
        super().__init__("Edit account" if acct else "New cash account", parent, width=520,
                         primary_text="Save" if acct else "Add account")
        self.ctx = ctx
        self.portfolio = portfolio
        self.view = view
        self.result: CashAccount | None = None
        self.kind = Segmented([("SAVINGS", "Savings"), ("MONEY_MARKET", "Money market fund")])
        self.kind.set_value(acct.kind.value if acct else "SAVINGS", emit=False)
        self.kind.changed.connect(lambda _v: self._on_kind())
        self.body.addWidget(self.kind)
        g = FieldGrid()
        self.grid = g
        self.name = QLineEdit(acct.name if acct else "")
        self.name.setPlaceholderText("e.g. Ally High-Yield Savings")
        g.add("name", "Name", self.name, 0, span=2)
        g.next_row()
        self.institution = QLineEdit(acct.institution or "" if acct else "")
        self.institution.setPlaceholderText("Optional, e.g. Ally Bank")
        g.add("institution", "Institution", self.institution, 0, span=2)
        g.next_row()
        self.symbol = QLineEdit(acct.symbol or "" if acct else "")
        self.symbol.setPlaceholderText("e.g. SPAXX, VMFXX, SWVXX")
        self.symbol_label = g.add("symbol", "Fund symbol", self.symbol, 0)
        self.manual = QCheckBox("Enter the yield myself")
        self.manual.setChecked(bool(acct and acct.manual_rate))
        self.manual.setToolTip("Otherwise the fund's yield is fetched with prices and updates itself")
        self.manual.toggled.connect(lambda _on: self._on_kind())
        g.add("manual", "", self.manual, 1)
        g.next_row()
        self.apy = NumberEdit(3, placeholder="4.00")
        if view is not None and view.apy is not None:
            self.apy.set_value(view.apy * 100)
        self.apy_label = g.add("apy", "APY %", self.apy, 0)
        self.payout = _payout_combo(acct.payout_day if acct else 0)
        self.payout.setToolTip("When the bank or fund pays interest each month")
        g.add("payout", "Interest paid on", self.payout, 1)
        g.next_row()
        today = ctx.today()
        self.balance = NumberEdit(2, placeholder="0.00")
        self.opened = _date_edit(acct.opened_on if acct else today)
        if view is not None:
            self.balance.set_value(view.opening)
        g.add("balance", "Opening balance" if acct else "Balance today", self.balance, 0)
        g.add("opened", "Opened on" if acct else "As of", self.opened, 1)
        g.next_row()
        self.notes = QLineEdit(acct.notes or "" if acct else "")
        self.notes.setPlaceholderText("Optional")
        g.add("notes", "Notes", self.notes, 0, span=2)
        self.body.addLayout(g)
        self.closed = QCheckBox("Account closed (hidden unless All is shown; no interest proposed)")
        self.closed.setChecked(bool(acct and acct.closed))
        self.closed.setVisible(acct is not None)
        self.body.addWidget(self.closed)
        self.hint = label("", role="muted")
        self.hint.setWordWrap(True)
        self.body.addWidget(self.hint)
        self._on_kind()
        self.name.setFocus()

    def _is_fund(self) -> bool:
        return self.kind.value() == "MONEY_MARKET"

    def _on_kind(self) -> None:
        fund = self._is_fund()
        for w in (self.symbol, self.symbol_label, self.manual):
            w.setVisible(fund)
        show_apy = not fund or self.manual.isChecked()
        self.apy.setVisible(show_apy)
        self.apy_label.setVisible(show_apy)
        self.apy_label.setText("Yield %" if fund else "APY %")
        if self.view is not None:
            self.hint.setText("Changing the rate here applies from today. Use Change rate… to date a change.")
        elif fund and not self.manual.isChecked():
            self.hint.setText("The fund's yield (1 share = $1) is fetched with the next price refresh.")
        else:
            self.hint.setText("Interest is estimated from the balance and rate each month and proposed for you to "
                              "confirm.")
        QTimer.singleShot(0, self.adjustSize)

    def submit(self) -> bool:
        g = self.grid
        g.clear_errors()
        name = self.name.text().strip()
        fund = self._is_fund()
        symbol = self.symbol.text().strip().upper() or None if fund else None
        apy_pct = self.apy.value()
        bal = self.balance.value()
        ok = True
        if not name:
            g.set_error("name", "Enter a name")
            ok = False
        if fund and not symbol:
            g.set_error("symbol", "Enter the fund's symbol")
            ok = False
        needs_apy = not fund or self.manual.isChecked()
        if needs_apy and (apy_pct is None or apy_pct < 0 or apy_pct > 100):
            g.set_error("apy", "Enter a rate between 0 and 100")
            ok = False
        if bal is None or bal < 0:
            g.set_error("balance", "Enter the balance (0 or more)")
            ok = False
        opened = _pydate(self.opened.date())
        if opened > self.ctx.today():
            g.set_error("opened", "Can't be in the future")
            ok = False
        if not ok:
            return False
        apy = _pct_to_frac(apy_pct) if needs_apy else None
        repo = self.ctx.repos.cash_accounts
        fields = dict(name=name, kind=CashAccountKind(self.kind.value()), institution=self.institution.text().strip()
                      or None, symbol=symbol, manual_rate=fund and self.manual.isChecked(),
                      payout_day=int(self.payout.currentData()), opened_on=opened,
                      notes=self.notes.text().strip() or None)
        try:
            if self.view is None:
                self.result = repo.create(CashAccount(None, self.portfolio.id, **fields), bal,  # type: ignore[arg-type]
                                          apy)
            else:
                a = replace(self.view.account, closed=self.closed.isChecked(), **fields)
                repo.update(a)
                opening = next((t for t in self.view.txns if t.kind == CashTxnKind.OPENING), None)
                if opening is not None and opening.amount != bal:
                    repo.update_txn(replace(opening, amount=bal, txn_date=opened))
                if apy is not None and apy != self.view.apy:
                    today = self.ctx.today()
                    repo.set_rate(CashRate(a.id, max(today, opened) if self.view.rates else opened, apy))  # type: ignore[arg-type]
                self.result = a
        except RepoError as e:
            self.show_error(str(e))
            return False
        self.ctx.data_changed(self.portfolio.id)  # type: ignore[arg-type]
        return True


# ---------------------------------------------------------------------------- move money
class MoveMoneyDialog(AppDialog):
    """Deposit, withdrawal or transfer between accounts; with `txn`, edits that entry."""

    def __init__(self, ctx: AppContext, portfolio: Portfolio, snap: CashSnapshot, account_id: int | None = None,
                 txn: CashTxn | None = None, parent: QWidget | None = None):
        super().__init__("Edit entry" if txn else "Move money", parent, width=520,
                         primary_text="Save" if txn else "Record")
        self.ctx = ctx
        self.portfolio = portfolio
        self.snap = snap
        self.txn = txn
        accts = [v for v in snap.accounts if not v.account.closed or v.id in (account_id, txn and txn.account_id)]
        self.accts = accts
        options = [("DEPOSIT", "Deposit", "positive"), ("WITHDRAWAL", "Withdrawal", "negative")]
        if len(accts) > 1 or (txn and txn.kind == CashTxnKind.TRANSFER):
            options.append(("TRANSFER", "Transfer"))
        if txn and txn.kind in (CashTxnKind.INTEREST, CashTxnKind.ADJUSTMENT):
            options = [(txn.kind.value, txn.kind.value.title())]
        self.kind = Segmented(options)
        self.kind.changed.connect(lambda _v: self._on_kind())
        self.body.addWidget(self.kind)
        g = FieldGrid()
        self.grid = g
        self.account = QComboBox()
        self.to = QComboBox()
        for v in accts:
            self.account.addItem(v.account.name, v.id)
            self.to.addItem(v.account.name, v.id)
        self.account_label = g.add("account", "Account", self.account, 0, span=2)
        g.next_row()
        self.to_label = g.add("to", "To account", self.to, 0, span=2)
        g.next_row()
        self.date = _date_edit(txn.txn_date if txn else ctx.today())
        self.amount = NumberEdit(2, allow_negative=bool(txn and txn.kind == CashTxnKind.ADJUSTMENT),
                                 placeholder="0.00")
        g.add("date", "Date", self.date, 0)
        g.add("amount", "Amount", self.amount, 1)
        g.next_row()
        self.note = QLineEdit(txn.note or "" if txn else "")
        self.note.setPlaceholderText("Optional")
        g.add("note", "Note", self.note, 0, span=2)
        self.body.addLayout(g)
        self.balance_hint = label("", role="muted", font_token="num")
        self.body.addWidget(self.balance_hint)
        self.account.currentIndexChanged.connect(lambda _i: self._update_hint())
        self.to.currentIndexChanged.connect(lambda _i: self._update_hint())
        self.amount.textChanged.connect(lambda _t: self._update_hint())
        if txn is not None:
            self.kind.set_value(txn.kind.value, emit=False)
            self.kind.setEnabled(False)
            src = txn.account_id
            other = None
            if txn.kind == CashTxnKind.TRANSFER and txn.transfer_id:
                t2 = ctx.repos.cash_accounts.get_txn(txn.transfer_id)
                other = t2.account_id if t2 else None
                if txn.amount > 0 and other is not None:  # edit from the sending side's point of view
                    src, other = other, src
            self.account.setCurrentIndex(max(0, self.account.findData(src)))
            if other is not None:
                self.to.setCurrentIndex(max(0, self.to.findData(other)))
            self.account.setEnabled(False)
            self.to.setEnabled(False)
            self.amount.set_value(abs(txn.amount) if txn.kind != CashTxnKind.ADJUSTMENT else txn.amount)
        else:
            if account_id is not None:
                self.account.setCurrentIndex(max(0, self.account.findData(account_id)))
            if self.to.count() > 1:
                self.to.setCurrentIndex(1 if self.to.currentIndex() == self.account.currentIndex() else 0)
        self._on_kind()
        self.amount.setFocus()

    def _on_kind(self) -> None:
        transfer = self.kind.value() == "TRANSFER"
        self.to.setVisible(transfer)
        self.to_label.setVisible(transfer)
        self.account_label.setText("From account" if transfer else "Account")
        self._update_hint()
        QTimer.singleShot(0, self.adjustSize)

    def _view(self, aid) -> AccountView | None:
        return self.snap.get(aid) if aid is not None else None

    def _update_hint(self) -> None:
        v = self._view(self.account.currentData())
        if v is None:
            self.balance_hint.setText("")
            return
        amt = self.amount.value() or Decimal(0)
        k = self.kind.value()
        if self.txn is not None:
            self.balance_hint.setText(f"Balance today {fmt.money(v.balance)}")
            return
        after = v.balance + (amt if k == "DEPOSIT" else -amt)
        text = f"{v.account.name}: {fmt.money(v.balance)} → {fmt.money(after)}"
        if k == "TRANSFER":
            t = self._view(self.to.currentData())
            if t is not None:
                text += f"\n{t.account.name}: {fmt.money(t.balance)} → {fmt.money(t.balance + amt)}"
        self.balance_hint.setText(text)

    def submit(self) -> bool:
        g = self.grid
        g.clear_errors()
        amt = self.amount.value()
        k = self.kind.value()
        if amt is None or amt == 0 or (k != "ADJUSTMENT" and amt < 0):
            g.set_error("amount", "Enter an amount above zero")
            return False
        d = _pydate(self.date.date())
        aid = self.account.currentData()
        repo = self.ctx.repos.cash_accounts
        note = self.note.text().strip() or None
        try:
            if self.txn is not None:
                t = self.txn
                signed = amt if k in ("DEPOSIT", "INTEREST", "ADJUSTMENT") else -amt
                if k == "TRANSFER":
                    signed = amt if t.amount > 0 else -amt
                repo.update_txn(replace(t, txn_date=d, amount=signed, note=note))
            elif k == "TRANSFER":
                to = self.to.currentData()
                if to == aid:
                    g.set_error("to", "Choose a different account")
                    return False
                repo.transfer(aid, to, d, amt, note)
            else:
                repo.add(CashTxn(None, aid, d, CashTxnKind(k), amt, note=note))
        except RepoError as e:
            self.show_error(str(e))
            return False
        self.ctx.data_changed(self.portfolio.id)  # type: ignore[arg-type]
        return True


# ---------------------------------------------------------------------------- true up
class TrueUpDialog(AppDialog):
    """Enter the real balance on a date; the difference is booked as interest or an adjustment."""

    def __init__(self, ctx: AppContext, portfolio: Portfolio, view: AccountView, parent: QWidget | None = None):
        super().__init__(f"True up · {view.account.name}", parent, width=500, primary_text="True up")
        self.ctx = ctx
        self.portfolio = portfolio
        self.view = view
        self.result: CashTxn | None = None
        g = FieldGrid()
        self.grid = g
        self.date = _date_edit(ctx.today())
        self.actual = NumberEdit(2, allow_negative=True, placeholder="0.00")
        g.add("date", "Balance on", self.date, 0)
        g.add("actual", "Actual balance", self.actual, 1)
        self.body.addLayout(g)
        panel, pv = inset_panel()
        self.app_line = label("", font_token="num")
        self.diff_line = label("", font_token="num")
        self.pending_line = label("", role="muted")
        self.pending_line.setWordWrap(True)
        for w in (self.app_line, self.diff_line, self.pending_line):
            pv.addWidget(w)
        self.body.addWidget(panel)
        self.book_label = label("BOOK THE DIFFERENCE AS", role="caption")
        self.body.addWidget(self.book_label)
        self.book = Segmented([("INTEREST", "Interest"), ("ADJUSTMENT", "Adjustment")])
        self.book.setToolTip("Interest counts in interest earned and yield; an adjustment corrects the balance only")
        self.body.addWidget(self.book)
        g2 = FieldGrid()
        self.note = QLineEdit()
        self.note.setPlaceholderText("Optional, e.g. statement Nov 30")
        g2.add("note", "Note", self.note, 0, span=2)
        self.body.addLayout(g2)
        self._book_touched = False
        self.book.changed.connect(lambda _v: setattr(self, "_book_touched", True))
        self.date.dateChanged.connect(lambda _d: self._update())
        self.actual.textChanged.connect(lambda _t: self._update())
        self._update()
        self.actual.setFocus()

    def _diff(self) -> tuple[date, Decimal, Decimal | None]:
        d = _pydate(self.date.date())
        app = self.view.balance_on(d)
        actual = self.actual.value()
        return d, app, (actual - app) if actual is not None else None

    def _update(self) -> None:
        d, app, diff = self._diff()
        self.app_line.setText(f"Balance in the app on {fmt.short_date(d)}:  {fmt.money(app)}")
        if diff is None:
            self.diff_line.setText("Difference:  —")
        else:
            self.diff_line.setText(f"Difference:  {fmt.signed_money(diff)}" + ("  ✓ matches" if diff == 0 else ""))
        settled = [p for p in self.view.proposals() if p.period_end <= d]
        if settled:
            est = sum((p.amount for p in settled), Decimal(0))
            self.pending_line.setText(
                f"Unconfirmed interest up to this date, about {fmt.money(est)} "
                f"({len(settled)} payment{'s' if len(settled) != 1 else ''}), is settled by this true-up: the "
                "difference includes it.")
        else:
            self.pending_line.setText("")
        self.pending_line.setVisible(bool(settled))
        has_diff = bool(diff)
        self.book.setEnabled(has_diff)
        self.book_label.setEnabled(has_diff)
        if not self._book_touched and diff is not None:
            self.book.set_value("INTEREST" if diff > 0 else "ADJUSTMENT", emit=False)
        QTimer.singleShot(0, self.adjustSize)

    def submit(self) -> bool:
        self.grid.clear_errors()
        d, _app, diff = self._diff()
        if diff is None:
            self.grid.set_error("actual", "Enter the balance your bank shows")
            return False
        if d > self.ctx.today():
            self.grid.set_error("date", "Can't be in the future")
            return False
        if d < self.view.account.opened_on:
            self.grid.set_error("date", "Before the account was opened")
            return False
        kind = CashTxnKind(self.book.value()) if diff else CashTxnKind.ADJUSTMENT
        actual = self.actual.value()
        try:
            self.result = self.ctx.repos.cash_accounts.add(CashTxn(
                None, self.view.id, d, kind, diff, true_up_balance=actual, note=self.note.text().strip() or None))
        except RepoError as e:
            self.show_error(str(e))
            return False
        self.ctx.data_changed(self.portfolio.id)  # type: ignore[arg-type]
        return True


# ---------------------------------------------------------------------------- interest
class ConfirmInterestDialog(AppDialog):
    """Confirm (or correct) the expected interest for one payout period, or skip it."""

    def __init__(self, ctx: AppContext, portfolio: Portfolio, view: AccountView, proposal: InterestProposal,
                 parent: QWidget | None = None):
        super().__init__(f"Interest · {view.account.name}", parent, width=480, primary_text="Confirm")
        self.ctx = ctx
        self.portfolio = portfolio
        self.view = view
        self.proposal = proposal
        self.result: CashTxn | None = None
        p = proposal
        panel, pv = inset_panel()
        pv.addWidget(label(f"For {fmt.short_date(p.period_start + timedelta(days=1))} – "
                           f"{fmt.short_date(p.period_end)} · {p.days} days", font_token="body_strong"))
        rate = f"at {fmt.pct(p.apy, 2)} {'yield' if view.account.is_money_market else 'APY'}" if p.apy else ""
        pv.addWidget(label(f"Expected {fmt.money(p.amount)} {rate} on the daily balance", role="muted"))
        self.body.addWidget(panel)
        g = FieldGrid()
        self.grid = g
        self.amount = NumberEdit(2, placeholder="0.00")
        self.amount.set_value(p.amount)
        self.date = _date_edit(min(p.period_end, ctx.today()))
        g.add("amount", "Amount received", self.amount, 0)
        g.add("date", "Paid on", self.date, 1)
        self.body.addLayout(g)
        self.body.addWidget(label("Use the amount on your statement if it differs.", role="muted"))
        self.skip_btn = button("Skip this month", "ghost",
                               tooltip="No interest was paid: records $0 so it isn't proposed again")
        self.skip_btn.clicked.connect(self._skip)
        self.footer_left.addWidget(self.skip_btn)
        self.amount.setFocus()
        self.amount.selectAll()

    def _record(self, amount: Decimal) -> bool:
        try:
            self.result = self.ctx.repos.cash_accounts.add(CashTxn(
                None, self.view.id, _pydate(self.date.date()), CashTxnKind.INTEREST, amount,
                period_end=self.proposal.period_end))
        except RepoError as e:
            self.show_error(str(e))
            return False
        self.ctx.data_changed(self.portfolio.id)  # type: ignore[arg-type]
        return True

    def _skip(self) -> None:
        if self._record(Decimal(0)):
            self.accept()

    def submit(self) -> bool:
        self.grid.clear_errors()
        amt = self.amount.value()
        if amt is None or amt < 0:
            self.grid.set_error("amount", "Enter the interest received (0 or more)")
            return False
        return self._record(amt)


# ---------------------------------------------------------------------------- rate
class RateDialog(AppDialog):
    """Record a new APY (or fund yield) from a date."""

    def __init__(self, ctx: AppContext, portfolio: Portfolio, view: AccountView, parent: QWidget | None = None):
        fund = view.account.is_money_market
        super().__init__(f"Change {'yield' if fund else 'APY'} · {view.account.name}", parent, width=460,
                         primary_text="Save")
        self.ctx = ctx
        self.portfolio = portfolio
        self.view = view
        g = FieldGrid()
        self.grid = g
        self.apy = NumberEdit(3, placeholder="4.00")
        if view.apy is not None:
            self.apy.set_value(view.apy * 100)
        self.date = _date_edit(ctx.today())
        g.add("apy", "Yield %" if fund else "APY %", self.apy, 0)
        g.add("date", "From", self.date, 1)
        self.body.addLayout(g)
        hist = [f"{fmt.short_date(r.effective_date)}   {fmt.pct(r.apy, 2)}"
                + ("" if r.source == "MANUAL" else f"   ({r.source})") for r in view.rates[-8:]]
        if hist:
            self.body.addWidget(label("HISTORY", role="caption"))
            self.body.addWidget(label("\n".join(reversed(hist)), role="muted", font_token="num"))
        if fund and not view.account.manual_rate:
            self.body.addWidget(label("Saving switches this fund to a yield you enter; turn it back in Edit "
                                      "account.", role="muted"))
        self.apy.setFocus()
        self.apy.selectAll()

    def submit(self) -> bool:
        self.grid.clear_errors()
        v = self.apy.value()
        if v is None or v < 0 or v > 100:
            self.grid.set_error("apy", "Enter a rate between 0 and 100")
            return False
        d = max(_pydate(self.date.date()), self.view.account.opened_on)
        repo = self.ctx.repos.cash_accounts
        try:
            repo.set_rate(CashRate(self.view.id, d, _pct_to_frac(v)))  # type: ignore[arg-type]
            a = self.view.account
            if a.is_money_market and not a.manual_rate:
                repo.update(replace(a, manual_rate=True))
        except RepoError as e:
            self.show_error(str(e))
            return False
        self.ctx.data_changed(self.portfolio.id)  # type: ignore[arg-type]
        return True


# ---------------------------------------------------------------------------- links
class LinkPortfolioDialog(AppDialog):
    """Choose which Stock Trading / Long-term portfolios settle in this account, and from when."""

    def __init__(self, ctx: AppContext, portfolio: Portfolio, view: AccountView, parent: QWidget | None = None):
        super().__init__(f"Link portfolio · {view.account.name}", parent, width=520, primary_text="Link")
        self.ctx = ctx
        self.portfolio = portfolio
        self.view = view
        self.body.addWidget(label("From the date you choose, the portfolio's sells add cash here, its buys take cash "
                                  "out and its cash dividends add cash. Deployed / Max is not affected.",
                                  role="muted"))
        self.body.itemAt(self.body.count() - 1).widget().setWordWrap(True)
        repo = ctx.repos.cash_accounts
        accounts = {a.id: a.name for a in repo.list()}
        g = FieldGrid()
        self.grid = g
        self.target = QComboBox()
        for p in ctx.repos.portfolios.list():
            if p.type == "cash":
                continue
            link = repo.link_for(p.id)  # type: ignore[arg-type]
            if link is not None and link.account_id == view.id:
                continue
            note = f"  (now settles in {accounts.get(link.account_id, '?')})" if link else ""
            self.target.addItem(p.name + note, p.id)
        g.add("portfolio", "Portfolio", self.target, 0, span=2)
        g.next_row()
        self.since = _date_edit(ctx.today())
        self.since.setToolTip("Trades and dividends on or after this date settle in the account")
        g.add("since", "From", self.since, 0)
        self.body.addLayout(g)
        self.current = [(repo.link_for(pid), name) for pid, name in
                        ((p.id, p.name) for p in ctx.repos.portfolios.list() if p.type != "cash")]
        self.current = [(lk, n) for lk, n in self.current if lk is not None and lk.account_id == view.id]
        if self.current:
            self.body.addWidget(label("LINKED NOW", role="caption"))
            for lk, name in self.current:
                row = QHBoxLayout()
                row.addWidget(label(f"{name}  ·  since {fmt.short_date(lk.since)}"))
                row.addStretch(1)
                b = button("Unlink", "ghost", tooltip=f"{name} stops settling in this account")
                b.clicked.connect(lambda _=False, pid=lk.portfolio_id: self._unlink(pid))
                row.addWidget(b)
                self.body.addLayout(row)
        if self.target.count() == 0:
            self.target.addItem("No other Stock Trading or Long-term portfolio", None)
            self.target.setEnabled(False)
            self.primary_btn.setEnabled(False)
        self.changed = False
        self.linked_name: str | None = None

    def _unlink(self, pid: int) -> None:
        self.ctx.repos.cash_accounts.unlink(pid)
        self.ctx.data_changed(pid)
        self.ctx.data_changed(self.portfolio.id)  # type: ignore[arg-type]
        self.changed = True
        self.accept()

    def submit(self) -> bool:
        pid = self.target.currentData()
        if pid is None:
            return False
        since = _pydate(self.since.date())
        self.ctx.repos.cash_accounts.link(CashLink(pid, self.view.id, since))
        self.linked_name = self.target.currentText().split("  (")[0]
        self.ctx.data_changed(pid)
        self.ctx.data_changed(self.portfolio.id)  # type: ignore[arg-type]
        self.changed = True
        return True
