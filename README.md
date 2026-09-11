# PDF Search & Management System

A production-grade Flask application for PDF ingestion, full-text search, and document management. Built with SQLite FTS5, background indexing, and a clean modular architecture.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Architecture](#2-architecture)
3. [Tech Stack Justification](#3-tech-stack-justification)
4. [Project Structure](#4-project-structure)
5. [Setup & Installation](#5-setup--installation)
6. [API Documentation](#6-api-documentation)
7. [Search System Internals](#7-search-system-internals)
8. [Background Processing](#8-background-processing)
9. [Security Model](#9-security-model)
10. [Performance Considerations](#10-performance-considerations)
11. [Deployment](#11-deployment)
12. [Logging & Monitoring](#12-logging--monitoring)
13. [Known Limitations](#13-known-limitations)
14. [Future Improvements](#14-future-improvements)

---

## 1. System Overview

### What It Does

Accepts PDF uploads, extracts text from every page, indexes it into a SQLite FTS5 virtual table, and exposes a search API that returns ranked, snippeted results. The UI serves both authenticated admins (upload/delete/categorize) and public users (search, temporary uploads).

### Key Capabilities

- **Full-text search** with BM25 ranking, phrase queries, prefix queries, and boolean operators via SQLite FTS5
- **Per-page result snippets** with `<mark>`-wrapped highlights, generated at the SQL layer
- **Category-scoped and cross-category search**
- **Background PDF indexing** via RQ workers (falls back to daemon threads without Redis)
- **Dual upload modes**: permanent (admin, categorized) and temporary (public, auto-expiring)
- **Content-hash deduplication** prevents duplicate permanent uploads
- **Magic-byte validation** rejects files that are not real PDFs regardless of extension
- **Structured JSON logging** ready for Loki, Datadog, or CloudWatch ingestion
- **Audit log** tracks every admin action with user ID and IP

### Intended Use Cases

- Internal document libraries (technical manuals, compliance docs, SOPs)
- Self-hosted PDF search for teams that cannot use cloud services
- Foundation for a document intelligence pipeline (add LLM extraction on top)

---

## 2. Architecture

### High-Level Architecture

```
Browser / API Client
        │
        ▼
   Nginx (reverse proxy, static files, rate limiting)
        │
        ▼
   Gunicorn (WSGI, multi-worker)
        │
        ▼
   Flask Application
   ┌─────────────────────────────────────────────────────┐
   │  Blueprints: auth │ files │ search │ categories      │
   │                   │                                  │
   │  Services: FileService │ SearchService │ PDFService  │
   │                   │                                  │
   │  Models: User │ File │ Category (SQLite DAL)         │
   └─────────────────────────────────────────────────────┘
        │                        │
        ▼                        ▼
   SQLite (FTS5)            Filesystem
   data/pdf_system.db       storage/{uploads,temp,cache}
        │
        ▼
   Redis Queue  ──►  RQ Worker Process
                       (PDF extraction + FTS5 indexing)
```

### Component Breakdown

| Component | Role |
|-----------|------|
| `app/__init__.py` | Application factory (`create_app`). Wires blueprints, extensions, error handlers. |
| `app/api/` | Flask blueprints. Thin HTTP layer: parse request → call service → return JSON. No business logic. |
| `app/services/` | All business logic. PDF extraction, search execution, upload pipeline, job dispatch. |
| `app/models/` | Data access layer. All SQL lives here. Returns typed dataclasses, never raw rows. |
| `app/utils/` | Cross-cutting: validation schemas, auth decorators, response helpers, logging setup. |
| `app/extensions.py` | Singleton instances (thread-safe DB wrapper with reentrant lock, rate limiter with test isolation `reset()`). Imported by services/models, initialized in factory. |
| `app/config.py` | Typed configuration with env-var override. `validate()` fails fast on missing secrets. |
| `wsgi.py` | Entrypoint for Gunicorn. Loads `.env` via `load_dotenv()`, seeds admin user on first boot. |

### Data Flow

```
Upload → Processing → Indexing → Search

1. POST /api/files/upload
   └─ FileService.save_uploaded_file()
       ├─ Read bytes from request
       ├─ validate_pdf()          # magic bytes check
       ├─ compute_file_hash()     # SHA-256 for dedup
       ├─ Write to storage/uploads/<category_slug>/<uuid_name>.pdf
       ├─ INSERT INTO files (index_status='pending')
       └─ enqueue_index_job(file_id, path)
               │
               ▼ (async — RQ worker or daemon thread)
2. _index_job_task()
   └─ PDFService.index_file()
       ├─ extract_text_by_page()  # pypdf or PyMuPDF
       ├─ UPDATE files SET index_status='processing'
       ├─ DELETE FROM file_index WHERE file_id=?  # idempotent re-index
       ├─ INSERT INTO file_index (file_id, page_number, content) × N pages
       └─ UPDATE files SET index_status='indexed', page_count=N

3. POST /api/search/file  { file_id, query, page, page_size }
   └─ SearchService.search_in_file()
       ├─ Check index_status == 'indexed' (return 202 if still pending)
       ├─ FTS5 MATCH query with bm25() ranking
       ├─ snippet() for highlighted excerpts
       └─ Paginated JSON response
```

---

## 3. Tech Stack Justification

### Flask

Chosen for simplicity and control. This system does not require Django's ORM, admin, or auth framework — and doesn't benefit from their overhead. Flask's blueprint system gives clean module separation without framework magic. For a PDF-centric workload with straightforward routing, Flask is the right weight class.

### PyMuPDF (fitz) / pypdf

**PyMuPDF** is the primary extraction backend. It uses the MuPDF C library, which handles complex PDF layouts (multi-column, rotated text, embedded fonts) significantly better than pure-Python alternatives. It's 5–10× faster than pypdf on large documents.

**pypdf** is the auto-detected fallback when PyMuPDF is not installed (e.g., in restricted environments). The extraction backend is a single swappable function — switching between them requires changing only `requirements.txt` and nothing else.

PyPDF2 (the original codebase's library) is **not used** — it was deprecated in 2022 and merged into pypdf 3.x.

### SQLite with FTS5

SQLite was chosen over PostgreSQL for these reasons:

1. **Zero infrastructure** — no separate database process, no connection pooling daemon, no network
2. **FTS5 is production-quality** — powers search in large applications (including parts of Firefox and Chrome sync). BM25 ranking, unicode tokenization, and snippet generation are built in
3. **WAL mode** enables concurrent reads with single-writer semantics — adequate for this workload
4. **Single file** — the entire database is `data/pdf_system.db`, trivially backed up with `cp`

The schema is PostgreSQL-compatible in structure (no SQLite-specific types or functions in the models). Migration to PostgreSQL + pg_trgm or a dedicated search engine (Typesense, Meilisearch) is a schema-plus-service swap.

### RQ (Redis Queue)

RQ was chosen over Celery for this workload because:
- PDF indexing jobs are simple, single-function, short-lived (< 5 min)
- RQ has zero configuration for basic use — just `rq worker queue_name`
- No broker serialization format to configure (Celery's `CELERY_TASK_SERIALIZER`, result backends, etc.)
- The dashboard (`rq-dashboard`) is one pip install

The app **gracefully falls back to daemon threads** when Redis is unavailable. This means the system works identically in local development without Redis, with a minor trade-off: thread-based jobs have no retry logic and no visibility.

### Werkzeug Password Hashing

`werkzeug.security.generate_password_hash` uses PBKDF2-SHA256 with 600,000 iterations — above NIST SP 800-132 recommendations. No bcrypt dependency is required (bcrypt requires native compilation). If bcrypt is available in your environment, it's a one-line swap in `UserRepository.create()`.

---

## 4. Project Structure

```
pdf_system/
├── wsgi.py                    # Gunicorn entrypoint, admin seed
├── gunicorn.conf.py           # Worker count, timeouts, logging
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── .env.example
│
├── app/
│   ├── __init__.py            # create_app() factory
│   ├── config.py              # Config classes, fail-fast validation
│   ├── extensions.py          # db (SQLite wrapper), limiter (rate limiter)
│   ├── views.py               # HTML page routes (no logic, just render_template)
│   │
│   ├── api/                   # HTTP layer — one blueprint per domain
│   │   ├── auth.py            # POST /login, POST /logout, GET /me
│   │   ├── files.py           # Upload, list, delete, serve
│   │   ├── search.py          # FTS5 search (file-scoped + category-scoped)
│   │   └── categories.py      # CRUD for categories
│   │
│   ├── services/              # Business logic — no Flask imports
│   │   ├── pdf_service.py     # Extraction, validation, FTS5 indexing
│   │   ├── search_service.py  # Query building, ranking, pagination
│   │   ├── file_service.py    # Upload pipeline, dedup, cleanup
│   │   └── job_service.py     # RQ enqueue + thread fallback
│   │
│   ├── models/                # Data access layer — all SQL here
│   │   ├── file_model.py      # files table + FTS cleanup
│   │   ├── category_model.py  # categories table
│   │   └── user_model.py      # users table + password hashing
│   │
│   └── utils/                 # Cross-cutting concerns
│       ├── auth.py            # Decorators, session helpers, audit log
│       ├── validation.py      # Input validation (no marshmallow dependency)
│       ├── responses.py       # ok(), error(), paginated() helpers
│       └── logging_config.py  # JSON formatter, request logging middleware
│
├── docker/
│   └── nginx.conf             # Reverse proxy, static serving, rate limits
│
├── storage/                   # Created at runtime
│   ├── uploads/<category>/    # Permanent PDFs
│   ├── temp/                  # Temp PDFs (auto-expired)
│   └── cache/                 # Reserved for future use
│
├── data/
│   └── pdf_system.db          # SQLite database (WAL mode)
│
├── logs/
│   └── app.log                # Rotating JSON log
│
├── tests/
│   └── test_core.py           # 34 tests: auth, categories, upload, search, validation
│
└── templates/ static/         # Existing frontend (unchanged)
```

**Design rule:** Services never import from `api/`. Models never import from `services/` or `api/`. This makes services independently testable without HTTP and models testable without business logic.

---

## 5. Setup & Installation

### Prerequisites

- Python 3.11+
- Redis (optional — required for async indexing in production)
- libmupdf-dev (optional — required for PyMuPDF; pypdf works without it)

### Local Development

```bash
# 1. Clone and enter project
git clone <repo> && cd pdf_system

# 2. Create virtual environment
python -m venv .venv && source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env — minimum required fields:
#   SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_hex(32))">
#   ADMIN_PASSWORD=<your chosen password>
#   FLASK_ENV=development

# 5. Run development server
python wsgi.py
# → http://127.0.0.1:5000
# Admin user is seeded from ADMIN_USERNAME/ADMIN_PASSWORD on first start.
```

### Environment Variables

All configuration is driven by environment variables. The app refuses to start if `SECRET_KEY` is missing or shorter than 32 characters.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SECRET_KEY` | **Yes** | — | Flask session signing key. Min 32 chars. |
| `ADMIN_PASSWORD` | **Yes** (first run) | — | Plaintext password used only during initial DB seed. |
| `FLASK_ENV` | No | `production` | `development` disables secure cookie, enables debug logging. |
| `DATABASE_PATH` | No | `data/pdf_system.db` | SQLite file path. Use `:memory:` for tests. |
| `UPLOAD_FOLDER` | No | `storage/uploads` | Permanent PDF storage root. |
| `TEMP_UPLOAD_FOLDER` | No | `storage/temp` | Temporary PDF storage. |
| `MAX_UPLOAD_BYTES` | No | `33554432` (32 MB) | Maximum upload size. |
| `TEMP_FILE_LIFETIME_HOURS` | No | `14` | Hours before temp files are purged. |
| `REDIS_URL` | No | — | If unset, indexing runs in daemon threads. |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LOG_JSON` | No | `1` | Set to `0` for human-readable logs in dev. |
| `ADMIN_USERNAME` | No | `admin` | Username for initial admin seed. |

### Running with Redis (recommended for production)

```bash
# Terminal 1 — Application
FLASK_ENV=production python -m gunicorn wsgi:app --config gunicorn.conf.py

# Terminal 2 — RQ Worker
rq worker pdf_indexing --url redis://localhost:6379/0
```

---

## 6. API Documentation

All endpoints return JSON. Success responses include `"success": true`. Error responses include `"success": false` and `"error": "<message>"`.

### Authentication

#### `POST /api/auth/login`

```json
// Request
{ "username": "admin", "password": "yourpassword" }

// Response 200
{ "success": true, "message": "Login successful.", "user": { "id": 1, "username": "admin", "is_admin": true } }

// Response 401
{ "success": false, "error": "Invalid credentials." }
```

Rate limited to **10 attempts per minute per IP**. Returns 429 when exceeded.

#### `POST /api/auth/logout`

```json
// Response 200
{ "success": true, "message": "Logged out." }
```

#### `GET /api/auth/me`

```json
// Response 200 (authenticated)
{ "success": true, "authenticated": true, "user": { "id": 1, "username": "admin", "is_admin": true } }

// Response 200 (not authenticated)
{ "success": true, "authenticated": false, "is_admin": false }
```

---

### Categories

#### `GET /api/categories`

```json
// Response 200
{
  "success": true,
  "categories": [
    { "id": 1, "name": "Manuals", "slug": "manuals", "file_count": 12, "created_at": "2024-01-15T10:00:00" }
  ]
}
```

#### `POST /api/categories` *(admin)*

```json
// Request
{ "name": "Technical Specs" }

// Response 201
{ "success": true, "category": { "id": 2, "name": "Technical Specs", "slug": "technical_specs", ... } }

// Response 409 — already exists
{ "success": false, "error": "Category 'Technical Specs' already exists." }
```

#### `DELETE /api/categories/<id>` *(admin)*

Deletes category, all its files (DB records and filesystem), and their FTS index entries.

```bash
curl -X DELETE http://localhost:8000/api/categories/2 -b session_cookie
# 200: { "success": true, "message": "Category 'Technical Specs' deleted." }
```

---

### Files

#### `GET /api/files?category_id=<int>&page=1&page_size=20`

```json
// Response 200
{
  "success": true,
  "data": [
    {
      "id": 5,
      "filename": "a1b2c3d4_manual.pdf",
      "original_name": "user_manual.pdf",
      "category_id": 1,
      "category_slug": "manuals",
      "file_size": 204800,
      "page_count": 48,
      "index_status": "indexed",
      "created_at": "2024-01-15T10:05:00"
    }
  ],
  "pagination": { "total": 12, "page": 1, "page_size": 20, "total_pages": 1, "has_more": false }
}
```

#### `POST /api/files/upload` *(admin)*

```bash
curl -X POST http://localhost:8000/api/files/upload \
  -b session_cookie \
  -F "pdf=@/path/to/document.pdf" \
  -F "category_id=1"

# Response 201
{
  "success": true,
  "message": "File uploaded. Indexing in progress.",
  "file": { "id": 6, "index_status": "pending", ... }
}
```

#### `POST /api/files/temp-upload`

Public endpoint. Rate limited to **5 uploads per minute per IP**.

```bash
curl -X POST http://localhost:8000/api/files/temp-upload \
  -F "pdf=@/path/to/document.pdf"

# Response 201
{
  "success": true,
  "file": { "id": 7, "is_temp": true, "expires_at": "2024-01-16T00:05:00+00:00", ... }
}
```

#### `GET /api/files/<id>/status`

Poll this after upload to know when indexing is complete.

```json
// index_status values: "pending" | "processing" | "indexed" | "failed"
{
  "success": true,
  "file_id": 6,
  "index_status": "indexed",
  "page_count": 48,
  "index_error": null
}
```

#### `GET /api/files/<id>/serve`

Streams the raw PDF bytes. Sets `Content-Type: application/pdf`. Safe to embed in `<iframe>`.

#### `DELETE /api/files/<id>` *(admin)*

Removes DB record, FTS index entries, and filesystem file.

---

### Search

#### `POST /api/search/file`

Search within a single file. File must be in `index_status: "indexed"`.

```json
// Request
{
  "file_id": 6,
  "query": "installation procedure",
  "page": 1,
  "page_size": 20
}

// Response 200
{
  "success": true,
  "query": "installation procedure",
  "total": 3,
  "page": 1,
  "page_size": 20,
  "has_more": false,
  "results": [
    {
      "file_id": 6,
      "filename": "a1b2c3d4_manual.pdf",
      "original_name": "user_manual.pdf",
      "category": "manuals",
      "page": 12,
      "snippet": "...follow the <mark>installation</mark> <mark>procedure</mark> as described...",
      "rank": -1.2345
    }
  ]
}

// Response 202 — still indexing
{ "success": false, "error": "File is still being indexed.", "details": { "index_status": "processing" } }
```

**Supported query syntax:**

| Syntax | Example | Matches |
|--------|---------|---------|
| Simple | `flask` | pages containing "flask" |
| Phrase | `"web framework"` | exact phrase |
| Prefix | `install*` | "install", "installation", "installed" |
| AND | `flask AND sqlalchemy` | both terms |
| OR | `flask OR django` | either term |
| NOT | `python NOT java` | python without java |

#### `POST /api/search/category`

Same interface as `/api/search/file` but uses `category_id` instead of `file_id`. Searches across all indexed files in the category.

```json
// Request
{ "category_id": 1, "query": "safety warning", "page": 1, "page_size": 10 }
```

---

## 7. Search System Internals

### How Indexing Works

After a file is uploaded, `index_file()` in `pdf_service.py` runs (in a background job):

1. Opens the PDF with PyMuPDF or pypdf
2. Extracts text per page as a `list[str]`
3. Marks the file as `processing` in the DB
4. Deletes any existing `file_index` rows for this `file_id` (idempotent re-index)
5. Inserts one row per non-blank page into the `file_index` FTS5 virtual table
6. Updates `files.index_status = 'indexed'` and `files.page_count`

The FTS5 table schema:
```sql
CREATE VIRTUAL TABLE file_index USING fts5(
    file_id     UNINDEXED,   -- not tokenized, used for filtering
    page_number UNINDEXED,   -- not tokenized, returned in results
    content,                 -- full page text, tokenized
    tokenize = 'unicode61 remove_diacritics 2'
);
```

`unicode61` handles accented characters and unicode punctuation. `remove_diacritics 2` normalizes accented chars (e.g., "résumé" matches "resume").

### How Search Executes

```sql
SELECT
    fi.file_id,
    fi.page_number,
    snippet(file_index, 2, '<mark>', '</mark>', '...', 20) AS snippet,
    bm25(file_index, 0, 0, 10) AS rank
FROM file_index fi
JOIN files f ON f.id = fi.file_id
WHERE fi.file_id IN (?, ?, ...)
  AND fi.content MATCH ?
ORDER BY rank   -- bm25 returns negative values; lower = more relevant
LIMIT ? OFFSET ?
```

- `snippet()` is a built-in FTS5 function that extracts context around matches
- `bm25()` weights are `(0, 0, 10)` — only the `content` column (index 2) contributes to ranking
- The `file_id IN (...)` filter enables both single-file and multi-file (category) search with the same query

### Limitations

- **No cross-file ranking normalization**: Results from category search rank pages relative to each other within the FTS index. A short page with one exact match ranks higher than a long page with many contextual mentions.
- **No stemming**: The `unicode61` tokenizer does not stem. "running" does not match "run". Use prefix queries (`run*`) as a workaround.
- **Scanned PDFs**: If a PDF contains only image pages (no embedded text layer), extraction returns empty strings and the file will have `page_count > 0` but no searchable content. OCR is not built in.
- **No relevance feedback or query expansion**.
- **Single writer bottleneck**: SQLite WAL allows concurrent reads, but all writes (indexing) are serialized. Concurrent uploads will queue at the DB level.

---

## 8. Background Processing

### What Runs Asynchronously

- **PDF text extraction** — blocking I/O + CPU, unsuitable for a request thread
- **FTS5 indexing** — batch inserts, can be slow for large documents

### Job Flow

```
HTTP upload handler
    │
    ├─ Saves file to disk
    ├─ Creates DB record (index_status='pending')
    └─ enqueue_index_job(file_id, file_path)
            │
            ├─ [Redis available] → rq.Queue.enqueue(_index_job_task, ...)
            │       │
            │       └─ RQ Worker process picks up job
            │               └─ _index_job_task() → index_file()
            │
            └─ [No Redis] → threading.Thread(target=worker, daemon=True)
                            └─ worker() runs with app.app_context()
                                    └─ _index_job_task() → index_file()
```

### Failure Handling

**RQ path:**
- Failed jobs move to the `FailedJobRegistry`. Inspect with `rq info` or `rq-dashboard`
- Jobs set `failure_ttl=86400` — failed job metadata persists for 24 hours
- No automatic retry is configured. Add `retry=rq.Retry(max=3, interval=60)` to `queue.enqueue()` for retry logic
- `files.index_status` is set to `'failed'` with `files.index_error` containing the exception string

**Thread path:**
- Fire-and-forget. Exceptions are logged at ERROR level with `file_id`
- `files.index_status` is updated to `'failed'` by the exception handler in `pdf_service.index_file()`
- No retry. Re-upload the file to trigger a new index attempt

**Monitoring index failures:**
```sql
SELECT id, original_name, index_error, created_at
FROM files
WHERE index_status = 'failed'
ORDER BY created_at DESC;
```

---

## 9. Security Model

### Authentication

Session-based authentication using Flask's signed cookie (HMAC-SHA1 via `itsdangerous`). The session stores `user_id`, `username`, and `is_admin`. The session is invalidated server-side by calling `session.clear()` on logout.

No JWT, no OAuth. For an internal tool with a single admin account, session cookies are appropriate and avoid token refresh complexity.

### Password Storage

Passwords are hashed with PBKDF2-SHA256 at 600,000 iterations using `werkzeug.security.generate_password_hash`. Plaintext passwords are **never stored** in the database. The `ADMIN_PASSWORD` env var is read only during initial DB seed and only to produce the hash.

### File Upload Protections

| Threat | Mitigation |
|--------|------------|
| Extension spoofing (`.php` → `.pdf`) | Magic byte check: first 5 bytes must be `%PDF-` |
| Path traversal | `werkzeug.utils.secure_filename` strips `../` sequences; UUID prefix added |
| Duplicate uploads | SHA-256 hash computed before write; rejected if hash already in DB |
| Oversized uploads | `MAX_CONTENT_LENGTH` enforced by Flask (returns 413 before reading body) |
| Executable uploads | Only `.pdf` extension allowed (enforced independently of magic byte check) |
| Directory traversal via category | Categories use `slugify()` output as directory names; user input never used as a path directly |

### Rate Limiting

| Endpoint | Limit | Scope |
|----------|-------|-------|
| `POST /api/auth/login` | 10/minute | Per IP |
| `POST /api/files/temp-upload` | 5/minute | Per IP |

The in-process rate limiter (`_InMemoryRateLimiter`) is thread-safe but **not process-safe**. With multiple Gunicorn workers, each worker maintains its own counter — effective limit becomes `N × configured_limit`. Replace with Flask-Limiter + Redis backend for accurate multi-worker rate limiting.

### Known Risks

1. **Session fixation**: Flask's signed cookies mitigate this, but `session.regenerate()` is not called on login. Low risk for internal tools; worth addressing for public deployments.
2. **No CSRF protection**: POST endpoints accept JSON bodies from any origin if session cookie is present. Add `Flask-WTF` or check `Origin`/`Referer` headers if the frontend is served from a different domain.
3. **Temp upload public**: Anyone can upload PDFs and search them for 14 hours. No authentication required. Consider adding a pre-shared token for environments where this is unacceptable.
4. **Admin seeding**: `ADMIN_PASSWORD` exists in environment for the lifetime of the process. Mitigate by using a secrets manager (Vault, AWS SSM) and unsetting after seed.
5. **FTS query injection**: User input is passed to `FTS5 MATCH`. Light sanitization removes `--` and `;`. FTS5 syntax errors raise Python exceptions (caught and returned as 400). Full SQL injection is not possible because query parameters are never interpolated into the SQL string.

---

## 10. Performance Considerations

### Caching Strategy

The previous codebase used `lru_cache` on extracted text — a function-level in-process cache keyed on filename only. This was replaced with the FTS5 index as the durable store.

For repeated identical search queries (same `file_id`, same `query`), caching at the Flask layer is straightforward:

```python
# Add to search endpoint (example using functools.lru_cache won't work across requests)
# Use Flask-Caching + Redis for distributed cache:
#   @cache.cached(timeout=300, key_prefix=make_cache_key)
```

No application-layer query cache is implemented currently. SQLite's internal page cache (`PRAGMA cache_size=-32000`) handles repeated reads at the DB layer.

### Bottlenecks

| Bottleneck | Impact | Mitigation |
|------------|--------|------------|
| PDF text extraction | Blocks worker threads (sync) | Background jobs (already implemented) |
| FTS5 write lock | Serializes all indexing | Acceptable for < ~100 concurrent uploads/hour |
| SQLite single writer | All writes queue behind each other | Acceptable; upgrade to PostgreSQL if write throughput is a constraint |
| Large PDF files | Extraction memory spike (full file loaded) | Stream pages with PyMuPDF; pypdf loads full file |
| Nginx static file serving | Near-zero — Nginx is highly efficient for static assets | N/A |

### Scaling Limitations

This system is designed for **single-server deployment**. The SQLite + local filesystem architecture means you cannot horizontally scale without changing both:

1. **Database**: Replace SQLite with PostgreSQL + `pg_trgm` or a dedicated search engine
2. **File storage**: Replace local filesystem with S3/GCS/Azure Blob

Vertical scaling (bigger machine) works well. A single server with 8 cores, 16 GB RAM, and SSD storage can comfortably handle hundreds of concurrent search requests and dozens of concurrent uploads.

---

## 11. Deployment

### Docker (recommended)

```bash
# 1. Copy and configure environment
cp .env.example .env
# Fill in SECRET_KEY and ADMIN_PASSWORD

# 2. Build and start all services
docker-compose up -d

# Services started:
#   nginx   → port 80 (reverse proxy)
#   app     → port 8000 (internal, Gunicorn)
#   worker  → RQ worker process
#   redis   → port 6379 (internal)

# 3. Check logs
docker-compose logs -f app
docker-compose logs -f worker

# 4. Check worker queue
docker-compose exec redis redis-cli llen rq:queue:pdf_indexing
```

### Docker Volumes

| Volume | Contents | Backup priority |
|--------|----------|-----------------|
| `pdf_storage` | All uploaded PDFs | **Critical** |
| `pdf_data` | SQLite database | **Critical** |
| `redis_data` | Job queue state | Low — jobs are idempotent |
| `pdf_logs` | Application logs | Medium |

Backup `pdf_storage` and `pdf_data` together. The database references files by stored filename — they must stay in sync.

### Production Stack

```
Internet → Nginx (TLS termination, static files, rate limiting)
              → Gunicorn (4 workers, sync, 120s timeout)
                  → Flask app
                      → SQLite (WAL mode, 32MB cache)
                      → Filesystem (storage/)
              → RQ Worker (1 process, pdf_indexing queue)
              → Redis (job queue, optional rate limit backend)
```

### Gunicorn Worker Count

The default formula `(2 × CPU) + 1` is for CPU-bound workloads. PDF extraction is CPU-intensive during indexing, but search queries are fast SQL reads. Tune based on profiling:

```bash
# Start with the formula
GUNICORN_WORKERS=5  # on a 2-core machine

# If search latency is the bottleneck (many concurrent readers), increase workers
# If indexing is the bottleneck, increase RQ worker count instead
```

### TLS / HTTPS

The provided Nginx config handles HTTP only. For HTTPS:

```bash
# Using Certbot (Let's Encrypt)
certbot --nginx -d yourdomain.com

# Or add to nginx.conf:
# listen 443 ssl;
# ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
# ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;
```

Set `SESSION_COOKIE_SECURE=1` when serving over HTTPS (default in production config).

---

## 12. Logging & Monitoring

### Logging Approach

All log output is structured JSON in production:

```json
{
  "ts": "2024-01-15T10:05:23",
  "level": "INFO",
  "logger": "request",
  "msg": "POST /api/files/upload 201",
  "method": "POST",
  "path": "/api/files/upload",
  "status": 201,
  "duration_ms": 142.5,
  "ip": "10.0.1.5",
  "user_agent": "Mozilla/5.0..."
}
```

Every request is logged at INFO level with method, path, status, and duration. Business events (login, upload, delete) are logged at INFO with structured fields. Errors include full tracebacks.

In development (`LOG_JSON=0`), output is human-readable:

```
2024-01-15 10:05:23 [INFO] request: POST /api/files/upload 201
```

### Log Ingestion

The JSON format is directly ingestible by:
- **Loki** (via Promtail or Alloy): `json` pipeline stage, label by `level` and `logger`
- **Datadog**: `json` log format, automatic field extraction
- **CloudWatch Logs**: JSON structured logging with metric filters
- **Elasticsearch**: Logstash JSON input

### Key Log Queries (Loki syntax)

```logql
# All errors in last hour
{app="pdf-search"} | json | level="ERROR"

# Slow requests (> 500ms)
{app="pdf-search"} | json | duration_ms > 500

# Failed login attempts
{app="pdf-search"} | json | logger="app.api.auth" | msg =~ "Failed login"

# Index failures
{app="pdf-search"} | json | logger="app.services.pdf_service" | level="ERROR"
```

### Debugging Tips

**File not appearing in search results:**
```sql
SELECT id, filename, index_status, page_count, index_error FROM files WHERE id = ?;
-- index_status='failed' → check index_error column
-- index_status='pending' → worker not running or Redis down
-- index_status='indexed', page_count=0 → PDF has no extractable text (scanned image PDF)
```

**RQ worker not processing:**
```bash
rq info --url redis://localhost:6379/0
# Check: workers alive, failed job registry
rq failed --url redis://localhost:6379/0
```

**SQLite database locked:**
```bash
# Check for open connections
fuser data/pdf_system.db
# WAL mode should prevent this; if it persists, check for crashed workers
```

---

## 13. Known Limitations

1. **Scanned PDFs are not searchable.** If a PDF contains only images with no embedded text layer, extraction returns empty strings. No OCR pipeline is implemented. Workaround: pre-process with `ocrmypdf` before upload.

2. **No stemming.** The FTS5 `unicode61` tokenizer does not perform linguistic stemming. "installed" does not match "install". Use prefix queries (`install*`) or add a custom FTS5 tokenizer.

3. **Rate limiter is not multi-process safe.** The in-memory rate limiter maintains counters per Gunicorn worker. With 4 workers, the effective login limit is 40 attempts/minute, not 10. Replace with Flask-Limiter + Redis for correctness.

4. **No re-index trigger.** If a file fails indexing, there is no UI or API endpoint to trigger a retry. The workaround is to delete and re-upload the file.

5. **Category names are not renameable.** The category slug (used as the filesystem directory name) is computed from the name at creation time. Renaming a category would require moving files on disk. This operation is not implemented.

6. **No pagination on category search.** Cross-category search paginates at the page level, not the file level. There is no way to retrieve "all matches in file X within category Y" without knowing the file ID.

7. **SQLite write contention.** Simultaneous uploads from multiple users will cause index jobs to queue at the SQLite writer lock. For > 10 concurrent uploads, PostgreSQL is recommended.

8. **Temp file cleanup is lazy.** Cleanup runs on every request to the `/api/files` blueprint, not on a schedule. In a heavily loaded system, many requests to other blueprints will not trigger cleanup. A cron job calling `cleanup_expired_temp_files()` on a schedule is more reliable.

9. **No file versioning.** Uploading a file with the same content hash is rejected as a duplicate. There is no concept of file versions or revision history.

10. **No CSRF protection.** State-mutating API endpoints (POST, DELETE) do not validate CSRF tokens. This is acceptable for API-first clients but is a risk if the frontend is extended to use form submissions from untrusted origins.

---

## 14. Future Improvements

Concrete, prioritized roadmap — not vague aspirations.

### High Priority

1. **OCR pipeline integration** (`ocrmypdf` as a post-upload processing step)  
   Trigger: `index_status='indexed'` + `page_count > 0` + all pages have < 50 chars of text → re-process with OCR

2. **Accurate multi-process rate limiting**  
   Replace `_InMemoryRateLimiter` with `Flask-Limiter` + Redis backend. One-day effort.

3. **Re-index endpoint**  
   `POST /api/files/<id>/reindex` — admin only, resets `index_status='pending'`, enqueues job

4. **Category rename**  
   `PATCH /api/categories/<id>` — update name, re-slug, move filesystem directory, update DB

### Medium Priority

5. **FTS5 stemming tokenizer**  
   Build a custom FTS5 tokenizer using the Porter stemmer. Allows "run" to match "running", "runner"

6. **Search result caching**  
   Add Flask-Caching + Redis. Cache search results for 5 minutes keyed by `(file_id, query, page)`. Invalidate on re-index.

7. **Scheduled temp file cleanup**  
   Add an RQ scheduled job (`rq-scheduler`) running `cleanup_expired_temp_files()` every 30 minutes

8. **Bulk upload endpoint**  
   `POST /api/files/bulk-upload` — accept a ZIP of PDFs, extract and queue each one

9. **Search analytics**  
   Log every search query to a `search_log` table for relevance tuning and usage analytics

### Lower Priority

10. **PostgreSQL migration path**  
    Extract FTS5-specific SQL into a provider abstraction. Implement `PostgreSQLProvider` using `tsvector` + `ts_rank`. No model or service changes needed.

11. **WebSocket index status updates**  
    Replace polling `GET /api/files/<id>/status` with a WebSocket event when indexing completes

12. **User management API**  
    `POST /api/users`, `DELETE /api/users/<id>` — currently only one admin account is supported

13. **PDF preview thumbnails**  
    Use PyMuPDF's `page.get_pixmap()` to generate page thumbnails on upload. Store in `storage/cache/<file_id>/page_<n>.jpg`

14. **OpenAPI spec**  
    Generate an `openapi.yaml` from route definitions. Enables auto-generated client SDKs and Swagger UI

---

### Bug Fixes Applied

During the initial audit and testing phase, the following bugs were identified and fixed:

| # | Issue | File | Fix |
|---|-------|------|-----|
| 1 | App crashed on startup — `.env` never loaded | `wsgi.py` | Added `load_dotenv()` before any app imports |
| 2 | `POST /api/search/file` returned 500 on missing/invalid `file_id` | `app/api/search.py` | Replaced invalid `dict.get(..., type=int)` with `int()` + try/except |
| 3 | Nginx failed to start — `limit_req_zone` was commented out | `docker/nginx.conf` | Uncommented the `limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;` directive |
| 4 | Worker container crashed — `--with-scheduler` flag not in requirements | `docker-compose.yml` | Changed `python -m rq worker --with-scheduler` to `rq worker high default low` |
| 5 | SQLite `database is locked` errors from concurrent RQ worker threads | `app/extensions.py` | Added `threading.RLock` around all `Database.execute()` / `transaction()` calls |
| 6 | Rate limiter state leaked across tests causing false 429 failures | `app/extensions.py` | Added `reset()` method to `_InMemoryRateLimiter`; called from test `client()` teardown |
| 7 | Content-hash deduplication caused test collisions | `tests/test_core.py` | Each test now uses unique PDF text content and unique category names |

### Test Coverage

The test suite was expanded from 10 tests (one file) to **34 tests** covering all core workflows:

| Area | Tests |
|------|-------|
| Schema & data layer | `test_schema_created`, `test_fts_sanitizer`, `test_password_hash` |
| Authentication | login, logout, me, invalid credentials, unauthenticated rejection |
| Categories | create (admin required), list, duplicate rejection, delete cascade |
| File upload | admin-only, temp upload, invalid extension, invalid magic bytes, full workflow, duplicate rejection |
| File management | list all, list by category, serve, status check (not found), delete |
| Search | file-scoped success/phrase/pagination, category-scoped, validation (empty query), still-indexing, response shape |
| Edge cases | unauthenticated requests rejected on all protected endpoints |

## Appendix: What Changed from the Original Codebase

| Concern | Original | Redesigned |
|---------|----------|-----------|
| Architecture | Single 700-line `app.py` | Modular: api/, services/, models/, utils/ |
| Search | Python string scan (`str.startswith`) | SQLite FTS5 with BM25 ranking + snippets |
| PDF extraction | PyPDF2 (deprecated) | pypdf (maintained) / PyMuPDF (preferred) |
| Database | `categories.json` flat file | SQLite with typed schema, FK constraints, WAL |
| Password storage | Plaintext in `en_vars.env` | PBKDF2-SHA256 (600k iterations) |
| Credentials | Hardcoded `Aadmin@itc42` in config defaults | Env vars only; startup fails without `SECRET_KEY` |
| File validation | Extension check only (`.endswith('.pdf')`) | Magic byte check + extension + size limit |
| Background jobs | None (extraction blocks request thread) | RQ workers with thread fallback |
| Rate limiting | None | Per-IP limits on login and temp-upload |
| Logging | `logging.basicConfig` to file | Structured JSON, per-request middleware, audit log |
| Cache keys | MD5 of filename | SHA-256 of file content (collision-resistant, dedup-aware) |
| Input validation | Ad-hoc `if not field` checks | Centralized validation functions with typed error dicts |
| Error responses | Inconsistent (some dicts, some strings) | Standardized `{ success, error, details }` shape |
| Deduplication | None | SHA-256 content hash; duplicate permanent uploads rejected |
| Pagination | Applied in Python after full extraction | SQL `LIMIT/OFFSET` at query time |
