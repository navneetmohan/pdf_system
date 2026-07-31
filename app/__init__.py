"""
PDF Search & Management System
Production-grade Flask application with SQLite FTS5, background jobs, and security hardening.
"""
import logging
import os
from flask import Flask, jsonify
from .config import Config
from .extensions import db, limiter
from .utils.logging_config import configure_logging


def create_app(config_object: Config = None) -> Flask:
    """Application factory pattern."""
    app = Flask(__name__, template_folder="../templates", static_folder="../static")

    # Load config
    cfg = config_object or Config()
    app.config.from_object(cfg)

    # Configure structured logging early
    configure_logging(app)

    # Initialize extensions
    db.init_app(app)
    limiter.init_app(app)

    # Ensure storage directories exist
    for directory in [
        app.config["UPLOAD_FOLDER"],
        app.config["TEMP_UPLOAD_FOLDER"],
        app.config["PDF_CACHE_FOLDER"],
        os.path.dirname(app.config.get("LOG_FILE", "logs/app.log")),
    ]:
        if directory:
            os.makedirs(directory, exist_ok=True)

    # Initialize DB schema
    with app.app_context():
        db.init_schema()

    # Register blueprints
    from .api.auth import auth_bp
    from .api.files import files_bp
    from .api.search import search_bp
    from .api.categories import categories_bp
    from .views import views_bp

    app.register_blueprint(auth_bp, url_prefix="/api/auth")
    app.register_blueprint(files_bp, url_prefix="/api/files")
    app.register_blueprint(search_bp, url_prefix="/api/search")
    app.register_blueprint(categories_bp, url_prefix="/api/categories")
    app.register_blueprint(views_bp)

    # Global error handlers
    _register_error_handlers(app)

    logger = logging.getLogger(__name__)
    logger.info("Application initialized", extra={"env": app.config.get("ENV", "production")})

    return app


def _register_error_handlers(app: Flask):
    @app.errorhandler(400)
    def bad_request(e):
        return jsonify({"error": "Bad request", "message": str(e)}), 400

    @app.errorhandler(401)
    def unauthorized(e):
        return jsonify({"error": "Unauthorized"}), 401

    @app.errorhandler(403)
    def forbidden(e):
        return jsonify({"error": "Forbidden"}), 403

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "Not found"}), 404

    @app.errorhandler(413)
    def too_large(e):
        return jsonify({"error": "File too large"}), 413

    @app.errorhandler(429)
    def rate_limit(e):
        return jsonify({"error": "Rate limit exceeded", "message": str(e.description)}), 429

    @app.errorhandler(500)
    def internal_error(e):
        logger = logging.getLogger(__name__)
        logger.exception("Internal server error")
        return jsonify({"error": "Internal server error"}), 500
