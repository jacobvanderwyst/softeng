# Student degree plan tool

EduNav web application with a Flask API, separate users and plans databases, and Windows Server deployment tooling.
The integrated HTML/CSS/JavaScript frontend requires no Node build step.

| Path | Contents |
| --- | --- |
| `backend/` | the `degreeplan` package, migrations, tests, helper scripts, hash-pinned requirements |
| `backend/src/degreeplan/frontend/` | integrated EduNav pages and browser API client |
| `deploy/` | Windows Server install, backup and uninstall scripts; service and Caddy (TLS) templates |
| `docs/` | documentation (below) and integration examples |
| `poc/` | standalone login demo, **not deployed**. `pip install -r requirements.txt`, then `python server.py --init-demo` (random demo passwords are printed once); `pytest` runs its tests |

## Quick start

```powershell
cd backend
.\scripts\demo.ps1        # sets everything up, prints demo passwords once, serves http://localhost:5000
```

Open **http://localhost:5000/** and sign in using a demo student account (for example `alice`) and the password
shown by the script. Create a plan, add semesters/courses, and use My Degree to update student-reported progress.
On later launches, new random passwords for `alice` and `teacher1` are displayed without deleting any data.
Use `.\scripts\demo.ps1 -KeepDemoPasswords` from `backend` to keep existing/manually chosen passwords instead.
Use `.\scripts\demo.ps1 -Help` (or `-h`) for the built-in demo guide, options and troubleshooting without making changes.
Existing local databases are migrated, not reset. Browser-only data from the original frontend is not imported.
The catalog comes from the backend database, not the frontend's old demonstration arrays.

Frontend design source: [EduNav-2.0](https://github.com/TacticalTrunkMonkey443/EduNav-2.0),
commit `cde138a9e5133c6b189be8bb4787a7492fa6b6c4`. Imported HTML/CSS retains the original design; application
JavaScript is integrated here. The upstream snapshot has no license file; confirm redistribution rights before publishing.

## Documentation

| Document | Use it for |
| --- | --- |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | run locally, tests and quality gates, connect the frontend and the databases, helper scripts |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | design, security model, API reference |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Windows Server install, configuration, upgrades, go-live checklist |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | monitoring, user administration, backups, incident response, troubleshooting |
