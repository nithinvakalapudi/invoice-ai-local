# Invoice application verification — 5 October 2026

## Current user-trained Python extractor — 7 October 2026

Final verification for this update: **83 Python tests passed**, the React
production build passed, the live UI returned HTTP 200, the API reported the
user-trained extractor ready, and the cumulative workbook was refreshed with
all four sheets and no export warnings. `pip check` found no broken requirements
(the environment still has a non-blocking leftover `~treamlit` warning).

The React/FastAPI app uses no Gemini, Ollama, or other inference service.
It has an in-process Python OCR-line classifier trained from the supplied ten
corrected invoices. Human corrections from archived documents now cause local
supervised retraining; only readable OCR/label pairs become field examples.
This is not a general-purpose LLM.

The newer 50-invoice pilot's ten held-out PDFs yielded 90/100 correct **line
selections**, not 90% end-to-end extraction accuracy. Scanned variants yielded
150/200 selected lines. Only 9/50 labeled subtotals were visible in native
text. Accuracy on unseen company invoices remains unmeasured and cannot be
guaranteed from 50 examples. See `LOCAL_MODEL_PILOT_REPORT.md`.

Exact-field checking on the ten excluded native PDFs initially found 75/100
matches. After the resulting parser fixes, the regression check found 100/100;
those invoices were used to tune the rules, so this is not independent accuracy
evidence. The cumulative Excel workbook now includes the 18-column
`MDM Invoice Data` sheet alongside the existing CSV reports.

The 7 October screen-photo regression initially produced an `OCR_ONLY` record
with all five required fields missing. An enlarged document/table OCR pass now
finds the vendor, date, invoice number, currency, and a printed total that
reconciles with three visible charges. The exact saved batch was reprocessed
from its archived image and the cumulative CSV refreshed. It remains
`LOCAL_DRAFT` / `REVIEW_REQUIRED`; the account number and exact invoice-number
punctuation still need source verification.

The entries below are historical reports from earlier implementations, including
Gemini and Streamlit; they do not describe the current extraction backend.

## Configuration update — 6 October 2026

The primary model is now `gemini-3.5-flash-lite` with no fallbacks. A live
synthetic-PDF invoice went through the actual schema-constrained extraction call
and returned invoice number `TEST-ONLY-003`. The web interface at port 8502 was
also recovered from a duplicate Streamlit instance occupying the IPv6 localhost
address; both `localhost:8502` and `127.0.0.1:8502` now display the React
Invoice Intelligence page. Streamlit has a local port-8501 configuration.

The primary model was changed to `gemini-2.5-flash`. Initially the older models
remained as fallbacks; a newly processed Streamlit upload still attempted them.
A live extraction with this API key has not yet been verified for the 2.5 model.
The live-provider results below are from the earlier 3.7-primary configuration.

After a new Streamlit upload still attempted the older fallback models, the
fallback list was cleared in `.env` and the default configuration. Both running
servers were restarted. A fresh process now resolves only `gemini-2.5-flash`,
the Streamlit health endpoint responds, the API reports `gemini-2.5-flash`, and
the full suite passes (94 tests). Previously saved failed results still contain
their original error text; no invoice history was deleted.

## Result

**93 automated tests passed, 0 failed, 0 skipped** on the final run.
This includes 50 new regression/contract tests in this update. The frontend production
build passed and `pip check` reported no broken requirements. The live browser Run
button was checked against the running FastAPI interface.

**Live Gemini extraction remains blocked:** the latest synthetic-PDF check tried all
three configured models. `gemini-3.7-flash` returned HTTP **504**,
`gemini-3.8-flash` returned HTTP **503**, and `gemini-3.1-pro-preview` returned HTTP
**429** (rate limit or quota). An earlier primary-only check returned HTTP 503.
These are external provider responses, not successful extraction. The app records
the attempted models and an actionable final error.
Automatic function calling is now explicitly disabled in the Gemini request config;
the extraction request passes no tools, so this setting is not expected to resolve
the provider's HTTP 503 response.
When a model fails, the extraction service now tries Gemini 3.8 Flash and then
Gemini 3.1 Pro Preview (the official Pro API ID) using the same invoice bytes and
schema. The model sequence was verified with simulated provider failures and in the
live synthetic-PDF check above; live extraction success with this user's API key has
not been established.

