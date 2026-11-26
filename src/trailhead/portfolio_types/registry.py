"""Registry of available portfolio types."""

from __future__ import annotations

from .base import PortfolioType

_TYPES: dict[str, PortfolioType] = {}


def register(t: PortfolioType) -> PortfolioType:
    _TYPES[t.key] = t
    return t


def get(key: str) -> PortfolioType:
    _ensure_builtin()
    if key not in _TYPES:
        raise KeyError(f"Unknown portfolio type {key!r}")
    return _TYPES[key]


def all_types() -> list[PortfolioType]:
    _ensure_builtin()
    return list(_TYPES.values())


def _ensure_builtin() -> None:
    if "stock_trading" not in _TYPES:
        from .stock_trading.type import StockTradingType

        register(StockTradingType())
    if "long_term" not in _TYPES:
        from .long_term.type import LongTermType

        register(LongTermType())
    if "cash" not in _TYPES:
        from .cash.type import CashType

        register(CashType())
