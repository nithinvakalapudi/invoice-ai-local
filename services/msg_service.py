"""Read actual file attachments from uploaded MSG containers, entirely in memory."""
import io
from pathlib import PurePosixPath

from extract_msg import MSGFile
from extract_msg.enums import PropertiesType
from extract_msg.properties import PropertiesStore

from utils.file_utils import DocumentInput, is_supported


def extract_msg_attachments(filename: str, data: bytes, max_file_bytes: int) -> list[DocumentInput]:
    email_name = PurePosixPath(filename.replace('\\', '/')).name
    if len(data) > max_file_bytes:
        raise ValueError(f"MSG exceeds the upload size limit: {email_name}")
    documents = []
    try:
        # Base MSGFile does not extract or convert an email body. Delayed
        # attachments let us isolate malformed attachment streams individually.
        with MSGFile(io.BytesIO(data), delayAttachments=True) as message:
            entries = message.listDir(True, True, False)
            if entries == [['__properties_version1.0']]:
                raise ValueError(
                    f"Incomplete Outlook MSG file: {email_name}. It contains no email or attachment "
                    "content. In Outlook, open the original email and use File > Save As > "
                    "Outlook Message Format (.msg), then upload that saved file."
                )
            directories = sorted({p[0] for p in message.listDir(False, True, False)
                                  if p and p[0].startswith('__attach_version1.0_')})
            if len(directories) > 200:
                raise ValueError("MSG contains more than 200 attachments.")
            for index, directory in enumerate(directories, 1):
                name = f"attachment-{index}"
                try:
                    props = PropertiesStore(message.getStream([directory, '__properties_version1.0']),
                                            PropertiesType.ATTACHMENT)
                    # Outlook's hidden/content-in-body flags, content IDs and
                    # content locations identify signature and inline resources.
                    # Conservatively exclude CID resources without reading body.
                    hidden = props.getValue('7FFE000B', False)
                    flags = props.getValue('37140003', 0)
                    cid = message.getStringStream([directory, '__substg1.0_3712'])
                    location = message.getStringStream([directory, '__substg1.0_3713'])
                    if hidden or flags & 4 or cid or location:
                        continue
                    original = (message.getStringStream([directory, '__substg1.0_3707']) or
                                message.getStringStream([directory, '__substg1.0_3704']))
                    if original:
                        name = PurePosixPath(original.replace('\\', '/')).name
                    document = DocumentInput(name, b'', email_name, name, index)
                    method = props.getValue('37050003', 1)
                    if method != 1 or not is_supported(name):
                        document.intake_status = 'SKIPPED'
                        document.intake_message = 'Unsupported attachment type; only attached PDF and supported image files are processed.'
                    else:
                        payload = message.getStream([directory, '__substg1.0_37010102'])
                        if not payload:
                            raise ValueError('Attachment has no readable file content.')
                        if len(payload) > max_file_bytes:
                            raise ValueError('Attachment exceeds the document size limit.')
                        document.data = payload
                    documents.append(document)
                except Exception:
                    documents.append(DocumentInput(name, b'', email_name, name, index,
                                                   'FAILED', 'Attachment could not be read from the MSG file.'))
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Cannot read Outlook MSG file: {email_name}") from exc
    if not documents:
        documents.append(DocumentInput(email_name, b'', source_email=email_name,
                                       intake_status='SKIPPED',
                                       intake_message='No non-inline document attachments found.'))
    return documents
