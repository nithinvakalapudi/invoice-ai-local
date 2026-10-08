"""Retrain the user's own OCR-line classifier from verified invoice corrections.

This is supervised extraction, not a general-purpose language model. No model
weights or invoice text are sent to an external service.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from threading import RLock

import joblib

from training.local_pilot import FIELDS, _fit_field

_TRAINING_LOCK = RLock()
FIELD_MAP = {
    'contact_email': 'customer_email',
    'account_number': 'bank_account_number',
}


def retrain_from_reviews(examples: list[tuple[str, dict]], model_path: Path) -> dict:
    """Fit fresh classifiers to reviewed OCR/label pairs and replace atomically."""
    prepared = []
    for text, invoice in examples:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        row = {field: str(invoice.get(FIELD_MAP.get(field, field)) or '') for field in FIELDS}
        prepared.append((row, lines))
    fitted = {}
    positive_lines = {}
    for field in FIELDS:
        model, positives = _fit_field(prepared, field) if prepared else (None, 0)
        if model is not None:
            fitted[field] = model
            positive_lines[field] = positives
    report = {'reviewed_invoices': len(prepared), 'trained_fields': sorted(fitted),
              'positive_lines': positive_lines}
    with _TRAINING_LOCK:
        model_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.training-', suffix='.joblib',
                                                 dir=model_path.parent)
        os.close(descriptor)
        try:
            joblib.dump({'version': 1, 'models': fitted, 'report': report}, temporary)
            os.replace(temporary, model_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return report
