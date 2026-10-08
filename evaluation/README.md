# Public invoice evaluation

The React app displays historical runs under **Public dataset test results**, including VALID, REVIEW_REQUIRED, FAILED, and SKIPPED counts, review comments, and report downloads. These are historical records, not current measured local-model accuracy.

The evaluation corpus comes from [Voxel51/high-quality-invoice-images-for-ocr](https://huggingface.co/datasets/Voxel51/high-quality-invoice-images-for-ocr), revision `d21f03cfeea2b330e15a229883c66d7ebece8e69`. Its synthetic annotations are not fully human-verified; blank labels are unknown, not confirmed absent. Images and prior results stay in ignored `evaluation_data`.

`python -m evaluation.benchmark prepare` can prepare the public dataset. The benchmark's automatic evaluation command is intentionally blocked until a validated local extractor is deployed, so it cannot create misleading failed-only runs. For the ten-invoice offline line-selection pilot, see [LOCAL_MODEL_PILOT_REPORT.md](../LOCAL_MODEL_PILOT_REPORT.md) and run `python -m training.local_pilot <corrected-invoices.zip>` after installing `requirements-local.txt`.

Do not equate model self-reported confidence or a synthetic dataset score with production accuracy. Validate against independent, reviewed company-like invoices and scans before enabling unattended processing.
