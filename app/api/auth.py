"""
Authentication API blueprint.
Endpoints: POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me
"""
import logging

from flask import Blueprint, request

from ..models.user_model import UserRepository
from ..utils.auth import is_admin, login_user, logout_user, get_current_user_id
from ..utils.validation import validate_login, ValidationError
from ..utils.responses import ok, error
from ..extensions import limiter

auth_bp = Blueprint("auth", __name__)
logger = logging.getLogger(__name__)


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    POST /api/auth/login
    Body: { "username": "...", "password": "..." }
    Rate limited: 10 per minute per IP.
    """
    ip = request.remote_addr
    # Rate limit: 10 login attempts per minute per IP
    if not limiter.check(f"login:{ip}", limit=10, window_seconds=60):
        logger.warning("Login rate limit exceeded: ip=%s", ip)
        return error("Too many login attempts. Try again in a minute.", status=429)

    try:
        data = validate_login(request.get_json(silent=True) or request.form.to_dict())
    except ValidationError as e:
        return error("Validation failed.", details=e.errors, status=400)

    user = UserRepository.get_by_username(data["username"])

    # Constant-time failure path prevents username enumeration
    if not user or not user.check_password(data["password"]):
        logger.warning("Failed login attempt: username=%s ip=%s", data.get("username"), ip)
        return error("Invalid credentials.", status=401)

    login_user(user)
    logger.info("Successful login: user_id=%s ip=%s", user.id, ip)
    return ok({"user": user.to_dict()}, message="Login successful.")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """POST /api/auth/logout"""
    logout_user()
    return ok(message="Logged out.")


@auth_bp.route("/me", methods=["GET"])
def me():
    """GET /api/auth/me — Returns current session user info."""
    user_id = get_current_user_id()
    if not user_id:
        return ok({"authenticated": False, "is_admin": False})

    user = UserRepository.get_by_id(user_id)
    if not user:
        logout_user()
        return ok({"authenticated": False, "is_admin": False})

    return ok({"authenticated": True, "user": user.to_dict()})
