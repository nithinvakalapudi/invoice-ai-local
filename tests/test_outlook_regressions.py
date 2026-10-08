"""Real MSG containers and isolated storage: no company files or paid API calls."""
import csv
import io
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import web_app
from config.settings import settings
from services.local_storage import LocalStorage, _atomic_write
from services.outlook_folder import run_outlook_folder
from services.storage_lock import folder_operation_lock
from test_msg_uploads import msg_bytes


@pytest.fixture
def store(tmp_path):
    return LocalStorage(tmp_path / 'local_data')


def run(store, limit=1_000_000):
    return run_outlook_folder(store, .02, 95, limit)


def mail(store, name='invoice.msg', attachments=None):
    path = store.outlook_folder / name
    path.write_bytes(msg_bytes(attachments if attachments is not None else [
        {'name': 'invoice.pdf', 'data': b'%PDF-test'}]))
    return path


def test_empty_folder_creates_no_batch(store):
    outcome = run(store)
    assert outcome.folder.is_dir()
    assert outcome.batch_id is None
    assert store.count() == (0, 0)


def test_other_process_cannot_run_same_folder_or_reset_it(store):
    import subprocess
    import sys
    mail(store)
    with folder_operation_lock(store.root):
        with pytest.raises(RuntimeError, match='already in progress'):
            store.reset(str(store.root))
        code = ('from pathlib import Path; from services.storage_lock import folder_operation_lock; '
                'import sys; lock=folder_operation_lock(Path(sys.argv[1])); lock.__enter__()')
        child = subprocess.run([sys.executable, '-c', code, str(store.root)],
                               capture_output=True, text=True, timeout=15)
        assert child.returncode != 0
        assert 'already in progress' in child.stderr
    assert store.outlook_folder.joinpath('invoice.msg').is_file()
    with patch('services.document_processing.extract_invoice', return_value={}):
        assert run(store).emails_processed == 1  # released lock is usable again


def test_real_msg_multiple_attachments_filters_private_content(store):
    mail(store, 'first.MSG', [
        {'name': 'one.pdf', 'data': b'PDF A'},
        {'name': 'two.png', 'data': b'IMAGE B'},
        {'name': 'signature.png', 'data': b'PRIVATE', 'hidden': 1},
        {'name': 'logo.png', 'data': b'PRIVATE', 'cid': 'logo'},
    ])
    mail(store, 'second.msg')
    with patch('services.document_processing.extract_invoice', return_value={}) as ai:
        outcome = run(store)
    assert ai.call_count == 3
    assert all(b'PRIVATE' not in call.args[0] for call in ai.call_args_list)
    assert outcome.emails_processed == 2
    assert len(outcome.results) == 3
    assert [(r.source_email, r.source_attachment) for r in outcome.results] == [
        ('first.MSG', 'one.pdf'), ('first.MSG', 'two.png'), ('second.msg', 'invoice.pdf')]
    for payload in store.report_files().values():
        assert b'PRIVATE BODY' not in payload


def test_invalid_msg_and_failed_ai_do_not_stop_other_attachments(store):
    store.outlook_folder.joinpath('broken.msg').write_bytes(b'invalid')
    mail(store, attachments=[{'name': 'bad.pdf', 'data': b'A'}, {'name': 'ok.pdf', 'data': b'B'}])
    with patch('services.document_processing.extract_invoice', side_effect=[RuntimeError('quota exhausted'), {}]):
        outcome = run(store)
    assert len(outcome.results) == 3
    assert sum(r.processing_status == 'FAILED' for r in outcome.results) == 2
    assert any('quota exhausted' in ' '.join(r.errors) for r in outcome.results)
    assert store.count() == (1, 3)


def test_empty_and_unsupported_emails_do_not_call_ai(store):
    mail(store, 'empty.msg', [])
    mail(store, 'notes.msg', [{'name': 'notes.txt', 'data': b'notes'}])
    with patch('services.document_processing.extract_invoice') as ai:
        outcome = run(store)
    ai.assert_not_called()
    assert all(r.processing_status == 'SKIPPED' for r in outcome.results)


