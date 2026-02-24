#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_OUT_DEFAULT="$ROOT_DIR/data/caddy-root.crt"
CERT_NAME_DEFAULT="BrainDrive PoC5 Caddy Local Root"

CERT_OUT="$CERT_OUT_DEFAULT"
CERT_NAME="$CERT_NAME_DEFAULT"
EXPORT_ONLY=0
SKIP_SYSTEM=0
SKIP_NSS=0
NO_SUDO=0

usage() {
  cat <<'EOF'
Usage:
  ./scripts/setup_https_trust.sh [options]

Options:
  --export-only         Only export Caddy root cert to ./data/caddy-root.crt
  --skip-system         Skip system trust-store installation
  --skip-nss            Skip NSS trust-store installation (~/.pki/nssdb and Firefox profiles)
  --no-sudo             Do not use sudo for system trust installation
  --cert-out PATH       Output path for exported certificate
  --cert-name NAME      Certificate label used in NSS stores
  -h, --help            Show this help

Notes:
  - Docker compose stack must be available in this PoC5 directory.
  - This script starts caddy/router containers if they are not running.
  - On Linux, system trust uses update-ca-certificates (or trust anchor fallback).
  - On macOS, system trust uses the security tool.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --export-only) EXPORT_ONLY=1; shift ;;
    --skip-system) SKIP_SYSTEM=1; shift ;;
    --skip-nss) SKIP_NSS=1; shift ;;
    --no-sudo) NO_SUDO=1; shift ;;
    --cert-out)
      [[ $# -ge 2 ]] || { echo "Missing value for --cert-out" >&2; exit 2; }
      CERT_OUT="$2"
      shift 2
      ;;
    --cert-name)
      [[ $# -ge 2 ]] || { echo "Missing value for --cert-name" >&2; exit 2; }
      CERT_NAME="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! command -v docker >/dev/null 2>&1; then
  echo "docker command not found" >&2
  exit 1
fi

run_with_optional_sudo() {
  if [[ "$NO_SUDO" == "1" ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

export_caddy_root_cert() {
  mkdir -p "$(dirname "$CERT_OUT")"
  cd "$ROOT_DIR"

  echo "Ensuring PoC5 containers are running..."
  docker compose up -d bdp-secure-router caddy >/dev/null

  echo "Exporting Caddy root certificate..."
  docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$CERT_OUT"
  echo "Certificate exported: $CERT_OUT"
}

install_system_trust_linux() {
  if command -v update-ca-certificates >/dev/null 2>&1; then
    local target="/usr/local/share/ca-certificates/braindrive-poc5-caddy-root.crt"
    echo "Installing into system trust store via update-ca-certificates..."
    run_with_optional_sudo install -m 0644 "$CERT_OUT" "$target"
    run_with_optional_sudo update-ca-certificates
    echo "System trust installed (Linux CA store)."
    return 0
  fi

  if command -v trust >/dev/null 2>&1; then
    echo "Installing into system trust store via trust anchor..."
    run_with_optional_sudo trust anchor "$CERT_OUT"
    echo "System trust installed (p11-kit)."
    return 0
  fi

  echo "No Linux system trust tool found (update-ca-certificates or trust)." >&2
  return 1
}

install_system_trust_macos() {
  if ! command -v security >/dev/null 2>&1; then
    echo "security tool not found on macOS." >&2
    return 1
  fi

  if [[ "$NO_SUDO" == "1" ]]; then
    echo "Installing into login keychain (no sudo mode)..."
    security add-trusted-cert -d -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" "$CERT_OUT"
  else
    echo "Installing into macOS System keychain..."
    sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "$CERT_OUT"
  fi
  echo "System trust installed (macOS)."
}

init_nss_db() {
  local db_path="$1"
  mkdir -p "$db_path"
  if ! certutil -d "sql:$db_path" -L >/dev/null 2>&1; then
    certutil -d "sql:$db_path" -N --empty-password
  fi
}

install_nss_cert() {
  local db_path="$1"
  certutil -d "sql:$db_path" -D -n "$CERT_NAME" >/dev/null 2>&1 || true
  certutil -d "sql:$db_path" -A -n "$CERT_NAME" -t "C,," -i "$CERT_OUT"
}

install_nss_trust_linux() {
  if ! command -v certutil >/dev/null 2>&1; then
    echo "certutil not found; skipping NSS trust install." >&2
    return 1
  fi

  local installed_any=0
  local user_nss="$HOME/.pki/nssdb"
  echo "Installing certificate into user NSS DB: $user_nss"
  init_nss_db "$user_nss"
  install_nss_cert "$user_nss"
  installed_any=1

  local profile
  shopt -s nullglob
  for profile in "$HOME"/.mozilla/firefox/*.default* "$HOME"/.mozilla/firefox/*.default-release* "$HOME"/snap/firefox/common/.mozilla/firefox/*.default*; do
    if [[ -d "$profile" ]]; then
      echo "Installing certificate into Firefox profile DB: $profile"
      init_nss_db "$profile"
      install_nss_cert "$profile"
      installed_any=1
    fi
  done
  shopt -u nullglob

  if [[ "$installed_any" == "1" ]]; then
    echo "NSS trust installation complete."
    return 0
  fi

  return 1
}

main() {
  export_caddy_root_cert

  if [[ "$EXPORT_ONLY" == "1" ]]; then
    echo "Export-only mode complete."
    exit 0
  fi

  local os_name
  os_name="$(uname -s)"

  if [[ "$SKIP_SYSTEM" == "0" ]]; then
    case "$os_name" in
      Linux) install_system_trust_linux || true ;;
      Darwin) install_system_trust_macos || true ;;
      *)
        echo "System trust automation not implemented for OS: $os_name"
        ;;
    esac
  fi

  if [[ "$SKIP_NSS" == "0" && "$os_name" == "Linux" ]]; then
    install_nss_trust_linux || true
  fi

  echo
  echo "Done."
  echo "If a browser was open, restart it to pick up trust-store changes."
  local active_host="${BDP_HOST:-localhost}"
  local active_port="${CADDY_HTTPS_PORT:-8443}"
  echo "Active PoC5 URL: https://${active_host}:${active_port}/ui"
}

main "$@"
