"""Batch-local possible duplicate detection."""
from __future__ import annotations

from collections import defaultdict

from models.invoice_schema import InvoiceResult
from utils.number_utils import to_decimal


def _key(result: InvoiceResult) -> tuple[str, str, str, str] | None:
    invoice = result.invoice
    if not all((invoice.invoice_number, invoice.vendor_name, invoice.invoice_date, invoice.total_amount is not None)):
        return None
    amount = to_decimal(invoice.total_amount)
    if amount is None:
        return None
    return (invoice.invoice_number.strip().casefold(), invoice.vendor_name.strip().casefold(), invoice.invoice_date, str(amount))


def mark_duplicates(results: list[InvoiceResult],
                    prior_results: list[InvoiceResult] | None = None) -> list[InvoiceResult]:
    """Flag matching invoices within this batch or saved history; never remove them."""
    prior_keys = {key for prior in prior_results or [] if (key := _key(prior)) is not None}
    groups: dict[tuple[str, str, str, str], list[InvoiceResult]] = defaultdict(list)
    for result in results:
        result.duplicate_status = "NOT_DUPLICATE"
        result.warnings = [warning for warning in result.warnings
                           if not warning.startswith("Possible duplicate invoice")]
        candidate = _key(result)
        if candidate is not None and result.processing_status != "FAILED":
            groups[candidate].append(result)
    for key, matches in groups.items():
        if len(matches) > 1 or key in prior_keys:
            for result in matches:
                result.duplicate_status = "POSSIBLE_DUPLICATE"
                result.review_required = True
                result.validation_status = "REVIEW_REQUIRED"
                result.warnings.append("Possible duplicate invoice in saved history"
                                       if key in prior_keys else "Possible duplicate invoice in this batch")
    return results
