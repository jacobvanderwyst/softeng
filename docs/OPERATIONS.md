# Operations

Paths assume the default install: `C:\ProgramData\DegreePlan` (data) and `C:\Program Files\DegreePlan` (program).
The operator command is `& "C:\Program Files\DegreePlan\bin\degreeplan.ps1" <group> <command>` (elevated PowerShell).

## Monitoring and logs

- `GET /api/v1/health/live`: process is up. `GET /api/v1/health/ready`: databases reachable **and** migrated. Point the
  monitor at `ready` and alert on anything other than `200`.
- Also alert on: a service not running, low disk space on the data volume, a failed backup task, bursts of `401`/`429`
  in `app.log`, and any `ERROR` line.
- Logs in `C:\ProgramData\DegreePlan\logs` (rotated at 10 MB x 10):

  | File | Contents |
  | --- | --- |
  | `app.log` | application and request lines (JSON when `LOG_FORMAT=json`) |
  | `audit.log` | security events; **ship it to a separate log server** so it cannot be altered from this host |
  | `DegreePlanApi.out.log`, `.err.log` | service wrapper output, including startup and configuration errors |
  | `caddy-access.log` | proxy access log (client IP, path, status; credentials redacted) |

## User administration

```powershell
$cli = "C:\Program Files\DegreePlan\bin\degreeplan.ps1"
& $cli users list
& $cli users create <name> --role student --first-name <F> --last-name <L> --program-id 1 --advisor <teacher>
& $cli users set-password <name>        # also revokes all of the user's sessions
& $cli users deactivate <name>          # disables the account and revokes its sessions immediately
& $cli users activate <name>
& $cli users revoke-sessions <name>     # force re-login everywhere
```

Users change their own password through `POST /api/v1/auth/change-password`, which revokes their other sessions. A
forgotten password is handled by an administrator with `set-password` after verifying the person out of band. A locked
account (too many failed logins) unlocks by itself after the lock period.

## Backups

`deploy\windows\backup.ps1` takes a consistent online backup of both SQLite databases (safe while the service runs),
verifies each copy with `PRAGMA integrity_check`, stores it in `C:\ProgramData\DegreePlan\backups\<UTC timestamp>`
(Administrators/SYSTEM only) and prunes backups older than the retention period (default 30 days) only after a
successful run. Schedule it nightly:

```powershell
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
           -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\src\degreeplan\deploy\windows\backup.ps1"'
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00am
Register-ScheduledTask -TaskName 'DegreePlan backup' -Action $action -Trigger $trigger -User 'SYSTEM' -RunLevel Highest
```

Backups contain student records and password hashes: copy them to encrypted, access-controlled offsite storage, and
test a restore at least once per term.

### Restore

1. `Stop-Service DegreePlanProxy, DegreePlanApi`.
2. Keep the damaged files for investigation: rename `data\users.sqlite3` and `data\plans.sqlite3` (and any `-wal` or
   `-shm` siblings).
3. Copy the chosen backup files to `data\users.sqlite3` and `data\plans.sqlite3`.
4. Re-apply permissions by re-running `install.ps1` (it restores the service ACLs), or grant
   `NT SERVICE\DegreePlanApi:(OI)(CI)M` on the `data` folder.
5. `degreeplan.ps1 db status` must show both databases `ok`; then `Start-Service DegreePlanApi, DegreePlanProxy`.
6. An old users database brings back old password hashes and session rows: run `users revoke-sessions` for affected
   accounts and notify users whose credentials changed after the backup.

## Audit trail

```powershell
& "C:\Program Files\DegreePlan\venv\Scripts\python.exe" -c "
import sqlite3, datetime
db = sqlite3.connect('file:C:/ProgramData/DegreePlan/data/users.sqlite3?mode=ro', uri=True)
for row in db.execute('SELECT occurred_at, action, outcome, actor_user_id, target_type, target_id, ip, request_id FROM audit_log ORDER BY id DESC LIMIT 50'):
    print(datetime.datetime.fromtimestamp(row[0], datetime.UTC).isoformat(), *row[1:])
"
```

