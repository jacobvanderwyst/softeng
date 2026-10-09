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

The two databases are separate on purpose. There are **no cross-database foreign keys**: `students.program_id` and
`plans.student_id` are plain integers that the application validates (a plan is only served after the users database
says the caller may see that student). Either database can therefore move to its own server by changing its URL.

## Code layout (`backend/src/degreeplan`)

| Area | Purpose |
| --- | --- |
| `config.py` | `Settings`: environment-driven, validated at startup, secrets via `NAME` or `NAME_FILE`, excluded from `repr` |
| `db/tables.py` | the **only** place that names tables and columns of both databases (SQLAlchemy Core) |
| `db/engines.py`, `db/migrate.py`, `migrations/` | one engine per database; two Alembic environments (`users`, `plans`) with baseline revisions |
| `repositories/` | all SQL (Core expressions, bound parameters only); callers own the transaction |
| `services/` | business rules without HTTP: `plan_rules` (prerequisites, credit limits), `access` (authorization) |
| `security/` | passwords, sessions, lockout, auth decorators, request protection (CSRF/Origin, headers, CORS, rate limit) |
| `api/` | Flask blueprints under `/api/v1`: `health`, `auth`, `catalog`, `plans` |
| `audit.py`, `logging_setup.py` | audit trail, JSON/text logging with redaction and request IDs |
| `cli.py` | operator commands: `db`, `users`, and `dev` (development only) |
| `devtools/` | sample data for development and tests; refuses to run in production and is not registered there |

Request flow: request ID → Origin/CSRF check → route → repositories → explicit `commit()` → security headers and
request log line.

## Security model

- **Secrets** are never in the repository. They come from the host environment or `<NAME>_FILE` files protected by
  ACLs. `APP_ENV` must be set explicitly; production refuses to start with a weak or missing key, relative or
  in-source SQLite paths, insecure cookies, disabled rate limiting, or non-HTTPS CORS origins. Development generates a
  random per-start key.
- **Passwords** are hashed with Argon2id (per-password salt). Policy: 12+ characters, not a common password, must not
  contain the username. Legacy scrypt hashes (`scrypt:n:r:p$salt$hash`) still verify and are upgraded on next login.
- **Sessions** are server-side. The cookie (`__Host-sid`: `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, no `Domain`)
  holds an opaque random token; the database stores only an HMAC of it keyed with `SECRET_KEY`. Sessions expire after
  30 min idle or 8 h total, rotate on login, and are revoked on logout, password change, account deactivation and
  `users revoke-sessions`. In development the cookie is `sid` and not `Secure`.
- **Request protection**: state-changing requests need the per-session `X-CSRF-Token` and, if an `Origin` header is
  present, it must be same-origin or listed in `CORS_ORIGINS`. Bodies must be JSON and are capped at 1 MB. CORS is off
  unless origins are configured. Responses carry CSP `default-src 'none'`, `nosniff`, `X-Frame-Options: DENY`,
  `no-referrer`, COOP/CORP, `Permissions-Policy`, `Cache-Control: no-store`, and HSTS when cookies are `Secure`.
- **Brute-force defence**: per-IP rate limit on login plus a persistent per-username lockout with exponential backoff
  (unknown usernames are tracked identically). Login answers are identical for unknown user, wrong password and
  disabled account.
- **Authorization** (least privilege; anyone without access gets `404`, so existence is not revealed):

  | Role | Plans |
  | --- | --- |
  | student | read and write their **own** plans |
  | teacher | read-only, only for students assigned to them (`students.advisor_id`) |
  | admin | read-only for all plans; accounts are managed through the CLI, not the API |

- **Logging**: `app.log` (request lines: method, path without query string, status, duration, user id, IP, request ID)
  and `audit.log` plus the `audit_log` table (logins, failures, lockouts, logouts, password and account changes, plan
  changes). Passwords, tokens, cookies, request bodies and URL credentials are never logged (a redactor scrubs
  everything). Unexpected errors are logged with a traceback and returned to clients as a generic message.

## API reference

Base path `/api/v1`. JSON in, JSON out. Errors always look like
`{"error": {"code": "...", "message": "...", "request_id": "...", "details": [...]}}`, and `request_id` is also the
`X-Request-ID` response header and appears in every server log line for that request.

**Rules for clients** (the example client in `docs/examples/api-client.js` does all of these):

1. Send `Content-Type: application/json` (otherwise `415`).
2. Send cookies (`fetch(..., { credentials: "include" })`): the session is an HttpOnly cookie.
3. Send `X-CSRF-Token` on every `POST`, `PUT`, `PATCH`, `DELETE`. The token comes from `login` or `GET /auth/me`; keep it
   in memory, not in `localStorage`.
4. Call `GET /auth/me` on page load to restore the session (and get a fresh CSRF token).
5. Treat `401` on a protected call as "show the login page".

| Method and path | Who | Body / query | Returns |
| --- | --- | --- | --- |
| `POST /auth/login` | anyone | `{username, password}` | `{user: {username, role, profile}, csrf_token}` + cookie |
| `GET /auth/me` | logged in | | same shape as login (fresh `csrf_token`) |
| `POST /auth/logout` | logged in | | `{ok: true}` |
| `POST /auth/change-password` | logged in | `{current_password, new_password}` | `{ok: true}`; revokes the user's other sessions |
| `GET /courses` | logged in | `?q=&limit=&offset=` (limit 1-100) | `{items: [{id, code, title, credits}], total, limit, offset}` |
| `GET /courses/{id}` | logged in | | course with `description` and `prerequisites` |
| `GET /programs`, `GET /programs/{id}` | logged in | | programs; one program adds `requirements` |
| `GET /plans` | student; staff with `?student_id=` | | `{items: [plan headers]}` |
| `POST /plans` | students | `{name, program_id}` | `201` + plan |
| `GET /plans/{id}` | owner, assigned teacher, admin | | plan with `courses[{course_id, code, title, credits, term_index}]` and `total_credits` |
| `PATCH /plans/{id}` | owner | `{name}` | plan |
| `DELETE /plans/{id}` | owner | | `204` |
| `PUT /plans/{id}/courses` | owner | `{course_id, term_index (1-16)}` | plan plus `issues[]` (adds or moves a course) |
| `DELETE /plans/{id}/courses/{course_id}` | owner | | `204` |
| `GET /plans/{id}/validation` | same as `GET /plans/{id}` | | `{valid, issues[{type, message, course_id, ...}]}` |
| `GET /health/live`, `GET /health/ready` | anyone | | `{status}` (`ready` also checks both databases and their migration revision) |

Issue types: `missing_prerequisite`, `prerequisite_order`, `term_credit_limit`, `missing_requirement`. A plan is valid
only when every required course of its program is in it.

| Status | Meaning | Client action |
| --- | --- | --- |
| `401` | not logged in, session ended, or wrong credentials at login | show the login page |
| `403` | missing/invalid CSRF token, foreign Origin, or a read-only role | refresh the token with `GET /auth/me`; otherwise hide the action |
| `404` | not found **or not yours** | treat as not existing |
| `409` | rule conflict (term credit limit, default 21) | show `error.message` |
| `415` | body was not JSON | fix the `Content-Type` header |
| `422` | validation | show `error.details[].field` and `.message` |
| `429` | rate limit or lockout (`Retry-After` header) | show "try again in N seconds" |
| `503` | database temporarily unavailable | retry later |
