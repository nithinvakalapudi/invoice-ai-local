"""Optional end-to-end check with the user's separate labeled invoice archive."""
import csv
from pathlib import Path
from uuid import uuid4

import pytest

from services.document_processing import process_document
from services.local_storage import LocalStorage
from services.mdm_csv_service import COLUMNS
from training.local_pilot import load_examples
from utils.file_utils import DocumentInput


ARCHIVE = Path.home() / 'Downloads' / 'invoices_training_corrected.zip'


@pytest.mark.skipif(not ARCHIVE.is_file(), reason='Separate invoice archive is not available')
def test_ten_pdf_invoices_append_to_one_local_csv_and_reset(tmp_path):
    labels, documents = load_examples(ARCHIVE)
    selected = labels[:10]
    assert len(selected) == 10
    store = LocalStorage(tmp_path / 'local_data')
    for group in (selected[:5], selected[5:]):
        results = [process_document(DocumentInput(row['source_file'], documents[row['source_file']]),
                                    .02, 95) for row in group]
        assert all(result.processing_status != 'FAILED' for result in results)
        store.save_batch(uuid4().hex, results)
    report = store.root / 'MDM' / 'Invoice_data.csv'
    with report.open(encoding='utf-8-sig', newline='') as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 10
    assert list(rows[0]) == COLUMNS
    assert all(row['Invoice Number'] for row in rows)
    assert all('Verified accuracy (%)' not in row for row in rows)
    assert store.count() == (2, 10)
    outcome = store.reset(str(store.root))
    assert outcome['cleared_records'] == 10
    with report.open(encoding='utf-8-sig', newline='') as stream:
        assert list(csv.DictReader(stream)) == []
