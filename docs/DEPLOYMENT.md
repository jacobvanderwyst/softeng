# Deployment guide (Windows Server)

Target layout: **Caddy** (TLS on 80/443) → **waitress** on `localhost:8000` → the Flask app. Both run as Windows
services under non-administrator virtual accounts, wrapped by **WinSW**.

> The scripts in `deploy\windows` were written for this guide and syntax-checked, but have not been run on a real
> server. Do a full dry run on a staging machine first.

## 0. Prerequisites

- Windows Server 2019 or later, PowerShell 5.1, an elevated PowerShell session.
- Python 3.11+ installed for all users, with `python` on `PATH` (or pass `-PythonExe`).
- A DNS name for the site (for example `plans.example.edu`) pointing at the server, with inbound 80 and 443
  reachable (80 is needed for automatic certificates and the HTTP→HTTPS redirect).
- Disk encryption (BitLocker) on the volume that holds `C:\ProgramData\DegreePlan`: the SQLite files are not
  encrypted by the application.
- Downloaded and **checksum-verified** release binaries (do not skip verification; the installer enforces it):
  - WinSW v3 (`WinSW-x64.exe`) from the WinSW GitHub releases
  - Caddy v2 (`caddy.exe`) from caddyserver.com / the Caddy GitHub releases
  ```powershell
  (Get-FileHash C:\tools\WinSW-x64.exe -Algorithm SHA256).Hash   # compare with the published hash
  (Get-FileHash C:\tools\caddy.exe -Algorithm SHA256).Hash
  ```

## 1. Get and check the code

```powershell
git clone <repository-url> C:\src\degreeplan
cd C:\src\degreeplan
git verify-commit HEAD    # if you sign commits; otherwise review the tag/commit you were told to deploy
```

Optional but recommended on the build machine: `cd backend; pip install -r requirements\dev.txt; pip install --no-deps -e .; pytest`.

## 2. Install

```powershell
cd C:\src\degreeplan\deploy\windows
.\install.ps1 `
    -WinSWPath C:\tools\WinSW-x64.exe -WinSWSha256 <sha256-of-winsw> `
    -CaddyPath C:\tools\caddy.exe     -CaddySha256 <sha256-of-caddy> `
    -Hostname plans.example.edu
