from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import socket
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

PROTOCOL_VERSION = "0.1"

E_BAD_MESSAGE = "E_BAD_MESSAGE"
E_UNSUPPORTED_PROTOCOL = "E_UNSUPPORTED_PROTOCOL"
E_NODE_UNAVAILABLE = "E_NODE_UNAVAILABLE"
E_NODE_ERROR = "E_NODE_ERROR"
E_AUTH_REQUIRED = "E_AUTH_REQUIRED"
E_AUTH_INVALID = "E_AUTH_INVALID"
E_AUTH_FORBIDDEN = "E_AUTH_FORBIDDEN"

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def parse_int_env(name: str, default: int, min_value: int = 1) -> int:
    raw = str(os.getenv(name, str(default))).strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < min_value:
        return default
    return value


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_roles(value: Any, default: Optional[List[str]] = None) -> List[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = [item for item in value if isinstance(item, str)]
    else:
        raw_items = []

    roles: List[str] = []
    for item in raw_items:
        role = item.strip().lower()
        if role and role not in roles:
            roles.append(role)

    if not roles and default is not None:
        for item in default:
            role = str(item).strip().lower()
            if role and role not in roles:
                roles.append(role)

    return roles


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.match(username))


def valid_password(password: str) -> bool:
    return len(password) >= 8


ROUTER_PORT = parse_int_env("ROUTER_PORT", 8080, min_value=1)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
DATA_DIR = Path(os.getenv("BDP_DATA_DIR", "/workspace/data"))
EVENTS_FILE = DATA_DIR / "events.jsonl"
AUTH_EVENTS_FILE = DATA_DIR / "auth-events.jsonl"
CERT_DOWNLOAD_FILE = DATA_DIR / "caddy-root.crt"
USER_DB_FILE = Path(os.getenv("USER_DB_FILE", str(DATA_DIR / "users.json")))
UI_FILE = Path(__file__).resolve().parent / "static" / "index.html"

JWT_SECRET = str(os.getenv("JWT_SECRET", "change-this-in-real-deployments")).strip()
JWT_ISSUER = str(os.getenv("JWT_ISSUER", "bdp-poc5")).strip()
JWT_TTL_SEC = parse_int_env("JWT_TTL_SEC", 3600, min_value=60)

OLLAMA_DEFAULT_MAX_TOKENS = parse_int_env("OLLAMA_DEFAULT_MAX_TOKENS", 512, min_value=1)
OLLAMA_DEFAULT_STOP = [s.strip() for s in str(os.getenv("OLLAMA_DEFAULT_STOP", "")).split(",") if s.strip()]

NODE_PROFILES: Dict[str, Dict[str, str]] = {
    "general": {
        "node_id": "node.assistant.general",
        "default_model": "ministral-3:8b",
        "system_prompt": (
            "You are the BrainDrive general assistant node. "
            "Answer clearly, directly, and keep responses useful for engineering work."
        ),
    },
    "builder": {
        "node_id": "node.assistant.builder",
        "default_model": "qwen3:8b",
        "system_prompt": (
            "You are the BrainDrive builder node. "
            "Provide implementation-first guidance, concrete steps, and production-minded tradeoffs."
        ),
    },
}

DIRECTIVE_NODE_RE = re.compile(r"^/node:([^\s]+)$")
DIRECTIVE_MODEL_RE = re.compile(r"^/model:([^\s]+)$")


