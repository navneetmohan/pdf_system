"""
Categories API blueprint.
GET    /api/categories          — List all
POST   /api/categories          — Create (admin)
DELETE /api/categories/<id>     — Delete (admin)
"""
import logging
import shutil
from pathlib import Path

from flask import Blueprint, current_app, request

from ..models.category_model import CategoryRepository
from ..utils.auth import require_admin, log_action
from ..utils.validation import validate_category_name, ValidationError
from ..utils.responses import ok, created, error

categories_bp = Blueprint("categories", __name__)
logger = logging.getLogger(__name__)


@categories_bp.route("", methods=["GET"])
def list_categories():
    cats = CategoryRepository.list_all()
    return ok({"categories": [c.to_dict() for c in cats]})


@categories_bp.route("", methods=["POST"])
@require_admin
def create_category():
    body = request.get_json(silent=True) or request.form.to_dict()

    try:
        name = validate_category_name(body.get("name"))
    except ValidationError as e:
        return error("Validation failed.", details=e.errors)

    if CategoryRepository.get_by_name(name):
        return error(f"Category '{name}' already exists.", status=409)

    from ..utils.auth import get_current_user_id
    cat = CategoryRepository.create(name, created_by=get_current_user_id())

    # Create filesystem directory
    cat_dir = Path(current_app.config["UPLOAD_FOLDER"]) / cat.slug
    cat_dir.mkdir(parents=True, exist_ok=True)

    log_action("category.create", resource=cat.slug)
    logger.info("Category created: id=%s name=%s", cat.id, cat.name)
    return created({"category": cat.to_dict()})


@categories_bp.route("/<int:cat_id>", methods=["DELETE"])
@require_admin
def delete_category(cat_id: int):
    cat = CategoryRepository.get_by_id(cat_id)
    if not cat:
        return error("Category not found.", status=404)

    # Remove filesystem directory
    cat_dir = Path(current_app.config["UPLOAD_FOLDER"]) / cat.slug
    if cat_dir.exists():
        try:
            shutil.rmtree(cat_dir)
        except OSError as e:
            logger.error("Failed to delete category directory %s: %s", cat_dir, e)
            return error(f"Filesystem error: {e}", status=500)

    deleted = CategoryRepository.delete(cat_id)
    if not deleted:
        return error("Category not found.", status=404)

    log_action("category.delete", resource=cat.slug)
    logger.info("Category deleted: id=%s name=%s", cat_id, cat.name)
    return ok(message=f"Category '{cat.name}' deleted.")
