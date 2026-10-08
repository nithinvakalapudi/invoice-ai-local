"""Extraction, validation, file and CSV contracts with deterministic provider responses."""
import csv
import io
import subprocess
import zipfile
from dataclasses import fields
from unittest.mock import Mock

import pymupdf
import pytest
from PIL import Image

from models.invoice_schema import INVOICE_FIELDS, CSV_INVOICE_COLUMNS, Invoice, InvoiceResult
from services.document_processing import process_document
from services import document_processing
from services import local_extraction_service as local
from services import ocr_service as ocr
from services.csv_service import generate_csvs
from services.mdm_csv_service import generate_mdm_csv
from services.duplicate_service import mark_duplicates
from services.validation_service import validate_invoice
from utils.file_utils import DocumentInput, expand_upload


def test_local_extraction_is_review_only(monkeypatch):
    monkeypatch.setattr(local, '_pilot', lambda: None)
    monkeypatch.setattr(local, '_reviewed_model', lambda: None)
    assert local.extraction_ready() is False
    monkeypatch.setattr(local, 'extract_document_text', lambda *_: 'Invoice OCR text')
    payload = local.extract_invoice(b'PDF', 'invoice.pdf')
    assert payload['_ocr_only'] is True
    assert payload['invoice'] == {}
    assert payload['field_confidence'] == {}


def test_schema_alignment_and_valid_review_csv_statuses():
    assert set(INVOICE_FIELDS) == {field.name for field in fields(Invoice)}
    invoice = {'invoice_number': 'INV-1', 'vendor_name': 'Vendor', 'invoice_date': '10/01/2026',
               'currency': 'USD', 'subtotal': 100, 'total_amount': 100, 'bank_account_number': '0012345'}
    payload = {'invoice': invoice, 'line_items': [{'description': 'Service', 'quantity': 1,
                'unit_price': 100, 'line_total': 100}], 'field_confidence': {key: 99 for key in invoice}}
    valid = InvoiceResult.from_ai_payload(payload, 'invoice.pdf', '2026-10-05T00:00:00Z', 95)
    validate_invoice(valid, .02, 95)
    assert valid.validation_status == 'VALID'
    assert valid.invoice.bank_account_number == '0012345'
    valid.invoice.total_amount = 120
    validate_invoice(valid, .02, 95)
    assert valid.validation_status == 'REVIEW_REQUIRED'
    assert any('Total mismatch' in error for error in valid.errors)
    import csv
    rows = csv.DictReader(io.StringIO(generate_csvs([valid])['Invoice_Data.csv'].decode()))
    assert rows.fieldnames == CSV_INVOICE_COLUMNS
    assert next(rows)['Status'] == 'REVIEW_REQUIRED'


def test_invalid_vendor_email_is_a_validation_issue():
    invoice = Invoice(invoice_number='INV-1', vendor_name='Vendor', invoice_date='2026-10-01',
                      currency='USD', total_amount=100, vendor_email='not-an-email')
    result = InvoiceResult(invoice=invoice, field_confidence={key: 99 for key in INVOICE_FIELDS},
                           source_file='invoice.pdf')
    validate_invoice(result, .02, 95)
    assert 'Invalid vendor email address' in result.errors
    assert result.review_required


@pytest.mark.parametrize('confidence', [None, 72])
def test_scanned_values_survive_low_or_missing_confidence_for_review(confidence):
    values = {'invoice_number': 'SCAN-007', 'vendor_name': 'Read from scan',
              'invoice_date': '2026-10-01', 'currency': 'USD', 'total_amount': 100}
    raw = {'invoice': values, 'line_items': [{'description': 'Scanned service',
           'quantity': 1, 'unit_price': 100, 'line_total': 100}],
           'field_confidence': {} if confidence is None else {key: confidence for key in values}}
    result = InvoiceResult.from_ai_payload(raw, 'scan.jpg', '2026-10-05T00:00:00Z', 95)
    validate_invoice(result, .02, 95)
    assert result.invoice.invoice_number == 'SCAN-007'
    assert result.line_items[0].description == 'Scanned service'
    assert result.validation_status == 'REVIEW_REQUIRED'
    assert result.review_required
    rows = csv.DictReader(io.StringIO(generate_csvs([result])['Invoice_Data.csv'].decode()))
    assert next(rows)['Invoice'] == 'SCAN-007'
    mdm_rows = csv.DictReader(io.StringIO(generate_mdm_csv([result]).decode('utf-8-sig')))
    assert next(mdm_rows)['Invoice Number'] == 'SCAN-007'


@pytest.mark.parametrize('score,calibrated,expected', [
    (96, True, 'VALID'), (95, True, 'REVIEW_REQUIRED'),
    (99, False, 'REVIEW_REQUIRED'),
])
def test_local_draft_auto_pass_requires_calibrated_confidence_above_95(
    monkeypatch, score, calibrated, expected
):
    invoice = {'invoice_number': 'INV-96', 'vendor_name': 'Test Vendor',
               'invoice_date': '2026-10-01', 'currency': 'USD',
               'subtotal': '100.00', 'tax_amount': '0.00', 'total_amount': '100.00'}
    payload = {'invoice': invoice, 'line_items': [], '_local_draft': True,
               '_confidence_calibrated': calibrated,
               'field_confidence': {key: score for key in invoice}}
    monkeypatch.setattr(document_processing, 'extract_invoice', lambda *_: payload)
    result = process_document(DocumentInput('invoice.pdf', b'PDF'), .02, 95)
    assert result.validation_status == expected
    assert result.review_required is (expected != 'VALID')


