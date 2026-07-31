"""Category model — data access layer."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from ..extensions import db


def slugify(name: str) -> str:
    """Convert a category name to a safe filesystem/URL slug."""
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "_", slug)
    slug = slug.strip("_")
    return slug or "unnamed"


@dataclass
class Category:
    id: Optional[int]
    name: str
    slug: str
    created_at: str
    created_by: Optional[int]
    file_count: int = 0

    @classmethod
    def from_row(cls, row) -> "Category":
        return cls(
            id=row["id"],
            name=row["name"],
            slug=row["slug"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            file_count=row["file_count"] if "file_count" in row.keys() else 0,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "created_at": self.created_at,
            "file_count": self.file_count,
        }


class CategoryRepository:

    @staticmethod
    def create(name: str, created_by: Optional[int] = None) -> Category:
        slug = slugify(name)
        with db.transaction():
            cursor = db.execute(
                "INSERT INTO categories (name, slug, created_by) VALUES (?, ?, ?)",
                (name, slug, created_by),
            )
            cat_id = cursor.lastrowid
        return CategoryRepository.get_by_id(cat_id)

    @staticmethod
    def get_by_id(cat_id: int) -> Optional[Category]:
        row = db.execute(
            """
            SELECT c.*, COUNT(f.id) as file_count
            FROM categories c
            LEFT JOIN files f ON f.category_id = c.id AND f.is_temp = 0
            WHERE c.id = ?
            GROUP BY c.id
            """,
            (cat_id,),
        ).fetchone()
        return Category.from_row(row) if row else None

    @staticmethod
    def get_by_slug(slug: str) -> Optional[Category]:
        row = db.execute(
            """
            SELECT c.*, COUNT(f.id) as file_count
            FROM categories c
            LEFT JOIN files f ON f.category_id = c.id AND f.is_temp = 0
            WHERE c.slug = ?
            GROUP BY c.id
            """,
            (slug,),
        ).fetchone()
        return Category.from_row(row) if row else None

    @staticmethod
    def get_by_name(name: str) -> Optional[Category]:
        row = db.execute(
            "SELECT c.*, 0 as file_count FROM categories c WHERE c.name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        return Category.from_row(row) if row else None

    @staticmethod
    def list_all() -> list[Category]:
        rows = db.execute(
            """
            SELECT c.*, COUNT(f.id) as file_count
            FROM categories c
            LEFT JOIN files f ON f.category_id = c.id AND f.is_temp = 0
            GROUP BY c.id
            ORDER BY c.name
            """
        ).fetchall()
        return [Category.from_row(r) for r in rows]

    @staticmethod
    def delete(cat_id: int) -> bool:
        with db.transaction():
            # Files in this category are deleted via ON DELETE CASCADE
            # But we must clean up FTS index manually
            db.execute(
                "DELETE FROM file_index WHERE file_id IN (SELECT id FROM files WHERE category_id = ?)",
                (cat_id,),
            )
            cursor = db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
        return cursor.rowcount > 0
