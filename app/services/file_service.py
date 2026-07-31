"""
File Service — upload pipeline orchestration.

Responsibilities:
  - Validate uploaded files (magic bytes, extension, size)
  - Persist to filesystem with collision-safe names
  - Create DB records
  - Enqueue background indexing jobs
  - Handle temp file cleanup
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from ..models.file_model import File, FileRepository
from ..models.category_model import CategoryRepository
from .pdf_service import (
    PDFValidationError,
    compute_file_hash,
    validate_pdf,
    index_file,
)
from .job_service import enqueue_index_job

logger = logging.getLogger(__name__)


class FileServiceError(Exception):
    pass


class DuplicateFileError(FileServiceError):
    pass


def save_uploaded_file(
    file_storage: FileStorage,
    category_id: Optional[int],
    is_temp: bool,
    uploaded_by: Optional[int],
) -> File:
    """
    Full upload pipeline:
      1. Read bytes
      2. Validate (magic bytes + extension)
      3. Deduplication check
      4. Persist to storage
      5. Create DB record
      6. Enqueue indexing job

    Returns the created File record.
    Raises FileServiceError / PDFValidationError on failure.
    """
    raw = file_storage.read()

    # ── Validation ─────────────────────────────────────────────────────────────
    _validate_extension(file_storage.filename)
    try:
        validate_pdf(raw)
    except PDFValidationError as e:
        raise FileServiceError(str(e)) from e

    file_hash = compute_file_hash(raw)

    # ── Deduplication ──────────────────────────────────────────────────────────
    existing = FileRepository.get_by_hash(file_hash)
    if existing and not is_temp:
        raise DuplicateFileError(
            f"This file has already been uploaded as '{existing.original_name}' "
            f"(id={existing.id})."
        )

    # ── Persistence ────────────────────────────────────────────────────────────
    original_name = secure_filename(file_storage.filename) or "upload.pdf"
    stored_filename = _unique_filename(original_name)

    if is_temp:
        storage_dir = Path(current_app.config["TEMP_UPLOAD_FOLDER"])
    else:
        category = CategoryRepository.get_by_id(category_id)
        if not category:
            raise FileServiceError(f"Category {category_id} not found.")
        storage_dir = Path(current_app.config["UPLOAD_FOLDER"]) / category.slug

    storage_dir.mkdir(parents=True, exist_ok=True)
    file_path = storage_dir / stored_filename

    try:
        file_path.write_bytes(raw)
    except OSError as e:
        raise FileServiceError(f"Failed to write file to disk: {e}") from e

    # ── DB record ──────────────────────────────────────────────────────────────
    expires_at = None
    if is_temp:
        lifetime = current_app.config["TEMP_FILE_LIFETIME"]
        expires_at = (datetime.now(timezone.utc) + lifetime).isoformat()

    db_file = FileRepository.create(
        filename=stored_filename,
        original_name=original_name,
        category_id=category_id,
        is_temp=is_temp,
        file_hash=file_hash,
        file_size=len(raw),
        expires_at=expires_at,
        uploaded_by=uploaded_by,
    )

    # ── Enqueue indexing ───────────────────────────────────────────────────────
    enqueue_index_job(db_file.id, str(file_path))

    logger.info(
        "File uploaded: id=%s name=%s is_temp=%s size=%s",
        db_file.id, original_name, is_temp, len(raw),
    )
    return db_file


def delete_file_record(file_id: int) -> bool:
    """Delete file from DB and filesystem."""
    file = FileRepository.get_by_id(file_id)
    if not file:
        return False

    # Filesystem cleanup
    file_path = _resolve_file_path(file)
    if file_path and file_path.exists():
        try:
            file_path.unlink()
        except OSError as e:
            logger.warning("Could not delete file from disk: %s", e)

    return FileRepository.delete(file_id)


def cleanup_expired_temp_files() -> int:
    """
    Delete expired temp files from DB and filesystem.
    Should be called periodically (e.g., via a scheduled job or before_request).
    Returns count of deleted files.
    """
    expired = FileRepository.get_expired_temp_files()
    deleted = 0
    for f in expired:
        try:
            delete_file_record(f.id)
            deleted += 1
            logger.info("Cleaned up expired temp file: id=%s name=%s", f.id, f.filename)
        except Exception as e:
            logger.error("Error cleaning up file id=%s: %s", f.id, e)
    return deleted


def get_file_path(file: File) -> Optional[Path]:
    return _resolve_file_path(file)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _validate_extension(filename: str):
    if not filename:
        raise FileServiceError("No filename provided.")
    ext = Path(filename).suffix.lower().lstrip(".")
    allowed = current_app.config.get("ALLOWED_EXTENSIONS", {"pdf"})
    if ext not in allowed:
        raise FileServiceError(
            f"File type '.{ext}' is not allowed. Only PDF files are accepted."
        )


def _unique_filename(original: str) -> str:
    """Generate collision-safe filename using UUID prefix."""
    stem = Path(original).stem[:50]  # Truncate long names
    return f"{uuid.uuid4().hex[:8]}_{stem}.pdf"


def _resolve_file_path(file: File) -> Optional[Path]:
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    temp_folder = Path(current_app.config["TEMP_UPLOAD_FOLDER"])

    if file.is_temp:
        p = temp_folder / file.filename
    elif file.category_slug:
        p = upload_folder / file.category_slug / file.filename
    else:
        return None

    return p if p.exists() else None
