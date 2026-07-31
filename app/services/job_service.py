"""
Job Service — background task queue.

Primary: RQ (Redis Queue) — async, reliable, inspectable
Fallback: In-process threading — zero dependencies, works without Redis

The fallback is intentional for local dev / simple deployments.
In production, set REDIS_URL to enable proper async processing.

Usage:
    enqueue_index_job(file_id, file_path)
    # → If Redis available: queued to RQ worker
    # → Otherwise: runs in background thread immediately
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_rq_available = False
_queue = None

try:
    import redis
    import rq
    _rq_available = True
except ImportError:
    logger.info("RQ/Redis not available. Using in-process thread fallback for background jobs.")


def _get_queue(redis_url: str):
    global _queue
    if _queue is None and _rq_available:
        try:
            conn = redis.from_url(redis_url)
            conn.ping()  # Fail fast if Redis is down
            _queue = rq.Queue("pdf_indexing", connection=conn, default_timeout=300)
            logger.info("RQ queue connected to %s", redis_url)
        except Exception as e:
            logger.warning("Redis unavailable (%s). Falling back to thread-based jobs.", e)
    return _queue


def enqueue_index_job(file_id: int, file_path: str, redis_url: str = None) -> Optional[str]:
    """
    Enqueue a PDF indexing job.
    Returns job ID if queued via RQ, None if running in-process.
    """
    from flask import current_app
    redis_url = redis_url or current_app.config.get("REDIS_URL", "")
    queue = _get_queue(redis_url) if redis_url else None

    if queue is not None:
        try:
            job = queue.enqueue(
                _index_job_task,
                file_id,
                file_path,
                job_timeout=300,
                result_ttl=3600,
                failure_ttl=86400,
            )
            logger.info("Queued index job: file_id=%s job_id=%s", file_id, job.id)
            return job.id
        except Exception as e:
            logger.warning("RQ enqueue failed (%s). Falling back to thread.", e)

    # Fallback: run in background thread
    _run_in_thread(file_id, file_path)
    return None


def _run_in_thread(file_id: int, file_path: str):
    """Run indexing in a daemon thread. No job tracking; fire-and-forget."""
    from flask import current_app
    app = current_app._get_current_object()  # Detach from request context

    def worker():
        with app.app_context():
            try:
                _index_job_task(file_id, file_path)
            except Exception as e:
                logger.error("Thread-based index job failed: file_id=%s error=%s", file_id, e)

    t = threading.Thread(target=worker, daemon=True, name=f"index-{file_id}")
    t.start()


def _index_job_task(file_id: int, file_path: str):
    """
    Actual indexing work. Runs inside RQ worker or thread.
    Must be importable at module level (RQ requirement).
    """
    from .pdf_service import index_file
    logger.info("Starting index job: file_id=%s path=%s", file_id, file_path)
    page_count, status = index_file(file_id, file_path)
    logger.info("Index job complete: file_id=%s pages=%s status=%s", file_id, page_count, status)
    return {"file_id": file_id, "page_count": page_count, "status": status}
