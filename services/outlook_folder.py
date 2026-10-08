"""Manual, local-folder email intake through the existing invoice pipeline."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from models.invoice_schema import InvoiceResult
from services.document_processing import process_document
from services.duplicate_service import mark_duplicates
from services.local_storage import LocalStorage
from services.storage_lock import exclusive_folder_operation
from utils.file_utils import expand_upload

_RUN_LOCK = Lock()


@dataclass
class FolderRun:
    folder: Path
    batch_id: str | None
    emails_processed: int
    emails_already_processed: int
    ignored_files: int
    results: list[InvoiceResult]
    warnings: list[str]


@exclusive_folder_operation
def run_outlook_folder(storage: LocalStorage, tolerance: float, threshold: float,
                       max_file_bytes: int) -> FolderRun:
    """Process new .msg/.eml files, retaining originals and appending to history."""
    if not _RUN_LOCK.acquire(blocking=False):
        raise RuntimeError('An Outlook folder run is already in progress.')
    try:
        folder = storage.outlook_folder
        uploads: list[tuple[str, bytes]] = []
        identities: list[tuple[str, str]] = []
        skipped = ignored = 0
        intake_warnings = []
        for path in sorted(folder.iterdir(), key=lambda item: item.name.casefold()):
            if path.suffix.casefold() not in {'.msg', '.eml'} or not path.is_file():
                ignored += 1
                continue
            try:
                if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(folder):
                    raise ValueError('Linked Outlook files are not supported.')
                before = path.stat()
                if before.st_size > max_file_bytes:
                    raise ValueError(f'Email exceeds the {max_file_bytes / 1_000_000:g} MB limit.')
                # Bound the read even if another application is still copying the email.
                with path.open('rb') as stream:
                    data = stream.read(max_file_bytes + 1)
                after = path.stat()
                if len(data) > max_file_bytes:
                    raise ValueError('Email grew beyond the upload size limit.')
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ValueError('Email is still changing; finish copying it and select Run again.')
            except (OSError, ValueError) as exc:
                intake_warnings.append(f'{path.name}: {exc} This email was not processed; other emails continue.')
                continue
            digest = hashlib.sha256(data).hexdigest()
            if storage.outlook_processed(path.name, digest):
                skipped += 1
                continue
            uploads.append((path.name, data))
            identities.append((path.name, digest))
        if not uploads:
            return FolderRun(folder, None, 0, skipped, ignored, [], intake_warnings + storage.export_warnings())

        batch_id = uuid4().hex
        documents = []
        results = []
        for name, data in uploads:
            try:
                documents.extend(expand_upload(name, data, max_file_bytes))
            except ValueError as exc:
                failure = InvoiceResult.failed(name, str(exc), datetime.now(timezone.utc).isoformat())
                failure.source_email = name
                results.append(failure)
        storage.archive_uploads(batch_id, uploads)
        storage.archive_documents(batch_id, documents)
        results.extend(process_document(document, tolerance, threshold) for document in documents)
        prior_results = [prior for _, batch_results in storage.load_batches() for prior in batch_results]
        mark_duplicates(results, prior_results)
        warnings = intake_warnings + storage.save_batch(batch_id, results, outlook_files=identities)
        return FolderRun(folder, batch_id, len(uploads), skipped, ignored, results, warnings)
    finally:
        _RUN_LOCK.release()
