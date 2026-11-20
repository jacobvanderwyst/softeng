# Production deployment
The current release uses one application with isolated production and demo configuration. Production is fresh SQLite data, protected Windows services, durable secrets and HTTPS. The demo is repository-local sample data and loopback HTTP; do not deploy demo.ps1 as a server.
## Quickstarts
- [Windows Server production](WINDOWS_SERVER_QUICKSTART.md): staging prerequisites, installer preflight, TLS/trust, fresh accounts/catalog and host acceptance.
- [Windows 11 production](WINDOWS11_QUICKSTART.md): local-only production services at https://localhost:8443/.
- [Windows 11 demo](WINDOWS11_DEMO.md): generated sample credentials and presentation sequence at http://localhost:5000/.
## Production defaults and isolation
Code lives in `C:\Program Files\EduNav`; state lives in `C:\ProgramData\EduNav\production`. Separate `data\users.sqlite3` and `data\plans.sqlite3` are created/migrated empty. No Alice accounts, example catalog or browser localStorage data are copied automatically. Imported grades are self-reported planning records, not transcripts.
The installer creates `EduNavApi` and `EduNavProxy` under non-administrator virtual service identities. Waitress is loopback-only on port 8000; Caddy is the HTTPS entry point. The installation manifest fixes database/secret paths; management subprocesses sanitize inherited settings and use isolated Python imports.
Local mode is the default and does not open inbound LAN firewall rules. LAN/Public modes require explicit hostname, certificate configuration and remote-address scope. Use a local disk for SQLite, not a shared/synchronized folder. Existing legacy `DegreePlan` installations and unowned directories are not silently adopted.
## Build and install artifacts
From a prepared development checkout, run `backend/scripts/build_release.ps1`. It builds the application wheel without downloading build dependencies and bundles deployment scripts, the hash-pinned production dependency lock, docs and SHA256 manifests under `dist/`.
The target installer requires `-WheelPath/-WheelSha256`, `-WinSWPath/-WinSWSha256`, and `-CaddyPath/-CaddySha256`. Verify trusted release checksums before use. A locally computed checksum alone does not authenticate an untrusted download. The target does not build arbitrary source or automatically fetch/run latest binaries.
`install.ps1 -Help`, `-RenderOnly`, `-Preflight`, and `-WhatIf` support review before apply. Installing/applying requires elevation; script help and isolated rendering do not. Installed dependencies require network access or an explicitly prepared trusted package cache; no offline completeness is claimed.
## Security and certificate trust
Student self-registration is enabled by default. `-EnableRegistration:$false` persists a closed policy; omission
on upgrade preserves a stored choice, while manifests without the field use the enabled default. Explicit false
settings remain effective until reopened. See [OPERATIONS.md](OPERATIONS.md) for policy and abuse limits.
Bootstrap the first admin with the operator CLI before network exposure. The protected `admin.html` workspace
creates instructors and edits program requirements; public signup never grants teacher/admin roles.
Production uses Secure/HttpOnly cookies, same-origin frontend/API delivery, password hashing, CSRF/Origin checks and server-side permissions. Production configuration rejects insecure cookies and demo-owned database targets; demo commands are not registered in production.
TLS certificate trust is explicit. Internal Caddy roots must be reviewed and trusted on each intended client; institutional certificates must match the configured DNS name. `trust.ps1` verifies a supplied DER certificate SHA256 fingerprint before an explicit root-store write. Never disable TLS verification to make production appear healthy.
Code/config are read-only to services; database, log and certificate-state writes are scoped by service identity. `harden.ps1` audits host/application settings by default and supports application-specific repair preview using `-Apply -WhatIf`. It does not change RDP/WinRM, domain policy, disk encryption, Defender settings, or reboot the host.
Apply the correct institution-approved Windows baseline separately: Microsoft Security Compliance Toolkit for the matching Windows 11/Server release; OSConfig security-baseline tooling only supports Server 2025. Test domain-policy compatibility before enforcing anything.
## Upgrades, backups and removal
Read [OPERATIONS.md](OPERATIONS.md). Upgrade uses a verified new wheel, validates known current schemas, stops services before replacing packages, and retains pre-upgrade data/code backups. No blind schema stamping or automatic adoption is performed.
Paired production backups quiesce application writers and include checksums/metadata. They are not equivalent to two arbitrary live-file copies. Restore validates/migrates scratch copies, retains recovery snapshots and revokes restored sessions. Scheduling is explicit, with no automatic retention deletion or offsite transfer.
Uninstall removes only verified owned services/firewall rules and retains code, data, logs, secrets and backups. Plan manual secure cleanup/retention separately.
## Mandatory acceptance on the actual host
Before real student data, validate install/upgrade/reboot, service accounts and ACLs, trusted HTTPS from intended clients, listener/firewall scope, catalog/account imports, ownership/staff access, grade/GPA persistence, paired backup/restore and alerts. The development host is Windows 11; mocked tests and production-like HTTP smoke tests are not proof of real Windows Server policy/TLS/service acceptance.
## Remaining boundaries
SQLite has limited concurrent writes and no high availability. Rate limiting is process-local by default. Users/plans commits are independent, so audit/data changes are not distributed-atomic and cross-database references require verification. Simultaneous clients have no optimistic edit-conflict merge. Browser reset actions may partially finish across requests.
MFA/SSO, self-service recovery/forced first-login setup, official academic auditing and institutional compliance certification are not included. SQLite/backups need host disk encryption and access controls; keep encrypted offsite backups and ship audit logs off-host. Review imported frontend redistribution rights before publishing.
