"""
File model — data access layer.
All SQL for the 'files' table lives here. No SQL in routes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from ..extensions import db

logger = logging.getLogger(__name__)


@dataclass
class File:
    id: Optional[int]
    filename: str
    original_name: str
    category_id: Optional[int]
    is_temp: bool
    file_hash: Optional[str]
    file_size: Optional[int]
    page_count: Optional[int]
    index_status: str
    expires_at: Optional[str]
    uploaded_by: Optional[int]
    created_at: str
    index_error: Optional[str] = None
    category_slug: Optional[str] = None  # Joined from categories

    @classmethod
    def from_row(cls, row) -> "File":
        return cls(
            id=row["id"],
            filename=row["filename"],
            original_name=row["original_name"],
            category_id=row["category_id"],
            is_temp=bool(row["is_temp"]),
            file_hash=row["file_hash"],
            file_size=row["file_size"],
            page_count=row["page_count"],
            index_status=row["index_status"],
            expires_at=row["expires_at"],
            uploaded_by=row["uploaded_by"],
            created_at=row["created_at"],
            index_error=row["index_error"] if "index_error" in row.keys() else None,
            category_slug=row["category_slug"] if "category_slug" in row.keys() else None,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "original_name": self.original_name,
            "category_id": self.category_id,
            "category_slug": self.category_slug,
            "is_temp": self.is_temp,
            "file_size": self.file_size,
            "page_count": self.page_count,
            "index_status": self.index_status,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }


class FileRepository:
    """All database operations for files."""

    @staticmethod
    def create(
        filename: str,
        original_name: str,
        category_id: Optional[int],
        is_temp: bool,
        file_hash: str,
        file_size: int,
        expires_at: Optional[str],
        uploaded_by: Optional[int],
    ) -> File:
        with db.transaction():
            cursor = db.execute(
                """
                INSERT INTO files (
                    filename, original_name, category_id, is_temp,
                    file_hash, file_size, expires_at, uploaded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (filename, original_name, category_id, int(is_temp),
                 file_hash, file_size, expires_at, uploaded_by),
            )
            file_id = cursor.lastrowid
        return FileRepository.get_by_id(file_id)

    @staticmethod
    def get_by_id(file_id: int) -> Optional[File]:
        row = db.execute(
            """
            SELECT f.*, c.slug as category_slug
            FROM files f
            LEFT JOIN categories c ON c.id = f.category_id
            WHERE f.id = ?
            """,
            (file_id,),
        ).fetchone()
        return File.from_row(row) if row else None

    @staticmethod
    def get_by_filename_and_category(filename: str, category_id: int) -> Optional[File]:
        row = db.execute(
            """
            SELECT f.*, c.slug as category_slug
            FROM files f
            LEFT JOIN categories c ON c.id = f.category_id
            WHERE f.filename = ? AND f.category_id = ?
            """,
            (filename, category_id),
        ).fetchone()
        return File.from_row(row) if row else None

    @staticmethod
    def get_by_hash(file_hash: str) -> Optional[File]:
        """Detect duplicate uploads by content hash."""
        row = db.execute(
            "SELECT f.*, c.slug as category_slug FROM files f LEFT JOIN categories c ON c.id = f.category_id WHERE f.file_hash = ? LIMIT 1",
            (file_hash,),
        ).fetchone()
        return File.from_row(row) if row else None

    @staticmethod
    def list_by_category(
        category_id: int, page: int = 1, page_size: int = 20
    ) -> tuple[list[File], int]:
        """Paginated file listing for a category. Returns (files, total_count)."""
        offset = (page - 1) * page_size
        total = db.execute(
            "SELECT COUNT(*) FROM files WHERE category_id = ? AND is_temp = 0",
            (category_id,),
        ).fetchone()[0]
        rows = db.execute(
            """
            SELECT f.*, c.slug as category_slug
            FROM files f
            LEFT JOIN categories c ON c.id = f.category_id
            WHERE f.category_id = ? AND f.is_temp = 0
            ORDER BY f.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (category_id, page_size, offset),
        ).fetchall()
        return [File.from_row(r) for r in rows], total

    @staticmethod
    def list_temp(page: int = 1, page_size: int = 20) -> tuple[list[File], int]:
        total = db.execute(
            "SELECT COUNT(*) FROM files WHERE is_temp = 1"
        ).fetchone()[0]
        offset = (page - 1) * page_size
        rows = db.execute(
            """
            SELECT f.*, NULL as category_slug
            FROM files f
            WHERE f.is_temp = 1
            ORDER BY f.created_at DESC
            LIMIT ? OFFSET ?
            """,
            (page_size, offset),
        ).fetchall()
        return [File.from_row(r) for r in rows], total

    @staticmethod
    def update_index_status(file_id: int, status: str, page_count: int = None, error: str = None):
        with db.transaction():
            db.execute(
                """
                UPDATE files
                SET index_status = ?,
                    page_count   = COALESCE(?, page_count),
                    index_error  = ?
                WHERE id = ?
                """,
                (status, page_count, error, file_id),
            )

    @staticmethod
    def delete(file_id: int) -> bool:
        with db.transaction():
            # FTS index cleanup
            db.execute("DELETE FROM file_index WHERE file_id = ?", (file_id,))
            cursor = db.execute("DELETE FROM files WHERE id = ?", (file_id,))
        return cursor.rowcount > 0

    @staticmethod
    def get_expired_temp_files() -> list[File]:
        """Return temp files past their expiry time."""
        now = datetime.now(timezone.utc).isoformat()
        rows = db.execute(
            """
            SELECT f.*, NULL as category_slug
            FROM files f
            WHERE f.is_temp = 1 AND f.expires_at IS NOT NULL AND f.expires_at < ?
            """,
            (now,),
        ).fetchall()
        return [File.from_row(r) for r in rows]
