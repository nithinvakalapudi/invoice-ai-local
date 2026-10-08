"""Real in-memory Outlook containers; AI calls mocked at the document boundary."""
import csv
import io
import struct
import unittest
import zipfile
from unittest.mock import patch, call

from extract_msg import OleWriter
from models.invoice_schema import LineItem
from services.csv_service import generate_csvs
from services.document_processing import process_document
from utils.file_utils import expand_upload


def prop(tag, value):
    return struct.pack('<IIII', int(tag, 16), 0, value, 0)


def msg_bytes(attachments):
    writer = OleWriter()
    writer.addEntry('__properties_version1.0', bytes(32) + prop('340D0003', 0x40000))
    writer.addEntry('__substg1.0_001A001F', 'IPM.Note'.encode('utf-16-le'))
    writer.addEntry('__substg1.0_1000001F', 'PRIVATE BODY AND SIGNATURE'.encode('utf-16-le'))
    writer.addEntry('__substg1.0_0037001F', 'PRIVATE SUBJECT'.encode('utf-16-le'))
    for index, attachment in enumerate(attachments):
        directory = f'__attach_version1.0_#{index:08X}'
        writer.addEntry(directory, storage=True)
        properties = bytes(8) + prop('37050003', attachment.get('method', 1))
        properties += prop('7FFE000B', attachment.get('hidden', 0))
        properties += prop('37140003', attachment.get('flags', 0))
        writer.addEntry([directory, '__properties_version1.0'], properties)
        writer.addEntry([directory, '__substg1.0_3707001F'], attachment['name'].encode('utf-16-le'))
        if attachment.get('cid'):
            writer.addEntry([directory, '__substg1.0_3712001F'], attachment['cid'].encode('utf-16-le'))
        if 'data' in attachment:
            writer.addEntry([directory, '__substg1.0_37010102'], attachment['data'])
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class MsgUploadTests(unittest.TestCase):
    def expand(self, attachments, name='mail.msg'):
        return expand_upload(name, msg_bytes(attachments), 1_000_000)

    def test_only_actual_attachments_reach_ai(self):
        documents = self.expand([
            {'name': 'invoice.pdf', 'data': b'PDF'},
            {'name': 'invoice.png', 'data': b'IMAGE'},
            {'name': 'signature.png', 'data': b'PRIVATE', 'hidden': 1},
            {'name': 'logo.png', 'data': b'PRIVATE', 'cid': 'logo'},
            {'name': 'inline.jpg', 'data': b'PRIVATE', 'flags': 4},
        ])
        self.assertEqual([d.filename for d in documents], ['invoice.pdf', 'invoice.png'])
        with patch('services.document_processing.extract_invoice', return_value={}) as extract:
            results = [process_document(d, .01, 95) for d in documents]
        self.assertEqual(extract.call_args_list, [call(b'PDF', 'invoice.pdf'), call(b'IMAGE', 'invoice.png')])
        self.assertEqual([r.source_email for r in results], ['mail.msg'] * 2)

    def test_multiple_emails_and_duplicate_filenames(self):
        documents = self.expand([{'name': 'same.pdf', 'data': b'A'}], 'first.MSG')
        documents += self.expand([{'name': 'same.pdf', 'data': b'B'}, {'name': 'same.pdf', 'data': b'C'}], 'second.msg')
        self.assertEqual([(d.source_email, d.attachment_index) for d in documents],
                         [('first.MSG', 1), ('second.msg', 1), ('second.msg', 2)])

    def test_bad_attachment_does_not_stop_peers(self):
        documents = self.expand([{'name': 'broken.pdf'}, {'name': 'ok.pdf', 'data': b'OK'}])
        self.assertEqual(documents[0].intake_status, 'FAILED')
        self.assertEqual(documents[1].data, b'OK')

    def test_unsupported_and_empty_email_do_not_call_ai(self):
        documents = self.expand([{'name': 'notes.txt', 'data': b'notes'},
                                 {'name': 'embedded.msg', 'method': 5}]) + self.expand([])
        with patch('services.document_processing.extract_invoice') as extract:
            results = [process_document(d, .01, 95) for d in documents]
        extract.assert_not_called()
        self.assertTrue(all(r.processing_status == 'SKIPPED' and not r.review_required for r in results))

    def test_invalid_msg_and_upload_limit(self):
        with self.assertRaises(ValueError):
            expand_upload('broken.msg', b'not OLE', 1_000_000)
        with self.assertRaises(ValueError):
            expand_upload('large.msg', msg_bytes([]), 5)

    def test_properties_only_msg_reports_incomplete_email(self):
        writer = OleWriter()
        writer.addEntry('__properties_version1.0', bytes(32) + prop('340D0003', 0x40000))
        output = io.BytesIO()
        writer.write(output)
        with self.assertRaisesRegex(ValueError, 'Incomplete Outlook MSG file'):
            expand_upload('incomplete.msg', output.getvalue(), 1_000_000)

    def test_zip_reports_each_incomplete_email_without_losing_valid_peer(self):
        writer = OleWriter()
        writer.addEntry('__properties_version1.0', bytes(32) + prop('340D0003', 0x40000))
        incomplete = io.BytesIO()
        writer.write(incomplete)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as zipped:
            zipped.writestr('incomplete.msg', incomplete.getvalue())
            zipped.writestr('valid.msg', msg_bytes([{'name': 'invoice.pdf', 'data': b'PDF'}]))
        documents = expand_upload('emails.zip', archive.getvalue(), 1_000_000)
        self.assertEqual([doc.filename for doc in documents], ['incomplete.msg', 'invoice.pdf'])
        self.assertEqual(documents[0].intake_status, 'FAILED')
        self.assertIn('Incomplete Outlook MSG file', documents[0].intake_message)
        self.assertEqual(documents[1].source_email, 'valid.msg')

    def test_ai_failure_is_independent(self):
        documents = self.expand([{'name': 'one.pdf', 'data': b'A'}, {'name': 'two.pdf', 'data': b'B'}])
        with patch('services.document_processing.extract_invoice', side_effect=[RuntimeError('test failure'), {}]):
            results = [process_document(d, .01, 95) for d in documents]
        self.assertEqual(results[0].processing_status, 'FAILED')
        self.assertNotEqual(results[1].processing_status, 'FAILED')
        self.assertEqual(results[0].source_attachment, 'one.pdf')

    def test_all_reports_preserve_provenance(self):
        document = self.expand([{'name': 'invoice.pdf', 'data': b'A'}])[0]
        with patch('services.document_processing.extract_invoice', return_value={}):
            result = process_document(document, .01, 95)
        result.line_items = [LineItem(description='Service', line_total=10)]
        for filename, data in generate_csvs([result]).items():
            row = next(csv.DictReader(io.StringIO(data.decode())))
            self.assertEqual(row['source_email'], 'mail.msg', filename)
            self.assertEqual(row['source_attachment'], 'invoice.pdf', filename)
            self.assertEqual(row['attachment_index'], '1', filename)

    def test_existing_pdf_and_zip_inputs(self):
        direct = expand_upload('invoice.pdf', b'PDF', 1000)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('folder/invoice.pdf', b'PDF')
        zipped = expand_upload('invoices.zip', output.getvalue(), 1000)
        self.assertEqual(direct, zipped)
        self.assertEqual(direct[0].source_email, '')


if __name__ == '__main__':
    unittest.main()
