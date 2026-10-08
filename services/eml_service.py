"""Extract explicit, non-inline file attachments from RFC 822 email files."""
from email import policy
from email.parser import BytesParser
from pathlib import PurePosixPath

from utils.file_utils import DocumentInput, is_supported


def extract_eml_attachments(filename: str, data: bytes, max_file_bytes: int) -> list[DocumentInput]:
    email_name = PurePosixPath(filename.replace('\\', '/')).name
    if len(data) > max_file_bytes:
        raise ValueError(f"EML exceeds the upload size limit: {email_name}")
    try:
        message = BytesParser(policy=policy.default).parsebytes(data)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Cannot read email file: {email_name}") from exc
    documents: list[DocumentInput] = []
    attachment_index = 0
    for part in message.walk():
        if part.is_multipart() or part.get_content_disposition() != 'attachment':
            continue
        attachment_index += 1
        if attachment_index > 200:
            raise ValueError('Email contains more than 200 attachments.')
        original = part.get_filename() or f'attachment-{attachment_index}'
        name = PurePosixPath(original.replace('\\', '/')).name
        # CID resources can be signatures/logos even when marked as attachments.
        if part.get('Content-ID') or part.get('Content-Location'):
            continue
        document = DocumentInput(name, b'', email_name, name, attachment_index)
        if not is_supported(name):
            document.intake_status = 'SKIPPED'
            document.intake_message = 'Unsupported attachment type; only attached PDF and supported image files are processed.'
        else:
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    raise ValueError('Attachment has no readable file content.')
                if len(payload) > max_file_bytes:
                    raise ValueError('Attachment exceeds the document size limit.')
                document.data = payload
            except (TypeError, ValueError, UnicodeError):
                document.intake_status = 'FAILED'
                document.intake_message = 'Attachment could not be read from the EML file.'
        documents.append(document)
    return documents or [DocumentInput(email_name, b'', source_email=email_name,
                                       intake_status='SKIPPED',
                                       intake_message='No non-inline document attachments found.')]
