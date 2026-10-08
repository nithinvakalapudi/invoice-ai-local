"""Contract tests for the FLOWSTACK UI's local API, without an external AI call."""
from dataclasses import replace
from datetime import datetime, timezone
import io
import zipfile

from fastapi.testclient import TestClient
from openpyxl import Workbook
import pymupdf

import web_app
from models.invoice_schema import Invoice, InvoiceResult
from services.mdm_csv_service import DISPLAY_COLUMNS, LEGACY_DRAFT_WARNING
from test_msg_uploads import msg_bytes


def _fake_invoice(document, tolerance, threshold):
    return InvoiceResult(
        invoice=Invoice(invoice_number='INV-123', invoice_date='2026-10-01',
                        vendor_name='Example Vendor', currency='USD', total_amount='15.00'),
        source_file=document.filename,
        processed_at=datetime.now(timezone.utc).isoformat(),
        processing_status='PROCESSED', validation_status='REVIEW_REQUIRED',
        review_required=True,
    )


def test_manual_msg_upload_processes_only_real_pdf_attachment(tmp_path, monkeypatch):
    """The browser's Process endpoint must accept Outlook MSG, not just folder Run."""
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings,
                        local_storage_dir=str(tmp_path / 'local_data')))
    client = TestClient(web_app.app)
    pdf = pymupdf.open()
    pdf.new_page().insert_text((50, 70), '\n'.join([
        'Blue Ocean Technologies, LLC', 'Invoice #: MSG-1001',
        'Invoice Date: 10/01/2026', 'Amounts in USD',
        'Subtotal: $100.00', 'Sales Tax: $0.00', 'Total Amount Due: $100.00',
    ]))
    mail = msg_bytes([
        {'name': 'invoice.pdf', 'data': pdf.tobytes()},
        {'name': 'signature.png', 'data': b'not an invoice', 'hidden': 1},
    ])
    pdf.close()
    response = client.post('/api/process',
                           files=[('files', ('incoming.msg', mail, 'application/vnd.ms-outlook'))],
                           data={'tolerance': '0.02', 'threshold': '95'})
    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch['counts']['total'] == 1
    assert batch['counts']['failed'] == 0
    assert batch['results'][0]['source_email'] == 'incoming.msg'
    assert batch['results'][0]['source_attachment'] == 'invoice.pdf'
    assert batch['results'][0]['invoice']['invoice_number'] == 'MSG-1001'


def test_batch_counts_separate_extraction_from_approval():
    results = []
    for status, validation, needs_review in [
        ('PROCESSED', 'VALID', False),
        ('LOCAL_DRAFT', 'REVIEW_REQUIRED', True),
        ('OCR_ONLY', 'REVIEW_REQUIRED', True),
        ('IMPORTED', 'REVIEW_REQUIRED', True),
        ('FAILED', 'FAILED', True),
    ]:
        result = _fake_invoice(type('Source', (), {'filename': 'invoice.pdf'})(), .02, 95)
        result.processing_status = status
        result.validation_status = validation
        result.review_required = needs_review
        results.append(result)
    counts = web_app._counts(results)
    assert counts['total'] == 5
    assert counts['processed'] == 3
    assert counts['auto_approved'] == 1
    assert counts['local_draft'] == 1
    assert counts['ocr_only'] == 1
    assert counts['imported'] == 1
    assert counts['review'] == 4
    assert counts['failed'] == 1


def test_web_upload_history_review_and_download(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings,
                        local_storage_dir=str(tmp_path / 'local_data')))
    monkeypatch.setattr(web_app, 'process_document', _fake_invoice)
    monkeypatch.setattr(web_app, 'extraction_ready', lambda: True)
    client = TestClient(web_app.app)
    config = client.get('/api/config').json()
    assert config['storage_ready'] and config['api_ready']
    assert config['mdm_columns'] == DISPLAY_COLUMNS
    assert config['required_fields'] == ['Invoice Number', 'Vendor', 'Invoice Date',
                                         'Currency', 'Total Amount']

    response = client.post('/api/process', files=[('files', ('one.pdf', b'%PDF-1.4 example', 'application/pdf'))],
                           data={'tolerance': '0.02', 'threshold': '95'})
    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch['counts']['total'] == 1
    assert batch['results'][0]['source_file'] == 'one.pdf'
    assert list(batch['results'][0]['mdm_row']) == DISPLAY_COLUMNS
    assert batch['results'][0]['mdm_row']['Invoice Number'] == 'INV-123'
    assert client.get('/api/history').json()['batches'][0]['id'] == batch['batch_id']
    assert b'INV-123' in client.get(f"/api/download/batch/{batch['batch_id']}/Invoice_Data.csv").content

    correction = client.post(f"/api/batches/{batch['batch_id']}/review", json={
        'generation': batch['generation'], 'index': 0,
        'fields': {'invoice_number': 'INV-124', 'region': 'Austin, TX'},
        'tolerance': 0.02, 'threshold': 95,
    })
    assert correction.status_code == 200, correction.text
    assert b'INV-124' in client.get('/api/download/history/Invoice_Data.csv').content
    assert client.get(f"/api/batches/{batch['batch_id']}").json()['results'][0]['invoice']['invoice_number'] == 'INV-124'
    assert correction.json()['results'][0]['mdm_row']['Invoice Number'] == 'INV-124'
    assert correction.json()['results'][0]['mdm_row']['Region'] == 'Austin, TX'
    assert correction.json()['results'][0]['mdm_row']['Review status'] == 'SUCCESS'
    assert correction.json()['results'][0]['mdm_row']['Action'] == 'No Review Required'


