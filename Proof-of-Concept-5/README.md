# BrainDrive Protocol PoC 5 (Secure Mode + Admin Onboarding)

## Why this PoC exists

PoC 5 is the secure counterpart to PoC 4.

PoC 4 demonstrates open, fast experimentation.
PoC 5 demonstrates that the same routing and prompting model can be protected with authentication, role checks, and onboarding workflows.

## What PoC 5 demonstrates

1. **JWT authentication**
   - Login endpoint issues signed JWT tokens.
2. **Role-based authorization**
   - `admin` role required for sensitive operations.
3. **Secure prompting flow**
   - `/stream` and `/complete` require authenticated users.
4. **Admin-only audit access**
   - `/audit/recent` is restricted to admins.
5. **Multi-user onboarding (JSON-backed)**
   - Admin can create and update users without an external DB.
6. **Browser UI with both chat and admin panels**
   - Stream/complete chat experience plus admin user-management controls.

## Default test user

- Username: `tester`
- Password: `password`
- Roles: `admin,user`

Stored in:

- `data/users.json`

## Self-contained scope

All PoC 5 code lives in:

- `/home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-5`

No edits to PoC1–PoC4 are required.

## HTTPS with Caddy (new in PoC5)

PoC5 now runs behind **Caddy** so you can demonstrate secure transport (TLS/HTTPS) in addition to login and role checks.

Traffic flow:

`browser/CLI -> caddy (HTTP/HTTPS) -> secure router -> Ollama`

What this adds:

1. TLS termination at the edge.
2. HTTPS-first access on LAN.
3. Security headers on HTTPS responses.
4. JWT cookies marked `Secure` when requests arrive over HTTPS.
5. HTTP endpoint redirects to HTTPS (no plaintext app serving).

## Ports

- HTTP: host `8085` -> Caddy `:80`
- HTTPS: host `8443` -> Caddy `:443`
- Router service is internal-only (`bdp-secure-router:8080`), not directly published on host.

UI:

- Active host comes from `BDP_HOST` in `.env`.
- If `BDP_HOST=localhost`: `https://localhost:8443/ui`
- If `BDP_HOST=<server-ip>`: `https://<server-ip>:8443/ui`
- HTTP redirect entrypoint uses the same host at port `8085`.

## Run

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-5
docker compose up -d
```

All host/IP settings are centralized in:

- `/home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-5/.env`

For LAN use, set `BDP_HOST=<server-ip>` in `.env` before starting.

Optional one-line overrides:

```bash
BDP_HOST=<server-ip> docker compose up -d
```

Optional port override:

```bash
CADDY_HTTP_PORT=8085 CADDY_HTTPS_PORT=8443 docker compose up -d
```

## Troubleshooting: Blank `/ui` page

If `https://<host>:8443/ui` loads as a blank page, this is usually a **host mismatch**, not a cert problem.

Cause:

- Browser URL host does not match `BDP_HOST` in `.env`.
- Example: `.env` has `BDP_HOST=localhost` but you open `https://10.1.2.149:8443/ui`.

Fix:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-5
# set BDP_HOST to the exact host/IP users will type in browser
# e.g. BDP_HOST=10.1.2.149
docker compose up -d --force-recreate caddy bdp-secure-router
```

Quick verify:

```bash
curl -skD - "https://<BDP_HOST>:8443/ui" -o /tmp/poc5-ui.html | rg -i 'HTTP/|content-length'
wc -c /tmp/poc5-ui.html
```

Expected:

- `HTTP/2 200`
- HTML size is non-zero (typically tens of KB), not `0`.

## Trusting the PoC HTTPS certificate (LAN/dev)

Caddy uses `tls internal`, which means browsers will show a warning until you trust Caddy's local root certificate.

Automated helper (recommended):

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-5
./scripts/setup_https_trust.sh
```

What it does:

1. Ensures `caddy` is running.
2. Exports root cert to `data/caddy-root.crt`.
3. Tries to install into system trust store (Linux/macOS).
4. On Linux, also tries to install into NSS/Firefox DBs when `certutil` is available.

Useful flags:

```bash
./scripts/setup_https_trust.sh --export-only
./scripts/setup_https_trust.sh --skip-system --skip-nss
./scripts/setup_https_trust.sh --no-sudo
```

Client download shortcut (for other users on LAN):

- `https://<BDP_HOST>:<CADDY_HTTPS_PORT>/cert/root.crt`

This serves the exported cert directly from PoC5 so users can import it locally.

Manual path (if you prefer):

Root cert path inside the Caddy container:

- `/data/caddy/pki/authorities/local/root.crt`

Copy it out:

```bash
cd /home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-5
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./data/caddy-root.crt
```

Then import `./data/caddy-root.crt` into your client machine's trusted root store.

If you do not import it, HTTPS still works functionally but with browser/curl certificate warnings.

## Demo validation

```bash
./scripts/demo.sh
```

`demo.sh` now defaults to HTTPS and follows redirects. If your cert is not trusted yet, it runs with insecure TLS verification by default (`INSECURE_TLS=1`).

Strict mode (trusted cert required):

```bash
INSECURE_TLS=0 ./scripts/demo.sh
```

The demo script validates:

1. Public health endpoint works.
2. Protected endpoint fails without auth (`401`).
3. Admin login works.
4. Admin creates a new user (`analyst1`).
5. Admin can list users.
6. Non-admin login works for normal prompt access.
7. Non-admin is blocked from `/audit/recent` and `/admin/users` (`403`).
8. Admin can deactivate user.
9. Deactivated user login fails.

## API summary

Public endpoints:

- `GET /health`
- `GET /api`
- `GET /ui`
- `POST /auth/login`

Authenticated endpoints:

- `POST /auth/logout`
- `GET /auth/me`
- `GET /nodes`
- `GET /models`
- `POST /stream`
- `POST /complete`

Admin-only endpoints:

- `GET /audit/recent`
- `GET /admin/users`
- `POST /admin/users` (create user)
- `POST /admin/users/update` (update password/roles/active)

## CLI usage

Interactive secure chat:

```bash
python3 scripts/chat_cli.py
```

With self-signed cert before trust is installed:

```bash
python3 scripts/chat_cli.py --insecure-tls
```

Complete mode:

```bash
python3 scripts/chat_cli.py --insecure-tls --complete \
  --username tester --password password \
  "Give one sentence on secure routing."
```

Admin CLI:

```bash
python3 scripts/admin_cli.py --insecure-tls list
```

## Persistence

- `data/users.json` (user records)
- `data/auth-events.jsonl` (login/logout/user-admin events)
- `data/events.jsonl` (stream/complete routing events)
- `data/caddy_data` (Caddy cert/PKI state)
- `data/caddy_config` (Caddy runtime config)

## Environment config

Primary file:

- `/home/hacker/Projects/BrainDrive-Protocal/Proof-of-Concept-5/.env`

Variables used by compose/scripts:

- `OLLAMA_BASE_URL`
- `JWT_SECRET`
- `JWT_ISSUER`
- `JWT_TTL_SEC`
- `USER_DB_FILE`
- `OLLAMA_DEFAULT_MAX_TOKENS`
- `OLLAMA_DEFAULT_STOP`
- `BDP_HOST`
- `CADDY_HTTP_PORT`
- `CADDY_HTTPS_PORT`
- `ROUTER_BASE_URL` (CLI/demo default)
- `INSECURE_TLS` (demo default)

## Notes

- This PoC demonstrates secure patterns, not full production hardening.
- Default credentials and static secrets are demo-safe defaults and should be changed in real deployments.
