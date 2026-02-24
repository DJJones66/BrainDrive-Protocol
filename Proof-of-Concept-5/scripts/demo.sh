#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f ./.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source ./.env
  set +a
fi

BASE_URL="${ROUTER_BASE_URL:-https://${BDP_HOST:-localhost}:${CADDY_HTTPS_PORT:-8443}}"
INSECURE_TLS="${INSECURE_TLS:-1}"
TEST_USER="analyst_$(date +%s)"
TEST_PASS="password123"

if [[ "$INSECURE_TLS" == "1" ]]; then
  CURL_BASE=(curl -skL)
else
  CURL_BASE=(curl -sL)
fi

curl_json() {
  "${CURL_BASE[@]}" "$@"
}

curl_status() {
  local output_file="$1"
  shift
  "${CURL_BASE[@]}" -o "$output_file" -w "%{http_code}" "$@"
}

echo "== health (public) =="
curl_json "$BASE_URL/health" | jq

echo
echo "== secure endpoint without auth (should be 401) =="
HTTP_CODE="$(curl_status /tmp/poc5_noauth_nodes.json "$BASE_URL/nodes")"
cat /tmp/poc5_noauth_nodes.json | jq
if [[ "$HTTP_CODE" != "401" ]]; then
  echo "Expected 401 without auth, got $HTTP_CODE"
  exit 1
fi

echo
echo "== login as admin tester/password =="
ADMIN_LOGIN_PAYLOAD="$(curl_json "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"tester","password":"password"}')"
echo "$ADMIN_LOGIN_PAYLOAD" | jq

ADMIN_TOKEN="$(echo "$ADMIN_LOGIN_PAYLOAD" | jq -r '.token')"
if [[ -z "$ADMIN_TOKEN" || "$ADMIN_TOKEN" == "null" ]]; then
  echo "Admin login failed: token missing"
  exit 1
fi

echo
echo "== admin creates user $TEST_USER =="
CREATE_PAYLOAD="$(curl_json "$BASE_URL/admin/users" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$TEST_USER\",\"password\":\"$TEST_PASS\",\"roles\":[\"user\"],\"active\":true}")"
echo "$CREATE_PAYLOAD" | jq
if [[ "$(echo "$CREATE_PAYLOAD" | jq -r '.ok')" != "true" ]]; then
  echo "Create user failed"
  exit 1
fi

echo
echo "== admin can view users =="
curl_json "$BASE_URL/admin/users" -H "Authorization: Bearer $ADMIN_TOKEN" | jq '.ok, (.users | length)'

echo
echo "== login as non-admin $TEST_USER =="
USER_LOGIN_PAYLOAD="$(curl_json "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$TEST_USER\",\"password\":\"$TEST_PASS\"}")"
echo "$USER_LOGIN_PAYLOAD" | jq

USER_TOKEN="$(echo "$USER_LOGIN_PAYLOAD" | jq -r '.token')"
if [[ -z "$USER_TOKEN" || "$USER_TOKEN" == "null" ]]; then
  echo "User login failed: token missing"
  exit 1
fi

echo
echo "== non-admin audit access should be forbidden (403) =="
HTTP_CODE_AUDIT="$(curl_status /tmp/poc5_user_audit.json "$BASE_URL/audit/recent" -H "Authorization: Bearer $USER_TOKEN")"
cat /tmp/poc5_user_audit.json | jq
if [[ "$HTTP_CODE_AUDIT" != "403" ]]; then
  echo "Expected 403 for non-admin audit, got $HTTP_CODE_AUDIT"
  exit 1
fi

echo
echo "== non-admin admin/users should be forbidden (403) =="
HTTP_CODE_USERS="$(curl_status /tmp/poc5_user_users.json "$BASE_URL/admin/users" -H "Authorization: Bearer $USER_TOKEN")"
cat /tmp/poc5_user_users.json | jq
if [[ "$HTTP_CODE_USERS" != "403" ]]; then
  echo "Expected 403 for non-admin /admin/users, got $HTTP_CODE_USERS"
  exit 1
fi

echo
echo "== non-admin complete request with auth =="
python3 - <<PY
import json
import ssl
import urllib.request
import uuid

base = "${BASE_URL}"
username = "$TEST_USER"
password = "$TEST_PASS"
insecure = "${INSECURE_TLS}" == "1"
context = None
if insecure:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

req_login = urllib.request.Request(
    f"{base}/auth/login",
    data=json.dumps({"username": username, "password": password}).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req_login, timeout=10, context=context) as resp:
    login_payload = json.loads(resp.read().decode("utf-8"))
token = login_payload["token"]

message = {
    "protocol_version": "0.1",
    "message_id": str(uuid.uuid4()),
    "intent": "chat",
    "payload": {"text": "One sentence about secure routing."},
    "extensions": {
        "llm": {"node": "general", "model": "ministral-3:8b", "max_tokens": 100}
    },
}

req_complete = urllib.request.Request(
    f"{base}/complete",
    data=json.dumps(message).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    },
    method="POST",
)
with urllib.request.urlopen(req_complete, timeout=120, context=context) as resp:
    payload = json.loads(resp.read().decode("utf-8"))

print(json.dumps({
    "intent": payload.get("intent"),
    "route_mode": payload.get("payload", {}).get("route_mode"),
    "node": payload.get("payload", {}).get("node"),
}, indent=2))
PY

echo
echo "== admin updates $TEST_USER (deactivate) =="
UPDATE_PAYLOAD="$(curl_json "$BASE_URL/admin/users/update" \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$TEST_USER\",\"active\":false}")"
echo "$UPDATE_PAYLOAD" | jq
if [[ "$(echo "$UPDATE_PAYLOAD" | jq -r '.ok')" != "true" ]]; then
  echo "Update user failed"
  exit 1
fi

echo
echo "== deactivated user login should fail =="
HTTP_CODE_DISABLED="$(curl_status /tmp/poc5_disabled_login.json "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"$TEST_USER\",\"password\":\"$TEST_PASS\"}")"
cat /tmp/poc5_disabled_login.json | jq
if [[ "$HTTP_CODE_DISABLED" != "401" ]]; then
  echo "Expected 401 for disabled user login, got $HTTP_CODE_DISABLED"
  exit 1
fi

echo
echo "PoC5 secure demo checks completed."
