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

POC2_HOST="${POC2_HOST:-localhost}"
POC2_PORT="${POC2_PORT:-8080}"
ROUTER_URL="${ROUTER_URL:-http://${POC2_HOST}:${POC2_PORT}/route}"
export ROUTER_URL

python3 scripts/demo.py

echo
echo "== node unavailable scenario =="
docker compose stop node-echo >/dev/null
trap 'docker compose start node-echo >/dev/null || true' EXIT

python3 - <<'PY'
import json
import os
import urllib.request
import uuid

message = {
    "protocol_version": "0.1",
    "message_id": str(uuid.uuid4()),
    "intent": "echo",
    "payload": {"text": "node down test"},
    "extensions": {
        "identity": {
            "actor_id": "user.demo",
            "actor_type": "human",
            "roles": ["admin"],
        }
    },
}

req = urllib.request.Request(
    os.environ.get("ROUTER_URL", "http://localhost:8080/route"),
    data=json.dumps(message).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

with urllib.request.urlopen(req, timeout=5) as resp:
    payload = json.loads(resp.read().decode("utf-8"))

print("response:")
print(json.dumps(payload, indent=2))

code = payload.get("payload", {}).get("error", {}).get("code")
if code != "E_NODE_UNAVAILABLE":
    raise SystemExit(f"Expected E_NODE_UNAVAILABLE, got {code}")
PY

docker compose start node-echo >/dev/null
trap - EXIT

echo
echo "All PoC2 demo scenarios passed."
