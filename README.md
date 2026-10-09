# Student degree plan tool

Backend API for the web-based degree plan tool: students build and validate degree plans, teachers review the
plans of the students assigned to them, administrators manage accounts. The frontend and the databases are owned by
other team members; this repository holds the backend, its deployment tooling, and a throw-away proof of concept.

| Path | What it is |
| --- | --- |
| `backend/` | Flask API (`degreeplan` package), migrations, tests, hash-pinned requirements |
| `deploy/` | Windows Server deployment: service wrappers (WinSW), Caddy (TLS) template, install/backup scripts |
| `docs/` | **[Team guide](docs/TEAM_GUIDE.md)** (run, demo, connect frontend and database), [Architecture](docs/ARCHITECTURE.md), [Deployment](docs/DEPLOYMENT.md), [Operations](docs/OPERATIONS.md), [Security](docs/SECURITY.md) |
| `poc/` | **Not for deployment.** Standalone login/landing-page demo server, kept for reference |

Two databases (users, plans), server-side sessions, Argon2id passwords, role-scoped authorization, audit trail.
**No secrets or accounts are stored in the repository**: see [docs/SECURITY.md](docs/SECURITY.md).

## Quick start for teammates (PowerShell)

```powershell
cd backend
.\scripts\demo.ps1          # sets everything up, prints demo passwords once, starts http://localhost:5000
```

See the **[team guide](docs/TEAM_GUIDE.md)** to demo it and to plug in your frontend and database.

## Local development (PowerShell)

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements\dev.txt
pip install --no-deps -e .

$env:APP_ENV = "development"                        # mandatory; nothing defaults to a mode
flask --app degreeplan.wsgi db upgrade              # creates instance\users.sqlite3 and plans.sqlite3
flask --app degreeplan.wsgi dev seed                # sample data; random passwords are printed ONCE
flask --app degreeplan.wsgi run --debug             # http://localhost:5000
```

In development a random, per-start session key is generated if `SECRET_KEY` is unset, and cookies are not `Secure`
(so plain `http://localhost` works). Optional settings can go in `backend\.env` (git-ignored; see
`.env.example`). `.env` is **only** read when `APP_ENV=development`.

## Quality gates

```powershell
cd backend
pytest --cov                   # unit, integration and security tests (coverage must stay >= 85%)
ruff check src tests
mypy src
bandit -q -r src -c pyproject.toml
pip-audit -r requirements\prod.txt --no-deps --disable-pip
```

CI (`.github/workflows/ci.yml`) runs the same checks plus `gitleaks` for secrets.

## Deploying

Follow [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Day-2 tasks (backups, restores, incidents, user management) are in
[docs/OPERATIONS.md](docs/OPERATIONS.md).
