"""Scoring tests; no downloads or billable API calls."""
from decimal import Decimal
import unittest

from evaluation.benchmark import compare, normalized, truth
from models.invoice_schema import Invoice, InvoiceResult


class BenchmarkTests(unittest.TestCase):
    def test_unknown_bank_label_is_not_a_negative_example(self):
        self.assertNotIn('bank_account_number', truth({'payment_instructions': {'account_number': ''}}))

    def test_bank_number_keeps_leading_zeroes_and_does_not_correct_letters(self):
        self.assertEqual(normalized('bank_account_number', 'GB12 0012-0034'), 'GB1200120034')
        self.assertNotEqual(normalized('bank_account_number', '00123'), normalized('bank_account_number', '123'))
        self.assertNotEqual(normalized('bank_account_number', 'O123'), normalized('bank_account_number', '0123'))

    def test_european_and_us_amounts(self):
        for amount in ['1.234,56', '1,234.56', '1234,56']:
            self.assertEqual(normalized('total_amount', amount), Decimal('1234.56'))

    def test_failed_extraction_counts_as_missed_field(self):
        scores = compare({'annotation': {'invoice': {'invoice_number': '0012'}}}, InvoiceResult.failed('a.jpg', 'quota', ''))
        number = next(score for score in scores if score['field'] == 'invoice_number')
        self.assertFalse(number['correct'])

    def test_confidence_cannot_substitute_for_correctness(self):
        scores = compare({'annotation': {'invoice': {'invoice_number': '0012'}}},
                         InvoiceResult(invoice=Invoice(invoice_number='9999'), extraction_confidence=100))
        self.assertFalse(scores[0]['correct'])


if __name__ == '__main__':
    unittest.main()
