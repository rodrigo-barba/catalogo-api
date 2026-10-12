# Bootstrap database health and Alembic metadata

## Intent

Establish a secret-safe application configuration boundary, SQLAlchemy bootstrap, a health endpoint, and Alembic metadata wiring without changing the existing database schema.

## Scope

Implement only environment settings, database bootstrap, `GET /health`, and Alembic metadata/baseline configuration. Do not apply or generate schema migrations, inspect sensitive files, or implement R1, R2, or R3.

## Checklist

- [x] Add secret-safe settings and SQLAlchemy initialization.
- [x] Add and test `GET /health`.
- [x] Wire Alembic to application metadata without applying migrations.
- [x] Run focused tests and a runtime healthcheck.

## Evidence

- `env -u CATALOGO_DATABASE_URL python -m pytest -q`: exit 0; `4 passed, 1 warning in 1.75s`.
- `env -u CATALOGO_DATABASE_URL python -c "... TestClient(app).get('/health') ..."`: exit 0; returned HTTP 200 with `{"status": "ok"}`.
- `alembic -c alembic.ini history --verbose`: exit 0; no revisions were created or applied.
- `git diff --check` and `git diff --cached --check`: exit 0; no whitespace errors.
- Independent verification inspected settings with `env_file=None` and `secrets_dir=None`, lazy engine creation, the database-independent route, and Alembic `target_metadata = Base.metadata`.
- Test/runtime warning: `StarletteDeprecationWarning` reports the installed `httpx` integration used by `starlette.testclient` as deprecated. It does not fail the suite.

## Next

- No remaining work in this FTD — resolved.
