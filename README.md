# Student degree plan tool

Backend API for the degree plan web application (Flask, separate users and plans databases) plus Windows Server
deployment tooling.

| Path | Contents |
| --- | --- |
| `backend/` | the `degreeplan` package, migrations, tests, helper scripts, hash-pinned requirements |
| `deploy/` | Windows Server install, backup and uninstall scripts; service and Caddy (TLS) templates |
| `docs/` | documentation (below) and integration examples |
| `poc/` | standalone login demo, **not deployed**. `pip install -r requirements.txt`, then `python server.py --init-demo` (random demo passwords are printed once); `pytest` runs its tests |

## Quick start

```powershell
cd backend
.\scripts\demo.ps1        # sets everything up, prints demo passwords once, serves http://localhost:5000
```

## Documentation

| Document | Use it for |
| --- | --- |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | run locally, tests and quality gates, connect the frontend and the databases, helper scripts |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | design, security model, API reference |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Windows Server install, configuration, upgrades, go-live checklist |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | monitoring, user administration, backups, incident response, troubleshooting |
