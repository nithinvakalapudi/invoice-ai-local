"""Additional cumulative invoice workflow CSV, separate from the original report."""
import csv
import io
from datetime import datetime, timezone

from models.invoice_schema import InvoiceResult
from services.csv_service import _display_date, _invoice_age, _line_quantity_total
from services.validation_service import REQUIRED_FIELDS, REQUIRED_FIELD_LABELS
from utils.number_utils import decimal_or_none


REPORT_NAME = 'MDM/Invoice_data.csv'
LEGACY_DRAFT_WARNING = 'Local OCR/model draft has no calibrated confidence; verify every field against the source invoice.'
COLUMNS = [
    'Business Unit', 'Vendor -As per MDM', 'Billing Account',
    'Invoice Number (As per Invoice)', 'Invoice Number', 'Invoice Date',
    'Currency', 'Billing Start Date', 'Billing End Date',
    'Net Amount', 'Tax Amount', 'Invoice Received Date', 'Sent for Scanning Date', 'Owner',
    'Scanned Date in Expense', 'Ageing (In Days)',
    'Ageing (Today-Sent For Scanning Date)', 'Comments',
]
DISPLAY_COLUMNS = COLUMNS + [
    'Region', 'Service line count', 'Service descriptions', 'Service quantities', 'Total quantity',
    'Extraction completeness (%)', 'Review status', 'Review reasons', 'Duplicate status',
    'Source', 'Source email', 'Email file', 'Source attachment',
    'Processing result', 'Action',
]
EXPECTED_EXTRACTED_FIELDS = (
    'vendor_name', 'invoice_number', 'invoice_date', 'currency',
    'service_start_date', 'service_end_date', 'subtotal', 'tax_amount', 'total_amount',
)


def extraction_completeness(result: InvoiceResult) -> float:
    """Field coverage, not a claim that the values are correct."""
    invoice = result.invoice
    present = sum(getattr(invoice, field, None) not in (None, '')
                  for field in EXPECTED_EXTRACTED_FIELDS)
    return round(present * 100 / len(EXPECTED_EXTRACTED_FIELDS), 2)


def _source_email(value: str | None) -> str:
    """Do not export synthetic-domain placeholders as company contacts."""
    if not value or '@' not in value:
        return ''
    domain = value.rsplit('@', 1)[1].casefold()
    return '' if domain.endswith(('.test', '.example', '.invalid')) else value


def _received_date(processed_at: str) -> str:
    """Keep the local date of intake, rather than reusing invoice/service dates."""
    try:
        received = datetime.fromisoformat(processed_at.replace('Z', '+00:00'))
    except (AttributeError, ValueError):
        return ''
    if received.tzinfo is None:
        received = received.replace(tzinfo=timezone.utc)
    return received.astimezone().strftime('%m/%d/%Y')


def review_reasons(result: InvoiceResult) -> list[str]:
    """Explain review decisions without treating every blank output cell as required."""
    if result.processing_status == 'FAILED':
        return result.errors or ['Document processing failed']
    if result.processing_status == 'SKIPPED' or (
        not result.review_required and result.validation_status == 'VALID'
        and result.duplicate_status != 'POSSIBLE_DUPLICATE'
    ):
        return []
    missing = [REQUIRED_FIELD_LABELS[name] for name in REQUIRED_FIELDS
               if getattr(result.invoice, name, None) in (None, '')]
    reasons = [f"Missing required: {', '.join(missing)}"] if missing else []
    reasons.extend(error for error in result.errors
                   if not error.startswith('Missing required field:'))
    if result.duplicate_status == 'POSSIBLE_DUPLICATE' and not any(
        'duplicate' in reason.casefold() for reason in reasons
    ):
        reasons.append('Possible duplicate invoice')
    if result.processing_status == 'LOCAL_DRAFT' and result.review_required and (
        result.duplicate_status != 'POSSIBLE_DUPLICATE'
    ):
        reasons.append('Local extraction has no calibrated field confidence; verify against the invoice')
    elif result.processing_status == 'OCR_ONLY':
        reasons.append('OCR text was read, but invoice fields need verification')
    elif result.processing_status == 'IMPORTED':
        reasons.append('Imported row needs verification against the source invoice')
    if not reasons and result.review_required:
        reasons.append('Validation requires manual review')
    return list(dict.fromkeys(reasons))


def mdm_row(result: InvoiceResult) -> dict:
    """One source of truth for the on-screen and exported MDM columns."""
    invoice = result.invoice
    # Workflow/MDM values are deliberately empty without an authoritative source.
    row = {column: '' for column in DISPLAY_COLUMNS}
    comments = invoice.invoice_comments or ''
    scanning_date = _received_date(result.processed_at)
    needs_review = (result.processing_status != 'SKIPPED' and
                    (result.review_required or result.validation_status != 'VALID'
                     or result.duplicate_status == 'POSSIBLE_DUPLICATE'))
    row.update({
        'Region': invoice.region or '',
        'Vendor -As per MDM': invoice.vendor_name or '',
        'Billing Account': '',
        'Invoice Number (As per Invoice)': invoice.invoice_number or '',
        'Invoice Number': invoice.invoice_number or '',
        'Invoice Date': _display_date(invoice.invoice_date),
        'Currency': invoice.currency or '',
        'Billing Start Date': _display_date(invoice.service_start_date),
        'Billing End Date': _display_date(invoice.service_end_date),
        'Service line count': len(result.line_items) if result.line_items else '',
        'Service descriptions': '; '.join(item.description.strip() for item in result.line_items
                                          if item.description),
        'Service quantities': '; '.join(str(item.quantity) for item in result.line_items
                                         if item.quantity not in (None, '')),
        'Total quantity': _line_quantity_total(result),
        'Net Amount': decimal_or_none(invoice.subtotal),
        'Tax Amount': decimal_or_none(invoice.tax_amount),
        'Invoice Received Date': _received_date(result.processed_at),
        'Sent for Scanning Date': scanning_date,
        'Scanned Date in Expense': scanning_date,
        'Ageing (In Days)': _invoice_age(invoice.invoice_date),
        'Ageing (Today-Sent For Scanning Date)': _invoice_age(scanning_date),
        'Comments': comments,
        'Extraction completeness (%)': extraction_completeness(result),
        'Review status': ('SKIPPED' if result.processing_status == 'SKIPPED' else
                          'REVIEW_REQUIRED' if needs_review else 'SUCCESS'),
        'Review reasons': '; '.join(review_reasons(result)),
        'Duplicate status': result.duplicate_status,
        'Source': result.source_file,
        'Source email': _source_email(invoice.vendor_email),
        'Email file': result.source_email,
        'Source attachment': result.source_attachment,
        # Processing succeeded even when extracted values still require review.
        'Processing result': ('SUCCESS' if result.processing_status in {'PROCESSED', 'LOCAL_DRAFT'}
                              else result.processing_status),
        'Action': ('Skipped' if result.processing_status == 'SKIPPED' else
                   'Review Required' if needs_review else 'No Review Required'),
    })
    return row


def generate_mdm_csv(results: list[InvoiceResult]) -> bytes:
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=COLUMNS)
    writer.writeheader()
    for result in results:
        row = mdm_row(result)
        writer.writerow({column: row[column] for column in COLUMNS})
    return output.getvalue().encode('utf-8-sig')
