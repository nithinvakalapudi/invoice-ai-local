"""Check the deployed local extractor against a separate labeled invoice ZIP."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from services.local_extraction_service import extract_invoice
from training.local_pilot import _exact_match, load_examples

FIELDS = ('vendor_name', 'invoice_number', 'invoice_date', 'currency',
          'account_number', 'subtotal', 'tax_amount', 'total_amount')


def verify(archive: Path) -> dict:
    rows, documents = load_examples(archive)
    fields = tuple(field for field in FIELDS if field in rows[0]
                   and all(row.get(field, '').strip() for row in rows))
    scores = Counter()
    failures: list[dict] = []
    for row in rows:
        name = row['source_file']
        output = extract_invoice(documents[name], name)['invoice']
        missing = []
        for field in fields:
            key = 'bank_account_number' if field == 'account_number' else field
            if _exact_match(row[field], output.get(key), field):
                scores[field] += 1
            else:
                missing.append(field)
        if not missing:
            scores['all_fields'] += 1
        elif len(failures) < 10:
            failures.append({'source_file': name, 'incorrect_fields': missing})
    return {'tested_invoices': len(rows), 'tested_fields': fields,
            'all_fields_exact': scores['all_fields'],
            'field_exact': {field: scores[field] for field in fields},
            'sample_failures': failures}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.archive), indent=2))


if __name__ == '__main__':
    main()
