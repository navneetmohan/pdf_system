"""
HTML view routes. Serve the frontend templates.
No business logic here — all data comes from the API endpoints.
"""
from flask import Blueprint, redirect, render_template, session, url_for

from .utils.auth import is_admin

views_bp = Blueprint("views", __name__)


@views_bp.app_context_processor
def inject_auth_state():
    """Expose session auth state to all templates for nav rendering."""
    return {
        "current_user_is_admin": is_admin(),
        "current_username": session.get("username"),
        "current_user_id": session.get("user_id"),
    }


@views_bp.route("/")
def index():
    return render_template("home.html")


@views_bp.route("/search")
def search_page():
    return render_template("search.html")


@views_bp.route("/select")
def select_page():
    return render_template("select.html")


@views_bp.route("/upload")
def upload_page():
    if not is_admin():
        return redirect(url_for("views.login_page"))
    return render_template("upload.html")


@views_bp.route("/temp-upload")
def temp_upload_page():
    return render_template("temp_upload.html")


@views_bp.route("/login")
def login_page():
    return render_template("login.html")


@views_bp.route("/admin")
def admin_page():
    if not is_admin():
        return redirect(url_for("views.login_page"))
    return render_template("admin.html")


@views_bp.route("/delete")
def delete_page():
    if not is_admin():
        return redirect(url_for("views.login_page"))
    return render_template("delete.html")