```

What it does (and what it deliberately does not do):

| Step | Detail |
| --- | --- |
| Install tree | `C:\Program Files\DegreePlan` (`venv`, `service`, `bin`, `caddy`) |
| Data tree | `C:\ProgramData\DegreePlan` (`secrets`, `data`, `logs`, `backups`, `www`, `caddy`) |
| Dependencies | `pip install --require-hashes -r backend\requirements\prod.txt`: nothing unpinned or unverified |
| Session key | 48 random bytes from the OS CSPRNG written to `secrets\secret_key` **once**; never displayed; kept on re-runs |
| Permissions | `secrets`: Administrators/SYSTEM full, service read-only; `data` and `logs`: service modify; `backups`: administrators only |
| Services | `DegreePlanApi` and `DegreePlanProxy` as `NT SERVICE\...` virtual accounts (no passwords, not admins) |
| Network | API bound to `localhost:8000` only; firewall rules for 80/443 on Domain/Private profiles only |
| Migrations | `db upgrade` for new databases (or `db stamp` with `-AdoptExistingDatabases`) |
| Verification | waits for `/api/v1/health/ready` before reporting success |

It writes **no password anywhere**. There are no default accounts.

### Using your existing databases

```powershell
.\install.ps1 ... -UsersDatabasePath D:\db\users.sqlite3 -PlansDatabasePath D:\db\plans.sqlite3 -AdoptExistingDatabases
```

- The files must already match the schema in `backend\src\degreeplan\db\tables.py` (see
  [ARCHITECTURE.md](ARCHITECTURE.md#data-adaptation-your-real-schemas)). Adapt `tables.py` first if names differ.
- `-AdoptExistingDatabases` runs `db stamp` instead of creating tables, so no data is touched. Take a backup first.
- Database files must be outside the source tree and use absolute paths (the app refuses to start otherwise).

### Server databases (PostgreSQL, SQL Server)

Set `USERS_DATABASE_URL_FILE` / `PLANS_DATABASE_URL_FILE` to files in the `secrets` folder that contain the full
SQLAlchemy URLs (credentials included) and use **separate database accounts with least privilege**: read-only on the
catalog tables (`programs`, `courses`, `course_prerequisites`, `program_requirements`), read/write on `plans` and
`plan_courses`, and no access to the users database from the plans account (and vice versa). Add the matching driver
to `backend\pyproject.toml`, re-run `pip-compile` to refresh the hash-pinned lock files, and note that
`db backup` supports SQLite only (use the database's native backups).

## 3. Create the first administrator

```powershell
& "C:\Program Files\DegreePlan\bin\degreeplan.ps1" users create <admin-username> --role admin
```

The password is prompted (hidden, twice) and checked against the policy (12+ characters, not a common password,
must not contain the username). Create teachers and students the same way:

```powershell
$cli = "C:\Program Files\DegreePlan\bin\degreeplan.ps1"
& $cli users create tina --role teacher --first-name Tina --last-name Teacher --department "Computer Science"
& $cli users create sam  --role student --first-name Sam  --last-name Student --program-id 1 --advisor tina
```

For bulk loading from a script, use `--password-stdin` (one line on stdin). Never put a password on a command line.

## 4. Frontend

Copy the built frontend (`index.html`, assets) into `C:\ProgramData\DegreePlan\www`. Caddy serves it with a
single-page-app fallback and proxies `/api/*` to the backend, so both are **same-origin** and no CORS is needed. If
the frontend must live on another origin, set `CORS_ORIGINS=https://frontend.example.edu` (exact HTTPS origins only;
wildcards are rejected) in the service environment.

## 5. Verify the installation

```powershell
Get-Service DegreePlanApi, DegreePlanProxy                        # both Running
curl.exe -s https://plans.example.edu/api/v1/health/ready         # {"status":"ready"}
curl.exe -sI https://plans.example.edu/api/v1/health/live         # HSTS, nosniff, no "Server" header
& "C:\Program Files\DegreePlan\bin\degreeplan.ps1" db status      # both databases ok
netstat -ano | findstr ":8000"                                    # listening on 127.0.0.1 only
icacls C:\ProgramData\DegreePlan\secrets                          # only SYSTEM, Administrators, service (read)
```

Then log in through the website, check that `__Host-sid` is `Secure; HttpOnly`, and look at
`C:\ProgramData\DegreePlan\logs\audit.log` for the login event. Check the TLS grade with an external scanner
(TLS 1.2 and 1.3 only).

## 6. Configuration reference

All settings are environment variables on the `DegreePlanApi` service (rendered into its WinSW definition by
`install.ps1`). Any secret-bearing variable also accepts a `<NAME>_FILE` form pointing at a protected file.

| Variable | Default | Notes |
| --- | --- | --- |
| `APP_ENV` | none (**required**) | `production` on servers. Refuses to start if missing |
| `SECRET_KEY[_FILE]` | none (required) | 32+ random characters; placeholders are rejected |
| `SECRET_KEY_FALLBACKS[_FILE]` | empty | Comma-separated retired keys during rotation |
| `USERS_DATABASE_URL[_FILE]`, `PLANS_DATABASE_URL[_FILE]` | none (required) | Must differ. SQLite paths must be absolute and outside the source tree |
| `PROXY_HOPS` | `0` | Number of trusted reverse proxies (`1` behind Caddy). Never set when clients can reach the app directly |
| `CORS_ORIGINS` | empty | Exact HTTPS origins, comma separated |
| `SESSION_IDLE_TIMEOUT` / `SESSION_ABSOLUTE_TIMEOUT` | 1800 / 28800 s | Idle must not exceed absolute |
| `LOGIN_RATE_LIMIT` / `DEFAULT_RATE_LIMIT` | `20 per minute` / `300 per minute` | Per client IP |
| `LOCKOUT_THRESHOLD` / `LOCKOUT_SECONDS` | 5 / 300 | Per username, exponential backoff (max 1 h) |
| `RATELIMIT_STORAGE_URI[_FILE]` | `memory://` | Use `redis://...` if you run more than one worker process |
| `LOG_DIR`, `LOG_FORMAT`, `LOG_LEVEL` | none, `text`, `INFO` | Files rotate at 10 MB x 10 |
| `MAX_CREDITS_PER_TERM` | 21 | Business rule for plan validation |

Production refuses to start when: the key is weak, a database URL is missing or shared, a SQLite path is relative or
inside the source tree, cookies are not `Secure`, rate limiting is disabled, or a CORS origin is `*`/non-HTTPS.

## 7. Upgrades and rollback

1. Back up: `.\deploy\windows\backup.ps1`.
2. Update the code (`git pull`/checkout the release tag).
3. Re-run `install.ps1` with the same arguments. It keeps secrets and data, reinstalls hash-pinned dependencies,
   re-renders the service definitions and **applies new migrations** (`db upgrade`).
4. Verify as in section 5.

Rollback: stop the services, restore the pre-upgrade backup files over the databases (see
[OPERATIONS.md](OPERATIONS.md#restore)), check out the previous release, re-run `install.ps1`. Migrations are
forward-only in production; the backup is the rollback mechanism.

## 8. Session-key rotation

1. Rename `C:\ProgramData\DegreePlan\secrets\secret_key` to `secret_key_previous`.
2. Re-run `install.ps1` (same arguments). A new key is generated and the old one is registered as a fallback, so
   existing sessions keep working.
3. After the absolute session lifetime (8 h by default), delete `secret_key_previous` and re-run `install.ps1`.

To invalidate **all** sessions immediately (for example after a suspected key leak): rotate as above but delete
`secret_key_previous` at once.

## 9. Troubleshooting

- **Service stops immediately**: read `C:\ProgramData\DegreePlan\logs\DegreePlanApi.err.log`. Configuration
  mistakes are reported without secret values (for example `SECRET_KEY must be a random value of at least 32
  characters` or `database files must live outside the source tree`).
- **`/health/ready` returns 503**: the databases are unreachable or not at the expected migration revision. The
  reason is in `app.log`. Run `degreeplan.ps1 db status`.
- **Everyone is logged out after a restart**: the key changed or `SECRET_KEY_FILE` points at a different file.
- **Real client IPs show as 127.0.0.1 in logs**: `PROXY_HOPS` is not `1` behind Caddy.
- **Origin errors (`403 Origin not allowed`)**: the browser's origin differs from the host the proxy forwards; check
  `PROXY_HOPS` and `CORS_ORIGINS`.

## 10. Uninstall

```powershell
.\deploy\windows\uninstall.ps1                    # removes services, firewall rules, program files; keeps data
.\deploy\windows\uninstall.ps1 -RemoveData -Force # also deletes databases, secrets, logs and backups
```
