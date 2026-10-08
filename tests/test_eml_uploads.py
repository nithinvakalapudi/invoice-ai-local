"""Email uploads must process attached files, never body or inline resources."""
import csv
import io
import zipfile
from dataclasses import replace
from email.message import EmailMessage
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import web_app
from services.local_storage import LocalStorage
from services.mdm_csv_service import COLUMNS, REPORT_NAME
from utils.file_utils import expand_upload


def email_bytes():
    message = EmailMessage()
    message['Subject'] = 'Invoice email'
    message.set_content('PRIVATE EMAIL BODY; fake invoice.pdf is not an attachment')
    message.add_attachment(b'%PDF-1.4 attached', maintype='application', subtype='pdf', filename='invoice.pdf')
    message.add_attachment(b'IMAGE', maintype='image', subtype='png', filename='invoice.png')
    message.add_attachment(b'notes', maintype='text', subtype='plain', filename='notes.txt')
    message.add_attachment(b'INLINE', maintype='image', subtype='png',
                           cid='<logo>', filename='signature.png')
    return message.as_bytes()


def test_eml_only_real_supported_attachments_are_documents():
    documents = expand_upload('mail.eml', email_bytes(), 1_000_000)
    assert [(item.filename, item.source_email, item.attachment_index) for item in documents] == [
        ('invoice.pdf', 'mail.eml', 1), ('invoice.png', 'mail.eml', 2),
        ('notes.txt', 'mail.eml', 3),
    ]
    assert [item.data for item in documents[:2]] == [b'%PDF-1.4 attached', b'IMAGE']
    assert documents[2].intake_status == 'SKIPPED'


def test_email_only_zip_expands_attached_files():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('folder/mail.eml', email_bytes())
    documents = expand_upload('mails.zip', output.getvalue(), 1_000_000)
    assert [item.filename for item in documents] == ['invoice.pdf', 'invoice.png', 'notes.txt']
    assert all(item.source_email == 'mail.eml' for item in documents)


SAMPLE = Path.home() / 'Downloads' / 'two_outlook_invoice_emails.zip'


@pytest.mark.skipif(not SAMPLE.is_file(), reason='User email sample is not available')
def test_user_supplied_eml_files_upload_and_save_four_invoices(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    with zipfile.ZipFile(SAMPLE) as archive:
        files = [('files', (name, archive.read(name), 'message/rfc822'))
                 for name in archive.namelist() if name.endswith('.eml')]
    response = TestClient(web_app.app).post('/api/process', files=files)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['counts']['total'] == 4
    assert body['counts']['failed'] == 0
    assert {item['source_email'] for item in body['results']} == {
        'invoice_email_01.eml', 'invoice_email_02.eml'}
    assert all(item['source_attachment'].endswith('.pdf') for item in body['results'])
    with (LocalStorage(root).root / REPORT_NAME).open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 4
    assert list(rows[0]) == COLUMNS


@pytest.mark.skipif(not SAMPLE.is_file(), reason='User email sample is not available')
def test_user_supplied_eml_folder_run_skips_repeat(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    client = TestClient(web_app.app)
    folder = LocalStorage(root).outlook_folder
    with zipfile.ZipFile(SAMPLE) as archive:
        for name in archive.namelist():
            if name.endswith('.eml'):
                (folder / name).write_bytes(archive.read(name))
    first = client.post('/api/outlook/run', json={'tolerance': 0.02, 'threshold': 95})
    assert first.status_code == 200, first.text
    assert first.json()['emails_processed'] == 2
    assert first.json()['counts']['total'] == 4
    second = client.post('/api/outlook/run', json={'tolerance': 0.02, 'threshold': 95})
    assert second.status_code == 200
    assert second.json()['emails_already_processed'] == 2
    assert second.json()['emails_processed'] == 0
