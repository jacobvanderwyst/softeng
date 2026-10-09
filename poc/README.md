# Degree Plan Tool: standalone proof of concept

> **Not for deployment.** This is a throw-away demo kept for reference. The production system is in
> [`../backend`](../backend) and is documented in [`../docs`](../docs). The POC uses in-memory sessions, a single
> process, a self-signed certificate and its own minimal users table; it is intentionally excluded from the
> deployment scripts.

A small, dependency-light server (Python standard library) that:
- listens on **80** (HTTP) and **443** (HTTPS),
- serves only the pages it has (fixed route table, no filesystem lookups),
- shows the **login page first**, checks username/password against a SQLite `users` table,
  and serves the **landing page** on success.

## Run (PowerShell)
```powershell
cd poc
pip install -r requirements.txt          # cryptography (cert generation) + pytest
python server.py --init-demo             # creates a demo DB + self-signed cert, listens on 80/443
```
`--init-demo` creates four demo accounts (`admin`, `teacher1`, `alice`, `bob`) with **random passwords that are
printed once** in the terminal; they are not stored anywhere in clear text. Copy them from that output. (If a demo
database already exists, delete `poc\instance` first to get fresh accounts.)

Open https://localhost/ (accept the self-signed certificate warning). Port 80 redirects to HTTPS.

If 80/443 are taken (IIS, Skype, etc.) or you want no admin prompt:
`python server.py --http-port 8080 --https-port 8443`

Options: `--bind ""` (all interfaces; default is localhost only), `--db PATH`, `--cert/--key`,
`--http-mode serve` (serve pages over plain HTTP instead of redirecting; logins are then unencrypted).

## Routes
- `GET /` (also `/login`, `/index.html`): login page (redirects to `/landing` if already signed in)
- `POST /login`: valid -> sets session cookie, 303 to `/landing`; invalid -> login page with error (401)
- `GET /landing`: landing page, requires a session (otherwise 303 to `/`)
- `POST /logout`: ends the session
- `GET /static/style.css`; anything else -> 404 page

## Pages
Edit `pages/login.html`, `pages/landing.html`, `pages/404.html`, `static/style.css`.
Placeholders: `{{error}}` (login), `{{username}}`, `{{role}}` (landing). Add new pages by adding a route in `server.py`.

## Security notes
- scrypt password hashes with constant-work checks even for unknown users
- HttpOnly + Secure + SameSite=Strict session cookie; random server-side session IDs (in memory: lost on restart)
- 5 failed logins per minute per IP -> 429; request bodies capped at 4 KB; security headers; user input HTML-escaped
- Request/auth logging to console and `logs/poc.log`; passwords are never logged

## Tests
```powershell
pytest          # real sockets + TLS on random high ports; no admin rights needed
```

## Limitations (by design)
In-memory sessions, single process, self-signed cert, no CSRF token on logout (mitigated by SameSite=Strict),
`ThreadingHTTPServer` is not meant for production traffic, and it does **not** read the production databases (those
use Argon2id hashes and a different schema).
