# Offline invoice model: 2,000-PDF training report

## Data and method

- Source: user-supplied `invoice_2000_training_dataset.zip`, SHA-256 `5D189C78E4EAD9B7E36A7D7EC9A6DD61740618B6F20BE7783E99284888AD6224`.
- The archive contains 2,000 labeled, synthetic, text-layer PDFs in eight layouts (250 each). Embedded scripts were not run.
- A Python `DictVectorizer` plus logistic-regression line selector was fit separately for nine labeled fields. OCR/text parsing and field validation remain separate steps. No Gemini, Ollama, hosted model, or API key was used.
- First, 1,600 PDFs were used for model fitting and 400 held out. The final deployed model was subsequently refit on all 2,000 PDFs and saved atomically under `config/local_data/models/invoice_line_pilot.joblib`.

## Held-out measurements

| Test | Result |
| --- | ---: |
| Correct labeled value on the top-ranked line | 3,555 / 3,600 field checks (98.75%) |
| Vendor top-ranked line | 355 / 400 (88.75%) |
| Final extractor: all eight directly labeled requested fields exact | 400 / 400 PDFs |
| Final extractor: vendor, invoice number/date, currency, account, subtotal, tax, total individually | 400 / 400 each |

The eight-field result uses the local parser and the model trained only on the 1,600 training PDFs. Parser fallbacks explain why final vendor extraction exceeds top-line model selection. This is an evaluation on the same synthetic generator and layouts, not a guarantee for unfamiliar vendors, scans, photographs, or real company invoices.

## Separate supplied archives

| Archive | Eight fields exact on every PDF | Notes |
| --- | ---: | --- |
| `invoice_training_50 (1).zip` | 50 / 50 | Separate synthetic archive; vendor/amount parser issues found and fixed. |
| `invoices_training_corrected.zip` | 8 / 10 | Two PDFs do not visibly print the answer sheet's invoice date; the app leaves it blank rather than inventing it. All other seven checked fields matched on all ten. |

The 2,000 labels contain no Business Unit code, Owner, scanning dates, received date, billing start/end period, or general review decision labels. Those fields cannot be learned from this archive. Received date and ageing are calculated by the app; other workflow-only fields remain blank or require a configured mapping/manual entry. The model does not produce calibrated per-invoice confidence. Consequently, `REVIEW_REQUIRED` remains appropriate even though this synthetic benchmark exceeds 95% on its measured fields. Company invoices need a separately labeled, representative holdout set before unattended approval is safe.

## Application checks

- Python test suite: 102 passed after the cumulative-report and legacy-account safeguards, including the ten-PDF intake/reset check; React production build succeeded.
- The training ZIP was read as data only; its included `train_model.py` was not executed.
- Reset preserves `models/` and reviewed training examples, while clearing uploaded files, saved invoice rows, and report rows.
