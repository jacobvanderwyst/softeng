# Architecture

## Overview

```
 Browser ──HTTPS──▶ Caddy (TLS, headers, static frontend)
                      │  /api/*  (loopback only)
                      ▼
                 waitress ──▶ Flask app "degreeplan"
                                 │
                 ┌───────────────┴───────────────┐
                 ▼                               ▼
           USERS database                  PLANS database
   users, students, teachers,        programs, courses, prerequisites,
   sessions, login_attempts,         program_requirements, plans,
   audit_log                         plan_courses
```

Two databases, deliberately separate:

- **Users database**: identities, roles, sessions, login lockout counters, audit trail.
- **Plans database**: the degree catalog and the students' plans.

There are **no cross-database foreign keys**. `students.program_id` and `plans.student_id` are plain integers; the
application layer validates them (for example, a plan is only served after the users database says the caller may
see that student). This is what makes it possible to move either database to a different server later.

## Code layout (`backend/src/degreeplan`)

| Area | Purpose |
| --- | --- |
| `config.py` | `Settings`: environment-driven, fail-fast, secrets via `NAME` or `NAME_FILE`, secrets excluded from `repr` |
| `db/engines.py` | One SQLAlchemy engine per database, request-scoped connections, SQLite pragmas |
| `db/tables.py` | **Single place that names tables/columns** for both databases (SQLAlchemy Core) |
| `db/migrate.py`, `migrations/` | Two Alembic environments (`users`, `plans`) with baseline migrations |
| `repositories/` | All SQL (Core expressions, bound parameters only). Callers own the transaction |
| `services/` | Business rules with no HTTP: `plan_rules` (prerequisites, credit limits), `access` (authorization) |
| `security/` | `passwords` (Argon2id), `sessions` (server-side), `lockout`, `auth` (decorators), `protection` (CSRF/Origin, headers, CORS, rate limit) |
| `api/` | Flask blueprints under `/api/v1`: `health`, `auth`, `catalog`, `plans` |
| `audit.py`, `logging_setup.py` | Audit trail (DB + log stream), JSON/text logging with redaction and request IDs |
| `cli.py` | Operator commands: `db`, `users`, and `dev` (development only) |
| `devtools/` | Sample data for development and tests. Refuses to run in production and is not even registered there |

Request flow: `before_request` (request ID) → Origin/CSRF check → route → repository calls → explicit `commit()` →
`after_request` (security headers, request log line).

## Authentication and sessions

- `POST /api/v1/auth/login` verifies the password (Argon2id), creates a **server-side session**, and sets the
  `__Host-sid` cookie (`Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`).
- The cookie is an opaque 256-bit random token. The database stores only an **HMAC-SHA-256 of the token** (keyed by
  `SECRET_KEY`), so a leaked database cannot be turned into valid cookies.
- Sessions have an idle timeout (default 30 min) and an absolute lifetime (default 8 h). They are revoked on logout,
  password change (other sessions), account deactivation, and `users revoke-sessions`.
- A per-session CSRF token is returned by login and `/auth/me`. State-changing requests must send it in
  `X-CSRF-Token`, and any `Origin` header must be same-origin or an explicitly configured origin.
- Brute-force defenses: per-IP rate limit on login, plus a persistent per-username lockout with exponential
  backoff. Unknown usernames are tracked the same way so responses never reveal whether an account exists.

## Authorization

| Role | Plans |
| --- | --- |
| student | read and write **their own** plans only |
| teacher | read-only, **only for students assigned to them** (`students.advisor_id`) |
| admin | read-only for all plans; account management happens through the CLI, not the API |

Anyone without access gets `404` (not `403`), so plan existence is not revealed. A user who can see a plan but not
change it gets `403` on writes.

## Data adaptation (your real schemas)

The baseline migrations describe the schema this code expects. To run against existing databases:

1. Compare the real schema with `db/tables.py`. Rename tables/columns **there**; repositories only use those objects.
2. If the schemas then match the baseline, adopt them without changing data: `db stamp --target users|plans`.
3. If you need extra columns (for example password hashes in an existing users table), add a new Alembic revision
   under `migrations/<target>/versions/` instead of editing the baseline.

Passwords must be stored as Argon2id hashes; legacy scrypt hashes (`scrypt:n:r:p$salt$hash`) are verified and
upgraded to Argon2id on the user's next login.

## API summary

All routes are under `/api/v1`. Errors always look like
`{"error": {"code", "message", "request_id", "details"?}}` and the same `request_id` is in the `X-Request-ID` header
and in every log line for that request.

- `GET /health/live`, `GET /health/ready` (databases reachable and migrated)
- `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password`
- `GET /courses?q=&limit=&offset=`, `GET /courses/{id}`, `GET /programs`, `GET /programs/{id}`
- `GET /plans` (staff: `?student_id=`), `POST /plans`, `GET|PATCH|DELETE /plans/{id}`
- `PUT /plans/{id}/courses` (`{course_id, term_index}`: add or move), `DELETE /plans/{id}/courses/{course_id}`
- `GET /plans/{id}/validation` (prerequisite order, missing prerequisites/requirements, term credit limits)

Frontend contract: send JSON with `Content-Type: application/json`; use `credentials: "include"` when the API is on
another origin (or, better, serve both from one origin through Caddy and avoid CORS entirely); send the
`X-CSRF-Token` header on every `POST/PUT/PATCH/DELETE`; treat any `401` as "log in again".

## Logging

- `app.log`: application and request log (`LOG_FORMAT=json` recommended in production). Request lines contain
  method, path (never the query string or body), status, duration, user id, IP, and the request ID.
- `audit.log` plus the `audit_log` table: security events (login success/failure/lockout, logout, password changes,
  account changes, plan changes). No passwords, tokens or request bodies are ever recorded.
- All log output passes through a redactor for password/token/cookie/authorization values and URL credentials.
