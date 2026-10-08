"""Billing dates describe the invoiced service, never receipt or invoice dates."""
import csv
import io

from services import local_extraction_service as local
from services.mdm_csv_service import generate_mdm_csv
from services.tabular_intake import import_invoice_rows


def _mdm_row(results):
    return next(csv.DictReader(io.StringIO(generate_mdm_csv(results).decode('utf-8-sig'))))


def test_printed_service_range_populates_billing_dates(monkeypatch):
    text = """Invoice #: INV-1
Invoice Date: 10/01/2026
Service Period: 10/01/26 - 10/31/26
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    invoice = local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']
    assert invoice['service_start_date'] == '2026-10-01'
    assert invoice['service_end_date'] == '2026-10-31'


def test_separate_service_dates_populate_billing_columns(monkeypatch):
    text = """Invoice #: INV-2
Invoice Date: 09/25/2026
Service Start Date: 10/01/2026
Service End Date: 10/31/2026
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    invoice = local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']
    assert invoice['service_start_date'] == '2026-10-01'
    assert invoice['service_end_date'] == '2026-10-31'


def test_received_date_is_not_imported_as_service_start():
    data = (b'Invoice,Vendor,Invoice Date,Received Date,Billing End Date\n'
            b'INV-3,Example Vendor,09/25/2026,10/07/2026,10/31/2026\n')
    row = _mdm_row(import_invoice_rows('invoices.csv', data, .02, 95))
    assert row['Billing Start Date'] == ''
    assert row['Billing End Date'] == '10/31/2026'
    assert row['Invoice Date'] == '09/25/2026'


def test_explicit_service_columns_import_as_billing_dates():
    data = (b'Invoice,Vendor,Invoice Date,Service Start Date,Service End Date\n'
            b'INV-4,Example Vendor,09/25/2026,10/01/2026,10/31/2026\n')
    row = _mdm_row(import_invoice_rows('invoices.csv', data, .02, 95))
    assert row['Billing Start Date'] == '10/01/2026'
    assert row['Billing End Date'] == '10/31/2026'
