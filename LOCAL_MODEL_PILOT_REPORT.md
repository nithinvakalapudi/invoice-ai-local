# Local invoice-model pilot — 7 October 2026

## New 50-invoice training archive (current)

The supplied `invoice_training_50 (1).zip` contains 50 synthetic native PDFs,
50 corrected answer-sheet rows, and scanned PDF/JPEG variants of ten of those
invoices. The archive passed member/size checks. The app's own Python
line-selection model was trained on all 50 native PDFs and saved under
`config/local_data/models/invoice_line_pilot.joblib`. Reviewed user corrections
train a separate model automatically and take priority during extraction.

For evaluation, 40 PDFs were used for fitting and ten were held out. The
selected line contained the labeled value in **90/100 field checks**. The
scanned variants yielded **150/200** selected lines, but these are repeated
invoice identities and are not an independent test. Only **9/50** labeled
subtotals were visible as exact amounts in native PDF text, so subtotal cannot
be reliably learned from this corpus. These metrics measure line location,
**not exact field extraction, full invoice correctness, or 95% accuracy on
unseen company invoices**. All results remain review-required.

An exact-field check (40 training, ten excluded PDFs) initially yielded
**75/100**. After inspecting those errors, printed `BILL TO` customer names
without legal suffixes were accepted, issuer names without suffixes were
guarded by invoice-header position, and net amount was calculated only when
printed total and tax permitted it. The resulting regression check is
**100/100 exact fields on those ten synthetic PDFs**. Because the same ten
documents guided those rule changes, this is *not* an unbiased unseen test or
a 100% accuracy claim. The supplied archive has no labels for Business Unit,
Owner, scanning dates, or other workflow-only values; the app leaves them blank.

The sections below describe the older ten-invoice experiment for comparison.

## Dataset and method

The user-supplied corrected ZIP contains ten one-page, synthetic/demo PDFs, a
ten-row answer sheet, and scanned PDF and JPEG versions of three of those
invoices. No invoice bytes or account numbers were copied into the source
repository. A local logistic-regression classifier was trained to select the
text line most likely to contain each of ten labeled fields. It used eight
original PDFs for training and two distinct vendors for holdout evaluation.
The scanned versions were an OCR robustness check, not an independent-vendor
holdout because their source invoices also appear in training.

## Results

| Check | Result | Meaning |
| --- | ---: | --- |
| Held-out native PDFs | 18 / 20 | Correct value appeared on the model-selected line. This is **not** exact field-extraction or whole-invoice accuracy. |
| Invoice date in holdout | 0 / 2 | One date was missed by the model; one labeled date was not readable in the source PDF. |
| Scanned PDFs and JPEGs | 37 / 60 | Correct value appeared on the model-selected OCR line. |
| Scanned OCR visibility | 59 / 60 | Corrected values appeared somewhere in OCR text, whether or not selected. |
| Account-number visibility | 10 / 10 | The corrected native PDFs contain the labeled account number. |

The answer sheet gives invoice dates for `invoice_06_GDM-260913.pdf` and
`invoice_08_PLF-SG-261024.pdf` that were not found in native PDF text or
independent OCR. The model must not infer these as printed facts.

The ten PDFs share a synthetic layout. This pilot has **not** established 95%
accuracy on real company invoices or complete extraction of service dates,
line items, and workflow fields. The trained model is saved only under the
ignored `local_data/models/` directory. The initial pilot was not deployed for
automatic extraction at the time of this report; the later review-only draft
integration below supersedes that state. No Gemini request is made.

The updated Python suite passed 98 tests. The React frontend built successfully,
and the running `localhost:8502` API reports `extraction_backend=local`,
`api_ready=false`, and `storage_ready=true`.

## Reproduce

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-local.txt
.\.venv\Scripts\python.exe -m training.local_pilot C:\path\to\invoices_training_corrected.zip --model-path local_data\models\invoice_line_pilot.joblib
```

The current pilot needs representative, verified real invoices and a separate
vendor-held-out test set before it can safely replace automatic extraction.

## Review-only draft integration — 7 October 2026

The app now uses the saved line selector plus explicit printed labels to draft
invoice fields locally. Every result remains `REVIEW_REQUIRED`, and unclear
fields remain blank. This is not autonomous production extraction.

- Ten native demo PDFs: 98/100 labeled values matched. **These are training
  examples**, not an independent accuracy estimate. Two invoice dates are not
  printed/readable in the source and remain blank.
- Six scanned versions of three of those same invoices: 49/60 labeled values
  matched. These are not independent invoice identities.
- The user's screen photo yielded a partial draft (vendor, billed company,
  invoice date, and OCR-form invoice number). The amount and bank account were
  not readable enough to populate; they were left blank rather than guessed.
- Business Unit is a code, not the billed company name. It remains blank until
  an approved code mapping is supplied. Owner and scanning dates likewise need
  workflow data and are not inferred from the invoice.
