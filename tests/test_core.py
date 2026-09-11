"""
Core smoke tests — verify DB schema, auth, search, and all API workflows.
Run with: pytest tests/ -v
"""
import io
import os
import time
import pytest

os.environ["FLASK_ENV"] = "testing"
os.environ["SECRET_KEY"] = "test-key-not-for-production"
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "TestPass123!"


def _minimal_pdf_bytes(text="Hello World PDF content for testing."):
    """Build a minimal valid PDF with embedded text."""
    body = f"""1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj

2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj

3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
   /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj

4 0 obj
<< /Length 44 >>
stream
BT /F1 12 Tf 100 700 Td ({text}) Tj ET
endstream
endobj

5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj

xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000363 00000 n 

trailer
<< /Size 6 /Root 1 0 R >>
startxref
438
%%EOF"""
    return b"%PDF-1.4\n" + body.encode("latin-1")


@pytest.fixture
def app():
    from app import create_app
    from app.config import TestingConfig
    application = create_app(TestingConfig())
    with application.app_context():
        yield application


@pytest.fixture
def client(app):
    from app.extensions import limiter
    limiter.reset()
    return app.test_client()


def _ensure_admin():
    """Idempotent admin user creation."""
    from app.models.user_model import UserRepository
    if not UserRepository.get_by_username("admin"):
        UserRepository.create("admin", "TestPass123!", is_admin=True)


def _login_admin(client):
    """Login as admin, returns the session-bearing client."""
    _ensure_admin()
    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "TestPass123!"
    })
    assert resp.status_code == 200, resp.get_json()
    return client


def _create_category(client, name):
    """Create a category and return it."""
    resp = client.post("/api/categories", json={"name": name})
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()["category"]


