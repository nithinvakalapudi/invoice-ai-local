"""Offline drafts use printed evidence and remain review-required."""
import csv
import io
from uuid import uuid4

from openpyxl import load_workbook

from services import local_extraction_service as local
from services.document_processing import process_document
from services.mdm_csv_service import COLUMNS, _received_date, generate_mdm_csv, mdm_row
from services.local_storage import LocalStorage
from utils.file_utils import DocumentInput


def test_labeled_invoice_populates_requested_csv_columns(monkeypatch):
    text = """INVOICE
Vendor: Blue Ocean Technologies, LLC
Bill To: SG Securities (HK) Limited
Invoice #: SOCG_HK_10_2026_001
Invoice Date: 10/01/2026
Service Period
10/01/26 - 10/31/26
SUBTOTAL
$4,500.00
TAX / VAT
$0.00
TOTAL AMOUNT DUE
$4,500.00
Currency: USD
Account Number: 001234567
Payment Due Upon Receipt
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    result = process_document(DocumentInput('invoice.pdf', b'PDF'), .02, 95)
    row = next(csv.DictReader(io.StringIO(generate_mdm_csv([result]).decode('utf-8-sig'))))
    assert list(row) == COLUMNS
    assert row['Business Unit'] == ''  # A business-unit code needs an approved mapping.
    assert row['Vendor -As per MDM'] == 'Blue Ocean Technologies, LLC'
    assert row['Billing Account'] == ''
    assert row['Invoice Number (As per Invoice)'] == row['Invoice Number'] == 'SOCG_HK_10_2026_001'
    assert row['Invoice Date'] == '10/01/2026'
    assert row['Invoice Received Date'] == _received_date(result.processed_at)
    assert row['Currency'] == 'USD'
    assert row['Billing Start Date'] == '10/01/2026'
    assert row['Billing End Date'] == '10/31/2026'
    assert row['Net Amount'] == '4500.0'
    assert row['Tax Amount'] == '0.0'
    assert row['Sent for Scanning Date'] == row['Scanned Date in Expense'] == _received_date(result.processed_at)
    assert row['Owner'] == ''
    assert row['Ageing (Today-Sent For Scanning Date)'] == '0'
    assert row['Comments'] == ''
    assert result.processing_status == 'LOCAL_DRAFT'
    assert result.validation_status == 'REVIEW_REQUIRED'
    assert not any('no calibrated confidence' in warning for warning in result.warnings)


def test_dated_service_rows_populate_summary_and_line_item_csv(tmp_path, monkeypatch):
    from services.csv_service import generate_csvs
    text = """INVOICE
Vendor: Example Media LLC
Invoice #: EX-2026-001
Invoice Date: 10/01/2026
Currency: USD
Service Period Description Qty Rate / Unit Amount
10/01/26 - 10/31/26 Digital Campaign Management 1 $4,100.00 $4,100.00
10/01/26 - 10/31/26 Creative Production 6 $525.00 $3,150.00
10/01/26 - 10/31/26 Performance Reporting 1 $750.00 $750.00
Tax: $0.00
Total: $8,000.00
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    result = process_document(DocumentInput('services.pdf', b'PDF'), .02, 95)
    assert [item.description for item in result.line_items] == [
        'Digital Campaign Management', 'Creative Production', 'Performance Reporting']
    assert [item.quantity for item in result.line_items] == ['1', '6', '1']
    assert not any('Line-item' in error for error in result.errors)
    row = next(csv.DictReader(io.StringIO(generate_mdm_csv([result]).decode('utf-8-sig'))))
    detail = mdm_row(result)
    assert detail['Service line count'] == 3
    assert detail['Service descriptions'] == (
        'Digital Campaign Management; Creative Production; Performance Reporting')
    assert detail['Total quantity'] == 8
    assert detail['Service quantities'] == '1; 6; 1'
    assert row['Billing Start Date'] == '10/01/2026'
    assert row['Billing End Date'] == '10/31/2026'
    reports = generate_csvs([result])
    invoice_row = next(csv.DictReader(io.StringIO(reports['Invoice_Data.csv'].decode())))
    assert invoice_row['Service'] == detail['Service descriptions']
    assert invoice_row['Service line count'] == '3'
    assert invoice_row['Total quantity'] == '8'
    assert invoice_row['Service quantities'] == '1; 6; 1'
    details = list(csv.DictReader(io.StringIO(reports['Invoice_Line_Items.csv'].decode())))
    assert len(details) == 3
    assert [line['quantity'] for line in details] == ['1.0', '6.0', '1.0']
    store = LocalStorage(tmp_path / 'local_data')
    store.save_batch(uuid4().hex, [result])
    book = load_workbook(io.BytesIO(store.report_files()['Invoice_History.xlsx']), read_only=True)
    sheet = book['MDM Invoice Data']
    assert [cell.value for cell in sheet[1]] == COLUMNS
    assert [cell.value for cell in book['Invoice_Data'][1]].count('Service line count') == 1
    book.close()


