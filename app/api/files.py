"""
Files API blueprint.
GET    /api/files                   — List (with pagination)
POST   /api/files/upload            — Upload permanent PDF (admin)
POST   /api/files/temp-upload       — Upload temporary PDF (public)
DELETE /api/files/<id>              — Delete (admin)
GET    /api/files/<id>/status       — Index status
GET    /api/files/<id>/serve        — Serve PDF bytes
"""
import logging

from flask import Blueprint, current_app, request, send_file

from ..models.file_model import FileRepository
from ..models.category_model import CategoryRepository
from ..services.file_service import (
    DuplicateFileError,
    FileServiceError,
    cleanup_expired_temp_files,
    delete_file_record,
    get_file_path,
    save_uploaded_file,
)
from ..utils.auth import get_current_user_id, log_action, require_admin
from ..utils.responses import created, error, ok, paginated
from ..utils.validation import validate_pagination

files_bp = Blueprint("files", __name__)
logger = logging.getLogger(__name__)


@files_bp.before_request
def _cleanup():
    """Lazily purge expired temp files on every request to this blueprint."""
    cleanup_expired_temp_files()


@files_bp.route("", methods=["GET"])
def list_files():
    """
    GET /api/files?category_id=<int>&page=1&page_size=20
    Returns paginated file list.
    If all=1: returns all files.
    If temp=1: returns temp files.
    If category_id omitted and neither all nor temp: returns all categories + counts.
    """
    page, page_size = validate_pagination(request.args, current_app.config["DEFAULT_PAGE_SIZE"])
    category_id = request.args.get("category_id", type=int)

    if category_id is not None:
        cat = CategoryRepository.get_by_id(category_id)
        if not cat:
            return error("Category not found.", status=404)
        files, total = FileRepository.list_by_category(category_id, page, page_size)
        return paginated([f.to_dict() for f in files], total, page, page_size,
                         category=cat.to_dict())

    if request.args.get("all") in ("1", "true"):
        include_temp = request.args.get("include_temp") in ("1", "true")
        files, total = FileRepository.list_all(page, page_size, include_temp=include_temp)
        return paginated([f.to_dict() for f in files], total, page, page_size)

    if request.args.get("temp") in ("1", "true"):
        files, total = FileRepository.list_temp(page, page_size)
        return paginated([f.to_dict() for f in files], total, page, page_size)

    # Return category list with file counts
    cats = CategoryRepository.list_all()
    return ok({"categories": [c.to_dict() for c in cats]})


@files_bp.route("/upload", methods=["POST"])
@require_admin
def upload_file():
    """
    POST /api/files/upload
    Form: pdf=<file>, category_id=<int>
    Admin only. Enqueues indexing job after save.
    """
    if "pdf" not in request.files:
        return error("No file in request. Use field name 'pdf'.")

    category_id = request.form.get("category_id", type=int)
    if not category_id:
        return error("category_id is required.")

    cat = CategoryRepository.get_by_id(category_id)
    if not cat:
        return error("Category not found.", status=404)

    try:
        db_file = save_uploaded_file(
            file_storage=request.files["pdf"],
            category_id=category_id,
            is_temp=False,
            uploaded_by=get_current_user_id(),
        )
    except DuplicateFileError as e:
        return error(str(e), status=409)
    except FileServiceError as e:
        return error(str(e))

    log_action("file.upload", resource=db_file.filename)
    return created({"file": db_file.to_dict()}, message="File uploaded. Indexing in progress.")


@files_bp.route("/temp-upload", methods=["POST"])
def temp_upload():
    """
    POST /api/files/temp-upload
    Form: pdf=<file>
    Public. File expires after TEMP_FILE_LIFETIME.
    Rate limit: 5 uploads per minute per IP.
    """
    from ..extensions import limiter
    ip = request.remote_addr
    if not limiter.check(f"temp_upload:{ip}", limit=5, window_seconds=60):
        return error("Upload rate limit exceeded. Try again in a minute.", status=429)

    if "pdf" not in request.files:
        return error("No file in request. Use field name 'pdf'.")

    try:
        db_file = save_uploaded_file(
            file_storage=request.files["pdf"],
            category_id=None,
            is_temp=True,
            uploaded_by=get_current_user_id(),
        )
    except FileServiceError as e:
        return error(str(e))

    return created(
        {"file": db_file.to_dict()},
        message=f"Temporary file uploaded. Expires at {db_file.expires_at}."
    )


@files_bp.route("/<int:file_id>", methods=["DELETE"])
@require_admin
def delete_file(file_id: int):
    """DELETE /api/files/<id> — Admin only."""
    file = FileRepository.get_by_id(file_id)
    if not file:
        return error("File not found.", status=404)

    deleted = delete_file_record(file_id)
    if not deleted:
        return error("File not found.", status=404)

    log_action("file.delete", resource=str(file_id))
    return ok(message="File deleted.")


@files_bp.route("/<int:file_id>/status", methods=["GET"])
def get_status(file_id: int):
    """GET /api/files/<id>/status — Returns indexing status."""
    file = FileRepository.get_by_id(file_id)
    if not file:
        return error("File not found.", status=404)
    return ok({
        "file_id": file.id,
        "index_status": file.index_status,
        "page_count": file.page_count,
        "index_error": file.index_error,
    })


@files_bp.route("/<int:file_id>/serve", methods=["GET"])
def serve_file(file_id: int):
    """GET /api/files/<id>/serve — Stream PDF bytes to client."""
    file = FileRepository.get_by_id(file_id)
    if not file:
        return error("File not found.", status=404)

    path = get_file_path(file)
    if not path or not path.exists():
        return error("File not found on disk.", status=404)

    return send_file(
        str(path),
        mimetype="application/pdf",
        as_attachment=False,
        download_name=file.original_name,
    )
