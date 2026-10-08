"""Local web API for the FLOWSTACK interface; the extraction services remain shared."""
from __future__ import annotations

import json
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.settings import settings
from evaluation.reporting import export_run, invoice_rows, load_run, status_counts
from models.invoice_schema import INVOICE_FIELDS, InvoiceResult
from services.csv_service import build_zip, generate_csvs
from services.document_processing import process_document
from services.duplicate_service import mark_duplicates
from services.local_storage import LocalStorage
from services.local_extraction_service import extraction_ready
from training.continual_model import retrain_from_reviews
from services.outlook_folder import run_outlook_folder
from services.mdm_csv_service import DISPLAY_COLUMNS as MDM_COLUMNS, LEGACY_DRAFT_WARNING, REPORT_NAME as MDM_REPORT_NAME, generate_mdm_csv, mdm_row
from services.tabular_intake import import_invoice_rows
from services.validation_service import REQUIRED_FIELDS, REQUIRED_FIELD_LABELS, validate_invoice
from utils.file_utils import expand_upload

app = FastAPI(title="Invoice Intelligence")
_reset_lock = Lock()
_reset_tokens: dict[str, tuple[str, str]] = {}
_root = Path(__file__).resolve().parent
_dist = _root / "frontend" / "dist"


def storage() -> LocalStorage:
    return LocalStorage(settings.local_storage_dir)


def _same_origin(request: Request) -> None:
    """Block browser cross-site writes to the local-only service."""
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
        raise HTTPException(403, "Cross-origin request refused")


def _batch(storage_instance: LocalStorage, batch_id: str) -> list[InvoiceResult]:
    try:
        storage_instance._batch_path(batch_id)
    except ValueError as exc:
        raise HTTPException(404, "Batch not found") from exc
    for known_id, results in storage_instance.load_batches():
        if known_id == batch_id:
            return results
    raise HTTPException(404, "Batch not found")


def _counts(results: list[InvoiceResult]) -> dict[str, int]:
    return {
        "total": len(results),
        # Count completed document extraction, including review-only drafts.
        # Approval is a separate decision; otherwise a fully processed batch
        # misleadingly displays "Processed 0".
        "processed": sum(r.processing_status in {"PROCESSED", "OCR_ONLY", "LOCAL_DRAFT"}
                         for r in results),
        "auto_approved": sum(r.validation_status == "VALID" and not r.review_required
                             for r in results),
        "ocr_only": sum(r.processing_status == "OCR_ONLY" for r in results),
        "local_draft": sum(r.processing_status == "LOCAL_DRAFT" for r in results),
        "imported": sum(r.processing_status == "IMPORTED" for r in results),
        "review": sum(r.review_required for r in results),
        "failed": sum(r.processing_status == "FAILED" for r in results),
        "duplicate": sum(r.duplicate_status == "POSSIBLE_DUPLICATE" for r in results),
    }


def _result_payload(result: InvoiceResult) -> dict:
    payload = asdict(result)
    payload['warnings'] = [warning for warning in payload['warnings']
                           if warning != LEGACY_DRAFT_WARNING]
    return {**payload, "mdm_row": mdm_row(result)}


@app.get("/api/config")
def config():
    try:
        s = storage()
        outlook_folder = str(s.outlook_folder)
        batches, records = s.count()
        warnings = s.export_warnings()
        generation = s.generation
        available = True
    except Exception as exc:
        batches = records = 0
        warnings = [f"Local storage unavailable: {exc}"]
        generation = ""
        available = False
        outlook_folder = ""
    return {
        "model": 'User-trained Python invoice extractor',
        "mdm_columns": MDM_COLUMNS,
        "required_fields": [REQUIRED_FIELD_LABELS[name] for name in REQUIRED_FIELDS],
        "api_ready": extraction_ready(),
        "intake_ready": available,
        "extraction_backend": settings.extraction_backend,
        "setup_message": ('' if extraction_ready() else
                          'No trained invoice model is available yet. OCR and printed labels create review-only drafts. Review every field; corrected invoices train your own Python extractor. No model download or remote request is made.'),
        "storage_ready": available,
        "generation": generation,
        "max_file_mb": settings.max_file_mb,
        "default_tolerance": settings.default_tolerance,
        "minimum_field_confidence": settings.minimum_field_confidence,
        "batches": batches,
        "records": records,
        "reviewed_training_examples": len(s.training_examples()) if available else 0,
        "warnings": warnings,
        "outlook_folder": outlook_folder,
    }


@app.get("/api/history")
def history():
    s = storage()
    batches = s.load_batches()
    return {
        "generation": s.generation,
        "batches": [
            {"id": batch_id, "counts": _counts(results), "sources": [r.source_file for r in results[:3]]}
            for batch_id, results in reversed(batches)
        ],
        "report_files": list(s.report_files()),
        "warnings": s.export_warnings(),
    }


