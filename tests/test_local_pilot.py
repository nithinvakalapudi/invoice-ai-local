"""Offline model pilot keeps money matching exact and rejects unsafe archives."""
import io
import zipfile

import pytest

from training.local_pilot import FIELDS, _gold_in_line, _safe_archive_members, load_examples


def test_money_match_is_not_a_substring():
    assert _gold_in_line('4500.00', 'TOTAL $4,500.00', 'total_amount')
    assert not _gold_in_line('4500.00', 'TOTAL $14,500.00', 'total_amount')


def test_training_zip_rejects_traversal():
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as archive:
        archive.writestr('../wrong.pdf', b'%PDF')
    with zipfile.ZipFile(io.BytesIO(data.getvalue())) as archive:
        with pytest.raises(ValueError, match='unsafe'):
            _safe_archive_members(archive)


def test_training_zip_accepts_pdf_invoices_folder(tmp_path):
    path = tmp_path / 'training.zip'
    header = 'source_file,' + ','.join(FIELDS) + '\n'
    row = 'invoice_001.pdf,' + ','.join('value' for _ in FIELDS) + '\n'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('ground_truth_answer_sheet.csv', header + row)
        archive.writestr('pdf_invoices/invoice_001.pdf', b'%PDF-fixture')
    rows, documents = load_examples(path)
    assert len(rows) == 1
    assert documents['invoice_001.pdf'] == b'%PDF-fixture'


def test_training_zip_accepts_invoices_folder_without_email_label(tmp_path):
    path = tmp_path / 'training.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('ground_truth_answer_sheet.csv',
                         'source_file,invoice_number,vendor_name\ninvoice_001.pdf,INV-001,Test Vendor\n')
        archive.writestr('invoices/invoice_001.pdf', b'%PDF-fixture')
    rows, documents = load_examples(path)
    assert rows[0]['invoice_number'] == 'INV-001'
    assert documents['invoice_001.pdf'] == b'%PDF-fixture'
