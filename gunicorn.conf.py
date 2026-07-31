"""
Gunicorn production configuration.
Docs: https://docs.gunicorn.org/en/stable/configure.html
"""
import multiprocessing
import os

# ── Binding ───────────────────────────────────────────────────────────────────
bind = f"{os.getenv('HOST', '0.0.0.0')}:{os.getenv('PORT', '8000')}"

# ── Workers ───────────────────────────────────────────────────────────────────
# Formula: (2 × CPU cores) + 1. Adjust based on workload.
workers = int(os.getenv("GUNICORN_WORKERS", (2 * multiprocessing.cpu_count()) + 1))
worker_class = "sync"         # PDF I/O is CPU-bound; gthread for async
threads = int(os.getenv("GUNICORN_THREADS", 2))
worker_connections = 1000
timeout = int(os.getenv("GUNICORN_TIMEOUT", 120))
keepalive = 5
max_requests = 1000           # Restart workers after N requests (memory leak mitigation)
max_requests_jitter = 50      # Randomize restarts to avoid thundering herd

# ── Logging ───────────────────────────────────────────────────────────────────
accesslog = os.getenv("GUNICORN_ACCESS_LOG", "-")   # stdout
errorlog = os.getenv("GUNICORN_ERROR_LOG", "-")     # stdout
loglevel = os.getenv("LOG_LEVEL", "info").lower()
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms'

# ── Security ──────────────────────────────────────────────────────────────────
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190

# ── Process naming ────────────────────────────────────────────────────────────
proc_name = "pdf-search"
