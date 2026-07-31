"""User model with werkzeug password hashing. No plaintext passwords stored."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db

logger = logging.getLogger(__name__)


@dataclass
class User:
    id: Optional[int]
    username: str
    password_hash: str
    is_admin: bool
    created_at: str
    last_login: Optional[str]

    @classmethod
    def from_row(cls, row) -> "User":
        return cls(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            is_admin=bool(row["is_admin"]),
            created_at=row["created_at"],
            last_login=row["last_login"],
        )

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "is_admin": self.is_admin,
            "created_at": self.created_at,
        }


class UserRepository:

    @staticmethod
    def create(username: str, password: str, is_admin: bool = False) -> User:
        password_hash = generate_password_hash(password, method="pbkdf2:sha256:600000")
        with db.transaction():
            cursor = db.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
                (username, password_hash, int(is_admin)),
            )
            user_id = cursor.lastrowid
        return UserRepository.get_by_id(user_id)

    @staticmethod
    def get_by_id(user_id: int) -> Optional[User]:
        row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return User.from_row(row) if row else None

    @staticmethod
    def get_by_username(username: str) -> Optional[User]:
        row = db.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        return User.from_row(row) if row else None

    @staticmethod
    def update_last_login(user_id: int):
        with db.transaction():
            db.execute(
                "UPDATE users SET last_login = datetime('now') WHERE id = ?", (user_id,)
            )

    @staticmethod
    def exists_any() -> bool:
        count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return count > 0

    @staticmethod
    def seed_admin(username: str, password: str):
        """Idempotent: create admin user only if no users exist."""
        if not UserRepository.exists_any():
            UserRepository.create(username, password, is_admin=True)
            logger.info("Admin user seeded from environment variables.")
        else:
            logger.debug("Admin seed skipped: users already exist.")
