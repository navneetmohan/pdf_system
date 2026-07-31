"""
Auth utilities — session management and decorators.
Stateless session approach: user_id and is_admin stored in signed Flask session cookie.
"""
from __future__ import annotations

import logging
from functools import wraps

from flask import jsonify, request, session

from ..models.user_model import UserRepository

logger = logging.getLogger(__name__)


def get_current_user_id() -> int | None:
    return session.get("user_id")


def is_admin() -> bool:
    return bool(session.get("is_admin", False))


def login_user(user) -> None:
    """Store user identity in session."""
    session.permanent = True
    session["user_id"] = user.id
    session["username"] = user.username
    session["is_admin"] = user.is_admin
    UserRepository.update_last_login(user.id)
    logger.info("User logged in: id=%s username=%s", user.id, user.username)


def logout_user() -> None:
    session.clear()


def require_admin(f):
    """Decorator: reject non-admin requests with 401."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_admin():
            return jsonify({"error": "Admin access required."}), 401
        return f(*args, **kwargs)
    return decorated


def require_login(f):
    """Decorator: reject unauthenticated requests with 401."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not get_current_user_id():
            return jsonify({"error": "Authentication required."}), 401
        return f(*args, **kwargs)
    return decorated


def log_action(action: str, resource: str = None):
    """Write an audit log entry for the current request."""
    from ..extensions import db
    user_id = get_current_user_id()
    ip = request.remote_addr
    try:
        with db.transaction():
            db.execute(
                "INSERT INTO audit_log (user_id, action, resource, ip_address) VALUES (?, ?, ?, ?)",
                (user_id, action, resource, ip),
            )
    except Exception as e:
        logger.warning("Audit log write failed: %s", e)
