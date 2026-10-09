# degreeplan backend

Flask API for the degree plan tool. See the repository [README](../README.md) for setup and
[docs/ARCHITECTURE.md](../docs/ARCHITECTURE.md) for the design.

## Commands

```powershell
$env:APP_ENV = "development"
flask --app degreeplan.wsgi db upgrade|status|stamp|backup
flask --app degreeplan.wsgi users create|list|set-password|deactivate|activate|revoke-sessions
flask --app degreeplan.wsgi dev seed          # development only (not registered in production)
```

## Production smoke test

```powershell
python scripts\production_smoke_test.py   # real CLI + waitress with production settings in a temp directory
```

It uses the same environment variables and waitress arguments as the Windows service, simulates the headers
the TLS proxy sends, and checks cookies, CSRF/Origin handling, audit/app logs, and that no secret reaches a log.

## Layout

```
src/degreeplan/   application package (config, db, repositories, services, security, api, cli)
  migrations/     Alembic environments: users/ and plans/ (baseline revisions)
tests/            unit/, integration/, security/ (databases are built from the real migrations)
requirements/     hash-pinned lock files generated with pip-compile (prod.txt, dev.txt)
```

## Adapting to the real databases

Table and column names live in one place: `src/degreeplan/db/tables.py`. See
[ARCHITECTURE.md](../docs/ARCHITECTURE.md#data-adaptation-your-real-schemas).

## Refreshing the lock files

```powershell
pip-compile --generate-hashes --strip-extras -o requirements\prod.txt pyproject.toml
pip-compile --generate-hashes --strip-extras --extra dev -o requirements\dev.txt pyproject.toml
```
