"""Standard JSON response helpers to enforce consistent API shape."""
from flask import jsonify


def ok(data: dict = None, message: str = None, status: int = 200):
    body = {"success": True}
    if message:
        body["message"] = message
    if data is not None:
        body.update(data)
    return jsonify(body), status


def created(data: dict = None, message: str = None):
    return ok(data, message, status=201)


def error(message: str, details: dict = None, status: int = 400):
    body = {"success": False, "error": message}
    if details:
        body["details"] = details
    return jsonify(body), status


def paginated(items: list, total: int, page: int, page_size: int, **extra):
    return jsonify({
        "success": True,
        "data": items,
        "pagination": {
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": max(1, -(-total // page_size)),  # ceil division
            "has_more": (page * page_size) < total,
        },
        **extra,
    })