Automated passes establish the tested application behavior; they do not establish
95% extraction accuracy across real invoices. AI responses are controlled in the
automated suite. The separate live Gemini check used only a synthetic test invoice.

## How to use the folder workflow

1. Save/export email messages as `.msg` files from Outlook.
2. Copy them directly into `local_data/Outlook` inside this project, or into the
   Outlook folder shown by the app if `LOCAL_STORAGE_DIR` is customized.
3. Wait for copying to finish, then select **Run**.
4. Each supported PDF/image attachment is processed independently. Email body,
   subject, recipients and marked inline/signature resources do not enter the AI input.
5. Results from Run and manual uploads accumulate in `local_data/Invoice_Data.csv`.
   The original emails remain in Outlook. Use **Download cumulative CSV** or the
   saved-history downloads to retrieve reports.
6. Later runs skip an unchanged email with the same filename and content. A changed
   file is treated as a new version. This identity check persists across app restarts.

This is manually triggered local folder intake. It does not poll the Outlook mailbox.
Only `.msg` files directly in the folder are scanned; subfolders and other file types
are ignored and counted. Existing Excel, line-item and audit reports remain available.

## Feature coverage

| Area | Tests | Verified behavior | Final result |
| --- | ---: | --- | --- |
| Outlook Run API and cumulative CSV | 2 | Folder creation; manual upload plus successive folder runs in one CSV; email/attachment filenames; same-email skipping; download; missing key and cross-origin rejection | Pass |
| Outlook folder regressions | 17 | Real MSG containers; multiple emails/attachments; private-content filtering; empty folder; corrupt MSG; AI failure isolation; empty/unsupported emails; oversized/locked files; changed emails; restart; locked CSV recovery; save failure/retry; reset; invalid folder; Streamlit Run; invalid settings; cross-process locking | Pass |
| Pipeline contracts | 21 | API error handling and client cleanup; invalid/empty JSON; extraction/schema/CSV alignment; VALID and REVIEW_REQUIRED validation; low or missing confidence retains scanned values in both invoice CSVs; duplicate flags; native PDF text; image and scanned-PDF OCR; corrupt inputs; invalid ZIP; broken Tesseract launcher | Pass |
| Additional MDM CSV | 7 | Exact requested headers; date/amount mappings; unknown workflow values blank; cumulative append and correction; preservation of original CSV; batch/history downloads; ZIP paths without Windows filename collision; empty report; failed-scan explanation; scanned image attachments through both manual upload and Outlook folder into the cumulative CSV | Pass |
| MSG extraction | 8 | Actual attachments only; multiple messages; repeated attachment filenames; malformed attachments; unsupported/empty email; limits; per-attachment API failures; source fields in all reports; existing PDF/ZIP inputs | Pass |
| Local storage | 11 | Append/reopen; same-batch retry and correction; archive filenames; Excel lock/recovery; failed records; account-number formatting; formula-safe Excel text; empty exports; Streamlit restart/legacy-session handling | Pass |
| Reset | 6 | Two confirmations and cancel paths; scoped deletion in temporary test storage; stale-writer rejection; protected/linked paths; locked-file error | Pass |
| Existing web API | 2 | Upload, history, review, cumulative downloads; cross-origin protection; reset token/confirmation | Pass |
| Existing email intake | 6 | Existing intake and report behavior covered by the prior regression suite | Pass |
| Evaluation/benchmark reporting | 8 | Existing benchmark and report regressions | Pass |
| Frontend build | Separate build | React/FLOWSTACK production bundle compiles | Pass |
| Browser smoke test | Separate check | Run button visible/enabled when configured; empty folder reports “No new emails processed”; history initially collapsed; no batch created | Pass |
| Installed Tesseract | Included in pipeline tests | Real OCR reads invoice number, total and leading-zero account number from synthetic PNG and image-only PDF | Pass |
| Live Gemini | Separate provider call | Configured model contacted with synthetic PDF | Blocked: HTTP 503 |

