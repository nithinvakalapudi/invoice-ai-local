"""Conservative date validation and ISO normalization."""
from __future__ import annotations

from datetime import datetime

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%m-%d-%Y", "%d.%m.%Y", "%Y/%m/%d")


def normalize_date(value: str | None) -> str | None:
    """Return ISO date only when an explicit supported date format parses."""
    if not value or not isinstance(value, str):
        return None
    for date_format in DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), date_format).date().isoformat()
        except ValueError:
            continue
    return None
