"""Train and evaluate an offline line selector against corrected invoice labels.

This is an evidence-gathering pilot, not a replacement for full-field extraction.
The held-out invoices are never used to fit the vectorizer or classifier.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import tempfile
import zipfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

import joblib
import pymupdf
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression

from services.ocr_service import extract_document_text
from utils.date_utils import normalize_date

FIELDS = (
    'vendor_name', 'customer_name', 'invoice_number', 'invoice_date',
    'currency', 'contact_email', 'account_number', 'subtotal',
    'tax_amount', 'total_amount',
)
MONEY_FIELDS = {'subtotal', 'tax_amount', 'total_amount'}
MAX_ARCHIVE_BYTES = 100_000_000
TEST_INDICES = {3, 8}  # The original 10-invoice pilot's hold-out.


def _canonical(value: str, field: str) -> str:
    value = value.casefold().strip()
    if field in MONEY_FIELDS:
        value = value.replace(',', '')
        return re.sub(r'[^0-9.]', '', value)
    return re.sub(r'[^a-z0-9]', '', value)


def _gold_in_line(value: str, line: str, field: str) -> bool:
    if field in MONEY_FIELDS:
        try:
            expected = Decimal(value.replace(',', ''))
        except InvalidOperation:
            return False
        return any(Decimal(found.replace(',', '')) == expected
                   for found in re.findall(r'(?<!\d)\d[\d,]*\.\d{2}(?!\d)', line))
    target = _canonical(value, field)
    return bool(target) and target in _canonical(line, field)


def _safe_archive_members(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
        raise ValueError('Training archive is too large')
    for item in members:
        name = item.filename.replace('\\', '/')
        path = PurePosixPath(name)
        if name.startswith('/') or '..' in path.parts or item.flag_bits & 1:
            raise ValueError('Training archive contains an unsafe or encrypted entry')


def load_examples(archive_path: Path) -> tuple[list[dict], dict[str, bytes]]:
    with zipfile.ZipFile(archive_path) as archive:
        _safe_archive_members(archive)
        names = set(archive.namelist())
        sheet = 'ground_truth_answer_sheet.csv'
        if sheet not in names:
            raise ValueError('Training archive has no ground_truth_answer_sheet.csv')
        rows = list(csv.DictReader(io.StringIO(archive.read(sheet).decode('utf-8-sig'))))
        if not rows or 'source_file' not in rows[0] or not set(FIELDS).intersection(rows[0]):
            raise ValueError('Answer sheet does not match the required training fields')
        sources = [row['source_file'] for row in rows]
        if len(sources) != len(set(sources)) or any(not name.lower().endswith('.pdf') for name in sources):
            raise ValueError('Answer sheet PDF filenames are missing or duplicated')
        documents = {}
        for name in sources:
            matches = [candidate for candidate in (name, f'pdf_invoices/{name}', f'invoices/{name}') if candidate in names]
            if len(matches) != 1:
                raise ValueError(f'Answer sheet PDF is missing or ambiguous: {name}')
            documents[name] = archive.read(matches[0])
        for name in archive.namelist():
            if name.startswith('scanned_representatives/') and name.lower().endswith(('.pdf', '.jpg', '.jpeg')):
                documents[name] = archive.read(name)
    return rows, documents


def _lines(pdf_bytes: bytes, filename: str) -> list[str]:
    if filename.lower().endswith('.pdf'):
        with pymupdf.open(stream=pdf_bytes, filetype='pdf') as document:
            native = '\n'.join(page.get_text('text') for page in document).strip()
        text = native if len(native) >= 40 else extract_document_text(filename, pdf_bytes)
    else:
        text = extract_document_text(filename, pdf_bytes)
    return [line.strip() for line in text.splitlines() if line.strip()]


def _shape(value: str) -> str:
    value = re.sub(r'[A-Za-z]+', 'A', value)
    value = re.sub(r'\d+', '0', value)
    return value[:40]


def _tokens(value: str) -> set[str]:
    value = re.sub(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+', ' EMAIL ', value)
    value = re.sub(r'\d+', ' NUMBER ', value)
    return set(re.findall(r'[a-z]{2,}', value.casefold()))


def _features(lines: list[str], index: int) -> dict[str, object]:
    line = lines[index]
    result: dict[str, object] = {
        'position': min(9, index * 10 // max(len(lines), 1)),
        'length': min(9, len(line) // 10),
        'has_date': bool(re.search(r'\d{1,4}[/.-]\d{1,2}[/.-]\d{2,4}', line)),
        'has_email': '@' in line,
        'has_money': bool(re.search(r'\d[\d,]*\.\d\d', line)),
        'has_colon': ':' in line,
        'shape=' + _shape(line): True,
    }
    for offset, prefix in ((-2, 'prev2'), (-1, 'prev'), (0, 'this'), (1, 'next')):
        neighbor = index + offset
        if 0 <= neighbor < len(lines):
            for token in _tokens(lines[neighbor]):
                result[f'{prefix}:{token}'] = True
    return result


def _fit_field(examples: list[tuple[dict, list[str]]], field: str):
    vectors, labels = [], []
    for row, lines in examples:
        for index, line in enumerate(lines):
            vectors.append(_features(lines, index))
            labels.append(int(_gold_in_line(row[field], line, field)))
    if len(set(labels)) != 2:
        return None, sum(labels)
    vectorizer = DictVectorizer(sparse=True)
    transformed = vectorizer.fit_transform(vectors)
    model = LogisticRegression(class_weight='balanced', max_iter=500, random_state=0)
    model.fit(transformed, labels)
    return (vectorizer, model), sum(labels)


def _exact_match(expected: str, actual: object, field: str) -> bool:
    if actual in (None, ''):
        return False
    if field in MONEY_FIELDS:
        try:
            return Decimal(str(expected).replace(',', '')) == Decimal(str(actual).replace(',', ''))
        except InvalidOperation:
            return False
    if field == 'invoice_date':
        return normalize_date(expected) == normalize_date(str(actual))
    return _canonical(expected, field) == _canonical(str(actual), field)


def _evaluate_extraction(test: list[tuple[dict, list[str]]], fitted: dict) -> dict:
    """Run the actual local extractor on the holdout before fitting the final model."""
    from services import local_extraction_service as extraction
    target_fields = ('vendor_name', 'invoice_number', 'invoice_date', 'currency',
                     'account_number', 'subtotal', 'tax_amount', 'total_amount')
    target_fields = tuple(field for field in target_fields if field in fitted)
    original_pilot = extraction._pilot
    original_reviewed = extraction._reviewed_model
    original_ocr = extraction.extract_document_text
    scores = Counter()
    try:
        extraction._pilot = lambda: (fitted, _features)
        extraction._reviewed_model = lambda: None
        for row, lines in test:
            extraction.extract_document_text = lambda *_: '\n'.join(lines)
            output = extraction.extract_invoice(b'held-out-pdf', row['source_file'])['invoice']
            complete = True
            for field in target_fields:
                key = 'bank_account_number' if field == 'account_number' else field
                correct = _exact_match(row[field], output.get(key), field)
                scores[field] += int(correct)
                complete = complete and correct
            scores['all_fields'] += int(complete)
    finally:
        extraction._pilot = original_pilot
        extraction._reviewed_model = original_reviewed
        extraction.extract_document_text = original_ocr
    return {'tested_invoices': len(test),
            'all_requested_fields_exact': scores['all_fields'],
            'field_exact': {field: scores[field] for field in target_fields}}


def evaluate(archive_path: Path, model_path: Path | None = None) -> dict:
    rows, documents = load_examples(archive_path)
    fields = tuple(field for field in FIELDS if field in rows[0] and
                   all(row.get(field, '').strip() for row in rows))
    if not fields:
        raise ValueError('Answer sheet has no consistently labeled model fields')
    examples = [(row, _lines(documents[row['source_file']], row['source_file'])) for row in rows]
    holdout = TEST_INDICES if len(examples) <= 10 else {
        index for index in range(1, len(examples) + 1) if index % 5 == 3}
    train = [item for index, item in enumerate(examples, 1) if index not in holdout]
    test = [item for index, item in enumerate(examples, 1) if index in holdout]
    if not train or not test:
        raise ValueError('Training archive needs at least nine PDF/answer-sheet pairs')
    field_scores = {}
    fitted = {}
    source_coverage = {field: sum(any(_gold_in_line(row[field], line, field) for line in lines)
                                  for row, lines in examples) for field in fields}
    missing_from_pdf = {field: [row['source_file'] for row, lines in examples
                                if not any(_gold_in_line(row[field], line, field) for line in lines)]
                        for field in fields}
    for field in fields:
        fitted_model, positives = _fit_field(train, field)
        fitted[field] = fitted_model
        correct = 0
        for row, lines in test:
            if fitted_model is None:
                continue
            vectorizer, model = fitted_model
            probabilities = model.predict_proba(vectorizer.transform(
                [_features(lines, index) for index in range(len(lines))]))[:, 1]
            best_line = lines[int(probabilities.argmax())]
            correct += int(_gold_in_line(row[field], best_line, field))
        field_scores[field] = {'correct': correct, 'tested': len(test), 'training_positive_lines': positives}
    extraction_scores = _evaluate_extraction(test, fitted)
    scan_scores = Counter()
    for name, document in documents.items():
        if not name.startswith('scanned_representatives/'):
            continue
        matching = next((row for row in rows if row['invoice_number'] in name), None)
        if not matching:
            continue
        lines = _lines(document, name)
        for field in fields:
            scan_scores['tested'] += 1
            scan_scores['text_present'] += int(any(_gold_in_line(matching[field], line, field) for line in lines))
            fitted_model = fitted[field]
            if fitted_model is None:
                continue
            vectorizer, model = fitted_model
            probabilities = model.predict_proba(vectorizer.transform(
                [_features(lines, index) for index in range(len(lines))]))[:, 1]
            scan_scores['selected'] += int(_gold_in_line(matching[field], lines[int(probabilities.argmax())], field))
    if model_path is not None:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        all_models = {field: _fit_field(examples, field)[0] for field in fields}
        descriptor, temporary = tempfile.mkstemp(prefix='.pilot-', suffix='.joblib',
                                                 dir=model_path.parent)
        os.close(descriptor)
        try:
            joblib.dump({'version': 1, 'fields': fields, 'models': all_models}, temporary)
            os.replace(temporary, model_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return {
        'train_invoices': len(train), 'held_out_invoices': len(test),
        'trained_fields': fields,
        'evaluation': 'correct labeled value appears on the highest-scored text line; not exact field extraction',
        'source_text_coverage': source_coverage,
        'labels_not_visible_in_pdf_text': {field: {'count': len(names), 'samples': names[:5]}
                                           for field, names in missing_from_pdf.items() if names},
        'field_scores': field_scores,
        'held_out_extraction': extraction_scores,
        'overall_line_selection': {
            'correct': sum(score['correct'] for score in field_scores.values()),
            'tested': sum(score['tested'] for score in field_scores.values()),
        },
        'scanned_checks': dict(scan_scores),
        'model_saved': str(model_path) if model_path is not None else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path, help='ZIP containing PDFs and corrected answer sheet')
    parser.add_argument('--model-path', type=Path, help='Optional local path for a pilot model trained on all invoices')
    args = parser.parse_args()
    print(json.dumps(evaluate(args.archive, args.model_path), indent=2))


if __name__ == '__main__':
    main()