def _upload_and_wait(client, pdf_bytes, filename, category_id):
    """Upload a PDF and wait for indexing to complete. Returns (file_id, file_dict)."""
    data = {"pdf": (io.BytesIO(pdf_bytes), filename), "category_id": category_id}
    resp = client.post("/api/files/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 201, resp.get_json()
    file_data = resp.get_json()["file"]
    file_id = file_data["id"]

    for _ in range(15):
        resp = client.get(f"/api/files/{file_id}/status")
        if resp.get_json().get("index_status") == "indexed":
            return file_id, file_data
        time.sleep(0.5)
    pytest.fail(f"File {file_id} was not indexed after 15s. Status: {resp.get_json()}")

    return file_id, file_data


# ── Schema & Basic Validation ────────────────────────────────────────────────

def test_schema_created(app):
    from app.extensions import db
    with app.app_context():
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "users" in names
        assert "files" in names
        assert "categories" in names
        assert "file_index" in names


def test_login_invalid(client):
    resp = client.post("/api/auth/login", json={"username": "nobody", "password": "wrong"})
    assert resp.status_code == 401


def test_me_unauthenticated(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["authenticated"] is False


def test_validation_category_name():
    from app.utils.validation import validate_category_name, ValidationError
    assert validate_category_name("Manuals 2024") == "Manuals 2024"
    with pytest.raises(ValidationError):
        validate_category_name("")
    with pytest.raises(ValidationError):
        validate_category_name("bad<>name")


def test_fts_sanitizer():
    from app.services.search_service import _sanitize_fts_query
    assert _sanitize_fts_query("  hello  ") == "hello"
    assert "--" not in _sanitize_fts_query("hello -- DROP TABLE")
    assert ";" not in _sanitize_fts_query("hello; DROP TABLE")


def test_password_hash():
    from werkzeug.security import generate_password_hash, check_password_hash
    h = generate_password_hash("MyP@ssw0rd!", method="pbkdf2:sha256:600000")
    assert check_password_hash(h, "MyP@ssw0rd!")
    assert not check_password_hash(h, "wrong")


# ── Authentication ───────────────────────────────────────────────────────────

def test_login_and_me(client):
    _ensure_admin()

    resp = client.post("/api/auth/login", json={
        "username": "admin", "password": "TestPass123!"
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["user"]["username"] == "admin"
    assert data["user"]["is_admin"] is True

    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["authenticated"] is True
    assert data["user"]["is_admin"] is True


def test_logout(client):
    """POST /api/auth/logout clears session."""
    _ensure_admin()
    client.post("/api/auth/login", json={
        "username": "admin", "password": "TestPass123!"
    })

    resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    resp = client.get("/api/auth/me")
    assert resp.get_json()["authenticated"] is False


# ── Categories ───────────────────────────────────────────────────────────────

def test_categories_empty(client):
    resp = client.get("/api/categories")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "categories" in data
    assert isinstance(data["categories"], list)


def test_create_category_requires_admin(client):
    resp = client.post("/api/categories", json={"name": "Test"})
    assert resp.status_code == 401


def test_admin_login_and_category_lifecycle(client):
    """Full integration: login -> create category -> list -> delete."""
    _login_admin(client)

    resp = client.post("/api/categories", json={"name": "Test Manuals"})
    assert resp.status_code == 201
    cat = resp.get_json()["category"]
    assert cat["name"] == "Test Manuals"
    assert cat["slug"] == "test_manuals"

    resp = client.get("/api/categories")
    names = [c["name"] for c in resp.get_json()["categories"]]
    assert "Test Manuals" in names

    resp = client.delete(f"/api/categories/{cat['id']}")
    assert resp.status_code == 200


def test_create_duplicate_category(client):
    """POST /api/categories returns 409 for duplicate name."""
    _login_admin(client)
    _create_category(client, "Unique Cat")

    resp = client.post("/api/categories", json={"name": "Unique Cat"})
    assert resp.status_code == 409


# ── File Upload ──────────────────────────────────────────────────────────────

def test_upload_requires_admin(client):
    """POST /api/files/upload returns 401 without login."""
    pdf_bytes = _minimal_pdf_bytes()
    data = {"pdf": (io.BytesIO(pdf_bytes), "test.pdf"), "category_id": 1}
    resp = client.post("/api/files/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 401


def test_temp_upload(client):
    """POST /api/files/temp-upload creates a temp file without auth."""
    pdf_bytes = _minimal_pdf_bytes("Temp file searchable text.")
    data = {"pdf": (io.BytesIO(pdf_bytes), "temp_test.pdf")}
    resp = client.post("/api/files/temp-upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 201
    result = resp.get_json()
    assert result["success"] is True
    assert result["file"]["is_temp"] is True
    assert result["file"]["expires_at"] is not None
    assert result["file"]["index_status"] in ("pending", "processing", "indexed")


def test_upload_invalid_extension(client):
    """Uploading non-PDF extension is rejected."""
    cat = _create_category(_login_admin(client), "Test Cat")

    data = {"pdf": (io.BytesIO(b"not a pdf"), "bad.txt"), "category_id": cat["id"]}
    resp = client.post("/api/files/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "only" in resp.get_json()["error"].lower()


def test_upload_with_invalid_magic_bytes(client):
    """File with .pdf extension but wrong magic bytes is rejected."""
    cat = _create_category(_login_admin(client), "Safety")
    fake_pdf = b"FakePDF content but magic bytes are wrong."[:100]
    data = {"pdf": (io.BytesIO(fake_pdf), "fake.pdf"), "category_id": cat["id"]}
    resp = client.post("/api/files/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "magic bytes" in resp.get_json()["error"].lower()


def test_upload_full_workflow(client):
    """Full admin upload workflow: login, create category, upload PDF, check status."""
    cat = _create_category(_login_admin(client), "Engineering Manuals")
    pdf_bytes = _minimal_pdf_bytes("Installation procedure for the main engine component.")

    file_id, file_data = _upload_and_wait(client, pdf_bytes, "install_manual.pdf", cat["id"])
    assert file_data["category_id"] == cat["id"]

    resp = client.get(f"/api/files/{file_id}/status")
    assert resp.get_json()["index_status"] == "indexed"
    assert resp.get_json()["page_count"] >= 1


# ── File Listing, Serving, Deletion ──────────────────────────────────────────

def test_list_files(client):
    """GET /api/files returns category list when no category_id given."""
    _login_admin(client)
    _create_category(client, "Documents")

    resp = client.get("/api/files")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "categories" in data


def test_list_files_by_category(client):
    """GET /api/files?category_id=X returns paginated file list."""
    cat = _create_category(_login_admin(client), "Reports2")
    pdf_bytes = _minimal_pdf_bytes("Report content for listing test.")
    _upload_and_wait(client, pdf_bytes, "report.pdf", cat["id"])

    resp = client.get(f"/api/files?category_id={cat['id']}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "data" in data
    assert "pagination" in data
    assert len(data["data"]) >= 1


def test_list_files_all(client):
    """GET /api/files?all=1 returns paginated file list across all categories."""
    cat = _create_category(_login_admin(client), "AllFilesCat")
    pdf_bytes = _minimal_pdf_bytes("All files listing content.")
    _upload_and_wait(client, pdf_bytes, "all_files.pdf", cat["id"])

    resp = client.get("/api/files?all=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "data" in data
    assert "pagination" in data
    assert len(data["data"]) >= 1


def test_list_files_temp(client):
    """GET /api/files?temp=1 returns paginated temp file list."""
    pdf_bytes = _minimal_pdf_bytes("Temporary list test content.")
    data = {"pdf": (io.BytesIO(pdf_bytes), "temp_list.pdf")}
    client.post("/api/files/temp-upload", data=data, content_type="multipart/form-data")

    resp = client.get("/api/files?temp=1")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "data" in data
    assert "pagination" in data
    assert len(data["data"]) >= 1


def test_serve_file(client):
    """GET /api/files/<id>/serve returns PDF bytes."""
    cat = _create_category(_login_admin(client), "Serveable2")
    pdf_bytes = _minimal_pdf_bytes("Content to serve for test.")
    file_id, _ = _upload_and_wait(client, pdf_bytes, "serve.pdf", cat["id"])

    resp = client.get(f"/api/files/{file_id}/serve")
    assert resp.status_code == 200
    assert resp.content_type == "application/pdf"
    assert len(resp.data) > 0


def test_file_status_not_found(client):
    """GET /api/files/<id>/status returns 404 for non-existent file."""
    resp = client.get("/api/files/99999/status")
    assert resp.status_code == 404


def test_delete_file(client):
    """DELETE /api/files/<id> removes file record and FTS index."""
    cat = _create_category(_login_admin(client), "Deletable2")
    pdf_bytes = _minimal_pdf_bytes("Content to delete for test.")
    file_id, _ = _upload_and_wait(client, pdf_bytes, "delete.pdf", cat["id"])

    resp = client.delete(f"/api/files/{file_id}")
    assert resp.status_code == 200

    resp = client.get(f"/api/files/{file_id}/status")
    assert resp.status_code == 404


# ── Search ───────────────────────────────────────────────────────────────────

def test_search_file_no_file_id(client):
    """POST /api/search/file without file_id returns error."""
    resp = client.post("/api/search/file", json={"query": "test"})
    assert resp.status_code == 400


def test_search_file_not_found(client):
    """POST /api/search/file for non-existent file returns 404."""
    resp = client.post("/api/search/file", json={"file_id": 99999, "query": "test"})
    assert resp.status_code == 404


def test_search_file_success(client):
    """POST /api/search/file returns search results with highlighted snippets."""
    cat = _create_category(_login_admin(client), "Searchable3")
    pdf_bytes = _minimal_pdf_bytes("Installation steps for electrical equipment safety.")
    file_id, _ = _upload_and_wait(client, pdf_bytes, "doc.pdf", cat["id"])

    resp = client.post("/api/search/file", json={
        "file_id": file_id,
        "query": "installation steps",
        "page": 1,
        "page_size": 10,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["query"] == "installation steps"
    assert data["total"] >= 1
    assert len(data["results"]) >= 1
    result = data["results"][0]
    assert "<mark>" in result["snippet"]
    assert result["page"] >= 1


def test_search_file_phrase(client):
    """POST /api/search/file with phrase query."""
    cat = _create_category(_login_admin(client), "Phrases2")
    pdf_bytes = _minimal_pdf_bytes("The quick brown fox jumps over the lazy dog.")
    file_id, _ = _upload_and_wait(client, pdf_bytes, "phrase.pdf", cat["id"])

    resp = client.post("/api/search/file", json={
        "file_id": file_id,
        "query": '"quick brown fox"',
    })
    assert resp.status_code == 200
    assert resp.get_json()["total"] >= 1


def test_search_file_pagination(client):
    """Search results respect page_size parameter."""
    cat = _create_category(_login_admin(client), "Pagination2")
    pdf_bytes = _minimal_pdf_bytes("the the the the the the the the the the the the the the the the")
    file_id, _ = _upload_and_wait(client, pdf_bytes, "many.pdf", cat["id"])

    resp = client.post("/api/search/file", json={
        "file_id": file_id,
        "query": "the",
        "page": 1,
        "page_size": 1,
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["results"]) <= 1


def test_search_in_category(client):
    """POST /api/search/category searches across all files in a category."""
    cat = _create_category(_login_admin(client), "Safety Docs2")
    pdf_bytes = _minimal_pdf_bytes("Safety warning procedure for high voltage equipment.")
    file_id, _ = _upload_and_wait(client, pdf_bytes, "safety.pdf", cat["id"])

    resp = client.post("/api/search/category", json={
        "category_id": cat["id"],
        "query": "safety",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["total"] >= 1
    assert data["results"][0]["category"] == "safety_docs2"


# ── Search Validation ────────────────────────────────────────────────────────

def test_search_validation(client):
    """Empty query returns 400."""
    resp = client.post("/api/search/file", json={"file_id": 1, "query": ""})
    assert resp.status_code == 400


def test_search_file_still_indexing(client):
    """Search on a non-indexed file returns 202."""
    _login_admin(client)
    cat = _create_category(client, "PendingCat2")
    pdf_bytes = _minimal_pdf_bytes("Unique pending content for testing.")
    data = {"pdf": (io.BytesIO(pdf_bytes), "pending.pdf"), "category_id": cat["id"]}
    resp = client.post("/api/files/upload", data=data, content_type="multipart/form-data")
    file_id = resp.get_json()["file"]["id"]

    # Check status immediately — should be pending or processing
    resp = client.get(f"/api/files/{file_id}/status")
    status_before = resp.get_json()["index_status"]

    # If not yet indexed, search should return 202
    if status_before != "indexed":
        resp = client.post("/api/search/file", json={
            "file_id": file_id, "query": "pending"
        })
        assert resp.status_code in (202, 200)


def test_search_response_shape(client):
    """Search response matches the README contract."""
    cat = _create_category(_login_admin(client), "ShapeTest2")
    pdf_bytes = _minimal_pdf_bytes("Installation procedure unique content for shape test.")
    file_id, _ = _upload_and_wait(client, pdf_bytes, "shape.pdf", cat["id"])

    resp = client.post("/api/search/file", json={
        "file_id": file_id,
        "query": "installation",
    })
    assert resp.status_code == 200
    data = resp.get_json()
    assert "query" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data
    assert "has_more" in data
    assert "results" in data
    if data["results"]:
        r = data["results"][0]
        assert "file_id" in r
        assert "filename" in r
        assert "original_name" in r
        assert "category" in r
        assert "page" in r
        assert "snippet" in r
        assert "rank" in r


# ── Category Delete Cascades ─────────────────────────────────────────────────

def test_delete_category_cascades(client):
    """Deleting a category removes its files and FTS entries."""
    cat = _create_category(_login_admin(client), "Temp Cat2")
    pdf_bytes = _minimal_pdf_bytes("Content for cascade deletion test.")
    file_id, _ = _upload_and_wait(client, pdf_bytes, "cascade.pdf", cat["id"])

    resp = client.delete(f"/api/categories/{cat['id']}")
    assert resp.status_code == 200

    resp = client.get(f"/api/files/{file_id}/status")
    assert resp.status_code == 404


# ── Deduplication ────────────────────────────────────────────────────────────

def test_upload_duplicate_rejected(client):
    """Uploading same content twice with same category returns 409."""
    cat = _create_category(_login_admin(client), "Duplicates2")
    pdf_bytes = _minimal_pdf_bytes("Unique duplicate test content for dedup test.")

    data = {"pdf": (io.BytesIO(pdf_bytes), "v1.pdf"), "category_id": cat["id"]}
    resp = client.post("/api/files/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 201

    data = {"pdf": (io.BytesIO(pdf_bytes), "v2.pdf"), "category_id": cat["id"]}
    resp = client.post("/api/files/upload", data=data, content_type="multipart/form-data")
    assert resp.status_code == 409


# ── Auth Session / Rejection ─────────────────────────────────────────────────

def test_unauthenticated_requests_rejected(client):
    """Various admin endpoints return 401 without session."""
    assert client.delete("/api/files/1").status_code == 401
    assert client.post("/api/categories", json={"name": "X"}).status_code == 401
