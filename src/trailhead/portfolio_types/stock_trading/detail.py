"""Detail pane (PLAN §7.4): candle chart with level lines + one ladder card per open lot."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ...context import AppContext
from ...core.money import cents
from ...core.r_engine import ActionKind
from ...core.snapshot import LotView, PortfolioSnapshot, PositionView
from ...ui import format as fmt
from ...ui import theme
from ...ui.charts.candle_chart import CandleChart, ChartLevels, TradeMarker
from ...ui.theme import tokens
from ...ui.widgets.badge import Badge
from ...ui.widgets.common import button, icon_button, label
from ...ui.widgets.pill import LevelPill

LADDER_KS = ["-1", "-0.5", "0", "1", "2", "3", "6", "9", "12", "15", "18"]


class LadderCard(QFrame):
    confirm = Signal(int)  # lot id
    record_sell = Signal(int)

    def __init__(self, lv: LotView, near_pct: Decimal):
        super().__init__()
        self.setProperty("role", "ladder-card")
        p = theme.palette()
        lot, st = lv.lot, lv.state
        v = QVBoxLayout(self)
        v.setContentsMargins(tokens.SPACE[3], tokens.SPACE[3], tokens.SPACE[3], tokens.SPACE[3])
        v.setSpacing(tokens.SPACE[1] + 2)
        head = QHBoxLayout()
        t = QLabel(f"Lot #{lot.no}")
        t.setFont(theme.font("body_strong"))
        head.addWidget(t)
        head.addWidget(label(f"{fmt.short_date(lot.entry_date)} · {fmt.shares(lv.remaining)}/{fmt.shares(lot.shares)}"
                             f" sh @ {fmt.price(lot.entry)}", role="muted", font_token="num"))
        head.addStretch(1)
        head.addWidget(label(f"{lot.exit_plan} · R {fmt.price(lot.r)}", role="muted", font_token="num"))
        v.addLayout(head)

        pills = QHBoxLayout()
        pills.setSpacing(tokens.SPACE[2])
        stop_state = "hit" if lv.stop_hit else ("near" if lv.last is not None and lv.last <= lot.current_stop *
                                                (1 + near_pct) else "normal")
        pills.addWidget(LevelPill("stop", fmt.price(lot.current_stop), "current", stop_state))
        pills.addWidget(LevelPill("target", fmt.price(st.next_target), f"{st.next_r}R"))
        pills.addStretch(1)
        if lv.current_r is not None:
            r_lab = label(fmt.r_mult(lv.current_r), font_token="num",
                          tone="positive" if lv.current_r > 0 else "negative" if lv.current_r < 0 else None)
            pills.addWidget(r_lab)
        v.addLayout(pills)

        lad = lot.ladder
        last_i = max(st.next_milestone.index, 4)
        for i in range(1, last_i + 1):
            m = lad.milestone(i)
            reached = i <= st.reached.index
            is_next = i == st.next_milestone.index
            row = QHBoxLayout()
            row.setSpacing(tokens.SPACE[2])
            mark = QLabel("✓" if reached else "◆" if is_next else "○")
            mark.setFixedWidth(14)
            mark.setStyleSheet(f"color: {p.positive if reached else p.target if is_next else p.text_faint};")
            row.addWidget(mark)
            name = label(f"{m.name}", font_token="num", tone=None if reached or is_next else "faint")
            name.setFixedWidth(34)
            row.addWidget(name)
            trig = lad.trigger(m)
            row.addWidget(label(f"{m.label:>4} {fmt.price(cents(trig))}", font_token="num",
                                tone=None if reached or is_next else "faint"))
            row.addStretch(1)
            parts = []
            if m.tranche:
                parts.append(f"sell {fmt.shares(lad.tranche_sizes()[m.tranche - 1])}")
            parts.append(f"→ {fmt.price(cents(lad.stop_for(m)))}")
            row.addWidget(label(" · ".join(parts), role="muted" if reached or is_next else "faint",
                                font_token="caption"))
            v.addLayout(row)

        sold = label(f"Sold {fmt.shares(st.actual_sold)} of {fmt.shares(st.expected_sold)} expected at "
                     f"{st.reached.name} · ladder stop {fmt.price(cents(st.ladder_stop))}", role="muted")
        sold.setFont(theme.font("caption"))
        sold.setWordWrap(True)
        v.addWidget(sold)
        acts = {a.kind: a for a in st.actions}
        if acts:
            row = QHBoxLayout()
            row.setSpacing(tokens.SPACE[2])
            top = st.top_action
            kind = "stop_hit" if top and top.kind == ActionKind.STOP_HIT else "action"
            from .table_model import action_text

            row.addWidget(Badge(action_text(top, st.reached.label), kind))
            row.addStretch(1)
            if ActionKind.RAISE_STOP in acts:
                b = button(f"Confirm {fmt.price(acts[ActionKind.RAISE_STOP].price)}", "primary", size="small")
                b.setStyleSheet(f"background: {p.stop}; color: {p.text_on_accent};")
                b.clicked.connect(lambda: self.confirm.emit(lot.id))
                row.addWidget(b)
            if ActionKind.SELL_TRANCHE in acts or ActionKind.STOP_HIT in acts:
                b = button("Record stop-out" if ActionKind.STOP_HIT in acts else "Record sell", size="small")
                b.clicked.connect(lambda: self.record_sell.emit(lot.id))
                row.addWidget(b)
            v.addLayout(row)


class DetailPane(QFrame):
    close_requested = Signal()
    confirm = Signal(object, int)  # pv, lot id
    record_sell = Signal(object, int)

    def __init__(self, ctx: AppContext, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setProperty("role", "panel")
        self.pv: PositionView | None = None
        self.snap: PortfolioSnapshot | None = None
        v = QVBoxLayout(self)
        v.setContentsMargins(tokens.SPACE[4], tokens.SPACE[3], tokens.SPACE[3], tokens.SPACE[3])
        v.setSpacing(tokens.SPACE[2])
        head = QHBoxLayout()
        self.title = QLabel("Select a position")
        self.title.setFont(theme.font("title"))
        head.addWidget(self.title)
        self.subtitle = label("", role="muted")
        head.addWidget(self.subtitle)
        head.addStretch(1)
        self.loading = label("", role="caption")
        head.addWidget(self.loading)
        close = icon_button("x", "Hide detail pane", size=16)
        close.clicked.connect(self.close_requested.emit)
        head.addWidget(close)
        v.addLayout(head)
        body = QHBoxLayout()
        body.setSpacing(tokens.SPACE[3])
        self.chart = CandleChart()
        body.addWidget(self.chart, 1)
        self.cards_scroll = QScrollArea()
        self.cards_scroll.setWidgetResizable(True)
        self.cards_scroll.setFixedWidth(392)
        self.cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self.cards_scroll)
        v.addLayout(body, 1)
        self._fetching: set[str] = set()

    def show_position(self, pv: PositionView | None, snap: PortfolioSnapshot | None) -> None:
        self.pv, self.snap = pv, snap
        if pv is None:
            self.title.setText("Select a position")
            self.subtitle.setText("")
            self.chart.clear()
            self._set_cards([])
            return
        name = pv.market.name or ""
        self.title.setText(pv.symbol)
        self.subtitle.setText(" · ".join(x for x in (name, pv.market.sector) if x))
        self._draw_chart()
        near = Decimal(str(self.ctx.setting("alerts.near_stop_pct") or 0.02))
        self._set_cards([LadderCard(lv, near) for lv in pv.open_views])

    def _set_cards(self, cards: list[LadderCard]) -> None:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, tokens.SPACE[1], 0)
        v.setSpacing(tokens.SPACE[2])
        for c in cards:
            c.confirm.connect(lambda lot_id: self.confirm.emit(self.pv, lot_id))
            c.record_sell.connect(lambda lot_id: self.record_sell.emit(self.pv, lot_id))
            v.addWidget(c)
        if not cards and self.pv is not None:
            msg = label("Position closed.\nRealized " + fmt.signed_money(self.pv.realized_pl) + " · " +
                        fmt.r_mult(self.pv.realized_r), role="muted")
            msg.setWordWrap(True)
            v.addWidget(msg)
        v.addStretch(1)
        self.cards_scroll.setWidget(w)

    def _draw_chart(self) -> None:
        pv = self.pv
        if pv is None:
            return
        today = self.ctx.today()
        end = pv.position.close_date or today
        start = min(pv.position.open_date - timedelta(days=45), end - timedelta(days=183))
        bars = self.ctx.repos.market.bars(pv.symbol, start, end + timedelta(days=10))
        if (not bars or bars[0].date > pv.position.open_date) and self.ctx.market is not None \
                and pv.symbol not in self._fetching:
            self._fetching.add(pv.symbol)
            self.loading.setText("Loading history…")
            sym = pv.symbol
            self.ctx.market.fetch_history(sym, start, lambda res, s=sym: self._fetched(s, res))
        levels = ChartLevels()
        sv, tv = pv.stop_view, pv.target_view
        lv = sv or tv or (pv.lot_views[0] if pv.lot_views else None)
        if lv is not None:
            lad = lv.lot.ladder
            levels.entry = lv.lot.entry
            levels.ladder = [(f"{k}R", cents(lad.level(Decimal(k)))) for k in LADDER_KS
                             if Decimal(k) <= max(Decimal(6), lv.state.next_milestone.trigger_k or 0,
                                                  Decimal(lv.state.next_r))]
        if not pv.is_closed:
            levels.stop = pv.stop
            levels.target = pv.next_target
        markers = [TradeMarker(t.trade_date, t.side.value, t.price, f"{t.side.value} {t.shares}@{t.price}")
                   for t in pv.position.trades]
        divs = [d.ex_date for d in self.ctx.repos.market.dividends([pv.symbol], start)]
        self.chart.set_data(pv.symbol, bars, levels, markers,
                            pv.market.next_earnings if not pv.is_closed else None, divs)

    def _fetched(self, symbol: str, result) -> None:
        self._fetching.discard(symbol)
        if not self.isVisible():  # window closed (or app shutting down) while the fetch ran
            return
        self.loading.setText("" if not isinstance(result, Exception) else "History unavailable")
        if self.pv is not None and self.pv.symbol == symbol and not isinstance(result, Exception):
            self._draw_chart()
