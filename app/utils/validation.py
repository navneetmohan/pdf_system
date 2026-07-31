"""
Input validation utilities.
Validates and sanitizes all incoming request data before it touches services or DB.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


class ValidationError(Exception):
    def __init__(self, errors: dict):
        self.errors = errors
        super().__init__(str(errors))


def _non_empty_string(value: Any, field_name: str, max_len: int = 255) -> str:
    if not isinstance(value, str):
        raise ValidationError({field_name: "Must be a string."})
    stripped = value.strip()
    if not stripped:
        raise ValidationError({field_name: "Cannot be empty."})
    if len(stripped) > max_len:
        raise ValidationError({field_name: f"Must not exceed {max_len} characters."})
    return stripped


def validate_search_request(data: dict) -> dict:
    errors = {}
    result = {}

    query = data.get("query", "")
    if not isinstance(query, str) or not query.strip():
        errors["query"] = "Search query is required."
    elif len(query) > 500:
        errors["query"] = "Query must not exceed 500 characters."
    else:
        result["query"] = query.strip()

    try:
        page = int(data.get("page", 1))
        if page < 1:
            errors["page"] = "Page must be >= 1."
        else:
            result["page"] = page
    except (TypeError, ValueError):
        errors["page"] = "Page must be a positive integer."

    try:
        page_size = int(data.get("page_size", 20))
        if not (1 <= page_size <= 100):
            errors["page_size"] = "page_size must be between 1 and 100."
        else:
            result["page_size"] = page_size
    except (TypeError, ValueError):
        errors["page_size"] = "page_size must be a positive integer."

    if errors:
        raise ValidationError(errors)
    return result


def validate_category_name(name: Any) -> str:
    """
    Category names: 1-100 chars, alphanumeric + spaces + hyphens + underscores.
    """
    name = _non_empty_string(name, "name", max_len=100)
    if not re.match(r'^[\w\s\-]+$', name):
        raise ValidationError({"name": "Category name contains invalid characters."})
    return name


def validate_login(data: dict) -> dict:
    errors = {}
    result = {}

    username = data.get("username", "")
    if not username or not username.strip():
        errors["username"] = "Username is required."
    elif len(username) > 64:
        errors["username"] = "Username too long."
    else:
        result["username"] = username.strip()

    password = data.get("password", "")
    if not password:
        errors["password"] = "Password is required."
    elif len(password) > 256:
        errors["password"] = "Password too long."
    else:
        result["password"] = password

    if errors:
        raise ValidationError(errors)
    return result


def validate_pagination(args: dict, default_size: int = 20) -> tuple[int, int]:
    """Parse and clamp page/page_size from query args. Returns (page, page_size)."""
    try:
        page = max(1, int(args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    try:
        page_size = min(100, max(1, int(args.get("page_size", default_size))))
    except (TypeError, ValueError):
        page_size = default_size

    return page, page_size
