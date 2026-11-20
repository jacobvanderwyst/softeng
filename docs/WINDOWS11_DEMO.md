# Windows 11 demo quickstart and presentation
The demo is local-only HTTP with sample accounts and separate repository-local databases. It is not production and must not contain real student data.
## Start safely
Requirements: Python 3.11+, Windows PowerShell 5.1, and this repository. No administrator rights or service installation are required.
From the repository root:
```powershell
.\backend\scripts\demo.ps1 -Help
.\backend\scripts\demo.ps1
```
First setup creates the virtual environment, migrates both databases, seeds demonstration records and displays generated passwords once. Later launches display fresh admin/Alice/teacher1 passwords unless `-KeepDemoPasswords` is supplied. Copy passwords privately; do not include them in recordings or screenshots.
Open the **HTTP IPv4 loopback URL printed by the launcher**, not HTTPS. It uses port 5000 by default.
Using a numeric loopback host avoids a production `localhost` HSTS policy forcing the demo onto HTTPS.
If needed, construct the URL with `'http://' + [Net.IPAddress]::Loopback.ToString() + ':5000/'`.
Stop with Ctrl+C. Use another port if a previous process is still listening:
```powershell
.\backend\scripts\demo.ps1 -Port 5001 -KeepDemoPasswords
```
### Existing demo from an earlier release
Existing `backend\instance` databases without the new ownership marker are deliberately not adopted or wiped automatically. Stop the old server, keep a backup, confirm these are only your sample databases, then run once:
```powershell
.\backend\scripts\demo.ps1 -AdoptExistingDemo -KeepDemoPasswords
```
The launcher validates a complete SQLite pair before marking it as demo-owned. It rejects inherited database URLs and `_FILE` redirects, ignores `backend\.env`, pins database targets, and rejects links/junctions/hard-linked database files.
## Presentation sequence
For the administration demonstration, choose **Administrator sign in** and use `admin` with its displayed generated
password. Create an instructor account, then create a degree program and choose its required courses. Edit/reload
the program to show persistence. The editor manages catalog requirements, not personal student semester plans.
Created instructors can sign in immediately but have no student access until advisees are assigned by an operator.
Never put these demonstration passwords into presentation materials.
Registration is enabled by default in development/demo. To demonstrate it, follow **Create a student account**
from sign in, enter names, username, optional program and matching passwords, then sign in with the new credentials.
The account is a student with no advisor; use seeded Alice for the teacher-view demonstration below unless an
operator has assigned the new student to a teacher. `-ClearSavedData` keeps registered accounts, while `-Reset`
deletes them with the rest of the owned demo database pair. Set `REGISTRATION_ENABLED=false` before launching
if you want the signup page disabled for a presentation.
1. **Student login:** sign in as `alice` with the displayed password. Explain that authentication and access checks happen on the backend.
2. **Catalog:** open Courses; search a sample course and show its prerequisite details. Catalog content comes from the database, not hardcoded page arrays.
3. **Plan:** create a named plan. Open Planner, add a semester, add a course and show calculated planned credits.
4. **Validation:** add a course without its prerequisite in an earlier term; show the warning. The credit cap blocks excessive term credits; prerequisite/requirement issues are guidance, not an enrollment approval.
5. **Progress and grades:** open My Degree; mark a course Completed and choose A. Show the planning GPA. Change its grade to F: GPA includes zero quality points for that course, not earned-credit approval.
6. **Retention:** change the course to In Progress; its saved grade remains but is excluded from GPA. Return to Completed and confirm it contributes again. Reload and visit Dashboard to demonstrate database persistence.
7. **Alternate plan:** create a second plan and switch between them. Course progress/grades belong to the student and are shared across plans; semester layouts are plan-specific.
8. **Password change:** demonstrate the Change Password form if desired. Later normal demo startup replaces admin/Alice/teacher1 passwords; use `-KeepDemoPasswords` to preserve your chosen ones.
9. **Teacher login:** sign out and sign in as `teacher1`. Enter Alice's student ID (1 in the untouched sample dataset). Show read-only plan/progress/grade access; Carol (ID 3 in that dataset) is assigned to another teacher and is not accessible.
10. **Logout:** sign out and show that protected pages return to login.
## Clean up for another presentation
Stop the server first. These operations are destructive and do not create an automatic backup:
```powershell
# Clear ALL students' saved plans, semesters, courses, statuses and grades; keep accounts/catalog.
.\backend\scripts\demo.ps1 -ClearSavedData -KeepDemoPasswords

# Recreate BOTH demo databases, sample catalog/accounts and all sample passwords.
.\backend\scripts\demo.ps1 -Reset
```
`-NoRun` still performs setup, password refresh and requested clearing. `-Help`/`-h` changes nothing. `-Reset` takes precedence over `-ClearSavedData`.
## Demo versus production
Demo: repository-local databases, sample accounts, HTTP loopback port 5000, ephemeral session secret and no Windows services.
Production: fresh isolated databases outside the repository, explicit account/catalog administration, durable secrets, HTTPS and protected non-administrator services. See [Windows 11 production quickstart](WINDOWS11_QUICKSTART.md).
The frontend displays and collects information. The backend verifies identity/permissions, saves data, validates requests, calculates planning GPA, checks catalog-based plan rules and records audit events.
Grades and progress are self-reported. Planning GPA is not an official transcript GPA; the application does not validate all graduation rules, transfer equivalencies, retakes or institutional policies.
