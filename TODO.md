# TODO

## Startup Fix Plan

- [x] `wsgi.py`: Replace hardcoded `port=8080` with env-driven `5000` default
- [x] `app/utils/logging_config.py`: Set werkzeug logger to `INFO` so "Running on" banner is visible
- [x] Stop old server (port 8080) and restart `python wsgi.py`
- [x] Verify `* Running on http://127.0.0.1:5000` is printed
- [x] Verify homepage returns HTTP 200 on port 5000
- [x] Verify no startup errors remain

## UI Implementation & Fix Plan

- [x] Inspect backend auth implementation and UI templates
- [x] Confirm admin seeding runs on startup via `wsgi.py`
- [x] Implement login JS in `templates/login.html` (call `/api/auth/login`, handle errors, redirect)
- [x] Revamp UI styling in `static/style.css` (modern responsive layout, design system tokens, animations)
- [x] Add logout support across all UI templates via `static/js/app.js` and `static/js/logout.js`
- [x] Implement full Search interface in `templates/search.html` (scoped search, BM25 highlighted snippets, pagination, PDF links)
- [x] Implement Document Library in `templates/select.html` (category tabs, instant filtering, file cards, actions)
- [x] Implement Admin Upload in `templates/upload.html` (drag & drop, dynamic category picker, inline category creation, live indexing status polling)
- [x] Implement Public Temporary Upload in `templates/temp_upload.html` (drag & drop, 14h expiry, auto-indexing tracker)
- [x] Implement Admin Control Center in `templates/admin.html` (live metrics, category manager: create & delete)
- [x] Implement File Management in `templates/delete.html` (live filtering, confirmation modal, instant DOM update)
- [x] Enhance backend `GET /api/files` with `all=1` and `temp=1` query parameters
- [x] Run test suite: `python -m pytest` (36 passed)
- [x] End-to-end verification
