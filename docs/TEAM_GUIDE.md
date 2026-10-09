# Team guide: run it, demo it, plug your work in

For teammates who need to **run the backend locally**, **present it**, and **connect the frontend and the database**
they built. Everything here has been run and checked; where something was only reasoned through it says so.

Contents: [1. What this is](#1-what-this-is) · [2. Run it in two minutes](#2-run-it-in-two-minutes) ·
[3. Demo walkthrough](#3-demo-walkthrough) · [4. Connect the frontend](#4-connect-the-frontend) ·
[5. Connect the database](#5-connect-the-database) · [6. Integration checklist](#6-integration-checklist) ·
[7. Troubleshooting](#7-troubleshooting)

## 1. What this is

A Python (Flask) API that sits between the **frontend** and **two databases**:

```
 Frontend (browser)  ──JSON over HTTP──▶  Backend API  ──▶  USERS database  (accounts, roles, sessions, audit)
                                                       └──▶  PLANS database  (programs, courses, prerequisites, plans)
```

It handles login (sessions, lockout), who may see what (students: own plans; teachers: their advisees, read-only;
admins: read-only), plan validation (prerequisites, credit limits), logging and auditing. It never renders pages:
the frontend does that and talks to `/api/v1/...`.

Right now it runs on **sample data**. Connecting your real data is section 5.

## 2. Run it in two minutes

You need Windows, **Python 3.11 or newer** and the repository. No admin rights, no installers.

```powershell
cd <repo>\backend
.\scripts\demo.ps1
```

That one command creates the virtual environment and installs the pinned dependencies (first run only), builds the
local databases, loads sample data, **prints the demo passwords once**, and starts the API at
`http://localhost:5000`. Copy the passwords when they appear. Options:

| Command | Effect |
| --- | --- |
| `.\scripts\demo.ps1 -Reset` | wipe the demo databases, reseed, get new passwords |
| `.\scripts\demo.ps1 -FrontendOrigin http://localhost:5173` | allow your frontend dev server (CORS), use its real origin |
| `.\scripts\demo.ps1 -Port 8080` | different port |
| `.\scripts\demo.ps1 -NoRun` | set everything up but do not start the server |

Check it is alive: open `http://localhost:5000/api/v1/health/ready` (should show `{"status":"ready"}`).
The sample accounts are `admin`, `teacher1`, `teacher2`, `alice`, `bob`, `carol` (alice and bob are teacher1's
students, carol is teacher2's).

> Passwords are random for every machine and every reset. Nothing is stored in the repository, so there is nothing
> to look up: if you lose them, run with `-Reset`.

## 3. Demo walkthrough

A ten-minute story that shows the interesting behaviour. Replace `<...>` with the printed passwords.

1. **Log in as a student.** `POST /api/v1/auth/login` with `{"username":"alice","password":"<alice password>"}`.
   The reply contains the user, profile and a `csrf_token`; the browser stores a session cookie.
2. **Browse the catalog.** `GET /api/v1/courses?q=math`, `GET /api/v1/courses/3` (shows CS301's two prerequisites),
   `GET /api/v1/programs/1` (the required courses).
3. **Build a plan.** `POST /api/v1/plans` `{"name":"My plan","program_id":1}`, then add courses with
   `PUT /api/v1/plans/{id}/courses` `{"course_id":3,"term_index":1}`. The reply includes `issues`: CS301 needs CS201 and
   MATH201, so you see `missing_prerequisite` immediately. Then place all six courses in a sensible order (CS101 and
   MATH101 in term 1, CS201 and MATH201 in term 2, CS301 and CS350 in term 3) and `GET /plans/{id}/validation` flips to
   `{"valid": true}` (a plan is only valid once every required course of the program is in it).
4. **Show the rules being enforced.** A write without the CSRF header returns `403`. A student cannot open another
   student's plan (`404`). (The per-term credit cap of 21 is enforced with `409` too, but the six sample courses total
   exactly 21 credits, so you cannot trigger it live; it is covered by the automated tests.)
5. **Log in as a teacher** (`teacher1`). They can read alice's plan (`GET /plans?student_id=1`) but a rename returns
   `403`, and carol's plan is invisible to them (`404`) because carol belongs to `teacher2`.
6. **Security.** Five wrong passwords for one username lock it (`429` with a `Retry-After`). Logging out really ends the
   session on the server (replaying the old cookie gives `401`).
7. **Audit trail.** Every login, failure and plan change is recorded. From `backend`:
   ```powershell
   $env:APP_ENV = "development"
   .\.venv\Scripts\python.exe -c "import sqlite3; db = sqlite3.connect('instance/users.sqlite3'); [print(r) for r in db.execute('select action, outcome, actor_role, target_type, target_id from audit_log order by id desc limit 15')]"
   ```

Say out loud: *this is sample data and a development configuration; production adds TLS, service accounts and secret
files* (see [DEPLOYMENT.md](DEPLOYMENT.md)).

**If something goes wrong live:** restart with `.\scripts\demo.ps1 -Reset` (about 10 seconds). A locked demo user
unlocks itself after 5 minutes, or reset.

## 4. Connect the frontend

### 4.1 Rules the frontend must follow

| Rule | Why |
| --- | --- |
| Send JSON with `Content-Type: application/json` | other content types get `415` |
| Send cookies: `fetch(..., { credentials: "include" })` | the session lives in an HttpOnly cookie |
| Send `X-CSRF-Token` on every `POST`, `PUT`, `PATCH`, `DELETE` | the token comes from `login` / `GET /auth/me`; keep it **in memory**, not `localStorage` |
| Treat any `401` as "show the login page" | sessions expire after 30 min idle / 8 h total, or when logged out elsewhere |
| On page load call `GET /auth/me` | restores the session after a refresh and returns a fresh CSRF token |
| Never read or store the session cookie | it is `HttpOnly` on purpose |

### 4.2 Drop-in client

`docs/examples/api-client.js` is a dependency-free ES module that does all of the above. It was tested against a live
server (24 checks: login, restore, CRUD, validation errors, 401 hook, teacher read-only, lockout).

```js
import { api, configureApi } from "./api-client.js";

configureApi({ baseUrl: "http://localhost:5000/api/v1", onUnauthorized: () => showLoginPage() });

const user = (await api.restoreSession()) ?? (await api.login(usernameInput.value, passwordInput.value));
const plan = await api.plans.create({ name: "My plan", program_id: 1 });
const result = await api.plans.setCourse(plan.id, { course_id: 3, term_index: 1 });
console.log(result.issues);                    // [{ type: "missing_prerequisite", message: "..." }]
```

Errors are `ApiError` objects with `status`, `code`, `details` (field errors for `422`), `requestId` (quote it when
reporting a bug: it matches the server log) and `retryAfter` (for `429`).

### 4.3 Making the browser allow it (CORS)

If the frontend dev server is on a different origin than the API (for example `http://localhost:5173` talking to
`http://localhost:5000`):

```powershell
.\scripts\demo.ps1 -FrontendOrigin http://localhost:5173
```

The origin must be exact: scheme, host and port, no path, no `*`. (For manual runs put `CORS_ORIGINS=http://localhost:5173`
in `backend\.env`; it is read only in development.) Verified: the allowed origin receives the CORS headers and any other
origin does not.

Alternative: proxy `/api` through the dev server so the browser sees one origin (for example Vite's `server.proxy`
pointing `/api` at `http://localhost:5000`). Keep `-FrontendOrigin` set to the dev server's origin as well. This
option was reasoned through but not run here.

### 4.4 Endpoint reference

All paths are under `/api/v1`. Errors always look like
`{"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}`.

| Method and path | Who | Body / query | Returns |
| --- | --- | --- | --- |
| `POST /auth/login` | anyone | `{username, password}` | `{user: {username, role, profile}, csrf_token}` + cookie |
| `GET /auth/me` | logged in | | same shape as login (fresh `csrf_token`) |
| `POST /auth/logout` | logged in | | `{ok: true}` |
| `POST /auth/change-password` | logged in | `{current_password, new_password}` | `{ok: true}`; revokes other sessions |
| `GET /courses` | logged in | `?q=&limit=&offset=` (limit 1-100) | `{items: [{id, code, title, credits}], total, limit, offset}` |
| `GET /courses/{id}` | logged in | | course + `description` + `prerequisites` |
| `GET /programs`, `GET /programs/{id}` | logged in | | programs; one program adds `requirements` |
| `GET /plans` | student; staff with `?student_id=` | | `{items: [plan headers]}` |
| `POST /plans` | students | `{name, program_id}` | `201` + plan |
| `GET /plans/{id}` | owner, assigned teacher, admin | | plan with `courses[{course_id, code, title, credits, term_index}]`, `total_credits` |
| `PATCH /plans/{id}` | owner | `{name}` | plan |
| `DELETE /plans/{id}` | owner | | `204` |
| `PUT /plans/{id}/courses` | owner | `{course_id, term_index (1-16)}` | plan + `issues[]` (adds or moves a course) |
| `DELETE /plans/{id}/courses/{course_id}` | owner | | `204` |
| `GET /plans/{id}/validation` | as `GET /plans/{id}` | | `{valid, issues[{type, message, course_id, ...}]}` |
| `GET /health/live`, `GET /health/ready` | anyone | | `{status}` |

Issue types: `missing_prerequisite`, `prerequisite_order`, `term_credit_limit`, `missing_requirement`.

### 4.5 Status codes

| Code | Meaning | Frontend action |
| --- | --- | --- |
| `401` | not logged in / session ended | show the login page (wrong password at login also gives 401) |
| `403` | missing CSRF token, bad Origin, or read-only role | refresh the token with `GET /auth/me`; otherwise hide the action |
| `404` | not found **or not yours** | treat as "doesn't exist" |
| `409` | rule conflict (term credit limit) | show `error.message` |
| `415` | body was not JSON | fix the `Content-Type` header |
| `422` | validation | show `error.details[].field` / `.message` next to inputs |
| `429` | too many attempts | show "try again in `retryAfter` seconds" |
| `503` | database temporarily unavailable | retry later |

## 5. Connect the database

First decide what you have. Run this against the other team's SQLite file to see its tables:

```powershell
cd <repo>\backend
.\.venv\Scripts\python.exe -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print('\n'.join(r[0] for r in c.execute('select sql from sqlite_master where sql is not null')))" D:\team\their.sqlite3
```

### 5.1 What the backend needs

**Plans database** (the catalog and the students' plans)

| Table | Columns the backend uses |
| --- | --- |
| `programs` | `id`, `code`, `name`, `total_credits` |
| `courses` | `id`, `code`, `title`, `credits`, `description` |
| `course_prerequisites` | `course_id`, `prerequisite_id` (one row per prerequisite) |
| `program_requirements` | `program_id`, `course_id` (courses a program requires) |
| `plans`, `plan_courses` | created by the backend itself (a student's plans and their courses by term) |

**Users database** (accounts)

| Table | Columns the backend uses |
| --- | --- |
| `users` | `username` (unique), `password_hash`, `role` (`student`/`teacher`/`admin`), `is_active` |
| `students` | `user_id`, `first_name`, `last_name`, `program_id`, `advisor_id` (the teacher's id) |
| `teachers` | `user_id`, `first_name`, `last_name`, `department` |
| `sessions`, `login_attempts`, `audit_log` | created and owned by the backend |

There are no foreign keys *between* the two databases: `students.program_id` and `plans.student_id` are plain ids that
the code checks.

### 5.2 Path A (recommended): copy their data into this schema

Fast, low risk, and the right choice for the presentation. Their database is only read; ours is filled by SQL you
write.

**Catalog (courses, programs, prerequisites):**

```powershell
cd <repo>\backend
$env:APP_ENV = "development"
$py = ".\.venv\Scripts\python.exe"

# Start from EMPTY tables (sample data would collide with yours): remove the demo databases, then migrate only.
Remove-Item -Recurse -Force instance -ErrorAction SilentlyContinue
& $py -m flask --app degreeplan.wsgi db upgrade

# 1. copy ..\docs\examples\import-catalog.sql and edit the source table/column names to match their schema
# 2. rehearse (runs everything, prints the resulting counts, then rolls back):
& $py scripts\run_sql_import.py --source D:\team\their.sqlite3 ..\docs\examples\import-catalog.sql --dry-run
# 3. do it for real:
& $py scripts\run_sql_import.py --source D:\team\their.sqlite3 ..\docs\examples\import-catalog.sql
```

The import is **one transaction**: any SQL error, duplicate or broken relationship rolls everything back and tells you
what was wrong. It was tested for: dry run, commit, rerun (rolls back cleanly), a dangling prerequisite (caught by the
foreign-key check), and an unmigrated target. After this, `.\scripts\demo.ps1` starts the API on your imported data
(it only seeds sample data when there is no database yet), and you create the accounts with the CSV script below.

**Accounts:** put one row per person in a CSV and let the script create them with random one-time passwords:

```csv
username,role,first_name,last_name,department,program_id,advisor
tina,teacher,Tina,Teacher,Mathematics,,
sam,student,Sam,Student,,1,tina
```

```powershell
.\scripts\bulk_create_users.ps1 -Csv .\people.csv -OutFile $env:USERPROFILE\new-accounts.csv -DryRun   # check first
.\scripts\bulk_create_users.ps1 -Csv .\people.csv -OutFile $env:USERPROFILE\new-accounts.csv
```

Teachers and admins are created before students, so `advisor` can name a teacher listed anywhere in the same file; a
bad row (unknown advisor, bad program id, duplicate username) is reported and the rest still go through. The output
file holds the generated passwords, is readable only by you, and is never overwritten: hand the passwords out
securely, then delete it. Tested end to end (the generated passwords log in, profiles and advisor links are correct).

Existing password hashes: if their hashes are in werkzeug's scrypt format (`scrypt:32768:8:1$salt$hash`) they can be
copied into `users.password_hash` and are upgraded on first login. Any other format (bcrypt, plain text, MD5) cannot be
used: create fresh accounts with the script above.

### 5.3 Path B: make the backend use their schema directly

Choose this only if their tables must stay as they are (other tools write to them).

1. **Map the names in one file**: `backend/src/degreeplan/db/tables.py`. Rename tables/columns there; the repositories
   only use those objects. Where meaning differs (for example prerequisites stored as a text list) edit the matching
   function in `backend/src/degreeplan/repositories/`.
2. **Keep the tests honest**: the test-suite builds databases from the migrations and compares them with `tables.py`
   (`tests/integration/test_migrations.py`). After renaming, update `migrations/*/versions/*_0001_baseline.py` and
   `devtools/sample_data.py` to the same names so `pytest` stays green.
3. **Adopt the real file without changing it**: `flask --app degreeplan.wsgi db stamp --target plans` (and `users`).
   Stamping only records the schema version; it touches no data.
4. Point the backend at the files with `USERS_DATABASE_URL` / `PLANS_DATABASE_URL` (see
   [DEPLOYMENT.md](DEPLOYMENT.md#using-your-existing-databases)). The two must be different databases.

Extra columns the backend needs but their tables lack (`password_hash`, `is_active`, ...) go into a **new** Alembic
revision under `migrations/<target>/versions/`, not into the baseline.

### 5.4 Not SQLite (PostgreSQL, SQL Server)

Same code, different URL: `postgresql+psycopg://...` or `mssql+pyodbc://...` in `USERS_DATABASE_URL` /
`PLANS_DATABASE_URL`, plus the driver package added to `backend/pyproject.toml` and the lock files re-generated
(see [backend/README.md](../backend/README.md)). Use separate database accounts with least privilege (read-only on the
catalog tables). `db backup` is SQLite-only; use the server's native backups.

## 6. Integration checklist

Frontend owner
- [ ] Copy `docs/examples/api-client.js`, set `baseUrl`, wire `onUnauthorized` to the login page.
- [ ] Replace direct database or mock calls with the client; every write goes through the client (it adds the CSRF header).
- [ ] Call `restoreSession()` on page load; keep the CSRF token in memory only.
- [ ] Show `422` field errors, the `issues[]` from `setCourse`, and `429` retry messages.
- [ ] Run with `.\scripts\demo.ps1 -FrontendOrigin <your dev origin>` and click through all five roles' flows.

Database owner
- [ ] Share the schema dump (command in section 5) and a sample of real rows.
- [ ] Decide Path A or B together; for A, edit `import-catalog.sql` and run the dry run until the counts look right.
- [ ] Provide the people list (CSV) with advisor assignments and program ids.
- [ ] Check a teacher sees only their students and a student sees only their own plans with real data.

Backend owner
- [ ] Keep `pytest` green after any change (`cd backend; pytest`): it takes about 5 seconds.
- [ ] Re-run `python scripts\production_smoke_test.py` before deploying; follow [DEPLOYMENT.md](DEPLOYMENT.md).

## 7. Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `Python 3.11+ was not found` | install Python from python.org (tick "Add to PATH"), open a new terminal |
| `APP_ENV must be set` | manual runs need `$env:APP_ENV = "development"` first (`demo.ps1` sets it for you) |
| Browser blocks the request (CORS error) | start with `-FrontendOrigin <exact origin>`; check scheme and port |
| Login works in Postman but not in the browser | add `credentials: "include"` (the client does) and set CORS as above |
| Every write returns `403` "CSRF" | send `X-CSRF-Token`; after a page refresh call `restoreSession()` first |
| `403 Origin not allowed` | the page's origin is not the API's own origin nor in `CORS_ORIGINS` |
| `429` on login | too many failed attempts: wait 5 minutes, or `.\scripts\demo.ps1 -Reset` |
| Logged out after restarting the server | expected in development: the session key is random per start |
| `dev seed` fails with a constraint error | the database is already seeded: `.\scripts\demo.ps1 -Reset` |
| Import says "not migrated" | run `flask --app degreeplan.wsgi db upgrade` first (with `$env:APP_ENV = "development"`) |
| Quote this when asking for help | the `request_id` from the error body: it finds the exact server log lines |
