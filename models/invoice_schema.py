"""Typed invoice data structures and schema-safe parsing."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

INVOICE_FIELDS = [
    "invoice_number", "invoice_date", "vendor_name", "vendor_address",
    "vendor_tax_id", "customer_name", "customer_address", "customer_tax_id",
    "po_number", "billing_period", "currency", "subtotal", "discount",
    "tax_amount", "shipping_amount", "total_amount", "due_date",
    "payment_terms", "bank_details", "service_start_date", "service_end_date",
    "customer_email", "vendor_email", "bank_account_number", "scope", "invoice_comments", "region",
]
CSV_INVOICE_COLUMNS = [
    "Date", "Invoice", "Region", "SG/BSG", "Allocated to", "Vendor",
    "MDM Account number", "Category", "Received Date", "Invoice Date",
    "Invoice Aging", "Processed Date DDMMYYYY", "Categor2",
    "Awaiting Feedback date", "Reply rec'd Date", "Status", "Vouchel",
    "Query Type", "Query Pending With", "Inv inititn count", "Due coun",
    "Due Date", "TASK - Email", "Reasons for Awaiting", "Invoice Amount",
    "Currency", "Period", "Service", "Service line count", "Service quantities", "Total quantity",
    "Variance %", "Variance amounttTax",
    "Inter entity", "Comments", "Scope",
]
CSV_LINE_ITEM_COLUMNS = [
    "invoice_number", "source_file", "line_number", "description", "quantity",
    "unit_price", "tax", "discount", "line_total",
]
CSV_AUDIT_COLUMNS = [
    "source_file", "processing_timestamp", "processing_status", "validation_status",
    "confidence", "review_required", "duplicate_status", "errors", "warnings",
]
PROVENANCE_COLUMNS = ['source_email', 'source_attachment', 'attachment_index']
CSV_INVOICE_COLUMNS += PROVENANCE_COLUMNS
CSV_LINE_ITEM_COLUMNS += PROVENANCE_COLUMNS
CSV_AUDIT_COLUMNS += PROVENANCE_COLUMNS


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass
class LineItem:
    description: str | None = None
    quantity: Any = None
    unit_price: Any = None
    tax: Any = None
    discount: Any = None
    line_total: Any = None

    @classmethod
    def from_dict(cls, raw: Any) -> "LineItem":
        raw = raw if isinstance(raw, dict) else {}
        allowed = {item.name for item in fields(cls)}
        return cls(**{key: raw.get(key) for key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass
class Invoice:
    invoice_number: str | None = None
    invoice_date: str | None = None
    vendor_name: str | None = None
    vendor_address: str | None = None
    vendor_tax_id: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    customer_tax_id: str | None = None
    po_number: str | None = None
    billing_period: str | None = None
    currency: str | None = None
    subtotal: Any = None
    discount: Any = None
    tax_amount: Any = None
    shipping_amount: Any = None
    total_amount: Any = None
    due_date: str | None = None
    payment_terms: str | None = None
    bank_details: str | None = None
    service_start_date: str | None = None
    service_end_date: str | None = None
    customer_email: str | None = None
    vendor_email: str | None = None
    bank_account_number: str | None = None
    scope: str | None = None
    invoice_comments: str | None = None
    region: str | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> "Invoice":
        raw = raw if isinstance(raw, dict) else {}
        text_fields = set(INVOICE_FIELDS) - {
            "subtotal", "discount", "tax_amount", "shipping_amount", "total_amount"
        }
        values = {key: raw.get(key) for key in INVOICE_FIELDS}
        for key in text_fields:
            values[key] = _optional_text(values[key])
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        # Existing Streamlit sessions can hold instances created before a schema
        # update. Default missing attributes until the user reprocesses the file.
        return {key: getattr(self, key, None) for key in INVOICE_FIELDS}


@dataclass
class InvoiceResult:
    invoice: Invoice = field(default_factory=Invoice)
    line_items: list[LineItem] = field(default_factory=list)
    field_confidence: dict[str, float] = field(default_factory=dict)
    source_file: str = ""
    processed_at: str = ""
    processing_status: str = "PROCESSED"
    validation_status: str = "REVIEW_REQUIRED"
    review_required: bool = True
    duplicate_status: str = "NOT_DUPLICATE"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extraction_confidence: float = 0.0
    source_email: str = ""
    source_attachment: str = ""
    attachment_index: int | None = None

    @classmethod
    def failed(cls, source_file: str, error: str, processed_at: str) -> "InvoiceResult":
        return cls(source_file=source_file, processed_at=processed_at,
                   processing_status="FAILED", validation_status="FAILED",
                   errors=[error], review_required=True)

    @classmethod
    def from_ai_payload(
        cls, raw: dict[str, Any], source_file: str, processed_at: str, minimum_confidence: float = 95.0
    ) -> "InvoiceResult":
        lines = raw.get("line_items", [])
        confidences = raw.get("field_confidence", {})
        safe_confidences: dict[str, float] = {}
        for key, value in confidences.items():
            try:
                safe_confidences[str(key)] = max(0.0, min(100.0, float(value)))
            except (TypeError, ValueError):
                continue
        invoice_data = raw.get("invoice") if isinstance(raw.get("invoice"), dict) else {}
        # Confidence controls review status, not whether the scanned value survives.
        # Otherwise a missing confidence map silently empties every exported CSV.
        return cls(invoice=Invoice.from_dict(invoice_data),
                   line_items=[LineItem.from_dict(line) for line in lines if isinstance(line, dict)],
                   field_confidence=safe_confidences, source_file=source_file,
                   processed_at=processed_at)
