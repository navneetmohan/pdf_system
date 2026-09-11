"""
WSGI entrypoint.
Usage:
  Development:  python wsgi.py
  Gunicorn:     gunicorn wsgi:app --config gunicorn.conf.py
"""
import os
from dotenv import load_dotenv

load_dotenv()

from app import create_app
from app.config import get_config

config = get_config(os.getenv("FLASK_ENV", "production"))
app = create_app(config)

# Seed admin user from environment on first run
with app.app_context():
    from app.models.user_model import UserRepository
    admin_user = config.ADMIN_USERNAME
    admin_pass = config.ADMIN_PASSWORD
    if admin_user and admin_pass:
        UserRepository.seed_admin(admin_user, admin_pass)


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        # PORT is reserved for gunicorn/production; dev server uses FLASK_PORT (default 5000)
        port=int(os.getenv("FLASK_PORT", "5000")),
        debug=True,
    )
