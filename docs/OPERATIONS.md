# Production operations
Use the installed management scripts in an elevated Windows PowerShell session. Defaults below refer to `C:\Program Files\EduNav\bin` and `C:\ProgramData\EduNav\production`. Never use demo reset/wipe commands for production.
## Status and monitoring
```powershell
$bin = 'C:\Program Files\EduNav\bin'
& "$bin\manage.ps1" -Action Status
& "$bin\operator.ps1" -CliArgs @('db','verify')
& "$bin\harden.ps1"
```
Check both services, trusted HTTPS and `/api/v1/health/ready`. Readiness checks database connectivity/migration status; `db verify` additionally checks exact schema, SQLite integrity/FKs and student/program references. None of these alone validates host TLS trust, firewall scope or full academic rules.
Configure external alerts for service/readiness failures, disk space, failed scheduled backups, stale backup sets, ERROR logs and unusual login/lockout activity. Alerts are an operator responsibility; a documented check is not a running monitor.
API logs and audit logs are under production `logs\api`; service wrapper logs are split into API/proxy directories. Passwords, cookies and secret values must not be logged. Ship audit logs off-host; local privileged administrators can otherwise alter them.
## Account management
### Student self-registration
Student registration defaults to enabled in production and development. The public `register.html` page is linked
from sign in whenever enabled. Use `install.ps1 -EnableRegistration:$false` with the normal verified install/upgrade
arguments to disable it. Omitted flags preserve an existing stored choice; manifests without a field use the enabled
default. A stored false from an earlier release remains false until explicitly reopened with `-EnableRegistration`.
The protected manifest's boolean `RegistrationEnabled` is passed through the sanitized service environment;
setting `REGISTRATION_ENABLED` in an unrelated caller shell does not override an installed production service.

For manual non-service deployments, `REGISTRATION_ENABLED` controls availability and `REGISTRATION_RATE_LIMIT`
defaults to `5 per minute;20 per hour` per client IP. The standard Windows wrapper uses the application default
rate; changes require a reviewed service configuration change, not an untrusted caller environment override.
Shared NAT users share an IP limit; production rate counters remain process-local unless a shared limiter store is configured.

Signup accepts username, names, password and optional catalog program. The backend creates only a student profile,
with no advisor, and records an audit event. It does not grant teacher/admin access or verify enrollment, email ownership
or institutional identity. No approval/invitation/CAPTCHA/email-verification workflow is included. Use operator creation
instead if your institution requires verified provisioning. Duplicate responses expose username availability, not
account role or disabled status; keep rate limits and monitoring in place.

After signup, use `users list --details` and `users update-profile` to review program/advisor assignments.
Users sign in normally after creation; registration does not automatically replace an existing authenticated session.

### Web administrator workspace
Use **Administrator sign in** or `admin.html` with an existing admin account. The same secure authentication,
server-side sessions and CSRF protections apply; public registration cannot create privileged accounts.
Bootstrap the first production admin via `operator.ps1 users create` before exposing the site to a network.

The workspace creates instructor (`teacher`) accounts with typed initial passwords and lists/searches instructors.
Passwords are never returned by the API and form password fields are cleared after requests. Distribute credentials
securely; instructors can change their password after login. Forced first-login change is not implemented.
New instructors have no advisees until an operator assigns students with `users update-profile --advisor`.

The degree-program editor manages code, name, total credits and required courses from the existing catalog.
Import/create catalog courses through the controlled operator workflow first. Each program needs at least one
valid required course and positive total credits in the web editor. Changing requirements affects existing plans'
validation but preserves program IDs, personal plans, grades, progress and course records. A revision check rejects
stale browser edits; reload explicitly before retrying. Catalog writes and their users-database audit record remain
separate commits, so reload state after an error before resubmitting.

Administrator management of catalog programs does not grant write access to personal student semester plans or
grades. Teachers remain read-only for assigned students. Additional administrators are still operator-provisioned.

### Operator account commands
```powershell
& "$bin\operator.ps1" -CliArgs @('users','list','--details')
& "$bin\operator.ps1" -CliArgs @('catalog','list')
& "$bin\operator.ps1" -CliArgs @('users','create','site-admin','--role','admin')
& "$bin\operator.ps1" -CliArgs @('users','set-password','student1')
& "$bin\operator.ps1" -CliArgs @('users','update-profile','student1','--program-id','1','--advisor','advisor1')
& "$bin\operator.ps1" -CliArgs @('users','deactivate','student1')
& "$bin\operator.ps1" -CliArgs @('users','activate','student1')
& "$bin\operator.ps1" -CliArgs @('users','revoke-sessions','student1')
```
Passwords are prompted secretly. Program/advisor IDs and permissions must match real institutional records. `users list --details` exposes operator-only profile IDs needed for staff lookups, never password hashes. `catalog list` shows program IDs/codes/names. Profile commands support explicit clear flags for program/advisor/department; use command help for the exact role-specific options.
Users can change their password in the signed-in Change Password form. Forgotten passwords require verified administrator assistance; MFA/SSO and e-mail recovery are not supplied. A password reset revokes sessions. Production startup never refreshes/prints known demonstration passwords.
The release's `backend/scripts/bulk_create_users.ps1` helper is optional; inspect `-Help` and pass the installed operator
wrapper through `-Cli`. Dry run checks input structure, not live program/advisor references, and creation is per-row,
not an atomic batch. Generated credential files are sensitive and must be access-restricted, distributed privately and
removed under the institution's retention policy. No forced first-login change is promised.
## Controlled catalog imports
Use version-1 JSON with required `version`, `programs`, `courses`, `prerequisites`, `requirements` fields. References use codes; `docs/examples/catalog-v1.example.json` is synthetic, not an official catalog.
```powershell
& "$bin\operator.ps1" -CliArgs @('catalog','import','C:\reviewed\catalog.json','--dry-run')
& "$bin\operator.ps1" -CliArgs @('catalog','import','C:\reviewed\catalog.json')
& "$bin\operator.ps1" -CliArgs @('catalog','list')
```
Dry run validates then rolls back. Different existing scalar values require `--update`; relations are additive and existing records are not deleted. Duplicate keys/codes/edges, dangling references, prerequisite cycles and oversized/invalid inputs are rejected. Reconcile imported counts/codes with the official source and run `db verify`. Existing grade summaries can change if catalog credits are explicitly updated; review such changes before import.
This release does not automatically migrate arbitrary third-party schemas or copy demo data. Known-schema adoption requires explicit read-only schema validation and reviewed migration handling, not stamping an unknown schema to head.
### Optional CJ/COMS foundation reference import
The release includes `docs/examples/cj-coms-foundation.json`, transcribed from user-supplied Major: Foundation
screenshots. It contains 7 CJ courses at 3 credits each and 4 COMS courses at 3 credits each.
Programs are deliberately labeled partial/foundation-only; 21/12 are section subtotals, not full degree totals.
Footnote text, prerequisites, general education, electives and the rest of each curriculum are unverified.
No screenshot footnote number is interpreted as a prerequisite course ID.

