"""Read-only evaluation reports, kept separate from production invoice data."""
from collections import Counter
from contextlib import closing
import csv
import io
import json
from pathlib import Path
import sqlite3

from services.local_storage import _excel_bytes


def load_run(folder: Path):
    folder = folder.resolve()
    database = folder / 'predictions.sqlite3'
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        records = [json.loads(row[0]) for row in connection.execute('SELECT payload FROM predictions ORDER BY split, id')]
    metadata_path = folder / 'run_metadata.json'
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    else:
        summaries = sorted(folder.glob('*_summary.json'))
        metadata = json.loads(summaries[0].read_text(encoding='utf-8')) if summaries else {}
    return metadata, records


def status_counts(records):
    counts = dict.fromkeys(['VALID', 'REVIEW_REQUIRED', 'FAILED', 'SKIPPED'], 0)
    for record in records:
        status = record['result']['validation_status']
        counts[status] = counts.get(status, 0) + 1
    return counts


def invoice_rows(records):
    rows = []
    for record in records:
        result = record['result']
        scores = record.get('scores', [])
        comments = list(dict.fromkeys(result.get('errors', []) + result.get('warnings', [])))
        rows.append({'source_file': record['id'], 'split': record['split'],
                     'validation_status': result['validation_status'],
                     'processing_status': result['processing_status'],
                     'review_required': result['review_required'],
                     'review_comments': ' | '.join(comments),
                     'self_reported_confidence': result['extraction_confidence'],
                     'reference_fields_correct': sum(s['correct'] for s in scores),
                     'reference_fields_evaluated': len(scores),
                     'reference_mismatches': ' | '.join(sorted({s['field'] for s in scores if not s['correct']})),
                     'seconds': record['seconds'], **result['invoice'],
                     'line_items': json.dumps(result['line_items'], ensure_ascii=False)})
    return rows


def csv_bytes(rows, columns=None):
    output = io.StringIO(newline='')
    headers = columns or list(dict.fromkeys(key for row in rows for key in row))
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')


def export_run(folder: Path):
    metadata, records = load_run(folder)
    counts = status_counts(records)
    outcomes = [{'status': name, 'count': count} for name, count in counts.items()]
    comparisons = [{'source_file': record['id'], 'split': record['split'], **score}
                   for record in records for score in record.get('scores', [])]
    comments = Counter(comment for record in records
                       for comment in record['result'].get('errors', []) + record['result'].get('warnings', []))
    reports = {'Invoice_Results.csv': csv_bytes(invoice_rows(records)),
               'Field_Comparisons.csv': csv_bytes(comparisons),
               'Status_Summary.csv': csv_bytes(outcomes),
               'Review_Reasons.csv': csv_bytes([{'comment': comment, 'count': count} for comment, count in comments.most_common()], ['comment', 'count'])}
    manifest_path = folder.parent.parent / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        by_id = {record['id']: record for record in records}
        inventory = [{'source_file': sample['id'], 'split': sample['split'],
                      'status': by_id[sample['id']]['result']['validation_status'] if sample['id'] in by_id else 'NOT_TESTED',
                      'account_reference_available': bool(sample['annotation'].get('payment_instructions', {}).get('account_number'))}
                     for sample in manifest['samples']]
        reports['Dataset_Inventory.csv'] = csv_bytes(inventory)
    reports['Test_Results.xlsx'] = _excel_bytes(reports)
    reports['All_Results.json'] = json.dumps({'metadata': metadata, 'status_counts': counts, 'records': records},
                                            indent=2, ensure_ascii=False).encode()
    diagnostic = folder / 'provider_diagnostic.json'
    if diagnostic.exists():
        reports['Provider_Diagnostic.json'] = diagnostic.read_bytes()
    return metadata, records, reports


def save_reports(folder: Path):
    metadata, records, reports = export_run(folder)
    output = folder / 'reports'
    output.mkdir(exist_ok=True)
    for name, payload in reports.items():
        (output / name).write_bytes(payload)
    return metadata, records