@app.post("/api/history/refresh")
def refresh(request: Request):
    _same_origin(request)
    return {"warnings": storage().refresh_exports()}


class FolderRunSettings(BaseModel):
    tolerance: float = Field(ge=0, le=100)
    threshold: float = Field(ge=95, le=100)


@app.post("/api/outlook/run")
def outlook_run(payload: FolderRunSettings, request: Request):
    _same_origin(request)
    try:
        run = run_outlook_folder(storage(), payload.tolerance, payload.threshold,
                                 settings.max_file_mb * 1_000_000)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"folder": str(run.folder), "batch_id": run.batch_id,
            "emails_processed": run.emails_processed,
            "emails_already_processed": run.emails_already_processed,
            "ignored_files": run.ignored_files, "counts": _counts(run.results),
            "results": [_result_payload(result) for result in run.results], "warnings": run.warnings,
            "generation": storage().generation}


@app.post("/api/process")
async def process(
    request: Request,
    files: list[UploadFile] = File(...),
    tolerance: float = Form(settings.default_tolerance),
    threshold: float = Form(settings.minimum_field_confidence),
):
    _same_origin(request)
    if not 0 <= tolerance <= 100 or not 95 <= threshold <= 100:
        raise HTTPException(422, "Invalid processing settings")
    if len(files) > 100:
        raise HTTPException(413, "Select at most 100 source files per batch")
    max_bytes = settings.max_file_mb * 1_000_000
    uploads: list[tuple[str, bytes]] = []
    for item in files:
        data = await item.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise HTTPException(413, f"{item.filename} exceeds {settings.max_file_mb} MB")
        uploads.append((item.filename or "upload", data))
    if not uploads:
        raise HTTPException(422, "Select at least one file")
    s = storage()
    batch_id = uuid4().hex
    s.archive_uploads(batch_id, uploads)
    documents = []
    results = []
    for name, data in uploads:
        try:
            if Path(name).suffix.lower() in {'.csv', '.xlsx'}:
                results.extend(import_invoice_rows(name, data, tolerance, threshold))
            else:
                documents.extend(expand_upload(name, data, max_bytes))
        except ValueError as exc:
            failed = InvoiceResult.failed(name, str(exc), datetime.now(timezone.utc).isoformat())
            if Path(name).suffix.lower() in {'.msg', '.eml'}:
                failed.source_email = name
            results.append(failed)
    s.archive_documents(batch_id, documents)
    for document in documents:
        results.append(process_document(document, tolerance, threshold))
    prior_results = [prior for _, batch_results in s.load_batches() for prior in batch_results]
    results = mark_duplicates(results, prior_results)
    warnings = s.save_batch(batch_id, results)
    return {"batch_id": batch_id, "generation": s.generation, "counts": _counts(results),
            "results": [_result_payload(r) for r in results], "warnings": warnings}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    s = storage()
    results = _batch(s, batch_id)
    return {"batch_id": batch_id, "generation": s.generation, "counts": _counts(results),
            "results": [_result_payload(r) for r in results]}


class Correction(BaseModel):
    generation: str
    index: int = Field(ge=0)
    fields: dict[str, str | float | None]
    tolerance: float = Field(ge=0, le=100)
    threshold: float = Field(ge=95, le=100)


@app.post("/api/batches/{batch_id}/review")
def review(batch_id: str, correction: Correction, request: Request):
    _same_origin(request)
    s = storage()
    if correction.generation != s.generation:
        raise HTTPException(409, "Storage was reset; reload this page")
    results = _batch(s, batch_id)
    if correction.index >= len(results):
        raise HTTPException(404, "Invoice not found")
    result = results[correction.index]
    if result.processing_status == "FAILED":
        raise HTTPException(422, "Failed documents must be re-uploaded")
    if set(correction.fields) - set(INVOICE_FIELDS):
        raise HTTPException(422, "Unknown invoice field")
    for key, value in correction.fields.items():
        setattr(result.invoice, key, value if value not in ("", None) else None)
    validate_invoice(result, correction.tolerance, correction.threshold)
    # Saving this dialog is an explicit human verification of the visible fields.
    # It does not manufacture a model-confidence score for later invoices.
    if not result.errors:
        result.review_required = False
        result.validation_status = "VALID"
        result.warnings = [warning for warning in result.warnings
                           if not warning.startswith("Low-confidence extraction")]
    prior_results = [prior for known_id, batch_results in s.load_batches()
                     if known_id != batch_id for prior in batch_results]
    mark_duplicates(results, prior_results)
    warnings = s.save_batch(batch_id, results)
    remembered = False
    retrained = False
    if result.processing_status != 'IMPORTED':
        try:
            remembered = s.remember_review(batch_id, correction.index, result)
            if not remembered:
                warnings.append('Correction saved, but no unique source attachment was available for training memory.')
            else:
                report = retrain_from_reviews(
                    s.training_examples(), s.root / 'models' / 'invoice_line_reviews.joblib')
                retrained = bool(report['trained_fields'])
                if not retrained:
                    warnings.append('Correction saved, but its values were not readable in OCR; no field classifier could be trained.')
        except (OSError, ValueError, RuntimeError) as exc:
            warnings.append(f'Correction saved, but the local model could not be retrained: {exc}')
    return {"batch_id": batch_id, "generation": s.generation,
            "training_example_saved": remembered, "model_retrained": retrained,
            "counts": _counts(results),
            "results": [_result_payload(r) for r in results], "warnings": warnings}


