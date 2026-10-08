"""The user's supervised Python extractor learns only from reviewed examples."""
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
import joblib
import pymupdf

import web_app
from services import local_extraction_service as local
from training.continual_model import retrain_from_reviews


def test_review_retrains_local_field_classifiers(tmp_path, monkeypatch):
    root = tmp_path / 'local_data'
    monkeypatch.setattr(web_app, 'settings', replace(web_app.settings, local_storage_dir=str(root)))
    monkeypatch.setattr(local, 'settings', replace(local.settings, local_storage_dir=str(root)))
    document = pymupdf.open()
    document.new_page().insert_text((70, 70),
        'INVOICE\nVendor: New Supplier LLC\nInvoice #: NEW-7\n'
        'Invoice Date: 10/01/2026\nCurrency: USD\nTotal Amount Due: 12.50\n'
        'Account Number: 00123')
    client = TestClient(web_app.app)
    upload = client.post('/api/process', files=[
        ('files', ('invoice.pdf', document.tobytes(), 'application/pdf'))])
    assert upload.status_code == 200, upload.text
    batch = upload.json()
    corrected = client.post(f"/api/batches/{batch['batch_id']}/review", json={
        'generation': batch['generation'], 'index': 0,
        'fields': {'vendor_name': 'New Supplier LLC', 'invoice_number': 'NEW-7',
                   'total_amount': '12.50', 'currency': 'USD',
                   'invoice_date': '10/01/2026', 'bank_account_number': '00123'},
        'tolerance': 0.02, 'threshold': 95,
    })
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()['training_example_saved'] is True
    assert corrected.json()['model_retrained'] is True
    assert client.get('/api/config').json()['reviewed_training_examples'] == 1
    artifact_path = root / 'models' / 'invoice_line_reviews.joblib'
    assert artifact_path.is_file()
    artifact = joblib.load(artifact_path)
    assert artifact['report']['reviewed_invoices'] == 1
    assert {'vendor_name', 'invoice_number', 'total_amount'} <= set(artifact['models'])
    assert local._reviewed_model() is not None


def test_unreadable_or_unlabeled_corrections_do_not_train_false_fields(tmp_path):
    report = retrain_from_reviews([
        ('INVOICE\nReference text\nNothing readable', {'vendor_name': 'Unseen Company'})],
        Path(tmp_path) / 'model.joblib')
    assert report['reviewed_invoices'] == 1
    assert report['trained_fields'] == []
