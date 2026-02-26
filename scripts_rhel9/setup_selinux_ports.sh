#!/bin/bash
# setup_selinux_ports.sh - Configure SELinux port exceptions for benchmark tools
#
# Background:
#   On RHEL/Oracle Linux with SELinux enforcing, processes running under httpd_t
#   (nginx, httpd) can only bind to ports listed in the http_port_t type.
#   Benchmark tools use non-standard ports not in that list by default, causing
#   nginx to fail with "bind() failed (13: Permission denied)" at startup.
#
# Ports configured here:
#   8089/tcp  pts/nginx-3.0.1 benchmark (nginx listens; wrk client connects)
#             Not in default http_port_t; nginx fails to start without this entry.
#
# Idempotency:
#   semanage port -a  adds a new entry; fails if already exists.
#   semanage port -m  modifies an existing entry; used as fallback.
#   Both commands are safe to re-run.
#
# Scope:
#   Runs only on EL systems (Ubuntu/Debian redirected by prepare_tools.sh fail-safe).
#   Also skips silently if SELinux is Disabled or if getenforce is not present.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/dnf_utils.sh"

# ---- Skip if SELinux is not active ----
if ! command -v getenforce >/dev/null 2>&1; then
    echo "[INFO] setup_selinux_ports: getenforce not found, SELinux not present. Skipping."
    exit 0
fi

SELINUX_MODE=$(getenforce 2>/dev/null || echo "Disabled")
echo "[INFO] SELinux mode: ${SELINUX_MODE}"

if [ "${SELINUX_MODE}" = "Disabled" ]; then
    echo "[INFO] SELinux is Disabled. Skipping port configuration."
    exit 0
fi

# ---- Ensure semanage is available ----
if ! command -v semanage >/dev/null 2>&1; then
    echo "[INFO] semanage not found. Installing policycoreutils-python-utils..."
    sudo dnf -y install policycoreutils-python-utils
fi

# ---- Helper: add or modify a port type entry ----
# Usage: selinux_allow_port <port> <type> <protocol>
selinux_allow_port() {
    local port="$1"
    local type="$2"
    local proto="${3:-tcp}"

    if sudo semanage port -a -t "${type}" -p "${proto}" "${port}" 2>/dev/null; then
        echo "[OK]   ${proto}/${port} -> ${type} (added)"
    elif sudo semanage port -m -t "${type}" -p "${proto}" "${port}" 2>/dev/null; then
        echo "[OK]   ${proto}/${port} -> ${type} (modified)"
    else
        echo "[WARN] Could not configure ${proto}/${port} for ${type}"
        echo "[WARN] nginx benchmark may fail with 'Connection refused'"
        echo "[WARN] To fix manually: sudo semanage port -a -t ${type} -p ${proto} ${port}"
    fi
}

echo "[INFO] Configuring SELinux port exceptions for benchmark tools..."

# pts/nginx-3.0.1: nginx listens on 8089 for wrk load testing
selinux_allow_port 8089 http_port_t tcp

echo "[OK] SELinux port configuration complete."
