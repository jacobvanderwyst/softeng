# Operations runbook

Paths below assume the default install under `C:\ProgramData\DegreePlan` and `C:\Program Files\DegreePlan`.
The operator command is `& "C:\Program Files\DegreePlan\bin\degreeplan.ps1" <group> <command>` (elevated PowerShell).

## Daily checks and monitoring

- `GET /api/v1/health/live`: process is up. `GET /api/v1/health/ready`: databases reachable **and** migrated.
  Point your monitor at `ready` and alert on anything other than `200`.
- Alert on: service not running; `ready` failing for more than a few minutes; disk space low on the data volume;
  failed backups (non-zero scheduled-task result); spikes of `401`/`429` in `app.log`; any `ERROR` line.
- Logs (`C:\ProgramData\DegreePlan\logs`):
  - `app.log`: application + request lines (JSON when `LOG_FORMAT=json`), rotated at 10 MB x 10
  - `audit.log`: security events (same rotation). **Ship this file to your SIEM / log server** so it cannot be
    altered by someone who compromises the host
  - `DegreePlanApi.out.log` / `.err.log`: service wrapper output, including startup/config errors
  - `caddy-access.log`: proxy access log (client IP, path, status; credentials are redacted)

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

Users can change their own password through the API (`POST /api/v1/auth/change-password`); doing so revokes their
other sessions. There is no e-mail based reset: a forgotten password is handled by an administrator with
`set-password`, after verifying the person's identity out of band.

A locked-out account (too many failed logins) unlocks automatically after the lock period; an administrator can
speed this up with `set-password`.

## Backups

`deploy\windows\backup.ps1` takes a consistent online backup of both SQLite databases (SQLite backup API, safe while
the service runs), verifies each copy with `PRAGMA integrity_check`, stores it under
`C:\ProgramData\DegreePlan\backups\<UTC timestamp>` (Administrators/SYSTEM only), and prunes backups older than the
retention period (default 30 days) **only after** a successful run.

Schedule it nightly as SYSTEM:

```powershell
$action  = New-ScheduledTaskAction -Execute 'powershell.exe' `
           -Argument '-NoProfile -ExecutionPolicy Bypass -File "C:\src\degreeplan\deploy\windows\backup.ps1"'
$trigger = New-ScheduledTaskTrigger -Daily -At 2:00am
Register-ScheduledTask -TaskName 'DegreePlan backup' -Action $action -Trigger $trigger `
    -User 'SYSTEM' -RunLevel Highest
```

Backups contain student records and password hashes. Copy them to **encrypted, access-controlled offsite storage**
and test a restore at least once per term.

### Restore

1. Stop the services: `Stop-Service DegreePlanProxy, DegreePlanApi`.
2. Keep the damaged files for investigation: rename `data\users.sqlite3` and `data\plans.sqlite3` (and any
   `-wal`/`-shm` siblings).
3. Copy the chosen backup files to `data\users.sqlite3` and `data\plans.sqlite3`.
4. Re-apply permissions: `icacls C:\ProgramData\DegreePlan\data /reset /T`, then re-run `install.ps1` (it restores
   the service ACLs) or grant `NT SERVICE\DegreePlanApi:(OI)(CI)M` to the folder.
5. `degreeplan.ps1 db status` must report both databases `ok`; then `Start-Service DegreePlanApi, DegreePlanProxy`.
6. Restoring an old users database brings back old password hashes and session rows: consider
   `users revoke-sessions` for affected accounts and notify users if credentials were changed after the backup.

## Querying the audit trail

```powershell
& "C:\Program Files\DegreePlan\venv\Scripts\python.exe" -c "
import sqlite3, datetime
db = sqlite3.connect('file:C:/ProgramData/DegreePlan/data/users.sqlite3?mode=ro', uri=True)
for row in db.execute('SELECT occurred_at, action, outcome, actor_user_id, target_type, target_id, ip, request_id FROM audit_log ORDER BY id DESC LIMIT 50'):
    print(datetime.datetime.fromtimestamp(row[0], datetime.UTC).isoformat(), *row[1:])
"
```

Useful actions: `auth.login` (outcomes `success`, `failure`, `locked`), `auth.logout`, `auth.change_password`,
`user.create`, `user.set_password`, `user.deactivate`, `user.activate`, `user.revoke_sessions`, `plan.create`,
`plan.update`, `plan.delete`, `plan.course.set`, `plan.course.remove`.

## Certificates

- Public host name: Caddy obtains and renews certificates automatically. Check `DegreePlanProxy` logs after DNS or
  firewall changes.
- Institutional certificate: replace the `tls { ... }` block in `C:\Program Files\DegreePlan\caddy\Caddyfile` with
  `tls <cert.pem> <key.pem>`, keep the key under `secrets` with restrictive ACLs, then `Restart-Service DegreePlanProxy`.
  Note that re-running `install.ps1` re-renders the Caddyfile from the template, so make the same change in
  `deploy\caddy\Caddyfile.template`.

## Incident response

**Suspected stolen session or compromised account**
1. `users deactivate <name>` (kills sessions at once) or `users revoke-sessions <name>`.
2. Review `audit_log` / `audit.log` for that user and the IPs involved; check `plan.*` events for tampering.
3. `users set-password <name>` after the person is verified; then `users activate <name>`.

**Suspected leak of the session signing key or of a database backup**
1. Rotate the key **without** a fallback (see DEPLOYMENT.md, section 8): every session becomes invalid.
2. Passwords are stored as Argon2id hashes, but treat a leaked users database as a credential exposure: force
   password changes (`set-password`) for privileged accounts first, then everyone, and notify per your policy.

**Brute-force or scraping**
1. Look for bursts of `auth.login failure/locked` events and `429` responses; block offending addresses at the
   Windows Firewall or upstream.
2. Tighten `LOGIN_RATE_LIMIT`/`LOCKOUT_*` if needed (re-run `install.ps1` after editing the environment table in the
   script, or edit the service definition and restart).

**Service is down**
1. `Get-Service DegreePlanApi`; read `logs\DegreePlanApi.err.log` and `app.log`.
2. Configuration errors are explicit and contain no secrets. WinSW restarts the service on failure (10 s, 30 s, 60 s).

## Capacity notes

The deployment is a single waitress process with 8 threads: ample for a department-scale tool. The login rate limiter
keeps its counters in memory, which is only correct for one process. If you ever run more than one worker, set
`RATELIMIT_STORAGE_URI` to a Redis URL (kept in a `_FILE` secret). SQLite allows one writer at a time; the
application keeps write transactions short, but plan a move to a server database (see DEPLOYMENT.md) if write
concurrency grows.
