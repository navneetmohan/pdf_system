"""
Flask extension singletons.
Import from here, not from individual extension packages.
This avoids circular imports and keeps init centralized in create_app().
"""
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from flask import current_app, g


# ── Simple in-process rate limiter (no Redis dependency) ──────────────────────
# Replace with Flask-Limiter + Redis in production for multi-process safety.

class _InMemoryRateLimiter:
    """Thread-safe in-memory rate limiter for single-process deployments."""

    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict = {}
        self._app = None

    def init_app(self, app):
        self._app = app

    def reset(self):
        """Clear all rate limit buckets (useful for tests)."""
        with self._lock:
            self._buckets.clear()

    def check(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return True if request is allowed, False if rate limit exceeded."""
        import time
        now = time.time()
        with self._lock:
            bucket = self._buckets.get(key, {"count": 0, "reset_at": now + window_seconds})
            if now > bucket["reset_at"]:
                bucket = {"count": 0, "reset_at": now + window_seconds}
            if bucket["count"] >= limit:
                self._buckets[key] = bucket
                return False
            bucket["count"] += 1
            self._buckets[key] = bucket
            return True


limiter = _InMemoryRateLimiter()


# ── SQLite database wrapper ────────────────────────────────────────────────────

class Database:
    """
    Thin SQLite wrapper with:
    - Per-request connection (via Flask 'g') for file-based DBs
    - Shared connection for :memory: (testing)
    - FTS5 virtual table support
    - WAL mode for concurrent reads
    - Automatic schema migrations
    - Thread-safe with reentrant lock for shared connections
    """

    _schema_version = 3  # Increment when schema changes
    _shared_conn: sqlite3.Connection = None  # Used only for :memory: in tests
    _lock = threading.RLock()  # Thread safety for shared connections

    def init_app(self, app):
        app.teardown_appcontext(self._teardown)

    def _teardown(self, exception):
        if self._shared_conn is not None:
            return  # Never close shared in-memory connection
        conn = g.pop("_db_conn", None)
        if conn is not None:
            conn.close()

    def _get_connection(self) -> sqlite3.Connection:
        db_path = current_app.config["DATABASE_PATH"]

        # In-memory DB: reuse a single shared connection (SQLite :memory: is per-connection)
        if db_path == ":memory:":
            if self._shared_conn is None:
                self._shared_conn = self._make_connection(db_path)
            return self._shared_conn

        if "_db_conn" not in g:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            g._db_conn = self._make_connection(db_path)
        return g._db_conn

    @staticmethod
    def _make_connection(db_path: str) -> sqlite3.Connection:
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-32000")  # 32 MB cache
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        return self._get_connection()

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            return self._get_connection().execute(sql, params)

    def executemany(self, sql: str, params) -> sqlite3.Cursor:
        with self._lock:
            return self._get_connection().executemany(sql, params)

    def commit(self):
        with self._lock:
            self._get_connection().commit()

    def rollback(self):
        with self._lock:
            self._get_connection().rollback()

    @contextmanager
    def transaction(self) -> Generator:
        with self._lock:
            conn = self._get_connection()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def init_schema(self):
        """Create all tables and FTS5 indexes if they don't exist."""
        conn = self._get_connection()

        conn.executescript("""
            -- ── Users / Admin ──────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT    NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT  NOT NULL,
                is_admin    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                last_login  TEXT
            );

            -- ── Categories ──────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS categories (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL UNIQUE,
                slug        TEXT    NOT NULL UNIQUE,   -- filesystem-safe name
                created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
                created_by  INTEGER REFERENCES users(id)
            );

            -- ── Files ───────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS files (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                filename      TEXT    NOT NULL,
                original_name TEXT    NOT NULL,
                category_id   INTEGER REFERENCES categories(id) ON DELETE CASCADE,
                is_temp       INTEGER NOT NULL DEFAULT 0,
                file_hash     TEXT,                      -- SHA-256 of file bytes
                file_size     INTEGER,
                page_count    INTEGER,
                mime_type     TEXT    NOT NULL DEFAULT 'application/pdf',
                index_status  TEXT    NOT NULL DEFAULT 'pending'
                                CHECK(index_status IN ('pending','processing','indexed','failed')),
                index_error   TEXT,
                expires_at    TEXT,                      -- NULL = permanent
                uploaded_by   INTEGER REFERENCES users(id),
                created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
                UNIQUE(filename, category_id)
            );

            CREATE INDEX IF NOT EXISTS idx_files_category ON files(category_id);
            CREATE INDEX IF NOT EXISTS idx_files_is_temp  ON files(is_temp);
            CREATE INDEX IF NOT EXISTS idx_files_hash     ON files(file_hash);
            CREATE INDEX IF NOT EXISTS idx_files_expires  ON files(expires_at);

            -- ── FTS5 Full-Text Search ────────────────────────────────────────
            -- tokenize=unicode61 handles accented chars, case-folding
            CREATE VIRTUAL TABLE IF NOT EXISTS file_index USING fts5(
                file_id     UNINDEXED,
                page_number UNINDEXED,
                content,
                tokenize = 'unicode61 remove_diacritics 2'
            );

            -- ── Audit Log ────────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER REFERENCES users(id),
                action      TEXT    NOT NULL,
                resource    TEXT,
                ip_address  TEXT,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            -- ── Schema version tracking ──────────────────────────────────────
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER NOT NULL
            );
        """)
        conn.commit()


db = Database()
