# Invoice Intelligence

React/FLOWSTACK frontend with a local FastAPI backend for invoice intake, review, and cumulative CSV/Excel history. The older Streamlit interface and Gemini integration have been removed.

## Extraction status

On the current-batch dashboard, **Processed** counts documents whose extraction
ran (including OCR-only and review-only drafts); **Auto-approved** counts only
validated records needing no review. A completed extraction does not prove its
fields correct. **Duplicates** counts matches with saved history and can overlap
with **Review needed**.

`Region` uses the vendor's printed location (for example, `West Palm Beach, FL`
for Blue Ocean), not the Bill To/customer address. When no vendor location can
be identified from the invoice, the cell stays blank for manual correction.

The current required-field policy is Invoice Number, Vendor, Invoice Date,
Currency, and Total Amount. Only blanks in these fields are missing-field
validation errors. The on-screen `Review reasons` column
lists the exact missing required fields and other review triggers, such as a
possible duplicate or an unverified local draft. Optional blanks, including
Business Unit, Billing Account, Owner, and Source email, do not independently
trigger review. The user may revise the mandatory-field policy later.

When PDF text or OCR preserves a dated service table, either as whole rows or
as one text line per cell, the app records each item's description, quantity,
unit rate, and amount in `Invoice_Line_Items.csv`.
`Invoice_Data.csv` includes the number of parsed service lines, their
quantities in source order, total quantity, and joined descriptions. The
`MDM/Invoice_data.csv` report contains only the 18 requested workflow columns;
Region, service details, and review reasons remain available on screen and in
the other reports. A row whose printed quantity ×
rate disagrees with its amount is not included as a reliable line item. The
latest ten-invoice batch was specifically backfilled from its archived PDFs;
other existing saved results are not silently re-extracted.

PDF, image, ZIP, Outlook `.msg`, UTF-8 CSV, and XLSX uploads are accepted and saved. Native PDF text or Tesseract OCR feeds a Python-trained line classifier and conservative field parsers. The current local model has no calibrated per-field accuracy scores, so unverified results remain `LOCAL_DRAFT` / `REVIEW_REQUIRED`; unclear fields stay blank. A calibrated extractor can auto-pass only when every populated field scores strictly above the configured threshold (at least 95%), invoice checks pass, and no line items or derived values lack calibration. Human verification in **Review details** can also clear review when validation passes and no duplicate is detected. If no usable field is found, the record is `OCR_ONLY` / `REVIEW_REQUIRED`. CSV/XLSX rows are `IMPORTED` / `REVIEW_REQUIRED` until checked. No Ollama, Gemini, model download, cloud API, or external inference service is used. See [TRAINING_2000_REPORT.md](TRAINING_2000_REPORT.md) for measured results and limitations. Check every draft against its source.

### Your own model and ongoing training

The line-selection classifier is now trained on the supplied 2,000 labeled PDFs. After each upload, open **Review details**, correct the fields against the source, and save. The app stores corrected labels with OCR text in its local SQLite database and immediately retrains a separate supervised classifier. Future uploads use reviewed corrections first, then the 2,000-invoice model. The app never trains on unreviewed guesses. The home screen shows the number of reviewed examples. Reset keeps those examples and the trained models so learning is not lost.

This is a **supervised document extractor, not a newly trained general-purpose LLM**. The classifier learns where printed fields appear; OCR and validation still limit accuracy, so unverified local drafts require review. The 2,000 PDFs are synthetic and do not label Business Unit, Owner, scanning dates, or billing periods. Business Unit and Owner remain blank until an authoritative mapping is supplied.

## Run the React app

On Windows PowerShell, from this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
cd frontend
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe -m uvicorn web_app:app --host 127.0.0.1 --port 8502
```

Open `http://127.0.0.1:8502/`. Keep the server on loopback: stored invoices and reports are private. Copy `.env.example` to `.env` if you need to change the storage path, OCR executable, or thresholds. No cloud API key is needed. For frontend development, use `npm run dev` in `frontend`; Vite proxies `/api` to port 8502.

