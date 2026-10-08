"""Measure exact field extraction on invoices excluded from model fitting."""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from pathlib import Path
from tempfile import TemporaryDirectory

import joblib

from services import local_extraction_service as extractor
from training.local_pilot import FIELDS, MONEY_FIELDS, TEST_INDICES, _fit_field, _lines, load_examples
from utils.date_utils import normalize_date

FIELD_MAP = {'contact_email': 'customer_email', 'account_number': 'bank_account_number'}


def _match(expected: str, actual: object, field: str) -> bool:
    if actual in (None, ''):
        return False
    if field == 'invoice_date':
        return normalize_date(expected) == normalize_date(str(actual))
    if field in MONEY_FIELDS:
        try:
            return Decimal(expected.replace(',', '')) == Decimal(str(actual).replace(',', ''))
        except InvalidOperation:
            return False
    return ''.join(char for char in expected.casefold() if char.isalnum()) == ''.join(
        char for char in str(actual).casefold() if char.isalnum())


def evaluate_exact(archive_path: Path) -> dict:
    rows, documents = load_examples(archive_path)
    holdout = TEST_INDICES if len(rows) <= 10 else {
        index for index in range(1, len(rows) + 1) if index % 5 == 3}
    train = [(row, _lines(documents[row['source_file']], row['source_file']))
             for index, row in enumerate(rows, 1) if index not in holdout]
    models = {field: _fit_field(train, field)[0] for field in FIELDS}
    scores = {field: {'correct': 0, 'tested': 0} for field in FIELDS}
    source_results = []
    with TemporaryDirectory(prefix='invoice-heldout-') as temporary:
        folder = Path(temporary) / 'models'
        folder.mkdir()
        joblib.dump({'version': 1, 'models': models}, folder / 'invoice_line_pilot.joblib')
        original_settings = extractor.settings
        extractor.settings = replace(original_settings, local_storage_dir=temporary)
        try:
            for index, row in enumerate(rows, 1):
                if index not in holdout:
                    continue
                payload = extractor.extract_invoice(documents[row['source_file']], row['source_file'])
                invoice = payload['invoice']
                correct = 0
                for field in FIELDS:
                    expected = row.get(field, '')
                    if not expected:
                        continue
                    matched = _match(expected, invoice.get(FIELD_MAP.get(field, field)), field)
                    scores[field]['tested'] += 1
                    scores[field]['correct'] += int(matched)
                    correct += int(matched)
                source_results.append({'source_file': row['source_file'], 'correct_fields': correct,
                                       'tested_fields': len(FIELDS)})
        finally:
            extractor.settings = original_settings
    return {'train_invoices': len(train), 'held_out_invoices': len(holdout),
            'field_scores': scores, 'exact_fields': {
                'correct': sum(value['correct'] for value in scores.values()),
                'tested': sum(value['tested'] for value in scores.values())},
            'source_results': source_results,
            'note': 'Same synthetic dataset/layout family; not real-company generalization.'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate_exact(args.archive), indent=2))


if __name__ == '__main__':
    main()
