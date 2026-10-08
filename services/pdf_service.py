"""PDF native-text extraction with image rendering for OCR fallback."""
from __future__ import annotations


import pymupdf as fitz
from PIL import Image



def extract_native_text(pdf_bytes: bytes) -> str:
    """Extract all selectable text from a PDF, raising a useful error if corrupt."""
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        with document:
            return "\n".join(page.get_text("text") for page in document).strip()
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ValueError("PDF could not be opened or is corrupted") from exc


def render_pdf_pages(pdf_bytes: bytes, dpi: int = 250,
                     max_pages: int | None = None) -> list[Image.Image]:
    """Render every PDF page into RGB images for OCR."""
    scale = dpi / 72
    matrix = fitz.Matrix(scale, scale)
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
        images: list[Image.Image] = []
        with document:
            for index, page in enumerate(document):
                if max_pages is not None and index >= max_pages:
                    break
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                images.append(Image.frombytes("RGB", [pixmap.width, pixmap.height], pixmap.samples))
        return images
    except (fitz.FileDataError, RuntimeError) as exc:
        raise ValueError("PDF pages could not be rendered for OCR") from exc
