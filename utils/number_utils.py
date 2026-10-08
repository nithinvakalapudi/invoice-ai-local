"""Decimal parsing and financial comparison helpers."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any


def to_decimal(value: Any) -> Decimal | None:
    """Convert common invoice number formats to Decimal without guessing."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "").replace("₹", "").replace("$", "").replace("€", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def decimal_or_none(value: Any) -> float | None:
    number = to_decimal(value)
    return float(number) if number is not None else None


def close_enough(left: Decimal, right: Decimal, tolerance: float) -> bool:
    return abs(left - right) <= Decimal(str(tolerance))