def test_oversized_email_does_not_block_valid_peer(store):
    store.outlook_folder.joinpath('huge.msg').write_bytes(b'x' * 100_001)
    mail(store, 'valid.msg')
    with patch('services.document_processing.extract_invoice', return_value={}):
        outcome = run(store, 100_000)
    assert outcome.emails_processed == 1
    assert any('huge.msg' in warning for warning in outcome.warnings)
    assert store.count() == (1, 1)


def test_unreadable_email_does_not_block_valid_peer(store):
    blocked = mail(store, 'blocked.msg')
    mail(store, 'valid.msg')
    original = Path.open
    def open_file(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError('File is being copied')
        return original(path, *args, **kwargs)
    with patch.object(Path, 'open', open_file), patch('services.document_processing.extract_invoice', return_value={}):
        outcome = run(store)
    assert outcome.emails_processed == 1
    assert any('blocked.msg' in warning for warning in outcome.warnings)


def test_restart_skips_old_mail_but_changed_mail_is_new_version(store):
    source = mail(store)
    with patch('services.document_processing.extract_invoice', return_value={}) as ai:
        run(store)
        assert run(LocalStorage(store.root)).emails_already_processed == 1
        source.write_bytes(msg_bytes([{'name': 'changed.pdf', 'data': b'changed'}]))
        assert run(LocalStorage(store.root)).emails_processed == 1
    assert ai.call_count == 2
    assert store.count() == (2, 2)


def test_locked_csv_keeps_data_and_refresh_recovers_without_reprocessing(store):
    mail(store)
    def locked(path, data):
        if path.name == 'Invoice_Data.csv':
            raise PermissionError('CSV open in Excel')
        return _atomic_write(path, data)
    with patch('services.document_processing.extract_invoice', return_value={}) as ai:
        with patch('services.local_storage._atomic_write', side_effect=locked):
            outcome = run(store)
        assert outcome.warnings
        assert store.count() == (1, 1)
        assert store.refresh_exports() == []
        assert run(store).emails_already_processed == 1
    assert ai.call_count == 1
    assert len(list(csv.DictReader(io.StringIO(store.report_files()['Invoice_Data.csv'].decode('utf-8-sig'))))) == 1


def test_save_failure_can_be_retried_without_losing_original(store):
    source = mail(store)
    with patch('services.document_processing.extract_invoice', return_value={}):
        with patch.object(store, 'save_batch', side_effect=OSError('Disk full')):
            with pytest.raises(OSError, match='Disk full'):
                run(store)
        assert store.count() == (0, 0)
        assert source.is_file()
        assert run(store).emails_processed == 1
    assert store.count() == (1, 1)


def test_reset_removes_intake_and_ledger(store):
    mail(store)
    with patch('services.document_processing.extract_invoice', return_value={}):
        run(store)
        store.reset(str(store.root))
        fresh = LocalStorage(store.root)
        assert fresh.count() == (0, 0)
        mail(fresh)
        assert run(fresh).emails_processed == 1


def test_bad_folder_configuration_returns_diagnostic_not_server_error(store, monkeypatch):
    (store.root / 'Outlook').write_text('not a folder')
    monkeypatch.setattr(web_app, 'settings', replace(settings, local_storage_dir=str(store.root)))
    response = TestClient(web_app.app).get('/api/config')
    assert response.status_code == 200
    assert response.json()['storage_ready'] is False
    assert response.json()['warnings']


@pytest.mark.parametrize('payload', [
    {'tolerance': -1, 'threshold': 95}, {'tolerance': .02, 'threshold': 94},
    {'tolerance': .02, 'threshold': 101}, {},
])
def test_invalid_run_settings_rejected(payload):
    assert TestClient(web_app.app).post('/api/outlook/run', json=payload).status_code == 422
