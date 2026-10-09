# Deployment guide (Windows Server)

Layout: **Caddy** (TLS on 80/443) → **waitress** on `localhost:8000` → the Flask app. Both services run under
non-administrator virtual accounts, wrapped by **WinSW**.

> The scripts in `deploy\windows` are syntax-checked and the templates they render are validated, and the
> production configuration is exercised by `backend\scripts\production_smoke_test.py`, but the installer has not been
> run on a real server. Do a complete dry run on a staging machine first.

## Prerequisites

- Windows Server 2019 or later, PowerShell 5.1, an elevated PowerShell session.
- Python 3.11+ installed for all users, `python` on `PATH` (or pass `-PythonExe`).
- A DNS name for the site (for example `plans.example.edu`) pointing at the server, with inbound 80 and 443 reachable
  (80 serves the HTTPS redirect and automatic certificates).
- Disk encryption (BitLocker) on the volume holding `C:\ProgramData\DegreePlan`: the SQLite files are not encrypted by
  the application.
- Checksum-verified release binaries (the installer enforces the hashes): WinSW v3 (`WinSW-x64.exe`) and Caddy v2
  (`caddy.exe`). Compare with the published hashes:
  ```powershell
  (Get-FileHash C:\tools\WinSW-x64.exe -Algorithm SHA256).Hash
  (Get-FileHash C:\tools\caddy.exe -Algorithm SHA256).Hash
  ```
- The repository checked out at the release being deployed (for example `C:\src\degreeplan`).

## Install

```powershell
cd C:\src\degreeplan\deploy\windows
.\install.ps1 `
    -WinSWPath C:\tools\WinSW-x64.exe -WinSWSha256 <sha256-of-winsw> `
    -CaddyPath C:\tools\caddy.exe     -CaddySha256 <sha256-of-caddy> `
    -Hostname plans.example.edu