def test_duplicate_invoice_is_flagged_across_separate_uploads(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    monkeypatch.setattr(web_app, 'process_document', _fake_invoice)
    client = TestClient(web_app.app)
    files = [('files', ('one.pdf', b'%PDF-1.4 example', 'application/pdf'))]
    first = client.post('/api/process', files=files).json()
    second = client.post('/api/process', files=files).json()
    assert first['counts']['duplicate'] == 0
    assert second['counts']['duplicate'] == 1
    assert second['results'][0]['duplicate_status'] == 'POSSIBLE_DUPLICATE'
    assert second['results'][0]['mdm_row']['Action'] == 'Review Required'
    report = client.get('/api/download/history/MDM/Invoice_data.csv')
    assert report.status_code == 200
    assert b'Duplicate status' not in report.content
    changed = client.post(f"/api/batches/{second['batch_id']}/review", json={
        'generation': second['generation'], 'index': 0,
        'fields': {'invoice_number': 'INV-999'}, 'tolerance': 0.02, 'threshold': 95,
    })
    assert changed.status_code == 200, changed.text
    assert changed.json()['results'][0]['duplicate_status'] == 'NOT_DUPLICATE'
    assert changed.json()['results'][0]['mdm_row']['Action'] == 'No Review Required'


def test_invalid_batch_id_returns_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings,
                        local_storage_dir=str(tmp_path / 'local_data')))
    client = TestClient(web_app.app)
    assert client.get('/api/batches/not-a-batch').status_code == 404
    assert client.get('/api/download/batch/not-a-batch/Invoice_Data.csv').status_code == 404


def test_saved_legacy_draft_warning_is_not_shown_as_new_error(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings,
                        local_storage_dir=str(tmp_path / 'local_data')))
    store = web_app.storage()
    result = _fake_invoice(type('Source', (), {'filename': 'old.pdf'})(), .02, 95)
    result.processing_status = 'LOCAL_DRAFT'
    result.warnings = [LEGACY_DRAFT_WARNING]
    store.save_batch('a' * 32, [result])
    item = TestClient(web_app.app).get('/api/batches/' + 'a' * 32).json()['results'][0]
    assert item['warnings'] == []
    assert LEGACY_DRAFT_WARNING not in item['mdm_row']['Comments']
    assert item['validation_status'] == 'REVIEW_REQUIRED'


def test_cross_origin_write_and_reset_require_confirmations(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings,
                        local_storage_dir=str(tmp_path / 'local_data')))
    client = TestClient(web_app.app)
    client.get('/api/config')
    assert client.post('/api/reset/prepare', headers={'Origin': 'https://other.example'}).status_code == 403
    prepared = client.post('/api/reset/prepare').json()
    assert client.post('/api/reset', json={**prepared, 'confirmation': 'NO'}).status_code == 422
    response = client.post('/api/reset', json={**prepared, 'confirmation': 'CLEAR SAVED INVOICES'})
    assert response.status_code == 200, response.text
    assert response.json()['cleared_records'] == 0
    assert client.get('/api/config').json()['records'] == 0


def test_local_pilot_accepts_upload_for_ocr_review(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings,
                        local_storage_dir=str(root)))
    from services import local_extraction_service
    monkeypatch.setattr(local_extraction_service, 'extract_document_text', lambda *_: 'Readable invoice OCR text')
    monkeypatch.setattr(web_app, 'extraction_ready', lambda: False)
    client = TestClient(web_app.app)
    config = client.get('/api/config').json()
    assert config['extraction_backend'] == 'local'
    assert config['api_ready'] is False
    assert config['intake_ready'] is True
    assert 'Review every field' in config['setup_message']
    upload = client.post('/api/process', files=[('files', ('invoice.pdf', b'%PDF-test', 'application/pdf'))])
    assert upload.status_code == 200, upload.text
    assert upload.json()['results'][0]['processing_status'] == 'OCR_ONLY'
    assert upload.json()['results'][0]['validation_status'] == 'REVIEW_REQUIRED'
    assert upload.json()['results'][0]['invoice']['invoice_number'] is None
    assert upload.json()['counts']['processed'] == 1
    assert upload.json()['counts']['auto_approved'] == 0
    assert upload.json()['counts']['ocr_only'] == 1
    assert client.get('/api/history').json()['batches'][0]['counts']['total'] == 1
    csv_data = client.get('/api/download/history/Invoice_Data.csv').content
    assert b'REVIEW_REQUIRED' in csv_data
    assert b'OCR-only intake' in csv_data


