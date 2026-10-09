# Security

## Principles

1. **No secrets in code, config templates, tests, logs or git.** Secrets come from the host (environment variables or
   `*_FILE` secret files protected by ACLs). The repository contains no accounts and no passwords; sample data uses
   randomly generated passwords that are printed once.
2. **Fail closed.** Production refuses to start with weak or missing configuration. `APP_ENV` must be set explicitly.
3. **Least privilege** for users (role-scoped access), for services (non-admin virtual accounts, loopback-only
   listener), and for the files they can touch (ACLs).
4. **Defence in depth** (TLS + HSTS, same-origin cookies, Origin and CSRF checks, rate limiting, lockout, input
   validation, parameterized SQL, redacted logs).
5. **Everything security-relevant is audited**, and the audit stream can be shipped off the host.

## Controls

| Area | Control | Where |
| --- | --- | --- |
| Transport | TLS 1.2+/HSTS at Caddy; app only on loopback; `Secure` cookies enforced in production | `deploy/caddy`, `config.py` |
| Passwords | Argon2id (RFC 9106 low-memory parameters), per-password salt, policy (12+ chars, denylist, no username), legacy scrypt upgrade on login | `security/passwords.py` |
| Login | Identical responses for unknown user / wrong password / disabled account; constant-work verification; per-IP rate limit; persistent per-username lockout with backoff | `api/auth.py`, `security/lockout.py` |
| Sessions | Opaque random token, HMAC-stored, server-side revocation, idle + absolute timeouts, rotation on login, `__Host-` cookie | `security/sessions.py` |
| CSRF | Per-session token header + Origin check on all state-changing requests; `SameSite=Lax` | `security/protection.py` |
| Authorization | Student: own plans; teacher: assigned students, read-only; admin: read-only plans; denials return 404 | `services/access.py` |
| Input | Strict pydantic models (`extra=forbid`), JSON content-type required, 1 MB body cap, bounded pagination | `validation.py`, `api/` |
| SQL | SQLAlchemy Core with bound parameters only; LIKE wildcards escaped | `repositories/` |
| Headers | CSP `default-src 'none'`, `nosniff`, `X-Frame-Options: DENY`, `no-referrer`, COOP/CORP, `Permissions-Policy`, `Cache-Control: no-store` | `security/protection.py` |
| Errors | Uniform JSON; unexpected errors are generic to clients and fully logged server-side | `errors.py` |
| Logging | Redaction of passwords/tokens/cookies/URL credentials; request IDs; no query strings or bodies | `logging_setup.py` |
| Audit | DB table + separate log stream for auth, account and plan events | `audit.py` |
| Secrets | `*_FILE` support, key rotation with fallbacks, secrets excluded from `repr`, generated with the OS CSPRNG | `config.py`, `install.ps1` |
| Supply chain | Hash-pinned dependencies installed with `--require-hashes`; `pip-audit`, `bandit`, `ruff`, `mypy`, `gitleaks` in CI | `backend/requirements`, `.github/workflows` |
| Host | Non-admin service accounts, strict ACLs, firewall limited to 80/443, binaries verified by SHA-256 | `deploy/windows/install.ps1` |

## Threats considered

- **Credential stuffing / brute force**: rate limit, lockout, strong hashing, uniform responses, audit trail.
- **Stolen database or backup**: no plaintext passwords; session tokens are stored only as keyed hashes; backups are
  administrator-only and must be stored encrypted offsite.
- **Stolen cookie**: `HttpOnly` + `Secure` + `__Host-`; server-side revocation; short idle timeout.
- **CSRF / cross-site requests**: Origin allow-list, CSRF token, `SameSite=Lax`, no CORS unless configured.
- **Injection**: parameterized SQL; strict input validation; JSON-only API; no HTML rendering.
- **Privilege escalation / IDOR**: authorization is evaluated per object against the users database; outsiders see
  `404`, not `403`.
- **Misconfiguration**: fail-fast configuration validation; production cannot run with debug, insecure cookies,
  disabled rate limits or placeholder keys; development tooling is not registered in production.
- **Information leakage**: generic errors, minimal health endpoints, redacted logs, and the `Server` header is
  stripped by Caddy (waitress itself would send one, which is another reason it only listens on loopback).

## Residual risks and limits (read before go-live)

- **Data at rest**: SQLite files are not encrypted by the application. Use BitLocker (or equivalent) and the ACLs the
  installer sets, or move to a server database with transparent data encryption.
- **No MFA or SSO**: authentication is username/password only. If the institution has SSO, integrating it would
  replace the login flow (and remove password storage entirely for those users).
- **Audit integrity**: audit rows live in the users database; an attacker with host administrator access could alter
  them. Ship `audit.log` to a separate log server for tamper resistance.
- **Single-process rate limiting** uses memory; use Redis if scaling out (see OPERATIONS.md).
- **Account recovery** is administrator-assisted (no e-mail reset), which avoids reset-token attacks but needs a
  verification procedure.
- **Self-managed TLS**: certificate renewal depends on the Caddy service and reachable ports; monitor expiry.
- **Privacy/compliance**: the system handles student education records. Confirm retention periods, access reviews
  and incident-notification duties (for example FERPA) with your institution; the audit trail supports access
  reviews but is not a compliance program by itself.

## Dependency and patch management

- Production dependencies are pinned with hashes in `backend/requirements/prod.txt` (generated by `pip-compile`).
  To update: `pip-compile --generate-hashes --strip-extras -o requirements/prod.txt pyproject.toml` (and the same with
  `--extra dev -o requirements/dev.txt`), run the test suite, review the diff.
- CI runs `pip-audit` on the production lock file; also run it before every release. Apply Windows, Python, Caddy and
  WinSW updates on a regular schedule.

## Reporting

Report suspected vulnerabilities privately to the project maintainers (do not open public issues containing exploit
details or real data).

## Pre-launch checklist

- [ ] `APP_ENV=production`; the service starts cleanly with the real configuration.
- [ ] `secrets\secret_key` exists, is unreadable by ordinary users (`icacls`), and is backed up securely by an
      administrator (losing it only logs everyone out).
- [ ] No default or shared accounts exist: `users list` shows only intended accounts; every account has a unique
      password.
- [ ] Database files are outside the source tree on an encrypted volume; `backups` is administrators-only.
- [ ] TLS works for the real host name; HTTP redirects to HTTPS; scanner shows TLS 1.2/1.3 only; HSTS present.
- [ ] Only ports 80/443 are reachable from outside; `8000` is bound to loopback.
- [ ] Both services run as `NT SERVICE\...` accounts, not as an administrator or SYSTEM.
- [ ] Backups run nightly, a **restore has been tested**, and copies go offsite encrypted.
- [ ] `audit.log` is shipped to a separate system; alerts exist for service down, `ready` failing and login bursts.
- [ ] CI is green (tests, `ruff`, `mypy`, `bandit`, `pip-audit`, `gitleaks`); `git log -p` has been scanned for secrets.
- [ ] Teachers' student assignments (`advisor`) have been loaded and spot-checked (a teacher sees only their students).
- [ ] The frontend sends `X-CSRF-Token`, handles `401` by returning to login, and does not store tokens in
      `localStorage`.
- [ ] Incident contacts and the procedures in OPERATIONS.md are known to the on-call staff.