```

| Step | Detail |
| --- | --- |
| Install tree | `C:\Program Files\DegreePlan` (`venv`, `service`, `bin`, `caddy`) |
| Data tree | `C:\ProgramData\DegreePlan` (`secrets`, `data`, `logs`, `backups`, `www`, `caddy`) |
| Dependencies | `pip install --require-hashes -r backend\requirements\prod.txt`: nothing unpinned or unverified |
| Session key | 48 random bytes from the OS CSPRNG written to `secrets\secret_key` once; never displayed; kept on re-runs |
| Permissions | `secrets`: Administrators/SYSTEM full, service read-only; `data` and `logs`: service modify; `backups`: administrators only |
| Services | `DegreePlanApi` and `DegreePlanProxy` as `NT SERVICE\...` virtual accounts (no passwords, not administrators) |
| Network | API bound to `localhost:8000` only; firewall rules for 80/443 on Domain and Private profiles only |
| Migrations | `db upgrade` for new databases (`db stamp` with `-AdoptExistingDatabases`) |
| Verification | waits for `/api/v1/health/ready` before reporting success |

No password is written anywhere and there are no default accounts. Re-running the installer is safe: secrets and data
are kept, code and service definitions are refreshed.

### Using existing databases

```powershell
.\install.ps1 ... -UsersDatabasePath D:\db\users.sqlite3 -PlansDatabasePath D:\db\plans.sqlite3 -AdoptExistingDatabases
```

- The files must already match `backend\src\degreeplan\db\tables.py`; adapt it first if names differ
  ([DEVELOPMENT.md](DEVELOPMENT.md#43-path-b-use-the-existing-schema-directly)).
- `-AdoptExistingDatabases` runs `db stamp` instead of creating tables, so no data is touched. Back up first.
- Database files must be outside the source tree and use absolute paths; the app refuses to start otherwise.

### Server databases (PostgreSQL, SQL Server)

Set `USERS_DATABASE_URL_FILE` and `PLANS_DATABASE_URL_FILE` to files in `secrets` containing the full SQLAlchemy URLs
(credentials included), with separate least-privilege database accounts (read-only on `programs`, `courses`,
`course_prerequisites`, `program_requirements`; read/write on `plans`, `plan_courses`; no cross access). Add the driver
and refresh the lock files ([DEVELOPMENT.md](DEVELOPMENT.md#2-tests-and-quality-gates)). `db backup` supports SQLite
only.

## First administrator and accounts

```powershell
$cli = "C:\Program Files\DegreePlan\bin\degreeplan.ps1"
& $cli users create <admin-username> --role admin           # password is prompted (hidden, twice)
& $cli users create tina --role teacher --first-name Tina --last-name Teacher --department "Computer Science"
& $cli users create sam  --role student --first-name Sam  --last-name Student --program-id 1 --advisor tina
```

Passwords must pass the policy (12+ characters, not common, not containing the username). For many accounts use
`backend\scripts\bulk_create_users.ps1 -Cli $cli` ([DEVELOPMENT.md](DEVELOPMENT.md#42-path-a-copy-existing-data-into-the-backend-schema-recommended)).
Never put a password on a command line; `--password-stdin` reads one line from stdin for automation.

## Frontend

Copy the built frontend into `C:\ProgramData\DegreePlan\www`. Caddy serves it (single-page-app fallback to
`index.html`) and proxies `/api/*` to the backend, so both share one origin and no CORS is needed. If the frontend
must live elsewhere, add its exact HTTPS origin to `CORS_ORIGINS` (wildcards are rejected).

## Verify

```powershell
Get-Service DegreePlanApi, DegreePlanProxy                        # both Running
curl.exe -s https://plans.example.edu/api/v1/health/ready         # {"status":"ready"}
curl.exe -sI https://plans.example.edu/api/v1/health/live         # HSTS and nosniff present, no Server header
& "C:\Program Files\DegreePlan\bin\degreeplan.ps1" db status      # both databases ok
netstat -ano | findstr ":8000"                                    # listening on loopback only
icacls C:\ProgramData\DegreePlan\secrets                          # only SYSTEM, Administrators, service (read)
```

Then log in through the site, confirm the `__Host-sid` cookie is `Secure; HttpOnly`, check the login event in
`C:\ProgramData\DegreePlan\logs\audit.log`, and run an external TLS scan (TLS 1.2 and 1.3 only).

## Configuration reference

Environment variables of the `DegreePlanApi` service (rendered into its WinSW definition by `install.ps1`). Every
secret-bearing variable also accepts a `<NAME>_FILE` form pointing at a protected file.

| Variable | Default | Notes |
| --- | --- | --- |
| `APP_ENV` | none (**required**) | `production` on servers; the app refuses to start without it |
| `SECRET_KEY[_FILE]` | none (required) | 32+ random characters; placeholders are rejected |
| `SECRET_KEY_FALLBACKS[_FILE]` | empty | comma-separated retired keys during rotation |
| `USERS_DATABASE_URL[_FILE]`, `PLANS_DATABASE_URL[_FILE]` | none (required) | must differ; SQLite paths absolute and outside the source tree |
| `PROXY_HOPS` | `0` | number of trusted reverse proxies (`1` behind Caddy); never set when clients can reach the app directly |
| `CORS_ORIGINS` | empty | exact HTTPS origins, comma separated |
| `SESSION_IDLE_TIMEOUT`, `SESSION_ABSOLUTE_TIMEOUT` | 1800, 28800 s | idle must not exceed absolute |
| `LOGIN_RATE_LIMIT`, `DEFAULT_RATE_LIMIT` | `20 per minute`, `300 per minute` | per client IP |
| `LOCKOUT_THRESHOLD`, `LOCKOUT_SECONDS` | 5, 300 | per username, exponential backoff (max 1 h) |
| `RATELIMIT_STORAGE_URI[_FILE]` | `memory://` | use `redis://...` for more than one worker process |
| `RATELIMIT_ENABLED` | `true` | production refuses `false` |
| `COOKIE_SECURE` | `true` (`false` in development) | production refuses `false`; also selects the cookie name (`__Host-sid` or `sid`) |
| `MAX_CONTENT_LENGTH` | 1048576 | request body limit in bytes (allowed 1024 to 10485760) |
| `PASSWORD_MIN_LENGTH` | 12 | minimum password length (allowed 12 to 128) |
| `LOG_DIR`, `LOG_FORMAT`, `LOG_LEVEL` | none, `text`, `INFO` | files rotate at 10 MB x 10 |
| `MAX_CREDITS_PER_TERM` | 21 | plan rule |

Production refuses to start when the key is weak, a database URL is missing or shared, a SQLite path is relative or
inside the source tree, cookies are not `Secure`, rate limiting is disabled, or a CORS origin is `*` or not HTTPS. The
service passes `--no-clear-untrusted-proxy-headers` to waitress so the app sees the proxy's `X-Forwarded-*` headers
(needed for client IPs and the Origin check); this is safe because waitress listens on loopback only.

## Upgrades and rollback

1. Back up: `.\deploy\windows\backup.ps1`.
2. Update the code to the release (`git pull` / check out the tag).
3. Re-run `install.ps1` with the same arguments: it keeps secrets and data, reinstalls the pinned dependencies,
   re-renders the service definitions and applies new migrations.
4. Verify as above.

Rollback: stop the services, restore the pre-upgrade backup files over the databases
([OPERATIONS.md](OPERATIONS.md#restore)), check out the previous release, re-run `install.ps1`. Production migrations
are forward-only; the backup is the rollback mechanism.

## Session-key rotation

1. Rename `C:\ProgramData\DegreePlan\secrets\secret_key` to `secret_key_previous`.
2. Re-run `install.ps1`: a new key is generated and the old one is registered as a fallback, so existing sessions keep
   working.
3. After the absolute session lifetime (8 h by default), delete `secret_key_previous` and re-run `install.ps1`.

To invalidate every session at once (suspected key leak), delete `secret_key_previous` immediately after step 2.

## Uninstall

```powershell
.\deploy\windows\uninstall.ps1                       # removes services, firewall rules, program files; keeps data
.\deploy\windows\uninstall.ps1 -RemoveData -Force    # also deletes databases, secrets, logs and backups
```

## Go-live checklist

- [ ] The service starts cleanly with `APP_ENV=production` and the real configuration.
- [ ] `secrets\secret_key` is unreadable by ordinary users (`icacls`) and backed up by an administrator (losing it only
      logs everyone out).
- [ ] `users list` shows only intended accounts; none are shared or default; every password is unique.
- [ ] Database files are outside the source tree on an encrypted volume; `backups` is administrators-only.
- [ ] TLS works for the real host name, HTTP redirects to HTTPS, only TLS 1.2/1.3 are offered, HSTS is present.
- [ ] Only ports 80/443 are reachable from outside; `8000` listens on loopback.
- [ ] Both services run as `NT SERVICE\...` accounts.
- [ ] Nightly backups run, **a restore has been tested**, and copies go to encrypted offsite storage.
- [ ] `audit.log` is shipped to a separate system; alerts exist for service down, `ready` failing and login bursts.
- [ ] CI is green (tests, `ruff`, `mypy`, `bandit`, `pip-audit`, `gitleaks`) and the git history has been scanned for secrets.
- [ ] Teacher-to-student assignments are loaded and spot-checked (a teacher sees only their students).
- [ ] The frontend sends `X-CSRF-Token`, returns to login on `401`, and stores no tokens in `localStorage`.

## Known limits

- **Data at rest**: SQLite files are not encrypted by the application; rely on BitLocker and the installer's ACLs, or
  move to a server database with transparent data encryption.
- **Authentication** is username and password only (no MFA or SSO). Recovery is administrator-assisted
  (`users set-password`); there is no e-mail reset.
- **Audit integrity**: audit rows live in the users database, so a host administrator could alter them. Ship
  `audit.log` to a separate log server.
- **Rate limiting** keeps counters in memory (correct for one process only; see `RATELIMIT_STORAGE_URI`).
- **Concurrency**: one waitress process, SQLite allows one writer at a time. Plan a move to a server database if write
  load grows.
- **Compliance**: the system holds student education records. Confirm retention, access-review and incident-notification
  duties (for example FERPA) with the institution's compliance contacts; the audit trail supports access reviews but is not a compliance
  program.
