"""Physical precious metals held in troy ounces, priced at the metal's front-month futures price (≈ spot).

A bullion holding is an ordinary Long-term lot whose symbol is the metal's Yahoo futures symbol: shares are troy
ounces of pure metal and the price is the cost per ounce (premium included).
"""

from __future__ import annotations

METALS: dict[str, str] = {"GC=F": "Gold", "SI=F": "Silver", "PL=F": "Platinum", "PA=F": "Palladium"}
BY_NAME = {v: k for k, v in METALS.items()}


def is_bullion(symbol: str | None) -> bool:
    return symbol in METALS


def display_symbol(symbol: str) -> str:
    """'Gold' for GC=F; any other symbol unchanged."""
    return METALS.get(symbol, symbol)


def quantity_unit(symbol: str | None) -> str:
    return "oz" if is_bullion(symbol) else "sh"
