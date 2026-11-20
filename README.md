# Student degree plan tool

EduNav web application with a Flask API, separate users and plans databases, and Windows Server deployment tooling.
The integrated HTML/CSS/JavaScript frontend requires no Node build step. Version 1.1 adds persisted grades and
server-calculated planning GPA, production data management, and safer Windows installation/hardening workflows.
Version 1.2 adds student self-registration with password validation and optional degree-program selection.
Version 1.3 enables student registration by default and adds administrator instructor/degree-program management.
Explicit disabled registration settings are still respected.

## Choose your setup

- **Windows Server production:** [quickstart](docs/WINDOWS_SERVER_QUICKSTART.md).
- **Windows 11 production:** [quickstart](docs/WINDOWS11_QUICKSTART.md).
- **Windows 11 demo:** [setup and presentation walkthrough](docs/WINDOWS11_DEMO.md).
- **Production operations:** [accounts, catalog, backup/restore, upgrades](docs/OPERATIONS.md).

Production starts with fresh, separate SQLite databases under `C:\ProgramData\EduNav\production`, not demo records.
It uses protected non-administrator Windows services and defaults to **https://localhost:8443/**.
The demo remains **http://localhost:5000/** with repository-local sample databases. Actual Windows Server
service/TLS/firewall/restore acceptance must be performed on staging; automated tests alone do not certify a hardened host.

| Path | Contents |
| --- | --- |
| `backend/` | the `degreeplan` package, migrations, tests, helper scripts, hash-pinned requirements |
| `backend/src/degreeplan/frontend/` | integrated EduNav pages and browser API client |
| `deploy/` | Windows Server install, backup and uninstall scripts; service and Caddy (TLS) templates |
| `docs/` | documentation (below) and integration examples |

## Quick start

```powershell
cd backend
.\scripts\demo.ps1        # sets everything up, prints demo passwords once, serves http://localhost:5000
```

Open **http://localhost:5000/** and sign in using a demo student account (for example `alice`) and the password
shown by the script. Create a plan, add semesters/courses, and use My Degree to update student-reported progress.
Prefer the IPv4 loopback HTTP URL printed by the launcher if you also use production HTTPS: browsers may remember
a `localhost` HSTS policy and otherwise upgrade the HTTP demo to the wrong protocol.
On later launches, new random passwords for `admin`, `alice` and `teacher1` are displayed without deleting any data.
Use `.\scripts\demo.ps1 -KeepDemoPasswords` from `backend` to keep existing/manually chosen passwords instead.
Use `.\scripts\demo.ps1 -Help` (or `-h`) for the built-in demo guide, options and troubleshooting without making changes.
Use `.\scripts\demo.ps1 -ClearSavedData` to start with **all students' saved plans and progress deleted**, keeping
accounts and the catalog. Add `-KeepDemoPasswords` to preserve passwords too. Normal startup does not clear saved work.
Existing local databases are migrated, not reset. Browser-only data from the original frontend is not imported.
The catalog comes from the backend database, not the frontend's old demonstration arrays.

Frontend design source: [EduNav-2.0](https://github.com/TacticalTrunkMonkey443/EduNav-2.0),
commit `7db3e17f7ef14469df4ba50b6a3090c87a99a395`. Imported HTML/CSS retains the updated grade/GPA design; application
JavaScript is integrated here. The upstream snapshot has no license file; confirm redistribution rights before publishing.

### Upgrade an existing local demo

Older demo databases need a one-time explicit ownership check before the launcher will modify them:
`.\backend\scripts\demo.ps1 -AdoptExistingDemo -KeepDemoPasswords`.
Review/back up the pair first; never adopt production data as demo data. The launcher ignores dotenv redirects,
rejects database/file overrides and linked targets, and pins both demo databases to `backend\instance`.

## Student registration

In the demo, choose **Create a student account** on the sign-in page, complete the form, then sign in with your
new credentials. Self-registration creates a student account only; teachers/admins and advisor assignments remain
administrator-managed. Registration is not verification of institutional enrollment, and no email verification or
approval workflow is provided.

Registration is enabled by default in development and production. Use `-EnableRegistration:$false` in the Windows
installer to close signup, or `REGISTRATION_ENABLED=false` for manual deployments. Existing stored disabled choices
are preserved on upgrade; use `-EnableRegistration` explicitly to reopen them. `REGISTRATION_RATE_LIMIT` defaults to
`5 per minute;20 per hour` per client IP. Review abuse/shared-network limits before opening signup publicly.

## Administrator workspace

Choose **Administrator sign in**, or sign in normally with an admin account to open `admin.html`.
Administrators can create instructor accounts and create/edit degree programs and required-course lists.
Programme edits affect validation but do not rewrite students' saved semester plans, grades or progress.
Stale program edits are rejected until reloaded. Teachers remain read-only for assigned students.

For the demo, normal startup displays a freshly generated `admin` password; `-KeepDemoPasswords` preserves it.
Production's first admin must still be created with the protected operator CLI; no public admin registration or
hardcoded administrator password is provided. Import catalog courses through the operator workflow before building requirements.

## CJ and COMS foundation plans

Demo startup adds the user-supplied screenshot catalogs from `docs/examples/cj-coms-foundation.json`:

- **CJ — Criminal Justice:** 7 foundation courses, **21 foundation credits**.
- **COMS — Communication Studies:** 4 foundation courses, **12 foundation credits**.

These are explicitly labeled **Foundation only - partial**, not complete bachelor's-degree requirements.
The screenshots do not supply the full curriculum or footnote text; prerequisites and footnote conditions are
unverified, not inferred from superscript numbers. The 21/12 totals cover only the shown foundation sections.
New startup loads add missing course/program codes without replacing existing matching records, program edits,
requirements, saved plans, grades or accounts. They do not reassign students' programs. Select a major when
registering/creating a plan, or edit its requirements in Administration after official requirements are supplied.
Production does not automatically load these records; use the reviewed import workflow in `docs/OPERATIONS.md`.

## Build a production release

Run `.\backend\scripts\build_release.ps1` from a prepared development checkout. It creates a wheel, deployment
scripts, locked production dependencies, quickstarts and SHA256 manifests under `dist/`. Supply separately
verified Caddy/WinSW binaries to the target installer. No services or security policy are changed by the build.

## Tools

### Application development

- Python — backend application logic.
- Flask — web framework and REST API.
- SQLite — current users and degree-planning databases.
- Argon2 (argon2-cffi) — password hashing.
- Flask-Limiter — request rate limiting.
- Flask-CORS — controlled cross-origin access.
- PowerShell — demo launcher, setup, and administration scripts.
- pip and Python virtual environments (venv) — dependency installation and isolation.

## Documentation

| Document | Use it for |
| --- | --- |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | run locally, tests and quality gates, connect the frontend and the databases, helper scripts |
| [docs/WINDOWS_SERVER_QUICKSTART.md](docs/WINDOWS_SERVER_QUICKSTART.md) | fresh Windows Server production installation and host acceptance |
| [docs/WINDOWS11_QUICKSTART.md](docs/WINDOWS11_QUICKSTART.md) | Windows 11 local production services and HTTPS |
| [docs/WINDOWS11_DEMO.md](docs/WINDOWS11_DEMO.md) | isolated sample-data demo and presentation sequence |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | design, security model, API reference |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Windows Server install, configuration, upgrades, go-live checklist |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | monitoring, user administration, backups, incident response, troubleshooting |
