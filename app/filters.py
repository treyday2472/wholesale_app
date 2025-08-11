# app/filters.py
from decimal import Decimal, InvalidOperation
from typing import Any

def _to_number(val: Any):
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    try:
        return float(Decimal(str(val).replace(",", "").strip()))
    except (InvalidOperation, ValueError):
        return None

def currency_filter(val, decimals: int = 0, dash: str = "—"):
    num = _to_number(val)
    if num is None:
        return dash
    return f"${num:,.{decimals}f}" if decimals else f"${num:,.0f}"

def percent_filter(val, decimals: int = 2, dash: str = "—"):
    num = _to_number(val)
    if num is None:
        return dash
    return f"{num:.{decimals}f}%"

def register_filters(app):
    app.jinja_env.filters["currency"] = currency_filter
    app.jinja_env.filters["percent"] = percent_filter
