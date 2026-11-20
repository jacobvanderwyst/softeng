# Windows Server production quickstart
This is a single-server SQLite deployment, not a high-availability service or official degree audit. Use Windows Server 2019/2022/2025 and an elevated Windows PowerShell 5.1 session on a dedicated staging host first.
## 1. Prepare the release and host
- Install a supported Python 3.11+ x64 release from a trusted source for all users.
- Obtain WinSW and Caddy Windows x64 binaries from their official release sites. Verify published checksums/signatures through a trusted channel; do not treat a hash computed from an untrusted download as proof of authenticity.
- Build the EduNav release on the development workstation with `backend/scripts/build_release.ps1`, or obtain the release bundle and its independently verified checksum manifest.
- Keep database files on a local disk. Production defaults to `C:\Program Files\EduNav` for code and `C:\ProgramData\EduNav\production` for data, logs, secrets and backups. Existing legacy `DegreePlan` installations are not silently adopted.
- Resolve OS updates, reboot requirements, disk encryption/recovery-key custody, antivirus and administrator access before deployment. Apply your institution's matching Microsoft security baseline separately; this installer does not replace it.
## 2. Preflight and install
Run from the release directory. Replace the example paths and checksum values with the verified release values:
```powershell
$install = @{
    WinSWPath = 'C:\tools\WinSW.exe'
    WinSWSha256 = '<published-winsw-sha256>'
    CaddyPath = 'C:\tools\caddy.exe'
    CaddySha256 = '<published-caddy-sha256>'
    WheelPath = 'C:\release\degreeplan_backend-1.3.1-py3-none-any.whl'
    WheelSha256 = '<verified-release-wheel-sha256>'
}
.\deploy\windows\install.ps1 @install -Preflight
.\deploy\windows\install.ps1 @install -WhatIf
.\deploy\windows\install.ps1 @install
```
The safe default is **local-only HTTPS at https://localhost:8443/**. The API listens only on loopback port 8000. Installation creates **empty production databases**, not demo users or demonstration courses.
Do not change production cookies to insecure or run the Flask development server to bypass certificate setup.
### LAN access
Opt in explicitly using a DNS hostname and restricted client subnets:
```powershell
$install.AccessMode = 'LAN'
$install.Hostname = 'edunav.school.internal'
$install.HttpsPort = 443
$install.RemoteAddress = @('192.168.10.0/24')
$install.CertificateMode = 'Files'
$install.CertificatePath = 'C:\secure\school-issued.crt'
$install.PrivateKeyPath = Join-Path 'C:\secure' 'school-issued.key'
.\deploy\windows\install.ps1 @install -Preflight
.\deploy\windows\install.ps1 @install
```
Use an institution-issued certificate trusted by clients. Internal-CA mode is also available, but each client must explicitly trust that CA. Never distribute its private key. Public ACME hosting additionally requires correct public DNS, `-AccessMode Public -CertificateMode ACME -HttpsPort 443`, reachable challenge ports and reviewed firewall scopes. No public access is enabled by default.
## 3. Establish HTTPS trust
For local/internal certificates, inspect the public root certificate generated in Caddy's private data directory. Obtain its **DER certificate SHA256 fingerprint** through an independent trusted channel and verify issuer/expiry before trusting it:
With the default Caddy state configuration, the generated public root is normally
`C:\ProgramData\EduNav\production\caddy\caddy\pki\authorities\local\root.crt`; confirm it on staging.
Inspect the public certificate (never the `.key` file) on the trusted server:
```powershell
$caPath = Join-Path 'C:\ProgramData\EduNav\production\caddy\caddy\pki\authorities\local' 'root.crt'
$ca = New-Object Security.Cryptography.X509Certificates.X509Certificate2($caPath)
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $ca | Select-Object Subject, Issuer, NotBefore, NotAfter
    [BitConverter]::ToString($sha.ComputeHash($ca.RawData)).Replace('-', '')
} finally { $sha.Dispose(); $ca.Dispose() }
```
Compare the displayed fingerprint to your trusted out-of-band value before the explicit trust step:
```powershell
.\deploy\windows\trust.ps1 -CertificatePath 'C:\reviewed\root.crt' -ExpectedSha256 '<verified-der-sha256>' -WhatIf
.\deploy\windows\trust.ps1 -CertificatePath 'C:\reviewed\root.crt' -ExpectedSha256 '<verified-der-sha256>'
```
Trust modifies the local machine's root store and is an explicit administrative decision. A PEM file's file hash is not its DER certificate fingerprint. Never use browser certificate-error bypasses or disabled certificate validation as production setup.
## 4. Create accounts and load the catalog
Production management uses the installed manifest and ignores inherited demo/database settings:
```powershell
$operator = 'C:\Program Files\EduNav\bin\operator.ps1'
& $operator -CliArgs @('users','create','site-admin','--role','admin')
& $operator -CliArgs @('catalog','import','C:\reviewed\catalog.json','--dry-run')
& $operator -CliArgs @('catalog','import','C:\reviewed\catalog.json')
& $operator -CliArgs @('catalog','list')
& $operator -CliArgs @('users','create','advisor1','--role','teacher','--first-name','Alex','--last-name','Advisor')
& $operator -CliArgs @('users','create','student1','--role','student','--first-name','Sam','--last-name','Student','--program-id','1','--advisor','advisor1')
& $operator -CliArgs @('db','verify')
& $operator -CliArgs @('users','list','--details')
```
Passwords are prompted without echo. Confirm actual program IDs before creating students; do not assume a real import assigns ID 1. `docs/examples/catalog-v1.example.json` documents the format using synthetic data; review and replace it rather than treating it as an official catalog. Imports are additive; scalar changes require `--update`, not destructive replacement.
Student self-registration is enabled by default; `-EnableRegistration:$false` disables it. Omitting the flag preserves
an existing stored choice, including a previous false; use `-EnableRegistration` to explicitly reopen that installation.
Signup is not verified enrollment; new students have no advisor until an operator reviews their profile.
After bootstrapping the first administrator with the CLI, use **Administrator sign in** for `admin.html`: create
instructor accounts and create/edit degree programs and required-course lists from the imported catalog.
Additional admin accounts and student/advisor assignments remain operator-managed. No hardcoded admin password exists.
`users update-profile` updates names, program/advisor assignments or department; password and session controls are separate.
## 5. Verify, back up and audit
```powershell
$manage = 'C:\Program Files\EduNav\bin\manage.ps1'
& $manage -Action Status
& $manage -Action Backup
& 'C:\Program Files\EduNav\bin\harden.ps1'
```
Backups intentionally stop application writers briefly. Test restore on staging before relying on it. A restore accepts verified paired backup sets, retains recovery copies and revokes restored sessions. Use [OPERATIONS.md](OPERATIONS.md) for management, scheduling and restore details.
The hardening script audits by default. Preview application-specific repairs with `-Apply -WhatIf` before applying. It does **not** disable remote administration, change domain policy, enable BitLocker, reboot, or import a blanket baseline.
## Acceptance before real student data
Verify login/password change, roles and teacher assignments, grades/GPA persistence, imports, paired backup/restore, service restart/reboot, TLS trust from every intended client, listener/firewall scope, file permissions, and real monitoring alerts.
Automated and mocked tests do not prove these host behaviors. Production acceptance on an actual Windows Server staging host remains mandatory. MFA/SSO and institution-specific transcript/graduation rules are not included.
## Baseline references
- [Caddy local HTTPS and CA trust](https://caddyserver.com/docs/automatic-https#local-https).
- [Microsoft Security Compliance Toolkit](https://learn.microsoft.com/en-us/windows/security/operating-system-security/device-management/windows-security-configuration-framework/security-compliance-toolkit-10).
- [Server 2025 OSConfig security baseline](https://learn.microsoft.com/en-us/windows-server/security/osconfig/osconfig-how-to-configure-security-baselines) — not supported on older Server releases.