def test_all_supported_container_types_upload_and_archive(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    from services import local_extraction_service
    seen = []
    def read_text(name, data):
        seen.append((name, data))
        return 'Readable document text'
    monkeypatch.setattr(local_extraction_service, 'extract_document_text', read_text)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as zipped:
        zipped.writestr('nested/in_zip.pdf', b'%PDF-zip')
    mail = msg_bytes([{'name': 'attached.png', 'data': b'PNG attachment'},
                      {'name': 'signature.png', 'data': b'PRIVATE', 'hidden': 1}])
    files = [('files', ('direct.pdf', b'%PDF-direct', 'application/pdf')),
             ('files', ('image.jpg', b'JPEG', 'image/jpeg')),
             ('files', ('batch.zip', archive.getvalue(), 'application/zip')),
             ('files', ('message.msg', mail, 'application/vnd.ms-outlook'))]
    client = TestClient(web_app.app)
    response = client.post('/api/process', files=files)
    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch['counts']['total'] == 4
    assert batch['counts']['review'] == 4
    assert {name for name, _ in seen} == {'direct.pdf', 'image.jpg', 'in_zip.pdf', 'attached.png'}
    assert all(b'PRIVATE' not in data for _, data in seen)
    assert all(item['processing_status'] == 'OCR_ONLY' for item in batch['results'])
    assert next(item for item in batch['results'] if item['source_email'])['source_email'] == 'message.msg'
    batch_dir = root / 'uploads' / batch['batch_id']
    assert len(list((batch_dir / 'originals').iterdir())) == 5  # Four originals plus manifest.
    assert len(list((batch_dir / 'documents').iterdir())) == 5  # Four documents plus manifest.
    csv_data = client.get('/api/download/history/Invoice_Data.csv').content
    assert csv_data.count(b'REVIEW_REQUIRED') == 4


def test_real_native_pdf_upload_is_saved_for_review(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    pdf = pymupdf.open()
    pdf.new_page().insert_text((60, 60), 'Invoice INV-42 Vendor Example Currency USD Total 42.00 Service October 2026')
    response = TestClient(web_app.app).post('/api/process', files=[
        ('files', ('native.pdf', pdf.tobytes(), 'application/pdf'))])
    assert response.status_code == 200, response.text
    result = response.json()['results'][0]
    assert result['processing_status'] in {'OCR_ONLY', 'LOCAL_DRAFT'}
    assert result['review_required'] is True
    assert (root / 'uploads' / response.json()['batch_id'] / 'originals' / '0001_native.pdf').is_file()


def test_csv_and_excel_invoice_rows_are_imported_for_review(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    csv_data = b'Invoice,Vendor,Invoice Date,Currency,Invoice Amount,MDM Account number,Source email\nCSV-1,CSV Vendor,10/01/2026,USD,42.00,00123,billing@csv-vendor.co\n'
    book = Workbook()
    sheet = book.active
    sheet.title = 'Invoice_Data'
    sheet.append(['Invoice Number', 'Vendor -As per MDM', 'Billing Account', 'Invoice Date', 'Net Amount'])
    sheet.append(['XLSX-1', 'Excel Vendor', '00045', '2026-10-02', 51.25])
    output = io.BytesIO()
    book.save(output)
    response = TestClient(web_app.app).post('/api/process', files=[
        ('files', ('invoices.csv', csv_data, 'text/csv')),
        ('files', ('invoices.xlsx', output.getvalue(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'))])
    assert response.status_code == 200, response.text
    batch = response.json()
    assert batch['counts']['imported'] == 2
    assert batch['counts']['processed'] == 0
    assert batch['counts']['review'] == 2
    assert [result['invoice']['invoice_number'] for result in batch['results']] == ['CSV-1', 'XLSX-1']
    assert [result['invoice']['bank_account_number'] for result in batch['results']] == ['00123', '00045']
    assert batch['results'][0]['mdm_row']['Source email'] == 'billing@csv-vendor.co'
    assert all(result['validation_status'] == 'REVIEW_REQUIRED' for result in batch['results'])
    assert b'CSV-1' in (root / 'Invoice_Data.csv').read_bytes()
    assert len(list((root / 'uploads' / batch['batch_id'] / 'originals').iterdir())) == 3


def test_unrecognized_csv_becomes_a_failed_audit_row(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    response = TestClient(web_app.app).post('/api/process', files=[
        ('files', ('unknown.csv', b'foo,bar\n1,2\n', 'text/csv'))])
    assert response.status_code == 200
    assert response.json()['counts']['failed'] == 1
    assert 'Invoice or Invoice Number' in response.json()['results'][0]['errors'][0]