def test_service_row_with_inconsistent_amount_is_not_added(monkeypatch):
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: (
        'Service Period Description Qty Rate / Unit Amount\n'
        '10/01/26 - 10/31/26 Creative Production 6 $525.00 $3,150.00\n'
        '10/01/26 - 10/31/26 Corrupt OCR Row 6 $525.00 $9,999.00'))
    payload = local.extract_invoice(b'PDF', 'invoice.pdf')
    assert len(payload['line_items']) == 1
    assert payload['line_items'][0]['description'] == 'Creative Production'


def test_pdf_cell_per_line_service_table(monkeypatch):
    # PyMuPDF emits these PDF table cells one per text line, not as row text.
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: """Service Period
Description
Qty
Rate / Unit
Amount
10/01/26 - 10/31/26
Digital Campaign Management
1
$4,100.00
$4,100.00
10/01/26 - 10/31/26
Creative Production
6
$525.00
$3,150.00
10/01/26 - 10/31/26
Performance Reporting
1
$750.00
$750.00
SUBTOTAL
$8,000.00
""")
    payload = local.extract_invoice(b'PDF', 'invoice.pdf')
    assert [item['description'] for item in payload['line_items']] == [
        'Digital Campaign Management', 'Creative Production', 'Performance Reporting']
    assert [item['quantity'] for item in payload['line_items']] == ['1', '6', '1']


def test_pdf_cell_per_line_currency_and_single_day_service(monkeypatch):
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: """Service Period
Description
Qty
Rate / Unit
Amount
10/09/26
A4 Premium Copy Paper
40
I410.00
I16,400.00
10/09/26
Laser Toner Cartridge
8
I6,350.00
I50,800.00
""")
    payload = local.extract_invoice(b'PDF', 'invoice.pdf')
    assert len(payload['line_items']) == 2
    assert payload['line_items'][0]['unit_price'] == '410.00'
    assert payload['line_items'][1]['line_total'] == '50800.00'


def test_region_uses_vendor_location_not_bill_to_location(monkeypatch):
    text = """Blue Ocean Technologies, LLC
515 N Flagler Dr, Suite 350, West Palm Beach, FL 33401
Invoice #: BOT-2026-1001
Bill To: SG Securities (HK) Limited
1 Queen's Road East, 38F Pacific Place 3, Hong Kong
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    result = process_document(DocumentInput('invoice.pdf', b'PDF'), .02, 95)
    assert result.invoice.region == 'West Palm Beach, FL'
    assert result.invoice.region != 'Hong Kong'
    assert mdm_row(result)['Region'] == 'West Palm Beach, FL'


def test_region_stays_blank_without_a_printed_vendor_address(monkeypatch):
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: (
        'Vendor: Blue Ocean Technologies, LLC\nBill To: SG Securities (HK) Limited\n'
        '1 Queen\'s Road East, Hong Kong'))
    payload = local.extract_invoice(b'PDF', 'invoice.pdf')
    assert payload['invoice'].get('region') is None


def test_screen_photo_noise_does_not_become_vendor_or_total(monkeypatch):
    text = """A Market Place | Chat M365 Copilot
Invoice #: SOCG HK 10 2026 001
Blue Ocean Technologies, LLC
Invoice Date: 10/01/2026
Bill To: ATTN: Accounts Payable-Market Data
SG Securities (HK) Limited
Rate / Unit
$2,500.00
$1,500.00
$500.00
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    invoice = local.extract_invoice(b'IMAGE', 'screen.jpg')['invoice']
    assert invoice['vendor_name'] == 'Blue Ocean Technologies, LLC'
    assert invoice['customer_name'] == 'SG Securities (HK) Limited'
    assert invoice.get('total_amount') is None
    assert invoice.get('bank_account_number') is None


