"""
Search API blueprint.
POST /api/search/file      — Search within a single file
POST /api/search/category  — Search across all files in a category
"""
import logging

from flask import Blueprint, current_app, request

from ..models.file_model import FileRepository
from ..models.category_model import CategoryRepository
from ..services.search_service import search_in_file, search_across_category
from ..utils.responses import ok, error
from ..utils.validation import validate_search_request, ValidationError

search_bp = Blueprint("search", __name__)
logger = logging.getLogger(__name__)


@search_bp.route("/file", methods=["POST"])
def search_file():
    """
    POST /api/search/file
    Body: { "file_id": int, "query": str, "page": int, "page_size": int }

    Supported FTS5 query syntax:
      - Simple:  python
      - Phrase:  "machine learning"
      - Prefix:  pyth*
      - Boolean: flask AND sqlalchemy NOT django
    """
    body = request.get_json(silent=True) or request.form.to_dict()

    try:
        params = validate_search_request(body)
    except ValidationError as e:
        return error("Validation failed.", details=e.errors)

    try:
        file_id = int(body.get("file_id"))
    except (TypeError, ValueError, AttributeError):
        return error("file_id must be an integer.")

    file = FileRepository.get_by_id(file_id)
    if not file:
        return error("File not found.", status=404)

    if file.index_status == "pending" or file.index_status == "processing":
        return error(
            "File is still being indexed. Please retry in a moment.",
            details={"index_status": file.index_status},
            status=202,
        )

    if file.index_status == "failed":
        return error(
            "File indexing failed. The PDF may be corrupt or unreadable.",
            details={"index_error": file.index_error},
            status=422,
        )

    try:
        result = search_in_file(
            file_id=file_id,
            query=params["query"],
            page=params["page"],
            page_size=params["page_size"],
        )
    except ValueError as e:
        return error(str(e))
    except Exception as e:
        logger.exception("Search error for file_id=%s", file_id)
        return error("Search failed.", status=500)

    return ok(result.to_dict())


@search_bp.route("/category", methods=["POST"])
def search_category():
    """
    POST /api/search/category
    Body: { "category_id": int, "query": str, "page": int, "page_size": int }
    """
    body = request.get_json(silent=True) or {}

    try:
        params = validate_search_request(body)
    except ValidationError as e:
        return error("Validation failed.", details=e.errors)

    try:
        category_id = int(body.get("category_id"))
    except (TypeError, ValueError):
        return error("category_id must be an integer.")

    cat = CategoryRepository.get_by_id(category_id)
    if not cat:
        return error("Category not found.", status=404)

    try:
        result = search_across_category(
            category_id=category_id,
            query=params["query"],
            page=params["page"],
            page_size=params["page_size"],
        )
    except ValueError as e:
        return error(str(e))
    except Exception as e:
        logger.exception("Category search error: category_id=%s", category_id)
        return error("Search failed.", status=500)

    return ok(result.to_dict())
