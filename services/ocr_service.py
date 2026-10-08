"""Resilient OCR routines for invoice PDFs and images."""
from __future__ import annotations

import io
import subprocess
from pathlib import PurePosixPath

import pytesseract
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from config.settings import settings
from services.pdf_service import extract_native_text, render_pdf_pages

MIN_NATIVE_TEXT_CHARS = 40


def _prepare_image(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image).convert("L")
    return ImageOps.autocontrast(image)


def _ocr_image(image: Image.Image, photo: bool = False) -> str:
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    try:
        prepared = _prepare_image(image)
        # Dense invoices and phone photos of a document need different layouts.
        # Keep unique lines from both passes; downstream extraction still
        # requires printed labels or human review before accepting a value.
        dense = pytesseract.image_to_string(prepared, config="--oem 3 --psm 6")
        sparse = pytesseract.image_to_string(prepared, config="--oem 3 --psm 11")
        passes = [dense, sparse]
        if photo and image.width >= 900 and image.height >= 1100:
            # Phone photos of a screen often contain browser chrome and moiré.
            # Read an enlarged center region as a third, independent pass;
            # retain the full-image passes so margin text is not lost.
            width, height = image.size
            center = prepared.crop((int(width * .06), int(height * .14),
                                    int(width * .88), int(height * .96)))
            center = center.resize((center.width * 2, center.height * 2))
            center = ImageOps.autocontrast(center).filter(
                ImageFilter.UnsharpMask(radius=1, percent=160))
            passes.append(pytesseract.image_to_string(center, config="--oem 3 --psm 6"))
            table = prepared.crop((int(width * .10), int(height * .37),
                                   int(width * .90), int(height * .75)))
            table = ImageOps.autocontrast(table.resize(
                (table.width * 2, table.height * 2)))
            passes.append(pytesseract.image_to_string(table, config="--oem 3 --psm 6"))
        lines = dict.fromkeys(line.strip() for text in passes
                              for line in text.splitlines() if line.strip())
        return "\n".join(lines)
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError("Tesseract is not installed or TESSERACT_CMD is incorrect") from exc
    except pytesseract.TesseractError as exc:
        raise RuntimeError(f"Tesseract OCR failed: {exc}") from exc


def is_tesseract_available() -> bool:
    """Return whether the configured Tesseract executable can be invoked."""
    if settings.tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    try:
        pytesseract.get_tesseract_version()
        return True
    except (pytesseract.TesseractNotFoundError, OSError, subprocess.CalledProcessError):
        return False


def extract_document_text(filename: str, data: bytes) -> str:
    """Use native PDF text when viable, otherwise OCR all pages or an image."""
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix == ".pdf":
        native_text = extract_native_text(data)
        if len(native_text) >= MIN_NATIVE_TEXT_CHARS:
            return native_text
        ocr_text = "\n".join(_ocr_image(page) for page in render_pdf_pages(data)).strip()
        if not ocr_text:
            raise RuntimeError("OCR returned no readable text from PDF")
        return ocr_text
    try:
        with Image.open(io.BytesIO(data)) as image:
            text = _ocr_image(image, photo=True)
    except UnidentifiedImageError as exc:
        raise ValueError("Image could not be opened or is corrupted") from exc
    if not text:
        raise RuntimeError("OCR returned no readable text from image")
    return text