def test_duplicate_invoices_are_flagged_not_removed():
    def result():
        return InvoiceResult(invoice=Invoice(invoice_number='I-1', vendor_name='Vendor',
                                             invoice_date='2026-10-01', total_amount=100))
    results = mark_duplicates([result(), result()])
    assert len(results) == 2
    assert all(item.duplicate_status == 'POSSIBLE_DUPLICATE' for item in results)


def test_saved_history_duplicate_can_be_recomputed_after_correction():
    saved = InvoiceResult(invoice=Invoice(invoice_number='I-1', vendor_name='Vendor',
                                         invoice_date='2026-10-01', total_amount=100))
    current = InvoiceResult(invoice=Invoice(invoice_number='I-1', vendor_name='Vendor',
                                           invoice_date='2026-10-01', total_amount=100))
    mark_duplicates([current], [saved])
    assert current.duplicate_status == 'POSSIBLE_DUPLICATE'
    assert current.warnings == ['Possible duplicate invoice in saved history']
    current.invoice.invoice_number = 'I-2'
    mark_duplicates([current], [saved])
    assert current.duplicate_status == 'NOT_DUPLICATE'
    assert not any('duplicate invoice' in warning for warning in current.warnings)


def test_native_pdf_and_scanned_pdf_ocr_fallback(monkeypatch):
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 50), 'Invoice INV-1 Vendor Example Total USD 100 Account 0012345')
    assert 'INV-1' in ocr.extract_document_text('invoice.pdf', doc.tobytes())
    blank = pymupdf.open()
    blank.new_page()
    monkeypatch.setattr(ocr, '_ocr_image', lambda image: 'OCR invoice INV-2')
    assert ocr.extract_document_text('scan.pdf', blank.tobytes()) == 'OCR invoice INV-2'


def test_image_ocr_and_corrupt_input(monkeypatch):
    output = io.BytesIO()
    Image.new('RGB', (20, 20), 'white').save(output, format='PNG')
    monkeypatch.setattr(ocr, '_ocr_image', lambda image, **kwargs: 'Invoice image')
    assert ocr.extract_document_text('invoice.png', output.getvalue()) == 'Invoice image'
    with pytest.raises(ValueError, match='corrupted'):
        ocr.extract_document_text('bad.png', b'bad')
    with pytest.raises(ValueError, match='corrupted'):
        ocr.extract_document_text('bad.pdf', b'bad')


def test_installed_tesseract_reads_image_and_scanned_pdf():
    if not ocr.is_tesseract_available():
        pytest.skip('Optional local Tesseract executable is unavailable')
    from PIL import ImageDraw, ImageFont
    image = Image.new('RGB', (1200, 350), 'white')
    ImageDraw.Draw(image).text((40, 40), 'INVOICE 12345\nTOTAL USD 100.00\nACCOUNT 001234567',
                              font=ImageFont.load_default(size=40), fill='black', spacing=15)
    output = io.BytesIO()
    image.save(output, format='PNG')
    pdf = pymupdf.open()
    page = pdf.new_page(width=600, height=175)
    page.insert_image(page.rect, stream=output.getvalue())
    for name, data in [('image.png', output.getvalue()), ('scan.pdf', pdf.tobytes())]:
        text = ocr.extract_document_text(name, data)
        assert '12345' in text and '100.00' in text and '001234567' in text


def test_broken_tesseract_launcher_does_not_crash_config(monkeypatch):
    monkeypatch.setattr(ocr.pytesseract, 'get_tesseract_version',
                        Mock(side_effect=subprocess.CalledProcessError(3221225595, 'tesseract')))
    assert ocr.is_tesseract_available() is False


def test_invalid_zip_and_unsupported_upload_rejected():
    with pytest.raises(ValueError, match='Invalid ZIP'):
        expand_upload('bad.zip', b'bad', 1000)
    with pytest.raises(ValueError, match='Unsupported'):
        expand_upload('file.exe', b'bad', 1000)


def test_zip_limits_and_empty_archive_are_reported():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zipped:
        zipped.writestr('readme.txt', 'No invoices')
    empty = expand_upload('notes.zip', archive.getvalue(), 100)
    assert len(empty) == 1 and empty[0].intake_status == 'SKIPPED'

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zipped:
        for number in range(201):
            zipped.writestr(f'invoice-{number}.pdf', b'%PDF')
    with pytest.raises(ValueError, match='more than 200'):
        expand_upload('many.zip', archive.getvalue(), 100)

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zipped:
        for number in range(5):
            zipped.writestr(f'invoice-{number}.pdf', bytes(range(90)))
    with pytest.raises(ValueError, match='total size limit'):
        expand_upload('large.zip', archive.getvalue(), 100)

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zipped:
        zipped.writestr('oversized.pdf', bytes(range(101)))
    with pytest.raises(ValueError, match='file size limit'):
        expand_upload('oversized.zip', archive.getvalue(), 100)
