# Windows 11 production quickstart
Use Windows 11 Pro/Enterprise with Windows PowerShell 5.1 and administrator rights for installation. This installs the real production service stack locally; it is **not** the HTTP demo launcher.
## Production installation
1. Install trusted Python 3.11+ x64 for all users.
2. Obtain checksum-verified WinSW/Caddy binaries and an EduNav release bundle. The release includes the application wheel, locked production dependencies and deployment scripts; no Node build is required on the target.
3. Follow [Windows Server quickstart steps 2–4](WINDOWS_SERVER_QUICKSTART.md): use the default `Local` access mode, `Internal` certificates and `https://localhost:8443/`.
4. Explicitly review and trust the generated local CA using `trust.ps1`; the service does not silently modify certificate trust.
5. Create an administrator, import your reviewed catalog, then create teacher/student accounts. Production starts empty and never receives Alice or other demo accounts automatically.
6. Open **https://localhost:8443/**. Confirm the certificate is trusted, sign in, create a plan, save a course status/grade, reload and verify persistence.
### What is installed
- Code: `C:\Program Files\EduNav`.
- Production state: `C:\ProgramData\EduNav\production`.
- Separate production SQLite databases: `data\users.sqlite3` and `data\plans.sqlite3`.
- Windows services: `EduNavApi` and `EduNavProxy`.
- Backend API: loopback port 8000; production browser entry point: loopback HTTPS port 8443.
- No inbound LAN firewall exception in the default local-only profile.
Keep SQLite files on a local disk, not a mapped drive, OneDrive synchronization folder or network share. Browser-only course data from the public frontend is not imported automatically.
## Daily management
In elevated PowerShell:
```powershell
$bin = 'C:\Program Files\EduNav\bin'
& "$bin\manage.ps1" -Action Status
& "$bin\operator.ps1" -CliArgs @('db','verify')
& "$bin\manage.ps1" -Action Backup
& "$bin\harden.ps1"
```
Use Start/Stop actions for service control. Use the documented Backup/Restore workflow rather than copying live SQLite files or their WAL sidecars. Uninstall stops/removes owned services and firewall rules but retains files/data; review before any manual cleanup.
The shared [operations guide](OPERATIONS.md) covers imports, profile assignments, password resets, migration, backup/restore and upgrades.
## Windows 11 demo is separate
For presentations and sample accounts, use [WINDOWS11_DEMO.md](WINDOWS11_DEMO.md), not a production install:
```powershell
.\backend\scripts\demo.ps1
```
The demo uses **http://localhost:5000/** and repository-local `backend\instance` files. It does not use production accounts, secrets or service databases.
Prefer the launcher's printed IPv4 loopback URL for the HTTP demo: production HTTPS on `localhost` may leave a
browser HSTS policy that upgrades a `localhost` HTTP URL, regardless of its port.
Do not run demo wipe flags against production paths. The managed demo rejects alternate database/file settings and requires its ownership marker before clearing data.
## Hardening and host validation
Audit with `harden.ps1`; preview supported repairs with `-Apply -WhatIf`. Antivirus/Defender, updates, encrypted disk/recovery keys, user access and the correct Microsoft Windows 11 security baseline remain host-administration duties.
This Windows 11 workstation profile is suitable for a controlled single-host installation, not a substitute for dedicated, monitored institutional server hosting. Verify restore, reboots, certificate trust and access controls on the actual installation.
