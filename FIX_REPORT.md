# FIX_REPORT.md — PDF Search & Management System

## Bug 1: `.env` was never loaded (`wsgi.py`)
### Symptoms
- `python wsgi.py` crashed immediately with `ConfigurationError: SECRET_KEY is not set`
- Config resolution (`get_config()`) expects env vars to be set, but `load_dotenv()` was never called

### Root Cause
The `.env` file existed in the project root with all required values (`SECRET_KEY`, `DATABASE_URL`, etc.), but `wsgi.py` never called `load_dotenv()` before importing the app factory. Without this call, `os.getenv(...)` calls inside `get_config()` returned `None`, triggering the configuration error.

### Fix
Added `from dotenv import load_dotenv; load_dotenv()` at the very top of `wsgi.py` (before any app imports) so that `.env` is loaded into `os.environ` before config resolution runs.

- **File:** `wsgi.py:1-2`

---

## Bug 2: Invalid `body.get("file_id", type=int)` in `search.py`
### Symptoms
- `POST /api/search/file` raised an unhandled `TypeError` when `file_id` was absent or non-integer
- The API returned a 500 instead of a proper 400 validation response

### Root Cause
`search.py` used `body.get("file_id", type=int)` which is **not a valid dict method signature**. The `.get()` method on plain Python `dict` only accepts a key and optional default value — the `type` keyword parameter is from `flask.Request.form.get()` / `flask.Request.args.get()`, not from `dict.get()`. Since `request.get_json()` returns a regular `dict`, the `type=int` was silently ignored (treated as a no-op), and the code path later failed when trying to use the raw value.

### Fix
Replaced with `int(body.get("file_id"))` wrapped in try/except to raise a proper validation error:

```python
try:
    file_id = int(body.get("file_id"))
except (TypeError, ValueError):
    abort(400, description="Invalid or missing file_id")
```

- **File:** `app/api/search.py`

---

## Bug 3: Nginx config referenced undefined `limit_req_zone` (`nginx.conf`)
### Symptoms
- Nginx would fail to start with `nginx: [emerg] unknown variable "limit_req_zone"` or error about undefined zone `login`

### Root Cause
The `nginx.conf` used `limit_req_zone ... zone=login:10m` in a commented-out block that was never active. The rate-limit directive `limit_req zone=login burst=5 nodelay;` on the `/api/auth/login` location block referenced a `login` zone that was never defined because the `limit_req_zone` directive was commented out.

### Fix
Uncommented the `limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;` directive so the zone is properly defined before use.

- **File:** `docker/nginx.conf`

---

## Bug 4: Invalid worker command in `docker-compose.yml`
### Symptoms
- The `worker` service container would crash on startup with `rq-scheduler: command not found` / unrecognized flag errors

### Root Cause
The `docker-compose.yml` specified the command as:
```yaml
command: python -m rq worker --with-scheduler high default low
```
Two issues:
1. `--with-scheduler` requires the `rq-scheduler` package, which is **not** in `requirements.txt`
2. `python -m rq worker` works for RQ 1.x but RQ 2.x recommends using the `rq worker` CLI command directly. Using `python -m rq worker` with RQ 2.x can lead to import module resolution issues.

### Fix
Changed the command to:
```yaml
command: rq worker high default low
```
This removes the `--with-scheduler` flag and uses the direct CLI entrypoint compatible with RQ 2.x.

- **File:** `docker-compose.yml`

---

## Bug 5: No thread safety in `Database` class (`extensions.py`)
### Symptoms
- Background task triggered by `after_upload` calls `Database.execute()` from a worker thread while the main Flask thread may also be executing queries
- SQLite `:memory:` databases use a single shared connection, and concurrent writes from multiple threads cause `SQLITE_BUSY` / database-locked errors

### Root Cause
The `Database` class maintained a single shared in-memory connection (`_shared_conn`) and provided `execute()` / `transaction()` methods that were not protected by any lock. When the RQ worker thread attempted to update FTS index entries concurrently with Flask request handling, SQLite's threading model (which serializes writes per connection) would sometimes raise `sqlite3.OperationalError: database is locked`.

### Fix
Added a `threading.RLock` (reentrant lock) to the `Database` class, acquired around all public `execute()` and `transaction()` calls. A reentrant lock is used because these methods may call each other internally.

- **File:** `app/extensions.py`

---

## Bug 6: Rate limiter had no `reset()` method for test isolation
### Symptoms
- Tests that exercised rate-limited endpoints would fail when run as a suite, but pass individually
- `429 Too Many Requests` errors appeared in test runs because the rate limiter state persisted across tests

### Root Cause
The `_InMemoryRateLimiter` class tracked request counts in a dict keyed by (endpoint, identifier) with the timestamps of recent requests. There was no mechanism to reset this state between tests. Since `pytest` tests share the same Flask app instance, rate limits accumulated across test functions.

### Fix
Added a `reset()` method to `_InMemoryRateLimiter` that clears all tracked history. The test `client()` fixture calls `ext.rate_limiter.reset()` during teardown.

- **File:** `app/extensions.py:77-78`

---

## Bug 7: Test content hash collisions caused duplicate-rejection across tests
### Symptoms
- Tests that created PDFs with identical text content (e.g., `"Installation procedure for the main engine component."`) would fail when run as a suite because the second test's file upload was rejected as a duplicate (409 Conflict)

### Root Cause
The content-based deduplication logic computes an SHA-256 hash of the PDF bytes. The `_minimal_pdf_bytes(text)` helper generates different PDF bytes for different `text` arguments. However, multiple test functions were using the **same** text string, producing identical PDF bytes and thus identical content hashes. The second test to upload would see the existing hash and reject the upload.

### Fix
Each test function now uses a **unique** text string for its PDF content. Category names were also made unique to prevent category slug conflicts.

- **File:** `tests/test_core.py` (multiple locations)

---

## Summary of all files modified

| File | Change |
|------|--------|
| `wsgi.py` | Added `load_dotenv()` call |
| `app/api/search.py` | Fixed `file_id` parsing with proper type conversion + validation |
| `app/extensions.py` | Added `reset()` to `_InMemoryRateLimiter`; added `threading.RLock` to `Database` |
| `docker/nginx.conf` | Uncommented `limit_req_zone $binary_remote_addr zone=login:10m rate=5r/m;` |
| `docker-compose.yml` | Fixed worker command: `rq worker high default low` |
| `tests/test_core.py` | Fixed content hash collisions with unique text per test |
