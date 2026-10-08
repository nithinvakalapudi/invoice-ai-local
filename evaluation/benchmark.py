"""Reproducible public-invoice benchmark for a validated local extractor."""
from __future__ import annotations

import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import random
import re
import sqlite3
import time

import requests
from PIL import Image

from config.settings import settings
from services.local_extraction_service import extraction_ready
from services.document_processing import process_document
from utils.date_utils import normalize_date
from utils.file_utils import DocumentInput
from evaluation.reporting import status_counts, save_reports


REPO = 'Voxel51/high-quality-invoice-images-for-ocr'
REVISION = 'd21f03cfeea2b330e15a229883c66d7ebece8e69'
BASE = f'https://huggingface.co/datasets/{REPO}/resolve/{REVISION}'
ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'evaluation_data'
SEED = 20261005


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str), encoding='utf-8')
    temporary.replace(path)


def download(url):
    response = requests.get(url, timeout=(15, 60))
    response.raise_for_status()
    return response.content


def select_samples(samples):
    candidates, seen = [], set()
    for row in samples:
        if not row.get('json_annotation'):
            continue
        annotation = json.loads(row['json_annotation'])
        number = annotation.get('invoice', {}).get('invoice_number')
        if not number or number in seen:
            continue
        seen.add(number)
        name = Path(row['filepath']).name
        if not re.fullmatch(r'batch1-[\w-]+\.jpg', name):
            raise ValueError('Unexpected dataset filename')
        candidates.append({'id': name, 'source_path': 'data/' + name, 'annotation': annotation})
    rng = random.Random(SEED)
    positives = [s for s in candidates if s['annotation'].get('payment_instructions', {}).get('account_number')]
    others = [s for s in candidates if s not in positives]
    rng.shuffle(positives)
    rng.shuffle(others)
    chosen = positives + others[:1000 - len(positives)]
    if len(chosen) != 1000:
        raise ValueError('Dataset cannot supply exactly 1000 unique invoice numbers')
    # Stratify development/test by availability of the account-number label.
    dev_ids = {s['id'] for s in positives[:34] + others[:66]}
    rng.shuffle(chosen)
    for sample in chosen:
        sample['split'] = 'development' if sample['id'] in dev_ids else 'test'
    return chosen, len(candidates), len(positives)