@app.get("/api/download/batch/{batch_id}/{name:path}")
def batch_download(batch_id: str, name: str):
    results = _batch(storage(), batch_id)
    files = generate_csvs(results)
    files[MDM_REPORT_NAME] = generate_mdm_csv(results)
    if name == "invoice_results.zip":
        payload, media = build_zip(files), "application/zip"
    elif name in files:
        payload, media = files[name], "text/csv; charset=utf-8"
    else:
        raise HTTPException(404, "Report not found")
    return Response(payload, media_type=media, headers={"Content-Disposition": f'attachment; filename="{Path(name).name}"'})


@app.get("/api/download/history/{name:path}")
def history_download(name: str):
    store = storage()
    # Ageing is an as-of-today measure; refresh before each download so a
    # report saved yesterday cannot be handed out with yesterday's age.
    warnings = store.refresh_exports()
    if warnings:
        raise HTTPException(409, 'Reports could not be refreshed: ' + '; '.join(warnings))
    files = store.report_files()
    if name not in files:
        raise HTTPException(404, "Report not found")
    media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if name.endswith(".xlsx") else "text/csv; charset=utf-8"
    return Response(files[name], media_type=media, headers={"Content-Disposition": f'attachment; filename="{Path(name).name}"'})


@app.get("/api/tests")
def tests_list():
    root = _root / "evaluation_data" / "runs"
    runs = []
    for db in sorted(root.glob("*/predictions.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True):
        metadata, records = load_run(db.parent)
        runs.append({"id": db.parent.name, "model": metadata.get("model", "Unknown model"),
                     "attempted": len(records), "counts": status_counts(records),
                     "diagnostic": (db.parent / "provider_diagnostic.json").is_file()})
    return {"runs": runs}


@app.get("/api/tests/{run_id}")
def test_run(run_id: str, limit: int = 100, offset: int = 0):
    if not run_id.isalnum() or len(run_id) > 80:
        raise HTTPException(404, "Run not found")
    folder = _root / "evaluation_data" / "runs" / run_id
    if not (folder / "predictions.sqlite3").is_file():
        raise HTTPException(404, "Run not found")
    metadata, records = load_run(folder)
    rows = invoice_rows(records)
    return {"metadata": metadata, "counts": status_counts(records), "total": len(rows),
            "rows": rows[max(offset, 0):max(offset, 0) + min(max(limit, 1), 500)],
            "diagnostic": json.loads((folder / "provider_diagnostic.json").read_text(encoding="utf-8"))
            if (folder / "provider_diagnostic.json").is_file() else None}


@app.get("/api/download/tests/{run_id}/{name}")
def test_download(run_id: str, name: str):
    if not run_id.isalnum() or len(run_id) > 80:
        raise HTTPException(404, "Run not found")
    folder = _root / "evaluation_data" / "runs" / run_id
    if not (folder / "predictions.sqlite3").is_file():
        raise HTTPException(404, "Run not found")
    _, _, reports = export_run(folder)
    if name == "Test_Results.zip":
        payload, media = build_zip(reports), "application/zip"
    elif name in reports:
        payload = reports[name]
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if name.endswith(".xlsx") else "text/csv; charset=utf-8"
    else:
        raise HTTPException(404, "Report not found")
    return Response(payload, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.post("/api/reset/prepare")
def reset_prepare(request: Request):
    _same_origin(request)
    s = storage()
    token = secrets.token_urlsafe(32)
    with _reset_lock:
        _reset_tokens.clear()
        _reset_tokens[token] = (str(s.root), s.generation)
    return {"token": token, "generation": s.generation}


class ResetRequest(BaseModel):
    token: str
    generation: str
    confirmation: str


@app.post("/api/reset")
def reset(payload: ResetRequest, request: Request):
    _same_origin(request)
    if payload.confirmation != "CLEAR SAVED INVOICES":
        raise HTTPException(422, "Final confirmation is required")
    with _reset_lock:
        expected = _reset_tokens.pop(payload.token, None)
    s = storage()
    if expected != (str(s.root), s.generation) or payload.generation != s.generation:
        raise HTTPException(409, "Reset confirmation expired or storage changed")
    try:
        return s.reset(str(s.root))
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


if _dist.is_dir():
    app.mount("/assets", StaticFiles(directory=_dist / "assets"), name="assets")

    @app.get("/")
    def index():
        return FileResponse(_dist / "index.html")
