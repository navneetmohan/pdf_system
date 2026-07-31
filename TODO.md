# TODO

- [x] Inspect backend auth implementation and UI templates
- [x] Confirm admin seeding runs on startup via `wsgi.py`
- [x] Implement missing login JS in `templates/login.html` (call `/api/auth/login`, handle errors, redirect)

- [x] Revamp UI styling in `static/style.css` (modern layout for forms/buttons/cards)

- [ ] Add logout support in UI (button calling `/api/auth/logout` and redirect)

- [ ] Basic navigation improvements on key templates
- [ ] Run tests: `pytest tests/`
- [ ] Manual verification: login -> access admin pages -> logout

