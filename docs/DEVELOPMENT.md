# Development guide

Run the backend locally, test it, and connect the frontend and the databases. Design and the API reference are in
[ARCHITECTURE.md](ARCHITECTURE.md); installing on a server is in [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Run locally

Requirements: Windows, Python 3.11 or newer, this repository. No admin rights.

```powershell
cd backend
.\scripts\demo.ps1
```

The script creates `backend\.venv` and installs the hash-pinned dependencies (first run only), builds the databases in
`backend\instance`, loads sample data, **prints the demo passwords once**, and serves the API on
`http://localhost:5000`. It sets `APP_ENV=development` for its own process only.

| Option | Effect |
| --- | --- |
| `-Help` / `-h` | show usage, demo steps and troubleshooting; no setup or password/data changes |
| `-Reset` | delete the local databases, reseed, new passwords |
| `-ClearSavedData` | delete all students' saved plans, semesters, planned courses and progress; retain accounts/catalog |
| `-AdoptExistingDemo` | explicitly adopt an integrity-checked legacy repository-local demo pair once |
| `-KeepDemoPasswords` | preserve passwords instead of generating new ones for `admin`, `alice` and `teacher1` |
| `-FrontendOrigin http://localhost:5173` | allow that exact origin through CORS (the frontend dev server) |
| `-Port 8080` | different port |
| `-NoRun` | prepare everything but do not start the server |

Sample accounts: `admin`; teachers `teacher1`, `teacher2`; students `alice`, `bob` (advised by teacher1) and `carol`
(advised by teacher2). On first setup, all six random passwords are printed once. On each subsequent launch,
fresh random passwords for **admin**, **alice** and **teacher1** are generated and displayed, their old sessions revoked,
and their username lockouts cleared. Plans, progress, profiles and other accounts are preserved; password hashes
are stored, but plaintext passwords are not saved to a file or logged.
Copy the displayed passwords for the demo. Use `-KeepDemoPasswords` to retain manually chosen passwords or to run
against a custom development database without these demo accounts. The switch does not suppress initial
account creation on a fresh/reset database. `-NoRun` still performs password setup unless `-KeepDemoPasswords` is set.
Do not use `-Reset` just to recover a password: it deletes local demo data.

**Clean demo without rebuilding accounts/catalog:** run `.\scripts\demo.ps1 -ClearSavedData`.
To keep current passwords too, run `.\scripts\demo.ps1 -ClearSavedData -KeepDemoPasswords`.
This permanently clears saved work for **every student**, not just Alice. It preserves profiles, advisor assignments,
catalog courses, prerequisites, program requirements and existing audit history. Normal password refresh still occurs
unless `-KeepDemoPasswords` is supplied. Clearing runs even with `-NoRun`; `-Help` never clears anything.
`-Reset` takes precedence if both wipe options are supplied. Neither option creates a backup; stop any running
demo server first and back up anything you want to keep. Clearing is transactional within the plans database.

Development mode differs from production in three ways: the session key is random on every start (so a restart logs
everyone out), cookies are named `sid` and are not `Secure` (so plain `http://localhost` works), and the databases
default to `backend\instance\users.sqlite3` and `plans.sqlite3`. Optional settings can go in `backend\.env`
(git-ignored, template in `backend\.env.example`); it is read **only** for manual development when `APP_ENV=development`.
The managed `demo.ps1` instead sets `EDUNAV_DEMO=1`, skips dotenv, pins database paths, rejects file/direct URL
redirects, and checks `.edunav-demo.json` ownership before mutation. Existing unmarked databases require
`-AdoptExistingDemo` once. Linked/junction/hard-linked data targets are refused.
See [WINDOWS11_DEMO.md](WINDOWS11_DEMO.md) for the current demo guide and
[WINDOWS11_QUICKSTART.md](WINDOWS11_QUICKSTART.md) for the separate production installation.

**Without the script:**

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements\dev.txt
pip install --no-deps -e .

$env:APP_ENV = "development"                      # mandatory for every command below
flask --app degreeplan.wsgi db upgrade
flask --app degreeplan.wsgi dev seed
flask --app degreeplan.wsgi run                   # add --debug for auto-reload
```

**Commands** (all via `flask --app degreeplan.wsgi <group> <command>`; add `--help` for arguments):

| Group | Commands |
| --- | --- |
| `db` | `upgrade`, `status`, `stamp --target users\|plans`, `backup --dest <folder>` |
| `users` | `create`, `list`, `set-password`, `deactivate`, `activate`, `revoke-sessions` |
| `dev` | `seed`, `refresh-demo-passwords`, `clear-saved-data --yes` (development only; absent in production) |
| `catalog` | `import FILE [--dry-run] [--update]` (validated additive version-1 JSON import) |

Production also supports `db verify`, `db validate-schema --target users|plans --revision current`, profile
assignment via `users update-profile`, and `users revoke-all-sessions --yes`; use the installed operator wrapper
for production, not shell-exported development database URLs. See [OPERATIONS.md](OPERATIONS.md).

## 2. Tests and quality gates

```powershell
cd backend
pytest --cov                                              # unit, integration and security tests (coverage floor 85%)
ruff check src tests scripts
mypy src
bandit -q -r src -c pyproject.toml
pip-audit -r requirements\prod.txt --no-deps --disable-pip
python scripts\production_smoke_test.py                   # real CLI + waitress with production settings in a temp folder
```

CI (`.github/workflows/ci.yml`) runs the same checks plus `gitleaks`. The tests build their databases from the real
migrations, so a schema change that is missing from `db/tables.py` or the migrations fails a test.

Refresh the hash-pinned lock files after changing `pyproject.toml` (review the diff, then re-run the tests):

```powershell
pip-compile --generate-hashes --strip-extras -o requirements\prod.txt pyproject.toml
pip-compile --generate-hashes --strip-extras --allow-unsafe --extra dev -o requirements\dev.txt pyproject.toml
```

## 3. Connect the frontend
### Included EduNav application

Run `.\scripts\demo.ps1` from `backend`, then open `http://localhost:5000/`. Flask serves the pages and API on
one origin; no separate frontend server, npm installation, build, or CORS setting is needed. Assets live in
`backend/src/degreeplan/frontend/` so they are included in both development and installed application packages.
Do not open the HTML directly with `file://`; it needs the running API and session cookie.

Sign in as a student, create/select a saved plan, and add semesters and courses in Planner. My Degree stores
student-reported Completed/In Progress/Not Started statuses; Dashboard and Courses use the same saved progress.
These statuses are not official transcript records, are shared across alternate plans, and do not override the
planner's prerequisite-order validation. Reset Progress affects all statuses for that student; Reset Planner
affects only the selected plan. Multi-request resets reload the server state on failure and may need retrying.
Letter grades A/B/C/D/F are now stored with progress. Status-only changes preserve a saved grade; explicit null clears
it. Only Completed, validly graded, positive-credit records contribute to server-calculated planning GPA across plans.
An F contributes zero quality points, not earned-credit approval. Existing status-only records migrate with null grades.
Teachers/admins enter a student ID to view authorized records read-only; the server enforces access.
Semester indices remain stable (1–16), including after deletion; new dated semesters append in chronological order.
Rename/reuse a term or create another plan when the highest remaining term index is 16.

Existing databases are upgraded by the demo script without reseeding. The old frontend's localStorage records are
not imported, and no hardcoded completed courses are assumed. The sample backend catalog remains demonstration
data until replaced with an official catalog.

Dependency-free frontend unit/client tests (Node 20+): `node --test tests/frontend/*.test.mjs` from the repository root.
Optional real-browser verification: install `playwright` in the backend venv, then run
`python scripts/frontend_smoke_test.py` from `backend` (uses installed Edge/Chrome, isolated temporary databases,
and generated test credentials; never touches your local demo data).

### Separate/custom clients

Follow the client rules in [ARCHITECTURE.md](ARCHITECTURE.md#api-reference). `docs/examples/api-client.js` is a
dependency-free ES module that implements them (cookies, CSRF header, uniform `ApiError`, a single hook for expired
sessions) and wraps every endpoint; its header comment shows the usage. It was tested against a live server.

If the frontend dev server runs on a different origin than the API (for example `http://localhost:5173` calling
`http://localhost:5000`), allow that exact origin:

```powershell
.\scripts\demo.ps1 -FrontendOrigin http://localhost:5173
```

For manual runs set `CORS_ORIGINS=http://localhost:5173` instead (environment or `backend\.env`). The value must be an
exact origin: scheme, host and port, no path, no `*`. An alternative is proxying `/api` through the frontend dev server
so the browser sees one origin (not tested here); keep the dev server's origin in `CORS_ORIGINS` either way.

## 4. Connect the databases

### 4.1 What the backend needs

| Database | Table | Columns used |
| --- | --- | --- |
| plans | `programs` | `id`, `code`, `name`, `total_credits` |
| plans | `courses` | `id`, `code`, `title`, `credits`, `description` |
| plans | `course_prerequisites` | `course_id`, `prerequisite_id` (one row per prerequisite) |
| plans | `program_requirements` | `program_id`, `course_id` |
| plans | `plans`, `plan_courses` | created and owned by the backend |
| plans | `plan_semesters` | `plan_id`, `term_index`, `name` (optional names and empty terms) |
| plans | `student_course_progress` | `student_id`, `course_id`, `status`, `updated_at` (self-reported, not official) |
| users | `users` | `username` (unique), `password_hash`, `role` (`student`/`teacher`/`admin`), `is_active` |
| users | `students` | `user_id`, `first_name`, `last_name`, `program_id`, `advisor_id` (the teacher's id) |
| users | `teachers` | `user_id`, `first_name`, `last_name`, `department` |
| users | `sessions`, `login_attempts`, `audit_log` | created and owned by the backend |

To see another SQLite database's schema:

```powershell
cd backend
.\.venv\Scripts\python.exe -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print('\n'.join(r[0] for r in c.execute('select sql from sqlite_master where sql is not null')))" D:\data\source.sqlite3
```

### 4.2 Path A: copy existing data into the backend schema (recommended)

The source database is only read; the plans database is filled by the SQL statements of the import file.

**Catalog** (programs, courses, prerequisites):

```powershell
cd backend
$env:APP_ENV = "development"
$py = ".\.venv\Scripts\python.exe"

# Start from empty tables (sample data would collide): remove the local databases, then migrate only.
Remove-Item -Recurse -Force instance -ErrorAction SilentlyContinue
& $py -m flask --app degreeplan.wsgi db upgrade

# 1. copy ..\docs\examples\import-catalog.sql and edit its source table/column names to match the source schema
# 2. rehearse: runs everything, prints the resulting row counts, then rolls back
& $py scripts\run_sql_import.py --source D:\data\source.sqlite3 ..\docs\examples\import-catalog.sql --dry-run
# 3. import for real
& $py scripts\run_sql_import.py --source D:\data\source.sqlite3 ..\docs\examples\import-catalog.sql
```

The import is one transaction with foreign keys enforced and re-checked at the end: any SQL error, duplicate, or
dangling reference rolls everything back and reports what was wrong. Afterwards `.\scripts\demo.ps1` serves the
imported data (it seeds sample data only when no database exists yet).

**Accounts** from a CSV (teachers and admins are created before students, so `advisor` may name a teacher anywhere in
the file; a bad row is reported and the rest still go through):

```csv
username,role,first_name,last_name,department,program_id,advisor
tina,teacher,Tina,Teacher,Mathematics,,
sam,student,Sam,Student,,1,tina
```

```powershell
.\scripts\bulk_create_users.ps1 -Csv .\people.csv -OutFile $env:USERPROFILE\new-accounts.csv -DryRun   # validate first
.\scripts\bulk_create_users.ps1 -Csv .\people.csv -OutFile $env:USERPROFILE\new-accounts.csv
```

Every account gets a random initial password. The output file contains them, is readable only by the current user, and is never
overwritten: distribute the passwords securely, then delete it. There is no "force change at first login" yet; users can
use the Change Password form or `POST /api/v1/auth/change-password`. For production use the manifest-backed
`operator.ps1` documented in [OPERATIONS.md](OPERATIONS.md); consult the bulk helper's updated `-Help` before automation.

Existing password hashes: werkzeug-style scrypt hashes (`scrypt:32768:8:1$salt$hash`) can be copied into
`users.password_hash` and are upgraded to Argon2id at first login. Any other format (bcrypt, MD5, plain text) cannot
be used: create fresh accounts as above.

### 4.3 Path B: use the existing schema directly

Only if the existing tables must stay unchanged (for example because other tools write to them).

1. **Map the names** in `backend/src/degreeplan/db/tables.py`; repositories use only those objects. Where the meaning
   differs (for example prerequisites stored as a text list), edit the matching function in
   `backend/src/degreeplan/repositories/`.
2. **Keep the tests green**: they build databases from the migrations and compare them with `tables.py`
   (`tests/integration/test_migrations.py`). Update `migrations/*/versions/*_0001_baseline.py` and
   `devtools/sample_data.py` to the same names.
3. **Adopt the real file without changing it**: `flask --app degreeplan.wsgi db stamp --target plans` (and `users`).
   Stamping records the schema version only; no data is touched.
4. Point the backend at the files with `USERS_DATABASE_URL` / `PLANS_DATABASE_URL`
   (see [DEPLOYMENT.md](DEPLOYMENT.md#using-existing-databases)). The two must differ.

Columns the backend needs but the existing tables lack (`password_hash`, `is_active`, ...) go into a **new** revision, not the
baseline: add `migrations/<target>/versions/<target>_0002_<name>.py` with `down_revision = "<target>_0001"` and an
`upgrade()` that uses `op.add_column(...)` (copy the structure of the baseline file).

### 4.4 Other database servers (PostgreSQL, SQL Server)

Same code, different URL: `postgresql+psycopg://...` or `mssql+pyodbc://...` in `USERS_DATABASE_URL` /
`PLANS_DATABASE_URL`. Add the driver to `backend/pyproject.toml` and regenerate the lock files (section 2). Use separate
database accounts with least privilege (read-only on the catalog tables). `db backup` supports SQLite only; use the
server's native backups.

## 5. Helper scripts (`backend/scripts`)

| Script | Purpose |
| --- | --- |
| `demo.ps1` | venv, migrations, sample data (random passwords shown once), start the API |
| `run_sql_import.py` | transactional `INSERT ... SELECT` import from another SQLite database (`--dry-run`) |
| `bulk_create_users.ps1` | create accounts from a CSV with random one-time passwords |
| `production_smoke_test.py` | production-settings check: CLI, waitress, proxy headers, cookies, CSRF/Origin, log hygiene |