def prepare(limit=None):
    DATA.mkdir(parents=True, exist_ok=True)
    metadata_path = DATA / 'source_samples.json'
    if not metadata_path.exists():
        metadata_path.write_bytes(download(BASE + '/samples.json'))
    if not (DATA / 'SOURCE_README.md').exists():
        (DATA / 'SOURCE_README.md').write_bytes(download(BASE + '/README.md'))
    samples, unique, account_count = select_samples(json.loads(metadata_path.read_text(encoding='utf-8'))['samples'])
    # Download development first, so a small paid pilot needs no test images.
    ordered = sorted(samples, key=lambda s: (s['split'] != 'development', not bool(s['annotation'].get('payment_instructions', {}).get('account_number')), s['id']))
    selected = ordered[:limit] if limit else ordered
    directory = DATA / 'images'
    directory.mkdir(exist_ok=True)

    def fetch(sample):
        target = directory / sample['id']
        if not target.exists():
            content = download(BASE + '/' + sample['source_path'])
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
            target.write_bytes(content)
        sample['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
        return sample

    with ThreadPoolExecutor(max_workers=6) as pool:
        for index, sample in enumerate(pool.map(fetch, selected), 1):
            if index % 100 == 0 or index == len(selected):
                print(f'Downloaded/verified {index}/{len(selected)} images', flush=True)
    downloaded = {s['id']: s for s in selected}
    for sample in samples:
        if sample['id'] in downloaded:
            sample.update(downloaded[sample['id']])
    hashes = [s['sha256'] for s in samples if 'sha256' in s]
    if len(hashes) != len(set(hashes)):
        raise ValueError('Duplicate image bytes detected; resolve before benchmarking')
    manifest = {'dataset': REPO, 'revision': REVISION, 'seed': SEED, 'license': 'ODbL (dataset card)',
                'source_url': f'https://huggingface.co/datasets/{REPO}',
                'unique_annotated_candidates': unique, 'available_account_labels': account_count,
                'selected': len(samples), 'development': sum(s['split'] == 'development' for s in samples),
                'test': sum(s['split'] == 'test' for s in samples), 'downloaded': len(selected), 'samples': ordered}
    write_json(DATA / 'manifest.json', manifest)
    print(json.dumps({k: v for k, v in manifest.items() if k != 'samples'}), flush=True)


def truth(annotation):
    invoice = annotation.get('invoice', {})
    mapping = {'invoice_number': 'invoice_number', 'invoice_date': 'invoice_date',
               'seller_name': 'vendor_name', 'seller_address': 'vendor_address',
               'client_name': 'customer_name', 'client_address': 'customer_address', 'due_date': 'due_date'}
    expected = {target: invoice[source] for source, target in mapping.items() if invoice.get(source)}
    payments = annotation.get('payment_instructions', {})
    if payments.get('account_number'):
        expected['bank_account_number'] = payments['account_number']
    for source, target in [('total', 'total_amount'), ('tax', 'tax_amount'), ('discount', 'discount')]:
        value = annotation.get('subtotal', {}).get(source)
        if value not in (None, ''):
            expected[target] = value
    return expected


def normalized(field, value):
    if value is None or str(value).strip() == '':
        return None
    text = str(value).strip()
    if field in {'total_amount', 'tax_amount', 'discount', 'quantity', 'line_total'}:
        text = text.replace(' ', '')
        if ',' in text and '.' in text:
            text = text.replace(',', '') if text.rfind('.') > text.rfind(',') else text.replace('.', '').replace(',', '.')
        elif ',' in text:
            text = text.replace(',', '.')
        try:
            return Decimal(text)
        except InvalidOperation:
            return text
    if field in {'invoice_date', 'due_date'}:
        return normalize_date(text) or text
    if field == 'bank_account_number':
        return re.sub(r'[\s-]', '', text).upper()
    return ' '.join(text.split()).casefold()


def compare(sample, result):
    prediction = result.invoice.to_dict()
    scored = []
    for field, expected in truth(sample['annotation']).items():
        actual = prediction.get(field)
        scored.append({'field': field, 'expected': expected, 'actual': actual,
                       'correct': normalized(field, expected) == normalized(field, actual)})
    expected_lines = sample['annotation'].get('items', [])
    scored.append({'field': 'line_count', 'expected': len(expected_lines), 'actual': len(result.line_items),
                   'correct': len(expected_lines) == len(result.line_items)})
    for index, line in enumerate(expected_lines):
        actual_line = result.line_items[index].to_dict() if index < len(result.line_items) else {}
        for source, target in [('description', 'description'), ('quantity', 'quantity'), ('total_price', 'line_total')]:
            if line.get(source) not in (None, ''):
                expected, actual = line[source], actual_line.get(target)
                scored.append({'field': 'line_' + target, 'index': index, 'expected': expected, 'actual': actual,
                               'correct': normalized(target, expected) == normalized(target, actual)})
    return scored


def fingerprint():
    digest = hashlib.sha256()
    for name in ['services/local_extraction_service.py', 'services/document_processing.py',
                 'services/validation_service.py', 'models/invoice_schema.py', 'evaluation/benchmark.py']:
        digest.update((ROOT / name).read_bytes())
    digest.update(f'local:{settings.minimum_field_confidence}:{settings.default_tolerance}'.encode())
    return digest.hexdigest()[:16]


def evaluate(split, limit, pause, retry_failed=False, workers=1):
    if not extraction_ready():
        raise RuntimeError('Automatic local extraction is paused; use training.local_pilot for offline evaluation.')
    manifest = json.loads((DATA / 'manifest.json').read_text(encoding='utf-8'))
    samples = [s for s in manifest['samples'] if s['split'] == split]
    selected = samples[:limit] if limit else samples
    run_id = fingerprint()
    folder = DATA / 'runs' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    write_json(folder / 'run_metadata.json', {'run_id': run_id, 'model': 'local',
               'dataset': REPO, 'dataset_revision': REVISION, 'selected_invoices': 1000,
               'development_target': 100, 'test_target': 900,
               'minimum_field_confidence': settings.minimum_field_confidence})
    connection = sqlite3.connect(folder / 'predictions.sqlite3')
    connection.execute('CREATE TABLE IF NOT EXISTS predictions (id TEXT PRIMARY KEY, split TEXT, payload TEXT)')
    stop_reason = None
    consecutive_failures = 0
    def extract(sample):
        path = DATA / 'images' / sample['id']
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != sample.get('sha256'):
            raise ValueError('Image checksum missing or changed; run prepare first')
        started = time.monotonic()
        result = process_document(DocumentInput(sample['id'], content), settings.default_tolerance,
                                  settings.minimum_field_confidence)
        return {'id': sample['id'], 'split': split, 'image_sha256': sample['sha256'],
                'seconds': round(time.monotonic() - started, 3), 'result': asdict(result),
                'scores': compare(sample, result)}

    try:
        remaining = []
        for sample in selected:
            previous = connection.execute('SELECT payload FROM predictions WHERE id=?', (sample['id'],)).fetchone()
            if previous and (not retry_failed or json.loads(previous[0])['result']['processing_status'] != 'FAILED'):
                continue
            remaining.append(sample)
        queue = iter(remaining)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = set()
            exhausted = False
            while pending or not exhausted:
                while not exhausted and not stop_reason and len(pending) < workers:
                    sample = next(queue, None)
                    if sample is None:
                        exhausted = True
                        break
                    pending.add(pool.submit(extract, sample))
                    if pause:
                        time.sleep(pause)
                if not pending:
                    break
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    record = future.result()
                    result = record['result']
                    connection.execute('INSERT OR REPLACE INTO predictions VALUES (?, ?, ?)',
                                       (record['id'], split, json.dumps(record, default=str)))
                    connection.commit()
                    print(f"{record['id']}: {result['processing_status']}; {record['seconds']}s", flush=True)
                    if result['processing_status'] == 'FAILED':
                        consecutive_failures += 1
                        reason = ' | '.join(result['errors'])
                        if any(word in reason.lower() for word in ['quota', 'api key', 'billing', 'temporarily unavailable']) or consecutive_failures >= 3:
                            stop_reason = reason
                            print('Stopped scheduling extraction: ' + reason, flush=True)
                    else:
                        consecutive_failures = 0
                # Already-running work finishes and is saved; no new work is scheduled.
                if stop_reason:
                    exhausted = True
    finally:
        records = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM predictions WHERE split=?', (split,))]
        counters = defaultdict(lambda: {'correct': 0, 'evaluated': 0})
        for record in records:
            for score in record['scores']:
                counters[score['field']]['evaluated'] += 1
                counters[score['field']]['correct'] += int(score['correct'])
        for counts in counters.values():
            counts['accuracy_percent'] = round(100 * counts['correct'] / counts['evaluated'], 2)
        summary = {'run_id': run_id, 'model': 'local', 'split': split,
                   'utc': datetime.now(timezone.utc).isoformat(), 'target_for_split': len(samples),
                   'attempted': len(records), 'successful_extractions': sum(r['result']['processing_status'] != 'FAILED' for r in records),
                   'stop_reason': stop_reason, 'fields': dict(counters),
                   'status_counts': status_counts(records),
                   'complete': len(records) == len(samples) and all(r['result']['processing_status'] != 'FAILED' for r in records),
                   'limitations': ['Synthetic English invoices; mostly one layout family.',
                                   'Blank/missing annotations are unscored, not assumed absent.',
                                   'Publisher labels have not all been manually verified.',
                                   'API failures count as extraction misses; confidence is not accuracy.',
                                   'No model weight training; development split is for pipeline tuning only.']}
        write_json(folder / f'{split}_summary.json', summary)
        write_json(folder / f'{split}_details.json', records)
        print(json.dumps(summary, indent=2), flush=True)
        connection.close()
        save_reports(folder)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'evaluate'])
    parser.add_argument('--limit', type=int)
    parser.add_argument('--split', choices=['development', 'test'], default='development')
    parser.add_argument('--pause', type=float, default=1)
    parser.add_argument('--retry-failed', action='store_true')
    parser.add_argument('--workers', type=int, default=1, choices=range(1, 9))
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= 1000:
        parser.error('--limit must be between 1 and 1000')
    if args.pause < 0:
        parser.error('--pause cannot be negative')
    if args.action == 'prepare':
        prepare(args.limit)
    else:
        evaluate(args.split, args.limit, args.pause, args.retry_failed, args.workers)


if __name__ == '__main__':
    main()
