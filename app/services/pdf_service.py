"""
PDF Service — extraction, validation, and FTS5 indexing.

Extraction backend: pypdf (production upgrade path: swap for PyMuPDF/fitz
by replacing _extract_with_pypdf with _extract_with_pymupdf and updating
requirements.txt — the rest of this module stays unchanged).
"""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Backend detection ─────────────────────────────────────────────────────────
try:
    import fitz  # PyMuPDF — preferred
    _BACKEND = "pymupdf"
except ImportError:
    try:
        from pypdf import PdfReader  # noqa: F401
        _BACKEND = "pypdf"
    except ImportError:
        _BACKEND = "none"

logger.info("PDF extraction backend: %s", _BACKEND)


class PDFExtractionError(Exception):
    pass


class PDFValidationError(Exception):
    pass


def validate_pdf(file_bytes: bytes) -> None:
    """
    Validate that uploaded bytes represent a real PDF.
    Checks magic bytes (not just extension). Raises PDFValidationError on failure.
    """
    if not file_bytes:
        raise PDFValidationError("Empty file uploaded.")
    # PDF magic: %PDF-
    if not file_bytes[:5] == b"%PDF-":
        raise PDFValidationError(
            "File does not appear to be a valid PDF (invalid magic bytes)."
        )
    # Basic size sanity
    if len(file_bytes) < 128:
        raise PDFValidationError("File is too small to be a valid PDF.")


def compute_file_hash(file_bytes: bytes) -> str:
    """SHA-256 of raw file bytes. Used for deduplication."""
    return hashlib.sha256(file_bytes).hexdigest()


def extract_text_by_page(file_path: str) -> list[str]:
    """
    Extract text per page from a PDF file.
    Returns a list where index 0 = page 1 text, etc.
    Raises PDFExtractionError on failure.
    """
    path = Path(file_path)
    if not path.exists():
        raise PDFExtractionError(f"File not found: {file_path}")

    if _BACKEND == "pymupdf":
        return _extract_with_pymupdf(str(path))
    elif _BACKEND == "pypdf":
        return _extract_with_pypdf(str(path))
    else:
        raise PDFExtractionError(
            "No PDF extraction library available. "
            "Install PyMuPDF (pip install PyMuPDF) or pypdf (pip install pypdf)."
        )


def _extract_with_pymupdf(file_path: str) -> list[str]:
    """PyMuPDF extraction — better accuracy, handles complex layouts."""
    pages = []
    try:
        doc = fitz.open(file_path)
        for page in doc:
            text = page.get_text("text")  # plain text extraction
            pages.append(text or "")
        doc.close()
    except Exception as e:
        raise PDFExtractionError(f"PyMuPDF extraction failed: {e}") from e
    return pages


def _extract_with_pypdf(file_path: str) -> list[str]:
    """pypdf extraction — pure Python fallback."""
    from pypdf import PdfReader
    pages = []
    try:
        reader = PdfReader(file_path)
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
    except Exception as e:
        raise PDFExtractionError(f"pypdf extraction failed: {e}") from e
    return pages


def get_page_count(file_path: str) -> Optional[int]:
    """Fast page count without full extraction."""
    try:
        if _BACKEND == "pymupdf":
            doc = fitz.open(file_path)
            count = doc.page_count
            doc.close()
            return count
        elif _BACKEND == "pypdf":
            from pypdf import PdfReader
            return len(PdfReader(file_path).pages)
    except Exception:
        return None
    return None


# ── FTS5 Indexing ─────────────────────────────────────────────────────────────

def index_file(file_id: int, file_path: str, app_context=None) -> tuple[int, str]:
    """
    Extract text from PDF and insert into FTS5 index.
    Designed to run in a background worker (pass app context explicitly).

    Returns (page_count, status).
    Raises PDFExtractionError on failure.
    """
    from ..extensions import db
    from ..models.file_model import FileRepository

    FileRepository.update_index_status(file_id, "processing")

    try:
        pages = extract_text_by_page(file_path)
    except PDFExtractionError as e:
        FileRepository.update_index_status(file_id, "failed", error=str(e))
        raise

    page_count = len(pages)

    # Remove existing index entries (safe re-index)
    with db.transaction():
        db.execute("DELETE FROM file_index WHERE file_id = ?", (file_id,))
        rows = [
            (file_id, page_num + 1, text)
            for page_num, text in enumerate(pages)
            if text.strip()  # Skip blank pages
        ]
        if rows:
            db.executemany(
                "INSERT INTO file_index (file_id, page_number, content) VALUES (?, ?, ?)",
                rows,
            )

    FileRepository.update_index_status(file_id, "indexed", page_count=page_count)
    logger.info("Indexed file_id=%s pages=%s", file_id, page_count)
    return page_count, "indexed"
