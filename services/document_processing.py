"""UI-independent processing shared by uploads and extracted MSG files."""
from datetime import datetime, timezone

from config.settings import settings
from models.invoice_schema import InvoiceResult
from services.local_extraction_service import extract_invoice
from services.validation_service import validate_invoice
from utils.file_utils import DocumentInput


def process_document(document: DocumentInput, tolerance: float, threshold: float) -> InvoiceResult:
    timestamp = datetime.now(timezone.utc).isoformat()
    if document.intake_status:
        result = InvoiceResult.failed(document.filename, document.intake_message, timestamp)
        if document.intake_status == 'SKIPPED':
            result.processing_status = result.validation_status = 'SKIPPED'
            result.review_required = False
            result.warnings, result.errors = result.errors, []
    else:
        try:
            # Only attachment bytes and its filename cross the extraction boundary.
            payload = extract_invoice(document.data, document.filename)
            result = InvoiceResult.from_ai_payload(payload, document.filename, timestamp,
                                                   getattr(settings, 'minimum_field_confidence', 95.0))
            validate_invoice(result, tolerance, threshold)
            if payload.get('_local_draft'):
                # Only a separately calibrated field model could support an
                # automatic pass. The current OCR/line selector supplies no
                # calibrated scores, so its drafts always require review.
                confidence_limit = max(95.0, threshold)
                populated = [key for key, value in result.invoice.to_dict().items()
                             if value not in (None, '')]
                auto_valid = (payload.get('_confidence_calibrated') is True
                              and result.extraction_confidence > confidence_limit
                              and not result.errors and not result.line_items
                              and not payload.get('_derived_fields')
                              and all(result.field_confidence.get(key, 0) > confidence_limit
                                      for key in populated))
                if auto_valid:
                    result.processing_status = 'PROCESSED'
                    result.validation_status = 'VALID'
                    result.review_required = False
                else:
                    result.processing_status = 'LOCAL_DRAFT'
                    result.validation_status = 'REVIEW_REQUIRED'
                    result.review_required = True
                    result.warnings = [warning for warning in result.warnings
                                       if not warning.startswith('Low-confidence extraction')]
                    if 'subtotal' in payload.get('_derived_fields', []):
                        result.warnings.append('Net amount was calculated as total minus tax; verify discounts and other charges.')
            elif payload.get('_ocr_only'):
                result.processing_status = 'OCR_ONLY'
                result.validation_status = 'REVIEW_REQUIRED'
                result.review_required = True
                result.warnings = [warning for warning in result.warnings
                                   if not warning.startswith('Low-confidence extraction')]
                result.warnings.append('OCR completed; local field extraction is not validated. Review the source and enter invoice fields manually.')
        except Exception as exc:
            result = InvoiceResult.failed(document.filename, str(exc), timestamp)
    result.source_email = document.source_email
    result.source_attachment = document.source_attachment
    result.attachment_index = document.attachment_index
    return result
