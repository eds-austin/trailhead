"""Domain dataclasses mirroring the stored rows (PLAN §5). Pure Python."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class CashKind(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    DIVIDEND = "DIVIDEND"
    ADJUSTMENT = "ADJUSTMENT"


class StopReason(StrEnum):
    MANUAL = "MANUAL"
    LADDER_CONFIRMED = "LADDER_CONFIRMED"


STRATEGIES = ("Pullback", "Breakout", "Reversion")
TIMEFRAMES = ("Monthly/Weekly", "Weekly/Daily", "Daily/Hourly", "Intraday")
# Long-term Holdings: what kind of investment a BUY is. Income = REITs, bond funds, BDCs, preferreds …
ASSET_TYPES = ("Equity", "ETF", "Income", "Precious metals", "Other")


@dataclass(frozen=True)
class Portfolio:
    id: int | None
    name: str
    max_amount: Decimal
    risk_pct: Decimal = Decimal("0.01")
    type: str = "stock_trading"
    settings: dict = field(default_factory=dict, compare=False, hash=False)
    archived: bool = False
    sort_order: int = 0
    created_at: datetime | None = None

    @property
    def risk_per_trade(self) -> Decimal:
        return self.max_amount * self.risk_pct


@dataclass(frozen=True)
class Trade:
    id: int | None
    portfolio_id: int
    symbol: str
    side: Side
    trade_date: date
    shares: Decimal
    price: Decimal
    initial_stop: Decimal | None = None  # BUY only
    strategy: str | None = None  # BUY only
    timeframe: str | None = None  # BUY only
    lot_id: int | None = None  # SELL only
    notes: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    exit_plan: str | None = None  # BUY only: "1-3-6" (default when None) or "1-2-3"
    exit_reason: str | None = None  # SELL only: "STOP" for a stop-out, None for a normal sale
    asset_type: str | None = None  # BUY only (Long-term Holdings): one of ASSET_TYPES
    target_price: Decimal | None = None  # BUY only (Long-term Holdings): optional profit target
    drip: bool = False  # BUY only: bought by reinvesting a dividend
    spinoff_of: str | None = None  # BUY only: lot received in a spin-off of this parent symbol
    split_factor: Decimal = Decimal(1)  # not stored: > 1 when shown in post-split shares (see core/splits.py)

    @property
    def is_stop_out(self) -> bool:
        return self.side == Side.SELL and self.exit_reason == "STOP"

    @property
    def is_buy(self) -> bool:
        return self.side == Side.BUY

    @property
    def amount(self) -> Decimal:
        return self.shares * self.price


@dataclass(frozen=True)
class StopChange:
    id: int | None
    lot_id: int
    effective_date: date
    stop: Decimal
    reason: StopReason = StopReason.MANUAL
    created_at: datetime | None = None


@dataclass(frozen=True)
class CashEvent:
    id: int | None
    portfolio_id: int
    event_date: date
    kind: CashKind
    amount: Decimal  # signed: withdrawals negative
    symbol: str | None = None
    dividend_ex_date: date | None = None
    note: str | None = None


@dataclass(frozen=True)
class Instrument:
    symbol: str
    name: str | None = None
    sector: str | None = None
    industry: str | None = None
    next_earnings_date: date | None = None
    div_yield: Decimal | None = None
    annual_dividend: Decimal | None = None
    updated_at: datetime | None = None
    sector_manual: bool = False  # entered by the user; refreshes don't overwrite it
    ex_div_date: date | None = None  # next (or latest announced) ex-dividend date
    div_pay_date: date | None = None  # its pay date, when known


@dataclass(frozen=True)
class Quote:
    symbol: str
    last: Decimal
    prev_close: Decimal | None
    as_of: datetime
    source: str
    is_manual: bool = False
    pinned: bool = False


@dataclass(frozen=True)
class Bar:
    date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    volume: int | None = None


@dataclass(frozen=True)
class Dividend:
    symbol: str
    ex_date: date
    amount_per_share: Decimal
    source: str
    pay_date: date | None = None


@dataclass(frozen=True)
class AlertRecord:
    id: int | None
    portfolio_id: int
    symbol: str
    lot_id: int | None
    kind: str
    level: Decimal | None
    message: str
    triggered_at: datetime
    acknowledged: bool = False


# ---------------------------------------------------------------- Cash portfolio type
class CashAccountKind(StrEnum):
    SAVINGS = "SAVINGS"
    MONEY_MARKET = "MONEY_MARKET"


CASH_KIND_LABELS = {CashAccountKind.SAVINGS: "Savings", CashAccountKind.MONEY_MARKET: "Money market"}


class CashTxnKind(StrEnum):
    OPENING = "OPENING"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    TRANSFER = "TRANSFER"
    INTEREST = "INTEREST"
    ADJUSTMENT = "ADJUSTMENT"


@dataclass(frozen=True)
class CashAccount:
    id: int | None
    portfolio_id: int
    name: str
    kind: CashAccountKind
    opened_on: date
    institution: str | None = None
    symbol: str | None = None  # money market fund
    manual_rate: bool = False  # money market fund: the user enters the yield; refreshes don't change it
    payout_day: int = 0  # day of the month interest is paid; 0 = last day of the month
    closed: bool = False
    sort_order: int = 0
    notes: str | None = None
    created_at: datetime | None = None

    @property
    def is_money_market(self) -> bool:
        return self.kind == CashAccountKind.MONEY_MARKET


@dataclass(frozen=True)
class CashRate:
    account_id: int
    effective_date: date
    apy: Decimal  # fraction: 0.0425 = 4.25 %
    source: str = "MANUAL"


@dataclass(frozen=True)
class CashTxn:
    id: int | None
    account_id: int
    txn_date: date
    kind: CashTxnKind
    amount: Decimal  # signed
    transfer_id: int | None = None
    period_end: date | None = None  # INTEREST: the payout period it pays for
    true_up_balance: Decimal | None = None  # set on the row a true-up created
    note: str | None = None
    created_at: datetime | None = None

    @property
    def is_true_up(self) -> bool:
        return self.true_up_balance is not None


@dataclass(frozen=True)
class CashLink:
    portfolio_id: int
    account_id: int
    since: date
