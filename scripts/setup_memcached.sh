#!/bin/bash
#
# setup_memcached.sh - memcached/memtier_benchmark System Dependency Setup (Ubuntu/Debian)
#
# Installs missing build dependencies for pts/memcached-1.2.0 idempotently.
# Already-installed packages are skipped.
#
# Required by:
#   memcached-1.6.19     : libevent-dev
#   memtier_benchmark-1.4.0 (autoreconf + ./configure):
#                          libpcre3-dev, libssl-dev, zlib1g-dev
#                          autoconf, automake, libtool
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APT_UTILS="${SCRIPT_DIR}/lib/apt_utils.sh"
if [[ -f "${APT_UTILS}" ]]; then
    # shellcheck disable=SC1090
    source "${APT_UTILS}"
fi

log_mc() { echo "[setup_memcached] $*"; }

pkg_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

MEMCACHED_PACKAGES=(
    libevent-dev
    libpcre3-dev
    libssl-dev
    zlib1g-dev
    autoconf
    automake
    libtool
)

log_mc "=== memcached dependency setup (Ubuntu) ==="

if declare -F wait_for_apt_lock >/dev/null 2>&1; then
    wait_for_apt_lock
fi

MISSING_PKGS=()
for pkg in "${MEMCACHED_PACKAGES[@]}"; do
    if pkg_installed "$pkg"; then
        log_mc "[OK] already installed: $pkg"
    else
        log_mc "[MISS] will install: $pkg"
        MISSING_PKGS+=("$pkg")
    fi
done

if [[ ${#MISSING_PKGS[@]} -eq 0 ]]; then
    log_mc "All memcached dependencies are already installed. Nothing to do."
else
    log_mc "Installing missing packages: ${MISSING_PKGS[*]}"
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING_PKGS[@]}"
    log_mc "Done."
fi

log_mc "=== memcached dependency setup complete ==="