## Errors found and fixes verified

The first expanded folder test run had **3 failures and 13 passes**. All three failures
were reproduced before changes and passed after the fixes:

| Failure | Fix | Verification |
| --- | --- | --- |
| One oversized email aborted all processing | Skip that email with its filename and a warning; continue readable peers; bound the read size | Oversized email alongside valid MSG passes |
| One locked/unreadable email aborted all processing | Isolate filesystem errors per email; leave the source available for a later Run | Simulated file-copy lock alongside valid MSG passes |
| A regular file named Outlook crashed `/api/config` | Resolve the folder inside configuration error handling; return storage-unavailable diagnostics | Invalid-path API test passes |

Additional issues found while reviewing the workflow were also corrected:

- Streamlit and FastAPI previously had independent process locks. They now share an
  operating-system lock, which also prevents reset during a folder run. A separate
  Python process was used to verify exclusion and lock release.
- Folder files are checked before and after reading so detected changes during copying
  produce a retry message instead of being silently accepted.
- Folder warnings and report-export warnings are now surfaced in the web interface.
- Empty runs no longer claim that a CSV was saved. Run results distinguish failures
  and review counts. The setup badge says **Configured**, which does not imply a live
  provider health check.
- Stale storage instances cannot recreate the Outlook directory after a reset.
- The PDF helper now uses the supported `pymupdf` import name.

## Error recovery and limits

The additional `MDM/Invoice_data.csv` requested during verification is included in the
94-test run. The original `Invoice_Data.csv` remains separate. `Business Unit` and
`Vendor -As per MDM` are separate columns; the latter uses the extracted vendor name.
`Billing Account` uses the extracted remittance account number, including leading zeroes.
`Business Unit` remains blank pending an authoritative mapping. See README for the
complete default mapping.
After a scanned image was found to have a fully blank new-report row due to failed AI
extraction, the exporter was changed to put the source filename and error in `Comments`.
Both scan ingestion paths were then exercised with controlled successful AI responses,
and the new cumulative CSV was checked for the extracted invoice values.
The parser also discarded AI-extracted fields when their confidence was absent or below
95. This now preserves the scanned values in both CSVs while marking the invoice for
review; two tests cover missing and low confidence maps.

- **Oversized, locked or changing source file:** the email is not marked processed.
  Correct the issue and click Run again. Other readable emails continue.
- **CSV/Excel file open or locked:** records remain in SQLite; warnings identify the
  export problem. Close the output file and select Refresh reports. Repeating Run
  does not duplicate the already saved email.
- **Database save failure:** the original email remains, and the intake completion
  marker is not committed. Run can be retried after storage is fixed.
- **AI failure or malformed MSG already saved as a failed result:** the attempt is
  recorded and unchanged mail is skipped on later Run clicks. Re-upload that email
  using the manual uploader to deliberately retry it once the cause is resolved;
  this creates another batch and preserves the earlier failed attempt.
- Invoice-number duplicate marking remains batch-local. Folder identity tracking
  prevents repeated intake of the same unchanged filename/content; it is not a
  global invoice-number deduplication engine.
- No company emails were available in the intake folder during this test. Automated
  MSG cases use generated, structurally real Outlook containers. Unmarked signature
  images can be indistinguishable from genuine attachments without reading the body;
  the existing conservative inline-marker filtering remains in place.

## Remaining warnings

One non-failing Python warning comes from the installed Google SDK's use of
`_UnionGenericAlias`, scheduled for removal in Python 3.17. The current Python 3.14
test run succeeds. Vite also reports ignored `use client` directives from dependency
packages; the production build succeeds.

The live provider 503 is the remaining operational blocker. Local application fixes
cannot restore provider capacity. Live extraction and real-invoice accuracy need a
successful provider request before they can be reported as verified.

## Reproduce

