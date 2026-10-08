"""Environment-backed application settings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")
# Keep the existing default location so saved history under config/local_data
# remains visible after this cleanup.
DEFAULT_STORAGE_ROOT = Path(__file__).resolve().parent / "local_data"


@dataclass(frozen=True)
class Settings:
    extraction_backend: str = "local"

    tesseract_cmd: str | None = os.getenv("TESSERACT_CMD")

    max_file_mb: int = int(
        os.getenv("MAX_FILE_MB", "30")
    )

    default_tolerance: float = float(
        os.getenv("ROUNDING_TOLERANCE", "0.02")
    )

    minimum_field_confidence: float = float(
        os.getenv("MINIMUM_FIELD_CONFIDENCE", "95")
    )

    local_storage_dir: str = os.getenv(
        "LOCAL_STORAGE_DIR",
        str(DEFAULT_STORAGE_ROOT),
    )


settings = Settings()
