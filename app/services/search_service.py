"""
Search Service — FTS5-powered full-text search.

Query flow:
  1. Caller provides file_id(s) + query string
  2. FTS5 MATCH query executes against file_index virtual table
  3. Results include page number, snippet, and relevance rank
  4. Pagination applied at SQL layer (not in Python)

FTS5 features used:
  - bm25() ranking (built-in, better than rank for short docs)
  - snippet() for highlighted excerpts
  - Phrase queries ("exact phrase"), prefix queries (word*), OR/AND/NOT
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..extensions import db

logger = logging.getLogger(__name__)

_SNIPPET_TOKENS = 20   # Words around each match in snippet
_SNIPPET_HL_START = "<mark>"
_SNIPPET_HL_END = "</mark>"


@dataclass
class SearchResult:
    file_id: int
    filename: str
    original_name: str
    category_slug: Optional[str]
    page_number: int
    snippet: str
    rank: float

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id,
            "filename": self.filename,
            "original_name": self.original_name,
            "category": self.category_slug,
            "page": self.page_number,
            "snippet": self.snippet,
            "rank": round(self.rank, 4),
        }


@dataclass
class SearchResponse:
    results: list[SearchResult]
    total: int
    page: int
    page_size: int
    query: str
    has_more: bool

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "has_more": self.has_more,
            "results": [r.to_dict() for r in self.results],
        }


def search_in_file(
    file_id: int,
    query: str,
    page: int = 1,
    page_size: int = 20,
) -> SearchResponse:
    """
    Search within a single file using FTS5.
    Returns ranked, paginated results with snippets.
    """
    return _execute_search(
        query=query,
        file_ids=[file_id],
        page=page,
        page_size=page_size,
    )


def search_across_category(
    category_id: int,
    query: str,
    page: int = 1,
    page_size: int = 20,
) -> SearchResponse:
    """Search across all indexed files in a category."""
    rows = db.execute(
        "SELECT id FROM files WHERE category_id = ? AND index_status = 'indexed'",
        (category_id,),
    ).fetchall()
    file_ids = [r["id"] for r in rows]
    if not file_ids:
        return SearchResponse([], 0, page, page_size, query, False)
    return _execute_search(query=query, file_ids=file_ids, page=page, page_size=page_size)


def _execute_search(
    query: str,
    file_ids: list[int],
    page: int,
    page_size: int,
) -> SearchResponse:
    """
    Core FTS5 search. Raises ValueError on bad query syntax.

    FTS5 MATCH syntax supported by callers:
      - Simple:   python
      - Phrase:   "machine learning"
      - Prefix:   pyth*
      - Boolean:  python AND flask NOT django
    """
    if not query or not query.strip():
        raise ValueError("Search query cannot be empty.")

    if not file_ids:
        return SearchResponse([], 0, page, page_size, query, False)

    # Sanitize: FTS5 is generally safe but strip dangerous characters
    safe_query = _sanitize_fts_query(query)

    # Build IN clause placeholders
    placeholders = ",".join("?" * len(file_ids))

    # Count total matching pages (for pagination metadata)
    try:
        total = db.execute(
            f"""
            SELECT COUNT(*)
            FROM file_index fi
            WHERE fi.file_id IN ({placeholders})
              AND fi.content MATCH ?
            """,
            (*file_ids, safe_query),
        ).fetchone()[0]
    except Exception as e:
        # FTS5 syntax errors surface here
        logger.warning("FTS5 query error for query=%r: %s", query, e)
        raise ValueError(f"Invalid search query syntax: {e}") from e

    if total == 0:
        return SearchResponse([], 0, page, page_size, query, False)

    offset = (page - 1) * page_size

    rows = db.execute(
        f"""
        SELECT
            fi.file_id,
            fi.page_number,
            snippet(file_index, 2, ?, ?, '...', ?) AS snippet,
            bm25(file_index, 0, 0, 10) AS rank,
            f.filename,
            f.original_name,
            c.slug AS category_slug
        FROM file_index fi
        JOIN files f ON f.id = fi.file_id
        LEFT JOIN categories c ON c.id = f.category_id
        WHERE fi.file_id IN ({placeholders})
          AND fi.content MATCH ?
        ORDER BY rank
        LIMIT ? OFFSET ?
        """,
        (
            _SNIPPET_HL_START, _SNIPPET_HL_END, _SNIPPET_TOKENS,
            *file_ids, safe_query,
            page_size, offset,
        ),
    ).fetchall()

    results = [
        SearchResult(
            file_id=r["file_id"],
            filename=r["filename"],
            original_name=r["original_name"],
            category_slug=r["category_slug"],
            page_number=r["page_number"],
            snippet=r["snippet"] or "",
            rank=r["rank"],
        )
        for r in rows
    ]

    return SearchResponse(
        results=results,
        total=total,
        page=page,
        page_size=page_size,
        query=query,
        has_more=(offset + len(results)) < total,
    )


def _sanitize_fts_query(query: str) -> str:
    """
    Light sanitization for FTS5 queries.
    Removes SQL injection vectors while preserving FTS5 operators.
    """
    # Strip SQL comment sequences
    query = query.replace("--", " ").replace(";", " ")
    # Strip null bytes
    query = query.replace("\x00", "")
    return query.strip()
