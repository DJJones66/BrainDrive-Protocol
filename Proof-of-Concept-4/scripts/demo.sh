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

POC4_HOST="${POC4_HOST:-localhost}"
POC4_PORT="${POC4_PORT:-8080}"
ROUTER_BASE_URL="${ROUTER_BASE_URL:-http://${POC4_HOST}:${POC4_PORT}}"
export ROUTER_BASE_URL

echo "== health =="
curl -s "${ROUTER_BASE_URL}/health" | jq

echo
echo "== ui =="
echo "Open in browser: ${ROUTER_BASE_URL}/ui"

echo
echo "== nodes =="
curl -s "${ROUTER_BASE_URL}/nodes" | jq

echo
echo "== models (first 10) =="
curl -s "${ROUTER_BASE_URL}/models" | jq '.models[:10]'

echo
echo "== stream: default general node (ministral-3:8b) =="
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" "Give a concise 3-bullet summary of BrainDrive protocol goals."

echo
echo "== stream: builder node via directive with qwen3:8b =="
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" "/node:builder /model:qwen3:8b Draft a minimal streaming architecture in 4 bullet points."

echo
echo "== complete: explicit llama3.1:8b override =="
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" --complete --model llama3.1:8b "One sentence: why streaming + async paths should coexist."

echo
echo "== complete: forced async fallback to PoC3 =="
python3 scripts/chat_cli.py --router-base "${ROUTER_BASE_URL}" --complete --force-async --node builder --model qwen3:8b "Create a durable async job and return queue metadata."