def append_jsonl(path: Path, entry: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def append_event(entry: Dict[str, Any]) -> None:
    append_jsonl(EVENTS_FILE, entry)


def append_auth_event(entry: Dict[str, Any]) -> None:
    append_jsonl(AUTH_EVENTS_FILE, entry)


def ensure_data_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not USER_DB_FILE.exists():
        ts = now_iso()
        USER_DB_FILE.write_text(
            json.dumps(
                {
                    "users": [
                        {
                            "username": "tester",
                            "password_sha256": sha256_hex("password"),
                            "roles": ["admin", "user"],
                            "active": True,
                            "created_at": ts,
                            "updated_at": ts,
                            "created_by": "bootstrap",
                            "updated_by": "bootstrap",
                        }
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def load_users() -> List[Dict[str, Any]]:
    ensure_data_files()
    try:
        payload = json.loads(USER_DB_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, dict):
        return []
    users = payload.get("users", [])
    if not isinstance(users, list):
        return []
    return [u for u in users if isinstance(u, dict)]


def save_users(users: List[Dict[str, Any]]) -> None:
    ensure_data_files()
    USER_DB_FILE.write_text(json.dumps({"users": users}, indent=2) + "\n", encoding="utf-8")


def user_index(users: List[Dict[str, Any]], username: str) -> int:
    target = username.strip().lower()
    for idx, user in enumerate(users):
        if str(user.get("username", "")).strip().lower() == target:
            return idx
    return -1


def find_user(username: str) -> Optional[Dict[str, Any]]:
    users = load_users()
    idx = user_index(users, username)
    return users[idx] if idx >= 0 else None


def sanitize_user(user: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "username": str(user.get("username", "")),
        "roles": normalize_roles(user.get("roles", []), default=[]),
        "active": bool(user.get("active", True)),
        "created_at": user.get("created_at"),
        "updated_at": user.get("updated_at"),
        "created_by": user.get("created_by"),
        "updated_by": user.get("updated_by"),
    }


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def jwt_encode(claims: Dict[str, Any]) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    claims_b64 = b64url_encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    signature = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_b64}.{claims_b64}.{b64url_encode(signature)}"


def jwt_decode(token: str) -> Dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token")

    header_b64, claims_b64, sig_b64 = parts

    try:
        header = json.loads(b64url_decode(header_b64).decode("utf-8"))
        claims = json.loads(b64url_decode(claims_b64).decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid token encoding: {exc}")

    if not isinstance(header, dict) or header.get("alg") != "HS256":
        raise ValueError("Unsupported token algorithm")
    if not isinstance(claims, dict):
        raise ValueError("Invalid claims")

    signing_input = f"{header_b64}.{claims_b64}".encode("ascii")
    expected_sig = hmac.new(JWT_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    try:
        actual_sig = b64url_decode(sig_b64)
    except Exception:
        raise ValueError("Invalid signature encoding")

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid token signature")

    now = int(time.time())
    exp = int(claims.get("exp", 0)) if isinstance(claims.get("exp"), int) else 0
    iat = int(claims.get("iat", 0)) if isinstance(claims.get("iat"), int) else 0
    if exp <= now:
        raise ValueError("Token expired")
    if iat > now + 60:
        raise ValueError("Token issued in future")
    if claims.get("iss") != JWT_ISSUER:
        raise ValueError("Invalid issuer")

    claims["roles"] = normalize_roles(claims.get("roles", []), default=[])
    return claims


def validate_message(message: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(message, dict):
        return make_error(None, E_BAD_MESSAGE, "Message must be an object")

    msg_id = message.get("message_id")
    for field in ("protocol_version", "message_id", "intent", "payload"):
        if field not in message:
            return make_error(msg_id, E_BAD_MESSAGE, f"Missing required field: {field}")

    if not isinstance(message.get("protocol_version"), str):
        return make_error(msg_id, E_BAD_MESSAGE, "protocol_version must be string")
    if not isinstance(message.get("message_id"), str):
        return make_error(msg_id, E_BAD_MESSAGE, "message_id must be string")
    if not isinstance(message.get("intent"), str):
        return make_error(msg_id, E_BAD_MESSAGE, "intent must be string")
    if not isinstance(message.get("payload"), dict):
        return make_error(msg_id, E_BAD_MESSAGE, "payload must be object")

    if message["protocol_version"] != PROTOCOL_VERSION:
        return make_error(
            msg_id,
            E_UNSUPPORTED_PROTOCOL,
            f"PoC5 supports protocol version {PROTOCOL_VERSION}",
            {"received": message["protocol_version"]},
        )

    if message["intent"] not in {"chat", "prompt", "ask"}:
        return make_error(msg_id, E_BAD_MESSAGE, "intent must be one of: chat, prompt, ask")

    text = message["payload"].get("text")
    if not isinstance(text, str):
        return make_error(msg_id, E_BAD_MESSAGE, "payload.text must be string")

    return None


def make_error(parent_message_id: Optional[str], code: str, message: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": new_id(),
        "intent": "error",
        "payload": {
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": details or {},
            }
        },
        "extensions": {
            "trace": {
                "parent_message_id": parent_message_id,
                "depth": 1,
                "path": ["router.secure"],
            }
        },
    }


def parse_directives(text: str) -> Dict[str, Optional[str]]:
    selected_node: Optional[str] = None
    selected_model: Optional[str] = None
    cleaned_tokens = []

    for token in text.split():
        node_match = DIRECTIVE_NODE_RE.match(token)
        if node_match:
            selected_node = node_match.group(1).strip()
            continue

        model_match = DIRECTIVE_MODEL_RE.match(token)
        if model_match:
            selected_model = model_match.group(1).strip()
            continue

        cleaned_tokens.append(token)

    return {
        "node": selected_node,
        "model": selected_model,
        "prompt": " ".join(cleaned_tokens).strip(),
    }


def post_json(url: str, payload: Dict[str, Any], timeout_sec: float = 15.0) -> Dict[str, Any]:
    req = request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Expected JSON object response")
    return parsed


def get_models() -> Dict[str, Any]:
    req = request.Request(url=f"{OLLAMA_BASE_URL}/api/tags", method="GET")
    with request.urlopen(req, timeout=10.0) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Unexpected /api/tags response")
    return parsed


def tail_jsonl(path: Path, limit: int) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


class RouterHandler(BaseHTTPRequestHandler):
    server_version = "bdp-secure-router/0.2"

    def _send_json(self, status: int, body: Dict[str, Any], extra_headers: Optional[Dict[str, str]] = None) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def _send_html(self, status: int, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def _send_binary(
        self,
        status: int,
        body: bytes,
        content_type: str,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int = 204, extra_headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.send_header("Access-Control-Allow-Origin", "*")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()

    def _start_sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _sse(self, event: str, data: Dict[str, Any]) -> None:
        chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
        self.wfile.write(chunk)
        self.wfile.flush()

    def _read_json(self) -> Dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(size)
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("JSON body must be object")
        return parsed

    def _get_cookie(self, name: str) -> Optional[str]:
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            if key.strip() == name:
                return value.strip()
        return None

    def _extract_token(self) -> Optional[str]:
        auth_header = self.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer ") :].strip()
            if token:
                return token
        cookie_token = self._get_cookie("bdp_token")
        if cookie_token:
            return cookie_token
        return None

    def _is_https_request(self) -> bool:
        proto = self.headers.get("X-Forwarded-Proto", "")
        if proto:
            forwarded = proto.split(",", 1)[0].strip().lower()
            return forwarded == "https"
        return False

    def _cookie_header(self, value: str, max_age: int) -> str:
        attrs = [
            f"bdp_token={value}",
            "Path=/",
            f"Max-Age={max_age}",
            "HttpOnly",
            "SameSite=Lax",
        ]
        if self._is_https_request():
            attrs.append("Secure")
        return "; ".join(attrs)

    def _principal_or_error(self, required_role: Optional[str] = None) -> Optional[Dict[str, Any]]:
        token = self._extract_token()
        if not token:
            self._send_json(401, make_error(None, E_AUTH_REQUIRED, "Authentication required"))
            return None

        try:
            principal = jwt_decode(token)
        except Exception as exc:
            self._send_json(401, make_error(None, E_AUTH_INVALID, "Invalid authentication token", {"error": str(exc)}))
            return None

        if required_role and required_role not in normalize_roles(principal.get("roles", []), default=[]):
            self._send_json(
                403,
                make_error(None, E_AUTH_FORBIDDEN, f"{required_role} role required", {"required_role": required_role}),
            )
            return None

        return principal

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        route, _, query = self.path.partition("?")

        if route in {"/", "/ui", "/login"}:
            if UI_FILE.exists():
                self._send_html(200, UI_FILE.read_text(encoding="utf-8"))
            else:
                self._send_json(500, {"ok": False, "error": "ui_not_found"})
            return

        if route == "/favicon.ico":
            self._send_empty(204)
            return

        if route == "/cert/root.crt":
            if not CERT_DOWNLOAD_FILE.exists():
                self._send_json(
                    404,
                    {
                        "ok": False,
                        "error": "cert_not_found",
                        "details": {
                            "path": str(CERT_DOWNLOAD_FILE),
                            "hint": "Run ./scripts/setup_https_trust.sh --export-only",
                        },
                    },
                )
                return

            try:
                cert_bytes = CERT_DOWNLOAD_FILE.read_bytes()
            except Exception as exc:
                self._send_json(500, {"ok": False, "error": "cert_read_error", "details": {"error": str(exc)}})
                return

            self._send_binary(
                200,
                cert_bytes,
                "application/x-x509-ca-cert",
                extra_headers={"Content-Disposition": "attachment; filename=bdp-poc5-root.crt"},
            )
            return

        if route == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "bdp-secure-router",
                    "secure_mode": True,
                    "ollama_base_url": OLLAMA_BASE_URL,
                    "jwt_issuer": JWT_ISSUER,
                },
            )
            return

        if route == "/api":
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "bdp-secure-router",
                    "secure_mode": True,
                    "default_user": "tester",
                    "endpoints": [
                        "/health",
                        "/auth/login",
                        "/auth/logout",
                        "/auth/me",
                        "/nodes",
                        "/models",
                        "/complete",
                        "/stream",
                        "/audit/recent",
                        "/admin/users",
                        "/admin/users/update",
                        "/ui",
                    ],
                    "ollama_default_max_tokens": OLLAMA_DEFAULT_MAX_TOKENS,
                    "ollama_default_stop": OLLAMA_DEFAULT_STOP,
                },
            )
            return

        if route == "/auth/me":
            principal = self._principal_or_error()
            if principal is None:
                return
            self._send_json(
                200,
                {
                    "ok": True,
                    "user": {
                        "username": principal.get("sub"),
                        "roles": principal.get("roles", []),
                        "expires_at": principal.get("exp"),
                    },
                },
            )
            return

        if route == "/nodes":
            principal = self._principal_or_error()
            if principal is None:
                return
            self._send_json(200, {"ok": True, "nodes": NODE_PROFILES})
            return

        if route == "/models":
            principal = self._principal_or_error()
            if principal is None:
                return
            try:
                tags = get_models()
                model_names = [m.get("name") for m in tags.get("models", []) if isinstance(m, dict) and isinstance(m.get("name"), str)]
                self._send_json(200, {"ok": True, "models": model_names})
            except Exception as exc:
                self._send_json(502, {"ok": False, "error": f"Failed to query Ollama models: {type(exc).__name__}: {exc}"})
            return

        if route == "/audit/recent":
            principal = self._principal_or_error(required_role="admin")
            if principal is None:
                return

            params = parse.parse_qs(query, keep_blank_values=False)
            raw_limit = params.get("limit", ["40"])[0]
            try:
                limit = max(1, min(200, int(raw_limit)))
            except ValueError:
                limit = 40

            auth_events = tail_jsonl(AUTH_EVENTS_FILE, limit)
            route_events = tail_jsonl(EVENTS_FILE, limit)
            combined = sorted(auth_events + route_events, key=lambda e: str(e.get("ts", "")))[-limit:]
            self._send_json(
                200,
                {
                    "ok": True,
                    "limit": limit,
                    "auth_events": auth_events,
                    "router_events": route_events,
                    "combined": combined,
                },
            )
            return

        if route == "/admin/users":
            principal = self._principal_or_error(required_role="admin")
            if principal is None:
                return
            users = [sanitize_user(user) for user in load_users()]
            self._send_json(200, {"ok": True, "users": users})
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        route = self.path.partition("?")[0]

        if route == "/auth/login":
            self._handle_login()
            return

        if route == "/auth/logout":
            self._handle_logout()
            return

        if route == "/admin/users":
            principal = self._principal_or_error(required_role="admin")
            if principal is None:
                return
            self._handle_admin_create_user(principal)
            return

        if route == "/admin/users/update":
            principal = self._principal_or_error(required_role="admin")
            if principal is None:
                return
            self._handle_admin_update_user(principal)
            return

        if route == "/complete":
            principal = self._principal_or_error()
            if principal is None:
                return
            self._handle_complete(principal)
            return

        if route == "/stream":
            principal = self._principal_or_error()
            if principal is None:
                return
            self._handle_stream(principal)
            return

        self._send_json(404, {"ok": False, "error": "not_found"})

    def _resolve_target(self, message: Dict[str, Any]) -> Dict[str, str]:
        payload = message.get("payload", {})
        extensions = message.get("extensions", {}) or {}
        ext_llm = extensions.get("llm", {}) if isinstance(extensions, dict) else {}

        raw_text = str(payload.get("text", ""))
        directives = parse_directives(raw_text)

        requested_node = directives.get("node") or (ext_llm.get("node") if isinstance(ext_llm, dict) else None) or "general"
        requested_model = directives.get("model") or (ext_llm.get("model") if isinstance(ext_llm, dict) else None)
        cleaned_prompt = directives.get("prompt") or raw_text.strip()

        node_key = str(requested_node)
        if node_key not in NODE_PROFILES:
            node_key = "general"

        profile = NODE_PROFILES[node_key]
        model = str(requested_model or profile["default_model"])

        return {
            "node": node_key,
            "node_id": profile["node_id"],
            "model": model,
            "prompt": cleaned_prompt,
            "system_prompt": profile["system_prompt"],
        }

    def _parse_stop_sequences(self, value: Any) -> List[str]:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, list):
            candidates = [item for item in value if isinstance(item, str)]
        else:
            return []

        stop_sequences: List[str] = []
        for raw in candidates:
            token = raw.strip()
            if token and token not in stop_sequences:
                stop_sequences.append(token)
        return stop_sequences

    def _resolve_ollama_options(self, message: Dict[str, Any]) -> Dict[str, Any]:
        payload = message.get("payload", {})
        extensions = message.get("extensions", {}) or {}
        ext_llm = extensions.get("llm", {}) if isinstance(extensions, dict) else {}

        raw_max_tokens: Any = None
        if isinstance(ext_llm, dict):
            if "max_tokens" in ext_llm:
                raw_max_tokens = ext_llm.get("max_tokens")
            elif "num_predict" in ext_llm:
                raw_max_tokens = ext_llm.get("num_predict")
        if raw_max_tokens is None and isinstance(payload, dict):
            raw_max_tokens = payload.get("max_tokens")

        max_tokens = OLLAMA_DEFAULT_MAX_TOKENS
        if raw_max_tokens is not None:
            try:
                parsed = int(raw_max_tokens)
                if parsed > 0:
                    max_tokens = parsed
            except (TypeError, ValueError):
                pass

        raw_stop: Any = None
        if isinstance(ext_llm, dict) and "stop" in ext_llm:
            raw_stop = ext_llm.get("stop")
        elif isinstance(payload, dict):
            raw_stop = payload.get("stop")

        if raw_stop is None:
            stop_sequences = list(OLLAMA_DEFAULT_STOP)
        else:
            stop_sequences = self._parse_stop_sequences(raw_stop)

        options: Dict[str, Any] = {"num_predict": max_tokens}
        if stop_sequences:
            options["stop"] = stop_sequences
        return options

    def _ollama_chat(self, model: str, system_prompt: str, prompt: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        if options:
            payload["options"] = options
        return post_json(f"{OLLAMA_BASE_URL}/api/chat", payload, timeout_sec=180.0)

    def _handle_login(self) -> None:
        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return

        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))

        if not username or not password:
            self._send_json(400, {"ok": False, "error": "username_and_password_required"})
            return

        user = find_user(username)
        if user is None or not bool(user.get("active", True)):
            append_auth_event({"ts": now_iso(), "event": "login_failed", "username": username, "reason": "user_not_found_or_inactive"})
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return

        expected_hash = str(user.get("password_sha256", ""))
        if not expected_hash or not hmac.compare_digest(expected_hash, sha256_hex(password)):
            append_auth_event({"ts": now_iso(), "event": "login_failed", "username": username, "reason": "bad_password"})
            self._send_json(401, {"ok": False, "error": "invalid_credentials"})
            return

        issued_at = int(time.time())
        expires_at = issued_at + JWT_TTL_SEC
        roles = normalize_roles(user.get("roles", []), default=["user"])
        claims = {
            "iss": JWT_ISSUER,
            "sub": str(user.get("username")),
            "roles": roles,
            "iat": issued_at,
            "exp": expires_at,
        }
        token = jwt_encode(claims)

        append_auth_event({
            "ts": now_iso(),
            "event": "login_success",
            "username": username,
            "roles": roles,
            "exp": expires_at,
        })

        self._send_json(
            200,
            {
                "ok": True,
                "token": token,
                "token_type": "Bearer",
                "expires_in": JWT_TTL_SEC,
                "user": {
                    "username": claims["sub"],
                    "roles": roles,
                    "expires_at": expires_at,
                },
            },
            extra_headers={
                "Set-Cookie": self._cookie_header(token, JWT_TTL_SEC)
            },
        )

    def _handle_logout(self) -> None:
        username = "unknown"
        token = self._extract_token()
        if token:
            try:
                claims = jwt_decode(token)
                username = str(claims.get("sub", "unknown"))
            except Exception:
                pass

        append_auth_event({"ts": now_iso(), "event": "logout", "username": username})
        self._send_json(
            200,
            {"ok": True, "message": "logged_out"},
            extra_headers={"Set-Cookie": self._cookie_header("", 0)},
        )

    def _handle_admin_create_user(self, principal: Dict[str, Any]) -> None:
        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return

        username = str(body.get("username", "")).strip()
        password = str(body.get("password", ""))
        roles = normalize_roles(body.get("roles"), default=["user"])
        active = bool(body.get("active", True))

        if not valid_username(username):
            self._send_json(400, {"ok": False, "error": "invalid_username", "details": "Use 3-64 chars: letters, numbers, _, ., -"})
            return

        if not valid_password(password):
            self._send_json(400, {"ok": False, "error": "invalid_password", "details": "Password must be at least 8 characters"})
            return

        users = load_users()
        if user_index(users, username) >= 0:
            self._send_json(409, {"ok": False, "error": "user_exists"})
            return

        ts = now_iso()
        actor = str(principal.get("sub", "unknown"))
        record = {
            "username": username,
            "password_sha256": sha256_hex(password),
            "roles": roles,
            "active": active,
            "created_at": ts,
            "updated_at": ts,
            "created_by": actor,
            "updated_by": actor,
        }
        users.append(record)
        save_users(users)

        append_auth_event({
            "ts": ts,
            "event": "user_created",
            "actor": actor,
            "username": username,
            "roles": roles,
            "active": active,
        })

        self._send_json(201, {"ok": True, "user": sanitize_user(record)})

    def _handle_admin_update_user(self, principal: Dict[str, Any]) -> None:
        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, {"ok": False, "error": "invalid_json"})
            return

        username = str(body.get("username", "")).strip()
        if not username:
            self._send_json(400, {"ok": False, "error": "username_required"})
            return

        users = load_users()
        idx = user_index(users, username)
        if idx < 0:
            self._send_json(404, {"ok": False, "error": "user_not_found"})
            return

        user = users[idx]
        changed: List[str] = []

        if "password" in body:
            password = str(body.get("password", ""))
            if not valid_password(password):
                self._send_json(400, {"ok": False, "error": "invalid_password", "details": "Password must be at least 8 characters"})
                return
            user["password_sha256"] = sha256_hex(password)
            changed.append("password")

        if "roles" in body:
            roles = normalize_roles(body.get("roles"), default=[])
            if not roles:
                self._send_json(400, {"ok": False, "error": "invalid_roles"})
                return
            user["roles"] = roles
            changed.append("roles")

        if "active" in body:
            user["active"] = bool(body.get("active"))
            changed.append("active")

        if not changed:
            self._send_json(400, {"ok": False, "error": "no_changes"})
            return

        actor = str(principal.get("sub", "unknown"))
        user["updated_at"] = now_iso()
        user["updated_by"] = actor
        users[idx] = user
        save_users(users)

        append_auth_event({
            "ts": now_iso(),
            "event": "user_updated",
            "actor": actor,
            "username": username,
            "changed": changed,
        })

        self._send_json(200, {"ok": True, "user": sanitize_user(user), "changed": changed})

    def _handle_complete(self, principal: Dict[str, Any]) -> None:
        try:
            message = self._read_json()
        except Exception:
            self._send_json(400, make_error(None, E_BAD_MESSAGE, "Invalid JSON body"))
            return

        validation_error = validate_message(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        target = self._resolve_target(message)
        ollama_options = self._resolve_ollama_options(message)
        msg_id = message["message_id"]
        actor = str(principal.get("sub", "unknown"))

        if not target["prompt"]:
            self._send_json(200, make_error(msg_id, E_BAD_MESSAGE, "Prompt is empty after directive parsing"))
            return

        try:
            ollama = self._ollama_chat(target["model"], target["system_prompt"], target["prompt"], ollama_options)
            content = ollama.get("message", {}).get("content", "") if isinstance(ollama, dict) else ""
            response = {
                "protocol_version": PROTOCOL_VERSION,
                "message_id": new_id(),
                "intent": "chat_response",
                "payload": {
                    "text": content,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "route_mode": "secure_direct",
                    "ollama_done_reason": ollama.get("done_reason") if isinstance(ollama, dict) else None,
                },
                "extensions": {
                    "identity": {
                        "actor_id": actor,
                        "actor_type": "human",
                        "roles": principal.get("roles", []),
                    },
                    "trace": {
                        "parent_message_id": msg_id,
                        "depth": 1,
                        "path": ["router.secure.complete", target["node_id"]],
                    },
                },
            }

            append_event(
                {
                    "ts": now_iso(),
                    "event": "complete",
                    "message_id": msg_id,
                    "actor": actor,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "max_tokens": ollama_options.get("num_predict"),
                    "stop_count": len(ollama_options.get("stop", [])),
                    "prompt_preview": target["prompt"][:120],
                    "response_preview": str(content)[:120],
                }
            )
            self._send_json(200, response)
        except Exception as exc:
            append_event(
                {
                    "ts": now_iso(),
                    "event": "complete_error",
                    "message_id": msg_id,
                    "actor": actor,
                    "error": str(exc),
                }
            )
            self._send_json(
                200,
                make_error(
                    msg_id,
                    E_NODE_UNAVAILABLE,
                    f"Ollama unavailable: {type(exc).__name__}",
                    {"error": str(exc), "ollama_base_url": OLLAMA_BASE_URL},
                ),
            )

    def _handle_stream(self, principal: Dict[str, Any]) -> None:
        try:
            message = self._read_json()
        except Exception:
            self._send_json(400, make_error(None, E_BAD_MESSAGE, "Invalid JSON body"))
            return

        validation_error = validate_message(message)
        if validation_error:
            self._send_json(200, validation_error)
            return

        target = self._resolve_target(message)
        ollama_options = self._resolve_ollama_options(message)
        msg_id = message["message_id"]
        actor = str(principal.get("sub", "unknown"))

        if not target["prompt"]:
            self._send_json(200, make_error(msg_id, E_BAD_MESSAGE, "Prompt is empty after directive parsing"))
            return

        self._start_sse()

        try:
            self._sse(
                "meta",
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "message_id": msg_id,
                    "actor": actor,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "max_tokens": ollama_options.get("num_predict"),
                    "stop_count": len(ollama_options.get("stop", [])),
                },
            )

            req_payload = {
                "model": target["model"],
                "messages": [
                    {"role": "system", "content": target["system_prompt"]},
                    {"role": "user", "content": target["prompt"]},
                ],
                "stream": True,
            }
            if ollama_options:
                req_payload["options"] = ollama_options

            req = request.Request(
                url=f"{OLLAMA_BASE_URL}/api/chat",
                data=json.dumps(req_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            full_text = ""
            token_count = 0
            done_payload: Dict[str, Any] = {}

            with request.urlopen(req, timeout=600.0) as resp:
                for raw in resp:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue

                    try:
                        part = json.loads(line)
                    except Exception:
                        self._sse("error", {"code": E_NODE_ERROR, "message": "Invalid Ollama stream chunk", "raw": line[:200]})
                        continue

                    if isinstance(part, dict) and "error" in part:
                        self._sse(
                            "error",
                            {
                                "code": E_NODE_ERROR,
                                "message": str(part.get("error")),
                                "model": target["model"],
                            },
                        )
                        break

                    piece = ""
                    if isinstance(part, dict):
                        message_obj = part.get("message")
                        if isinstance(message_obj, dict):
                            piece = str(message_obj.get("content", ""))

                    if piece:
                        token_count += 1
                        full_text += piece
                        self._sse("token", {"text": piece})

                    if isinstance(part, dict) and bool(part.get("done", False)):
                        done_payload = part
                        break

            self._sse(
                "done",
                {
                    "message_id": msg_id,
                    "actor": actor,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "route_mode": "secure_direct",
                    "token_events": token_count,
                    "output_chars": len(full_text),
                    "ollama_done_reason": done_payload.get("done_reason"),
                    "max_tokens": ollama_options.get("num_predict"),
                    "stop_count": len(ollama_options.get("stop", [])),
                },
            )

            append_event(
                {
                    "ts": now_iso(),
                    "event": "stream_complete",
                    "message_id": msg_id,
                    "actor": actor,
                    "node": target["node"],
                    "node_id": target["node_id"],
                    "model": target["model"],
                    "prompt_preview": target["prompt"][:120],
                    "output_chars": len(full_text),
                    "token_events": token_count,
                    "ollama_done_reason": done_payload.get("done_reason"),
                }
            )
        except BrokenPipeError:
            append_event(
                {
                    "ts": now_iso(),
                    "event": "client_disconnected",
                    "message_id": msg_id,
                    "actor": actor,
                    "node": target["node"],
                    "model": target["model"],
                }
            )
        except error.URLError as exc:
            self._sse(
                "error",
                {
                    "code": E_NODE_UNAVAILABLE,
                    "message": f"Ollama request failed: {exc}",
                    "ollama_base_url": OLLAMA_BASE_URL,
                },
            )
        except Exception as exc:
            self._sse(
                "error",
                {
                    "code": E_NODE_ERROR,
                    "message": f"Router stream error: {type(exc).__name__}",
                    "details": {"error": str(exc)},
                },
            )
        finally:
            self.close_connection = True
            try:
                self.wfile.flush()
            except Exception:
                pass
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except Exception:
                pass

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    ensure_data_files()
    print(f"bdp secure router listening on :{ROUTER_PORT}")
    print(f"ollama base url: {OLLAMA_BASE_URL}")
    print(f"jwt issuer: {JWT_ISSUER}")
    print(f"jwt ttl sec: {JWT_TTL_SEC}")
    print(f"default max tokens: {OLLAMA_DEFAULT_MAX_TOKENS}")
    print(f"default stop count: {len(OLLAMA_DEFAULT_STOP)}")
    print(f"user db: {USER_DB_FILE}")
    server = ThreadingHTTPServer(("0.0.0.0", ROUTER_PORT), RouterHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