## Data and features

- Accepts PDFs, images, ZIP archives, saved Outlook `.msg` files, UTF-8 CSVs, and XLSX spreadsheets. Only actual attachments from `.msg` are selected; email body, signature, and metadata are excluded from extraction. Each attachment or spreadsheet row is processed separately. ZIP intake scans supported PDF/image members; old `.xls` workbooks are not supported.
- The `Outlook` subfolder in local storage accepts manually copied `.msg` files. Run tracks previously processed versions to avoid repeated records.
- Validation, review corrections, duplicate flags, and audit provenance are shared by uploads and folder intake.
- Saved batches produce cumulative `Invoice_Data.csv`, `Invoice_Line_Items.csv`, `Audit_Report.csv`, `MDM/Invoice_data.csv`, and `Invoice_History.xlsx`. The workbook includes an **MDM Invoice Data** sheet with the requested invoice fields. Windows treats `Invoice_Data.csv` and `Invoice_data.csv` as the same name, so the additional CSV schema is stored under `MDM`.
- `MDM/Invoice_data.csv` is rebuilt from every saved batch after each upload or Outlook-folder Run. Business Unit, Billing Account, and Owner remain blank for now. The two scanning dates use the local date the invoice was processed; `Ageing (Today-Sent For Scanning Date)` advances with today's date. `Source email` is an address actually read from the invoice, preferring the vendor contact when several appear; reserved synthetic domains such as `.test` are left blank. `Email file` identifies a saved `.msg` source. Comments contain only actual invoice comments, while errors and warnings remain in the audit report. The unused Verified Accuracy column was removed. `Action` reads `No Review Required` only after successful validation and calibrated extraction or explicit human confirmation; suspected duplicates still require review. Extraction completeness measures the share of nine expected fields present, not measured accuracy. Reset retains the header but removes all invoice rows.
- In MDM results, `Invoice Received Date` is the computer-local date the file was processed, saved permanently with the record. `Ageing (In Days)` and the older report's `Invoice Aging` are today's local date minus Invoice Date; they update when reports are generated or downloaded. A future-dated invoice can therefore have negative ageing.
- `Billing Start Date` and `Billing End Date` are the invoice's service-period start and end dates. They are not copied from Invoice Date or Received Date. If a service date is not printed or supplied in an imported spreadsheet, its billing-date cell stays blank for review.
- Originals and extracted attachments are archived beside the SQLite history. Reset uses two frontend confirmations and a one-time backend token. It clears saved invoice rows and Outlook/uploaded source files, then regenerates header-only CSV/Excel reports. The trained models and reviewed OCR/labels remain in local storage; those examples still contain invoice information. Reset does not erase them.

The existing default storage path is `config/local_data` for compatibility with saved history. To use another directory, set `LOCAL_STORAGE_DIR` in `.env` before starting the server. Do not move or delete existing history without a backup. The training pilot and evaluation corpus are separate from this history folder.

There is no direct corporate Outlook, Microsoft Graph, or inbound email-service connection in this app. Use the local Outlook folder for manually saved `.msg` files.

## Testing and offline pilot

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run build
```

The local draft path loads the app-produced model artifact from `config/local_data/models/invoice_line_pilot.joblib` by default. To recreate it from the supplied ZIP, run `python -m training.local_pilot "C:\Users\Nithin Chowdary\Downloads\invoice_2000_training_dataset.zip" --model-path config/local_data/models/invoice_line_pilot.joblib`. Run `python -m training.verify_model <labeled-archive.zip>` to verify a separate labeled set without changing the model. Set the model path under your configured `LOCAL_STORAGE_DIR` if customized. A new installation without that artifact can still read explicitly labeled fields, but model-based line selection will be unavailable. Do not load model files from uploads or untrusted sources; do not use drafts without human review.

Historical evaluation results remain visible in the React app. [TEST_REPORT.md](TEST_REPORT.md) records earlier runs and provider diagnostics; it is not a claim that the current offline extractor is production-ready.
