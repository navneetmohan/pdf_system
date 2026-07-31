"""
Configuration module.
All secrets MUST come from environment variables.
No credentials are hardcoded here. Startup fails fast if required env vars are missing.
"""
import os
import secrets
from datetime import timedelta
from pathlib import Path


class ConfigurationError(Exception):
    """Raised when required configuration is missing or invalid."""


class Config:
    # ── Flask Core ─────────────────────────────────────────────────────────────
    ENV: str = os.getenv("FLASK_ENV", "production")
    DEBUG: bool = os.getenv("FLASK_DEBUG", "0") == "1"
    TESTING: bool = False

    # SECRET_KEY is mandatory. Refuse to start without it.
    SECRET_KEY: str = os.getenv("SECRET_KEY", "")

    # ── Session ────────────────────────────────────────────────────────────────
    SESSION_COOKIE_SECURE: bool = os.getenv("SESSION_COOKIE_SECURE", "1") == "1"
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    PERMANENT_SESSION_LIFETIME: timedelta = timedelta(
        seconds=int(os.getenv("SESSION_LIFETIME_SECONDS", "1800"))
    )

    # ── Database ───────────────────────────────────────────────────────────────
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/pdf_system.db")

    # ── File Storage ───────────────────────────────────────────────────────────
    UPLOAD_FOLDER: str = os.getenv("UPLOAD_FOLDER", "storage/uploads")
    TEMP_UPLOAD_FOLDER: str = os.getenv("TEMP_UPLOAD_FOLDER", "storage/temp")
    PDF_CACHE_FOLDER: str = os.getenv("PDF_CACHE_FOLDER", "storage/cache")
    MAX_CONTENT_LENGTH: int = int(os.getenv("MAX_UPLOAD_BYTES", str(32 * 1024 * 1024)))  # 32 MB
    ALLOWED_EXTENSIONS: frozenset = frozenset({"pdf"})

    # Temp file lifetime before cleanup
    TEMP_FILE_LIFETIME: timedelta = timedelta(
        hours=int(os.getenv("TEMP_FILE_LIFETIME_HOURS", "14"))
    )

    # ── Admin ──────────────────────────────────────────────────────────────────
    # Passwords are stored as bcrypt hashes in the DB.
    # On first run, seed admin from these env vars (never store plaintext).
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")  # Plaintext only at seed time

    # ── Rate Limiting ──────────────────────────────────────────────────────────
    RATELIMIT_STORAGE_URI: str = os.getenv("REDIS_URL", "memory://")
    RATELIMIT_DEFAULT: str = "200 per hour"
    LOGIN_RATE_LIMIT: str = os.getenv("LOGIN_RATE_LIMIT", "10 per minute")

    # ── Logging ────────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FILE: str = os.getenv("LOG_FILE", "logs/app.log")
    LOG_JSON: bool = os.getenv("LOG_JSON", "1") == "1"

    # ── Background Jobs ────────────────────────────────────────────────────────
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    JOB_TIMEOUT: int = int(os.getenv("JOB_TIMEOUT_SECONDS", "300"))

    # ── Pagination ─────────────────────────────────────────────────────────────
    DEFAULT_PAGE_SIZE: int = int(os.getenv("DEFAULT_PAGE_SIZE", "20"))
    MAX_PAGE_SIZE: int = 100

    def validate(self) -> None:
        """Fail fast on missing critical config. Call at startup."""
        errors = []

        if not self.SECRET_KEY:
            errors.append(
                "SECRET_KEY is not set. "
                "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )

        if len(self.SECRET_KEY) < 32:
            errors.append("SECRET_KEY must be at least 32 characters.")

        db_dir = Path(self.DATABASE_PATH).parent
        try:
            db_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            errors.append(f"Cannot create database directory '{db_dir}': {e}")

        if errors:
            raise ConfigurationError("\n".join(f"  • {e}" for e in errors))


class DevelopmentConfig(Config):
    ENV = "development"
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    LOG_LEVEL = "DEBUG"
    LOG_JSON = False

    def validate(self) -> None:
        # In dev, auto-generate a secret key for convenience
        if not self.SECRET_KEY:
            self.SECRET_KEY = secrets.token_hex(32)


class TestingConfig(Config):
    TESTING = True
    DEBUG = True
    DATABASE_PATH = ":memory:"
    SESSION_COOKIE_SECURE = False
    SECRET_KEY = "test-secret-key-not-for-production"
    UPLOAD_FOLDER = "/tmp/test_uploads"
    TEMP_UPLOAD_FOLDER = "/tmp/test_temp"
    PDF_CACHE_FOLDER = "/tmp/test_cache"

    def validate(self) -> None:
        pass  # Skip validation in tests


def get_config(env: str = None) -> Config:
    env = env or os.getenv("FLASK_ENV", "production")
    configs = {
        "development": DevelopmentConfig,
        "testing": TestingConfig,
        "production": Config,
    }
    cfg_class = configs.get(env, Config)
    cfg = cfg_class()
    cfg.validate()
    return cfg