From the `invoice_ai` project directory:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q --tb=short --junitxml=test-results.xml
cd frontend
npm run build
```

`test-results.xml` contains the individual automated test names and outcomes. Tests
use temporary directories for saved invoices, CSV/Excel output and destructive reset
checks. The user's real invoice history is not reset by the test suite.
## React-only cleanup verification (2026-10-07)

- Removed the obsolete Streamlit interface, its UI-only tests, and Gemini provider code. The React/FastAPI app is the sole user interface; the offline extractor remains paused pending accuracy validation.
- Removed unused direct dependencies (`streamlit`, `google-genai`, `pandas`) and replaced CSV generation with the Python standard library. Also uninstalled unused `pyarrow` and `altair` from the existing virtual environment. The environment decreased from about 645 MB to 527 MB; Windows retained some temporary pip folders.
- Automated tests: **73 passed** after dependency removal. Python compilation and `pip check` passed. React production build passed (Vite emits third-party `use client` directive warnings).
- At this cleanup checkpoint, uploads were still gated. The later upload-enable update below supersedes that behavior. Existing saved records remained visible; no extraction-accuracy claim was made.
- Existing invoice history, evaluation corpus, pilot artifacts, and `.env` were not deleted. The removed legacy UI files are recoverable from Git history; the untracked Streamlit port config was removed.

## Upload-enable verification (2026-10-07)

- Direct PDF/image uploads, supported PDF/image ZIP members, Outlook `.msg` attachments, UTF-8 CSV invoice rows, and XLSX invoice rows now enter the saved pipeline. CSV/XLSX originals are archived; invoice rows are imported and flagged for review.
- OCR-only documents never receive guessed invoice values. They appear as `OCR_ONLY` / `REVIEW_REQUIRED` with a review explanation in the CSV. Imported spreadsheet rows appear as `IMPORTED` / `REVIEW_REQUIRED`. Corrupt files become per-source `FAILED` rows.
- **77 tests passed**, including an actual native PDF upload, a combined PDF/image/ZIP/MSG upload, CSV/XLSX imports, OCR fallback, storage, reset, and history. Python compilation and React production build passed. Frontend build still emits third-party `use client` warnings.
- Live configuration reports intake ready and storage ready while automatic field extraction remains unvalidated. No company invoice was uploaded during testing; tests used isolated temporary storage.

## Local draft extraction verification (2026-10-07)

- A trusted project-owned line-selector artifact is now used with local OCR and printed labels to draft readable invoice fields. Drafts remain `REVIEW_REQUIRED`; the app does not present a numeric confidence percentage for them.
- On the ten synthetic PDFs used to train the pilot, 98/100 corrected field values matched. On six scanned versions of three of those invoices, 49/60 matched. Neither score establishes 95% accuracy on independent invoices. The screen photo produced only partial fields; unreadable amounts/account values were left blank.
- `MDM/Invoice_data.csv` retains the exact 18 requested columns. Vendor, account, invoice number/date, currency, service dates, subtotal, tax, received-date rule, ageing, and comments use readable evidence. Business Unit stays blank pending the user-approved code mapping; owner and scanning dates remain blank without workflow data.
- The new parser tests cover these mappings and verify that screen/photo noise is not promoted to vendor or total. Saved historical rows were not rewritten; re-upload is required to create a new draft for an earlier source.

## Project cleanup verification (2026-10-07)

- At the user's direction, removed the dormant separate email-receiver API, worker, queue service, its tests, instructions, and optional requirements file. The manually populated Outlook `.msg` folder and its pipeline remain supported.
- Removed empty legacy Streamlit/output directories, their obsolete log files, an invalid residual Streamlit package-metadata directory, and unused logging declarations. The active `.venv`, frontend dependencies/build, both saved-data folders, trained models, and evaluation corpus were retained.
- The installed Starlette test client requires `httpx2` to avoid a deprecation warning, so it remains in `requirements-dev.txt` and the environment after verification.
- Current checks: **98 tests passed**, frontend production build passed, `pip check` reported no broken requirements, and the live `/api/config` endpoint remained available with 10 saved records. No reset or deletion of invoice history was performed.
- Earlier sections above are historical checkpoints; current `MDM/Invoice_data.csv` includes the requested fields plus completeness, review/duplicate status, source provenance, and a separate processing result.

## Full application regression (2026-10-08)

- **102 tests passed**. The suite covers PDF text and scanned-PDF OCR, image OCR, native PDF/image/ZIP/MSG and CSV/XLSX uploads, extraction validation, review correction, duplicate marking, Outlook-folder re-runs, cumulative CSV/Excel persistence, reset isolation, evaluation, and React API routes. The ten-invoice archive test uses temporary storage and checks append and reset without touching saved user history.
- Fixed duplicate detection so a newly uploaded or folder-run invoice is also compared with saved batches. Correcting a reviewed invoice re-evaluates its duplicate status. Added cross-batch regression tests.
- Limited ZIP expansion by member count, individual member size, and total uncompressed size; malformed or empty archives now yield a visible failed/skipped result. Added boundary tests.
- Invalid batch identifiers now return a 404 API response instead of an uncaught server error.
- React production build succeeded (third-party `use client` warnings remain non-fatal). `pip check` found no broken requirements; `git diff --check` found no whitespace errors. Live read-only checks returned HTTP 200 for `/`, `/api/config`, and `/api/history`.
- Tests do **not** establish 95% field accuracy on unseen company invoices. The installed local field selector is a `DictVectorizer` plus `LogisticRegression` model trained on labeled invoice lines, with OCR/layout rules around it. A separately labeled, vendor-disjoint holdout is required before automatic acceptance at a 95% target.
- Restarted the local FastAPI server on port 8502 so the fixes are active. `/api/config` then reported storage ready, local extraction selected, 3 saved batches and 20 saved records. No real invoice history, model artifacts, or configuration was reset during this audit.

## Invoice_data column and review update (2026-10-08)

- The MDM report leaves Business Unit, Billing Account and Owner blank. Sent for Scanning Date and Scanned Date in Expense use the saved processing date; their ageing is today's local date minus that scanning date. Comments contain only an actual printed/imported invoice comment. Removed Verified Accuracy (%).
- Source email now uses a vendor contact printed in the invoice document when the extractor can distinguish it from a bill-to address. The original `.msg` filename is retained separately as Email file. Action is `No Review Required` only when validation passes and the record needs no review; suspected duplicates remain Review Required. Human confirmation through the review dialog can clear an uncalibrated draft without manufacturing a model confidence score.
- **105 tests passed** after these changes. React production build, `pip check`, and `git diff --check` passed. The live server was restarted, and a CSV download returned HTTP 200 with the new header; 3 batches and 20 saved records remained intact. CSV/XLSX import preserves a supplied vendor email, and malformed vendor addresses require review.
- Separate labeled synthetic checks: `invoice_training_50 (1).zip` produced 400/400 exact field values on 50 invoices; `invoices_training_corrected.zip` produced 78/80 exact field values on 10 invoices (invoice date absent from two source PDFs). These results exceed 90% on those supplied synthetic samples but do not establish 90% on future real company invoices or calibrate per-invoice confidence.
- The 20 previously saved invoices remain Review Required because they were not human-confirmed and have no calibrated confidence evidence. Old records cannot acquire a printed vendor email without re-extracting their archived documents; no historical rows were silently changed or auto-approved.
- The supplied phone photograph of the Blue Ocean invoice was also reprocessed as a diagnostic. The vendor name, invoice number/date, currency and visible total were read, but its tiny footer email and some amounts were not legible to Tesseract. Source email correctly remained blank rather than copying the bill-to address or guessing a vendor address.

## Source-email correction (2026-10-08)

- Traced `0@example.test` to OCR of a synthetic scanned invoice: the native counterpart printed `billing10@example.test`, while OCR read `billing! 0@example.test`. The exporter now omits reserved synthetic-domain addresses, including previously saved results, rather than displaying a broken test contact.
- When an invoice prints multiple real addresses, the extractor prefers a vendor contact; if only another printed contact is available, it uses that instead of a fixed address. The clear Blue Ocean image supplied in chat was processed through the local extraction pipeline and yielded `accounting@blueocean-tech.io` for Source email.
- **107 tests passed**; the frontend build passed. After restarting port 8502 and refreshing the cumulative report, it returned HTTP 200 with 5 saved batches and 100 invoice rows unchanged. No `.test`/`.example`/`.invalid` address remained in Source email. Those saved invoices are synthetic, so Source email is blank for them rather than populated with test-only values.

## Ten-document dashboard diagnosis (2026-10-08)

- Reproduced the reported latest batch: 10 documents had completed extraction, but the old `Processed` counter displayed zero because it counted only status `PROCESSED` (automatic approval). The API now counts `PROCESSED`, `LOCAL_DRAFT`, and `OCR_ONLY` as processed documents, and reports automatic approvals separately. The UI replaces the redundant `Local drafts` tile with `Auto-approved`.
- In the saved ten-document batch, eight invoice numbers match earlier saved uploads. Two other PDFs (`invoice_06_GDM-260913.pdf` and `invoice_08_PLF-SG-261024.pdf`) have no extracted invoice date. The application has no calibrated per-field confidence for local drafts. These are genuine review conditions; no status or data was silently changed to manufacture success.
- A new regression test checks the counter semantics across successful, draft, OCR-only, imported, and failed records. **108 tests passed** and the React production build passed. After restarting the server, the latest batch API reports `total=10`, `processed=10`, `auto_approved=0`, `review=10`, `duplicate=8`, and the homepage returns HTTP 200. User invoice history and training data were not reset.

## Dated service-line extraction (2026-10-08)

- Added parsing for dated service-table rows containing a description, quantity, unit rate, and amount. The example with Digital Campaign Management, Creative Production, and Performance Reporting produces three line items, descriptions joined in source order, quantities `1; 6; 1`, and total quantity `8`.
- The current-batch MDM table, both invoice CSV exports, the separate line-item CSV, and the cumulative Excel report now expose the relevant summary/detail fields. A row is skipped if quantity multiplied by rate does not match its amount to within two cents; this avoids promoting contradictory OCR into a confident line item.
- Added tests for the example, arithmetic rejection, CSV schemas, and typed numeric cells in the cumulative workbook. Older saved records are not re-extracted automatically; re-upload their source invoices to populate newly parsed line items.

## PDF table-cell layout correction (2026-10-08)

- Diagnosed the latest ten-PDF upload: native PDF text emitted service dates, descriptions, quantities, rates, and amounts on five separate lines, so the first row-oriented parser returned zero line items. The parser now accepts both inline rows and this cell-per-line layout, including single-day services and the currency prefixes present in the supplied PDFs.
- Read-only re-extraction of all ten archived PDFs found exactly three reconciled service lines per invoice (30 total); each invoice's line amounts summed to its stored net amount. Added regression tests for the cell-per-line layout and currency/single-day variants. **112 tests passed**.
- Backfilled only the empty `line_items` fields in the user's latest ten-invoice batch `abaf544cbc5d4ffc83931843f4fd3caa` after verifying exact archived-source identity, invoice number, three-line count, net-amount reconciliation, and an unchanged batch snapshot. Regenerated cumulative CSV/Excel outputs. The Digital Campaign invoice now shows `3`, the three descriptions, `1; 6; 1`, and `8` in the API, saved CSV, and Excel sheet. Invoice history, training data, and other fields were preserved.

## Vendor Region column (2026-10-08)

- Added `Region` to the on-screen MDM invoice results, cumulative `MDM/Invoice_data.csv`, and the Excel MDM sheet. It uses the vendor's printed city/state/country, not the Bill To address. The old rule that set Hong Kong whenever it appeared anywhere in the document was removed. Region can be corrected in the existing review form and stays blank when the vendor address is not identifiable.
- Tested the location rule on vendor-versus-customer and missing-vendor-address cases. Read-only extraction of the ten archived PDFs found ten vendor locations. The latest saved batch was then backfilled after verifying archived-source identity, invoice numbers, and an unchanged batch snapshot; only `invoice.region` was modified. Blue Ocean now reads `West Palm Beach, FL`, not the customer's `Hong Kong`.
- **114 tests passed**; the React production build passed. The cumulative CSV and Excel sheet each have the new column and all ten locations. The restarted local API returned Region in its configured columns and the saved Blue Ocean result; `/` returned HTTP 200. Existing training data was not changed.