Actions: `auth.login` (outcomes `success`, `failure`, `locked`), `auth.logout`, `auth.change_password`, `user.create`,
`user.set_password`, `user.deactivate`, `user.activate`, `user.revoke_sessions`, `plan.create`, `plan.update`,
`plan.delete`, `plan.course.set`, `plan.course.remove`.

## Certificates

- Public host name: Caddy obtains and renews certificates automatically. After DNS or firewall changes check the
  `DegreePlanProxy` logs.
- Institutional certificate: in `C:\Program Files\DegreePlan\caddy\Caddyfile` replace the `tls { ... }` block with
  `tls <cert.pem> <key.pem>` (keep the key under `secrets` with restrictive ACLs) and `Restart-Service DegreePlanProxy`.
  `install.ps1` re-renders the Caddyfile from `deploy\caddy\Caddyfile.template`, so make the same change there.

## Incident response

**Suspected stolen session or compromised account**
1. `users deactivate <name>` (ends sessions at once) or `users revoke-sessions <name>`.
2. Review `audit_log` / `audit.log` for that user and the IPs involved; check `plan.*` events for tampering.
3. After verifying the person, `users set-password <name>`, then `users activate <name>`.

**Suspected leak of the session key or of a database backup**
1. Rotate the key without keeping a fallback so every session becomes invalid
   ([DEPLOYMENT.md](DEPLOYMENT.md#session-key-rotation)).
2. Passwords are Argon2id hashes, but treat a leaked users database as a credential exposure: reset privileged accounts
   first, then everyone, and notify according to the organization's policy.

**Brute force or scraping**
1. Look for bursts of `auth.login` `failure`/`locked` events and `429` responses; block offending addresses in the
   Windows Firewall or upstream.
2. Tighten `LOGIN_RATE_LIMIT` / `LOCKOUT_*` if needed: add the variable to the `$appEnv` table in `install.ps1` and
   re-run it.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| **Server:** service stops immediately | read `logs\DegreePlanApi.err.log`; configuration errors are explicit and contain no secrets (for example `SECRET_KEY must be a random value of at least 32 characters`, `database files must live outside the source tree`) |
| **Server:** `/health/ready` returns `503` | a database is unreachable or not at the expected migration revision; the reason is in `app.log`; run `degreeplan.ps1 db status` |
| **Server:** everyone is logged out after a restart | the key changed or `SECRET_KEY_FILE` points at a different file |
| **Server:** logs show the proxy's address instead of client IPs | `PROXY_HOPS` is not `1` behind Caddy |
| `403 Origin not allowed` | the page's origin is neither the API's own origin nor in `CORS_ORIGINS`; behind a proxy also check `PROXY_HOPS` |
| Every write returns `403` "CSRF" | send `X-CSRF-Token`; after a page refresh call `GET /auth/me` first to get a fresh token |
| Browser blocks the request (CORS error) | local: start with `-FrontendOrigin <exact origin>`; server: set `CORS_ORIGINS`; check scheme and port |
| Login works with a tool but not in the browser | send cookies (`credentials: "include"`) and fix CORS as above |
| `429` on login | per-IP rate limit or the username's lockout; wait out `Retry-After` (lockout default 5 minutes, doubling per extra failure up to 1 hour); locally `-Reset` clears it |
| Logged out after restarting the local server | expected in development (random session key per start) |
| `APP_ENV must be set` | export `APP_ENV` (`development` locally, `production` on servers); `demo.ps1` sets it automatically |
| `Python 3.11+ was not found` | install Python from python.org with "Add to PATH" and open a new terminal |
| PowerShell refuses to run a script from a zip | `Unblock-File .\scripts\demo.ps1` or `powershell -ExecutionPolicy Bypass -File .\scripts\demo.ps1` |
| `dev seed` fails with a constraint error | the database is already seeded: `.\scripts\demo.ps1 -Reset` |
| Import reports "not migrated" | run `flask --app degreeplan.wsgi db upgrade` first (with `APP_ENV` set) |
| Asking for help | quote the `request_id` from the error body; it finds the exact server log lines |