def test_unlabeled_total_requires_visible_amount_and_reconciling_charges(monkeypatch):
    text = """Invoice #: INV-77
Invoice Date: 10/01/2026
Blue Ocean Technologies, LLC
Payment Due Upon Receipt - Amounts in USD
Non-Display Fee 1 $2,500.00 $2,500.00
Internal Distribution Fee 1 $1,500.00 $1,500.00
Financial Product Price Calculation Fee 1 $500.00 $500.00
$4,500.00
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    invoice = local.extract_invoice(b'IMAGE', 'screen.jpg')['invoice']
    assert invoice['total_amount'] == '4500.00'
    assert invoice['currency'] == 'USD'
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text.replace('$4,500.00', '$4,600.00'))
    assert local.extract_invoice(b'IMAGE', 'screen.jpg')['invoice'].get('total_amount') is None


def test_sales_tax_not_included_line_populates_zero_tax_and_net(monkeypatch):
    text = """INVOICE
Invoice #: SOCG_HK_10_2026_001
Invoice Date: 10/01/2026
Currency: USD
Sales Tax Not Included
$0.00
Total Amount Due
$4,500.00
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    payload = local.extract_invoice(b'PDF', 'invoice.pdf')
    assert payload['invoice']['tax_amount'] == '0.00'
    assert payload['invoice']['total_amount'] == '4500.00'
    assert payload['invoice']['subtotal'] == '4500.00'
    assert payload['_derived_fields'] == ['subtotal']


def test_tax_percentage_is_not_mistaken_for_amount(monkeypatch):
    text = """INVOICE
Invoice Date: 2026-01-05
Subtotal
51,275.00
Tax (8.25%)
4,145.58
TOTAL (USD)
54,445.08
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    invoice = local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']
    assert invoice['subtotal'] == '51275.00'
    assert invoice['tax_amount'] == '4145.58'
    assert invoice['total_amount'] == '54445.08'
    assert invoice['currency'] == 'USD'


def test_tabular_invoice_date_does_not_use_payment_due_date(monkeypatch):
    text = """INVOICE
Invoice Number
Invoice Date
Payment Due
VCA/26-27/0187
10/05/2026
11/04/2026
BILL TO
Example Customer
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    assert local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']['invoice_date'] == '10/05/2026'


def test_invoice_date_stays_blank_when_only_service_and_due_dates_are_printed(monkeypatch):
    text = """INVOICE
Vendor: Pacific Logistics & Freight
Service Period
10/08/26 - 10/14/26
Payment due: 10/28/2026
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    assert 'invoice_date' not in local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']


def test_vendor_is_not_replaced_by_legal_named_bill_to_and_handling_is_a_charge(monkeypatch):
    text = """CedarPeak Security Services
INVOICE
Invoice #: INV-2026-10009
Invoice Date: 10/10/2026
BILL TO
Atlas Electronics Trading Pte. Ltd.
Handling Charge
10,488.00
Tax / VAT
2,694.58
TOTAL AMOUNT DUE
13,182.58
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    invoice = local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']
    assert invoice['vendor_name'] == 'CedarPeak Security Services'
    assert invoice['subtotal'] == '10488.00'


def test_page_header_cannot_become_account_number(monkeypatch):
    text = """Page 1
INVOICE
Invoice #: INV-100
Account Number
Page 1
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    assert 'bank_account_number' not in local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']


def test_source_email_comes_from_vendor_contact_not_bill_to_or_mail_metadata(monkeypatch):
    text = """Blue Ocean Technologies, LLC
INVOICE
Bill To: SG Securities (HK) Limited
ATTN Accounts Payable
Email: br-mkd-invoicing@sgcib.com
Please forward questions to accounting@blueocean-tech.io
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    invoice = local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']
    assert invoice['vendor_email'] == 'accounting@blueocean-tech.io'
    text = text.replace('Please forward questions to accounting@blueocean-tech.io', '')
    assert local.extract_invoice(b'PDF', 'invoice.pdf')['invoice']['vendor_email'] == 'br-mkd-invoicing@sgcib.com'


def test_synthetic_or_truncated_email_is_not_exported_as_company_contact(monkeypatch):
    text = """Example Vendor
INVOICE
Contact: billing! 0@example.test
"""
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: text)
    assert local.extract_invoice(b'PDF', 'invoice.pdf')['invoice'].get('vendor_email') is None
