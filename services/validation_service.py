"""Programmatic data-quality and financial invoice validation."""
from __future__ import annotations

import re
from decimal import Decimal

from models.invoice_schema import InvoiceResult
from utils.date_utils import normalize_date
from utils.number_utils import close_enough, to_decimal

REQUIRED_FIELDS = ("invoice_number", "vendor_name", "invoice_date", "currency", "total_amount")
REQUIRED_FIELD_LABELS = {
    "invoice_number": "Invoice Number", "vendor_name": "Vendor",
    "invoice_date": "Invoice Date", "currency": "Currency",
    "total_amount": "Total Amount",
}
NUMERIC_FIELDS = ("subtotal", "discount", "tax_amount", "shipping_amount", "total_amount")


def calculate_confidence(result: InvoiceResult) -> float:
    """Average confidence of fields that have actual extracted values."""
    values = result.invoice.to_dict()
    relevant = [
        result.field_confidence.get(key, 0.0)
        for key, value in values.items() if value not in (None, "")
    ]
    return round(sum(relevant) / len(relevant), 2) if relevant else 0.0


def _as_decimal(value: object, label: str, errors: list[str]) -> Decimal | None:
    parsed = to_decimal(value)
    if value not in (None, "") and parsed is None:
        errors.append(f"Invalid numerical value for {label}")
    return parsed


def validate_invoice(result: InvoiceResult, tolerance: float, review_threshold: float) -> InvoiceResult:
    """Validate dates, mandatory data and invoice maths; update workflow state."""
    if result.processing_status == "FAILED":
        return result
    result.errors = [error for error in result.errors if error.startswith("Duplicate")]
    result.warnings = []
    invoice = result.invoice
    for name in REQUIRED_FIELDS:
        if getattr(invoice, name) in (None, ""):
            result.errors.append(f"Missing required field: {name}")
    if invoice.vendor_email and not re.fullmatch(
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", invoice.vendor_email
    ):
        result.errors.append("Invalid vendor email address")
    for name in ("invoice_date", "due_date", "service_start_date", "service_end_date"):
        value = getattr(invoice, name, None)
        if value:
            normalized = normalize_date(value)
            if normalized is None:
                result.errors.append(f"Invalid date: {name}")
            else:
                setattr(invoice, name, normalized)
    values = {name: _as_decimal(getattr(invoice, name), name, result.errors) for name in NUMERIC_FIELDS}
    subtotal = values["subtotal"]
    tax = values["tax_amount"] or Decimal("0")
    shipping = values["shipping_amount"] or Decimal("0")
    discount = values["discount"] or Decimal("0")
    total = values["total_amount"]
    if subtotal is not None and total is not None:
        expected = subtotal + tax + shipping - discount
        if not close_enough(expected, total, tolerance):
            result.errors.append("Total mismatch: subtotal + tax + shipping - discount does not equal total")
    line_totals: list[Decimal] = []
    for index, item in enumerate(result.line_items, start=1):
        quantity = _as_decimal(item.quantity, f"line {index} quantity", result.errors)
        unit_price = _as_decimal(item.unit_price, f"line {index} unit_price", result.errors)
        line_total = _as_decimal(item.line_total, f"line {index} line_total", result.errors)
        if quantity is not None and unit_price is not None and line_total is not None:
            _as_decimal(item.discount, f"line {index} discount", result.errors)
            _as_decimal(item.tax, f"line {index} tax", result.errors)
            expected = quantity * unit_price
            if not close_enough(expected, line_total, tolerance):
                result.errors.append(f"Line-item mismatch on line {index}")
        if line_total is not None:
            line_totals.append(line_total)
    if subtotal is not None and line_totals and not close_enough(sum(line_totals), subtotal, tolerance):
        result.errors.append("Line-item total does not equal subtotal")
    result.extraction_confidence = calculate_confidence(result)
    if result.extraction_confidence < review_threshold:
        result.warnings.append(f"Low-confidence extraction ({result.extraction_confidence}%)")
    result.review_required = bool(result.errors or result.extraction_confidence < review_threshold)
    result.validation_status = "REVIEW_REQUIRED" if result.review_required else "VALID"
    return result
