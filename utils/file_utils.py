"""Safe handling of uploaded documents and archives."""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".webp", ".bmp"}
EMAIL_EXTENSIONS = {".msg", ".eml"}
MAX_ZIP_DOCUMENTS = 200


@dataclass
class DocumentInput:
    filename: str
    data: bytes
    source_email: str = ""
    source_attachment: str = ""
    attachment_index: int | None = None
    intake_status: str = ""
    intake_message: str = ""


def is_supported(filename: str) -> bool:
    return PurePosixPath(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def expand_upload(filename: str, data: bytes, max_file_bytes: int) -> list[DocumentInput]:
    """Expand ZIP inputs in memory; reject invalid or oversized members safely."""
    if PurePosixPath(filename).suffix.lower() == ".msg":
        from services.msg_service import extract_msg_attachments
        return extract_msg_attachments(filename, data, max_file_bytes)
    if PurePosixPath(filename).suffix.lower() == ".eml":
        from services.eml_service import extract_eml_attachments
        return extract_eml_attachments(filename, data, max_file_bytes)
    if PurePosixPath(filename).suffix.lower() != ".zip":
        if not is_supported(filename):
            raise ValueError(f"Unsupported document format: {filename}")
        if len(data) > max_file_bytes:
            raise ValueError(f"File exceeds {max_file_bytes // 1_000_000} MB limit: {filename}")
        return [DocumentInput(PurePosixPath(filename).name, data)]
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP archive: {filename}") from exc
    documents: list[DocumentInput] = []
    with archive:
        members = [info for info in archive.infolist()
                   if not info.is_dir() and (is_supported(PurePosixPath(info.filename).name)
                   or PurePosixPath(info.filename).suffix.lower() in EMAIL_EXTENSIONS)]
        if len(members) > MAX_ZIP_DOCUMENTS:
            raise ValueError(f"ZIP contains more than {MAX_ZIP_DOCUMENTS} supported documents: {filename}")
        if sum(info.file_size for info in members) > max_file_bytes * 4:
            raise ValueError(f"ZIP expands beyond the total size limit: {filename}")
        for info in members:
            name = PurePosixPath(info.filename)
            if info.file_size > max_file_bytes:
                raise ValueError(f"ZIP document exceeds the file size limit: {name.name}")
            if info.file_size and info.compress_size and info.file_size / max(info.compress_size, 1) > 100:
                raise ValueError(f"ZIP document has an unsafe compression ratio: {name.name}")
            try:
                payload = archive.read(info)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as exc:
                raise ValueError(f"ZIP document could not be read: {name.name}") from exc
            try:
                documents.extend(expand_upload(name.name, payload, max_file_bytes))
            except ValueError as exc:
                if name.suffix.lower() not in EMAIL_EXTENSIONS:
                    raise
                documents.append(DocumentInput(name.name, b'', source_email=name.name,
                                               intake_status='FAILED', intake_message=str(exc)))
            if len(documents) > MAX_ZIP_DOCUMENTS:
                raise ValueError(f"ZIP expands to more than {MAX_ZIP_DOCUMENTS} documents: {filename}")
    return documents or [DocumentInput(PurePosixPath(filename).name, b'',
                                       intake_status='SKIPPED',
                                       intake_message='ZIP contains no supported PDF, image, or email documents.')]
