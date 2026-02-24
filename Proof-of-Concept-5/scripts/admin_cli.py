from __future__ import annotations

import argparse
import json
import os
import ssl
from pathlib import Path
from typing import Any, Dict, List
from urllib import request


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")
BDP_HOST = os.getenv("BDP_HOST", "localhost")
CADDY_HTTPS_PORT = os.getenv("CADDY_HTTPS_PORT", "8443")
DEFAULT_ROUTER_BASE = os.getenv("ROUTER_BASE_URL", f"https://{BDP_HOST}:{CADDY_HTTPS_PORT}")


def ssl_context(insecure_tls: bool) -> ssl.SSLContext | None:
    if not insecure_tls:
        return None
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def post_json(
    url: str,
    body: Dict[str, Any],
    token: str | None = None,
    insecure_tls: bool = False,
) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(
        url=url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with request.urlopen(req, timeout=30, context=ssl_context(insecure_tls)) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Expected JSON object")
    return parsed


def get_json(
    url: str,
    token: str | None = None,
    insecure_tls: bool = False,
) -> Dict[str, Any]:
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url=url, headers=headers, method="GET")
    with request.urlopen(req, timeout=30, context=ssl_context(insecure_tls)) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Expected JSON object")
    return parsed


def login(router_base: str, username: str, password: str, insecure_tls: bool = False) -> str:
    payload = post_json(
        f"{router_base}/auth/login",
        {"username": username, "password": password},
        insecure_tls=insecure_tls,
    )
    token = payload.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError(f"Login failed: {payload}")
    return token


def parse_roles(raw: str) -> List[str]:
    return [r.strip() for r in raw.split(",") if r.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC5 admin onboarding CLI")
    parser.add_argument("--router-base", default=DEFAULT_ROUTER_BASE)
    parser.add_argument("--admin-user", default="tester")
    parser.add_argument("--admin-pass", default="password")
    parser.add_argument("--insecure-tls", action="store_true", help="Disable TLS certificate verification (dev only)")

    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List users")

    create = sub.add_parser("create", help="Create user")
    create.add_argument("username")
    create.add_argument("password")
    create.add_argument("--roles", default="user")
    create.add_argument("--inactive", action="store_true")

    update = sub.add_parser("update", help="Update user")
    update.add_argument("username")
    update.add_argument("--password", default=None)
    update.add_argument("--roles", default=None)
    update.add_argument("--active", choices=["true", "false"], default=None)

    args = parser.parse_args()

    token = login(
        args.router_base,
        args.admin_user,
        args.admin_pass,
        insecure_tls=args.insecure_tls,
    )

    if args.cmd == "list":
        payload = get_json(
            f"{args.router_base}/admin/users",
            token=token,
            insecure_tls=args.insecure_tls,
        )
        print(json.dumps(payload, indent=2))
        return

    if args.cmd == "create":
        body = {
            "username": args.username,
            "password": args.password,
            "roles": parse_roles(args.roles),
            "active": not args.inactive,
        }
        payload = post_json(
            f"{args.router_base}/admin/users",
            body,
            token=token,
            insecure_tls=args.insecure_tls,
        )
        print(json.dumps(payload, indent=2))
        return

    if args.cmd == "update":
        body: Dict[str, Any] = {"username": args.username}
        if args.password is not None:
            body["password"] = args.password
        if args.roles is not None:
            body["roles"] = parse_roles(args.roles)
        if args.active is not None:
            body["active"] = args.active == "true"

        payload = post_json(
            f"{args.router_base}/admin/users/update",
            body,
            token=token,
            insecure_tls=args.insecure_tls,
        )
        print(json.dumps(payload, indent=2))
        return


if __name__ == "__main__":
    main()
