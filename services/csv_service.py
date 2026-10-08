"""CSV and downloadable ZIP result generation."""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from models.invoice_schema import CSV_AUDIT_COLUMNS, CSV_INVOICE_COLUMNS, CSV_LINE_ITEM_COLUMNS, InvoiceResult
from utils.date_utils import normalize_date
from utils.number_utils import decimal_or_none, to_decimal


def _display_date(value: object) -> str:
    normalized = normalize_date(str(value)) if value not in (None, "") else None
    if not normalized:
        return ""
    return datetime.strptime(normalized, "%Y-%m-%d").strftime("%m/%d/%Y")


def _field(invoice: Any, name: str) -> Any:
    """Read an optional schema field safely."""
    return getattr(invoice, name, None)


def _provenance(result: InvoiceResult) -> dict[str, Any]:
    return {"source_email": getattr(result, "source_email", ""),
            "source_attachment": getattr(result, "source_attachment", ""),
            "attachment_index": getattr(result, "attachment_index", None)}


def _service_start(invoice: Any) -> str:
    return _display_date(_field(invoice, "service_start_date"))


def _invoice_age(invoice_date: object, today: date | None = None) -> int | str:
    normalized = normalize_date(str(invoice_date)) if invoice_date not in (None, "") else None
    if not normalized:
        return ""
    return ((today or date.today()) - datetime.strptime(normalized, "%Y-%m-%d").date()).days


def _due_date(invoice: Any) -> str:
    return _display_date(_field(invoice, "due_date"))


def _variance(result: InvoiceResult) -> float | None:
    total = to_decimal(_field(result.invoice, "total_amount"))
    if total is None:
        return None
    line_totals = [to_decimal(line.line_total) for line in result.line_items]
    parsed_lines = [value for value in line_totals if value is not None]
    if parsed_lines:
        return float(total - sum(parsed_lines))
    subtotal = to_decimal(_field(result.invoice, "subtotal"))
    if subtotal is None:
        return None
    tax = to_decimal(_field(result.invoice, "tax_amount")) or 0
    shipping = to_decimal(_field(result.invoice, "shipping_amount")) or 0
    discount = to_decimal(_field(result.invoice, "discount")) or 0
    return float(total - (subtotal + tax + shipping - discount))


def _comments(invoice: Any) -> str:
    values = [_field(invoice, "payment_terms"), _field(invoice, "invoice_comments")]
    return "; ".join(dict.fromkeys(str(value).strip() for value in values if value))


def _line_quantity_total(result: InvoiceResult) -> int | float | str:
    if not result.line_items:
        return ""
    values = []
    for item in result.line_items:
        try:
            values.append(Decimal(str(item.quantity)))
        except (InvalidOperation, TypeError):
            return ""
    total = sum(values)
    return int(total) if total == total.to_integral_value() else float(total)


def _invoice_rows(results: list[InvoiceResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        invoice = result.invoice
        variance = _variance(result)
        total = decimal_or_none(invoice.total_amount)
        variance_percent = "" if variance is None or not total else round((variance / total) * 100, 2)
        row = {
            "Date": _display_date(_field(invoice, "invoice_date")),
            "Invoice": _field(invoice, "invoice_number") or "",
            "Region": _field(invoice, "region") or "",
            "SG/BSG": "",
            "Allocated to": "",
            "Vendor": _field(invoice, "vendor_name") or "",
            "MDM Account number": _field(invoice, "bank_account_number") or "",
            "Category": "",
            "Received Date": _service_start(invoice),
            "Invoice Date": _display_date(_field(invoice, "invoice_date")),
            "Invoice Aging": _invoice_age(_field(invoice, "invoice_date")),
            "Processed Date DDMMYYYY": _processed_date(result.processed_at),
            "Categor2": "",
            "Awaiting Feedback date": "",
            "Reply rec'd Date": "",
            "Status": result.validation_status,
            "Vouchel": "",
            "Query Type": "",
            "Query Pending With": "",
            "Inv inititn count": "",
            "Due coun": "",
            "Due Date": _due_date(invoice),
            "TASK - Email": _field(invoice, "customer_email") or "",
            "Reasons for Awaiting": " | ".join(result.warnings) if result.processing_status in {"OCR_ONLY", "LOCAL_DRAFT", "IMPORTED"} else "",
            "Invoice Amount": total,
            "Currency": _field(invoice, "currency") or "",
            "Period": _field(invoice, "billing_period") or "",
            "Service": "; ".join(item.description.strip() for item in result.line_items if item.description),
            "Service line count": len(result.line_items) if result.line_items else "",
            "Service quantities": "; ".join(str(item.quantity) for item in result.line_items
                                             if item.quantity not in (None, '')),
            "Total quantity": _line_quantity_total(result),
            "Variance %": variance_percent,
            "Variance amounttTax": variance if variance is not None else "",
            "Inter entity": "",
            "Comments": "; ".join(part for part in (
                _comments(invoice),
                "OCR-only intake; invoice fields require manual review" if result.processing_status == "OCR_ONLY" else
                "Local model draft; verify every extracted field" if result.processing_status == "LOCAL_DRAFT" else
                "Imported spreadsheet row; verify against source invoice" if result.processing_status == "IMPORTED" else "",
            ) if part),
            "Scope": _field(invoice, "scope") or "",
        }
        row.update(_provenance(result))
        rows.append(row)
    return rows


def _processed_date(value: str) -> str:
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%d%m%Y")
    except ValueError:
        return ""


def _line_rows(results: list[InvoiceResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        for number, item in enumerate(result.line_items, start=1):
            row = item.to_dict()
            for field in ("quantity", "unit_price", "tax", "discount", "line_total"):
                row[field] = decimal_or_none(row[field])
            row.update({"invoice_number": result.invoice.invoice_number, "source_file": result.source_file,
                        "line_number": number})
            row.update(_provenance(result))
            rows.append(row)
    return rows


def _audit_rows(results: list[InvoiceResult]) -> list[dict[str, Any]]:
    return [{"source_file": item.source_file, "processing_timestamp": item.processed_at,
             "processing_status": item.processing_status, "validation_status": item.validation_status,
             "confidence": item.extraction_confidence, "review_required": item.review_required,
             "duplicate_status": item.duplicate_status, "errors": " | ".join(item.errors),
             "warnings": " | ".join(item.warnings), **_provenance(item)} for item in results]


def generate_csvs(results: list[InvoiceResult]) -> dict[str, bytes]:
    """Return schema-stable UTF-8 CSV payloads, including empty batches."""
    datasets = {"Invoice_Data.csv": (_invoice_rows(results), CSV_INVOICE_COLUMNS),
                "Invoice_Line_Items.csv": (_line_rows(results), CSV_LINE_ITEM_COLUMNS),
                "Audit_Report.csv": (_audit_rows(results), CSV_AUDIT_COLUMNS)}
    files = {}
    for name, (rows, columns) in datasets.items():
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        files[name] = buffer.getvalue().encode("utf-8")
    return files


def build_zip(csv_files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, payload in csv_files.items():
            archive.writestr(filename, payload)
    return buffer.getvalue()
