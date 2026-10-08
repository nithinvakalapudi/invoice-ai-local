"""Folder Run appends once per saved email version to the existing history."""
import csv
import io
from dataclasses import replace

from fastapi.testclient import TestClient
import pymupdf

import web_app
from models.invoice_schema import Invoice, InvoiceResult
from test_msg_uploads import msg_bytes
from utils.file_utils import DocumentInput


def test_outlook_run_and_cumulative_csv(tmp_path, monkeypatch):
    data_root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings,
                        local_storage_dir=str(data_root)))
    monkeypatch.setattr(web_app, 'extraction_ready', lambda: True)
    monkeypatch.setattr(web_app, 'run_outlook_folder', __import__(
        'services.outlook_folder', fromlist=['run_outlook_folder']).run_outlook_folder)
    import services.outlook_folder as folder_service

    seen = []

    def expand(name, data, limit):
        seen.append((name, data))
        return [DocumentInput('invoice.pdf', b'%PDF-test', name, 'invoice.pdf', 1)]

    def extract(document, tolerance, threshold):
        return InvoiceResult(invoice=Invoice(invoice_number=document.source_email or document.filename,
                             vendor_name='Vendor', invoice_date='2026-10-01',
                             currency='USD', total_amount='10.00'),
                             source_file=document.filename, source_email=document.source_email,
                             source_attachment=document.source_attachment,
                             attachment_index=document.attachment_index,
                             processing_status='PROCESSED', validation_status='REVIEW_REQUIRED',
                             review_required=True)

    monkeypatch.setattr(folder_service, 'expand_upload', expand)
    monkeypatch.setattr(folder_service, 'process_document', extract)
    monkeypatch.setattr(web_app, 'process_document', extract)
    client = TestClient(web_app.app)
    folder = data_root / 'Outlook'
    assert client.get('/api/config').json()['outlook_folder'] == str(folder)
    manual = client.post('/api/process', files=[('files', ('manual.pdf', b'%PDF-test', 'application/pdf'))])
    assert manual.status_code == 200, manual.text
    folder.joinpath('first.msg').write_bytes(b'email 1')
    folder.joinpath('notes.txt').write_text('ignored')
    payload = {'tolerance': 0.02, 'threshold': 95}
    first = client.post('/api/outlook/run', json=payload)
    assert first.status_code == 200, first.text
    assert first.json()['emails_processed'] == 1
    assert first.json()['ignored_files'] == 1
    assert first.json()['results'][0]['source_email'] == 'first.msg'
    again = client.post('/api/outlook/run', json=payload)
    assert again.json()['emails_processed'] == 0
    assert again.json()['emails_already_processed'] == 1
    assert len(seen) == 1
    folder.joinpath('second.msg').write_bytes(b'email 2')
    second = client.post('/api/outlook/run', json=payload)
    assert second.json()['emails_processed'] == 1
    rows = list(csv.DictReader(io.StringIO((data_root / 'Invoice_Data.csv').read_text(encoding='utf-8-sig'))))
    assert [row['Invoice'] for row in rows] == ['manual.pdf', 'first.msg', 'second.msg']
    assert [row['source_email'] for row in rows] == ['', 'first.msg', 'second.msg']
    assert folder.joinpath('first.msg').is_file()
    assert client.get('/api/download/history/Invoice_Data.csv').status_code == 200


def test_outlook_run_empty_folder_and_same_origin(tmp_path, monkeypatch):
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings,
                        local_storage_dir=str(tmp_path / 'local_data')))
    client = TestClient(web_app.app)
    payload = {'tolerance': 0.02, 'threshold': 95}
    empty_run = client.post('/api/outlook/run', json=payload)
    assert empty_run.status_code == 200
    assert empty_run.json()['emails_processed'] == 0
    assert client.post('/api/outlook/run', json=payload,
                       headers={'Origin': 'https://other.example'}).status_code == 403


def test_outlook_run_reads_real_msg_attachments_and_skips_repeat(tmp_path, monkeypatch):
    """Exercise real MSG decoding, PDF extraction, saved CSV, and repeat Run."""
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    client = TestClient(web_app.app)
    folder = root / 'Outlook'
    client.get('/api/config')

    def pdf(number):
        document = pymupdf.open()
        page = document.new_page()
        page.insert_text((50, 70), '\n'.join([
            'Blue Ocean Technologies, LLC', f'Invoice #: {number}',
            'Invoice Date: 10/01/2026', 'Amounts in USD',
            'Service: 10/01/2026 - 10/31/2026',
            'Account Number: 578013655', 'Subtotal: $100.00',
            'Sales Tax: $0.00', 'Total Amount Due: $100.00',
        ]))
        output = document.tobytes()
        document.close()
        return output

    folder.joinpath('mail-a.msg').write_bytes(msg_bytes([
        {'name': 'a.pdf', 'data': pdf('RUN-1001')},
        {'name': 'b.pdf', 'data': pdf('RUN-1002')},
        {'name': 'signature.png', 'data': b'not an invoice', 'hidden': 1},
    ]))
    folder.joinpath('mail-b.msg').write_bytes(msg_bytes([
        {'name': 'c.pdf', 'data': pdf('RUN-1003')},
    ]))
    payload = {'tolerance': 0.02, 'threshold': 95}
    response = client.post('/api/outlook/run', json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['emails_processed'] == 2
    assert body['counts']['total'] == 3
    assert body['counts']['failed'] == 0
    assert [row['source_email'] for row in body['results']] == [
        'mail-a.msg', 'mail-a.msg', 'mail-b.msg']
    assert [row['source_attachment'] for row in body['results']] == ['a.pdf', 'b.pdf', 'c.pdf']
    saved = list(csv.DictReader(io.StringIO(
        (root / 'MDM' / 'Invoice_data.csv').read_text(encoding='utf-8-sig'))))
    assert [row['Invoice Number'] for row in saved] == ['RUN-1001', 'RUN-1002', 'RUN-1003']
    assert all(row['mdm_row']['Processing result'] == 'SUCCESS' for row in body['results'])
    again = client.post('/api/outlook/run', json=payload)
    assert again.status_code == 200
    assert again.json()['emails_already_processed'] == 2
    assert again.json()['emails_processed'] == 0
    assert len(list(csv.DictReader(io.StringIO(
        (root / 'MDM' / 'Invoice_data.csv').read_text(encoding='utf-8-sig'))))) == 3