Production installation never seeds this reference automatically. If approved for your planning use, review it,
back up the target, then use the standard importer:
```powershell
& "$bin\operator.ps1" -CliArgs @('catalog','import','C:\release\docs\examples\cj-coms-foundation.json','--dry-run')
& "$bin\operator.ps1" -CliArgs @('catalog','import','C:\release\docs\examples\cj-coms-foundation.json')
```
Stop and reconcile existing `CJ`/`COMS` or course-code conflicts rather than using `--update` blindly.
The demo's `dev sync-foundation-catalog` only fills missing reference entries and preserves existing matching
program/scalar/requirement edits; it is not available in production. Controlled production imports retain the
usual explicit conflict/update rules. Extend the plans only after obtaining the full official requirements.

## Backup and restore
```powershell
& "$bin\manage.ps1" -Action Backup
# Substitute an actual verified owned backup-set directory:
& "$bin\manage.ps1" -Action Restore -BackupSet 'C:\ProgramData\EduNav\production\backups\set-<id>' -WhatIf
& "$bin\manage.ps1" -Action Restore -BackupSet 'C:\ProgramData\EduNav\production\backups\set-<id>'
```
Backup briefly stops both services and serializes supported management writes. It validates known schemas and produces paired SQLite snapshots plus hashes/metadata. Do not run independent database writers during maintenance. Each standalone CLI `db backup` snapshot is individually consistent, but two sequential live snapshots are not automatically one cross-database transaction.
Restore accepts only an owned paired set, verifies hashes before replacing live data, validates/migrates isolated scratch copies, preserves a pre-restore recovery pair and revokes restored sessions. Normal maintenance restores the prior running state unless `-LeaveStopped`; a failed restore/migration leaves services stopped and retains recovery/failed artifacts. Never mix restored databases with unrelated live WAL/SHM files.
Backups contain education records and password hashes. Protect and encrypt local storage, keep verified offsite copies, define retention, and test a restore on staging. Scheduling, retention deletion and offsite transfer are not automatic install side effects.
To opt into a reviewed daily SYSTEM backup task:
```powershell
& "$bin\schedule-backup.ps1" -Time '02:00' -WhatIf
& "$bin\schedule-backup.ps1" -Time '02:00'
```
The task runs quiesced backup and causes brief planned downtime. Verify its LastTaskResult and backup contents; configure failure alerts. Replacement of an existing owned task requires `-Replace`. No automatic pruning occurs.
## Upgrades and service lifecycle
Stop/Start through `manage.ps1`. Supply a newly verified wheel to the installer using the same owned roots, hostname, ports and access/certificate modes. Upgrades back up current known schemas and code before replacing packages, then migrate and verify. Preserve snapshots and the matching previous release artifact for recovery; don't downgrade production migrations ad hoc.
`manage.ps1 -Action Migrate` also creates a pre-migration backup. Failure leaves services stopped; inspect logs and retained recovery artifacts before retrying. An upgrade rollback may require the matched old wheel plus paired pre-upgrade databases; revoking sessions is necessary after restoring old account data.
Uninstall stops/removes verified application services/firewall rules and scheduled backup task but retains files. It does not wipe production data or silently delete recovery material.
## Hardening and certificates
`harden.ps1` is read-only by default. Preview app-specific repairs with `-Apply -WhatIf`. Host antivirus/updates, BitLocker/recovery keys, privileged access, RDP/WinRM policy, baseline enforcement and compliance review remain separate administrator duties.
Never suppress certificate errors. Internal-CA trust requires explicit reviewed fingerprint/issuer verification on each client. Protect root/private keys and persistent Caddy state; do not copy root private keys to clients. For institutional certificate renewal, validate the new certificate/key pair and permissions before a controlled proxy restart. External ACME requires working DNS/challenge connectivity.
## Incident response and limits
For suspected compromised accounts, deactivate/revoke sessions first, review security events, verify identity out of band, reset credentials and reactivate only after review. Coordinate application secret rotation with the service configuration and all management commands; do not print keys. An exposed user database/backup is a credential exposure even though passwords are hashed.
The databases are independently committed; an audit-write failure may follow an already committed plan/progress change. Retry carefully after reloading state. No distributed audit atomicity or HA is claimed. Planning GPA/progress are self-reported, not official transcript evidence or a complete graduation audit.
