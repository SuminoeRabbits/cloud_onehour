#!/bin/bash
#
# setup_memcached.sh - memcached/memtier_benchmark System Dependency Setup (RHEL/Oracle Linux EL9+)
#
# Installs missing build dependencies for pts/memcached-1.2.0 idempotently.
# Already-installed packages are skipped.
#
# Required by:
#   memcached-1.6.19     : libevent-devel
#   memtier_benchmark-1.4.0 (autoreconf + ./configure):
#                          pcre-devel (EL9) / built by setup_pcre.sh (EL10+)
#                          openssl-devel, zlib-devel
#                          autoconf, automake, libtool
#
# Note: PCRE v1 (pcre-devel) is not available in EL10+ repos.
#   EL9 : installed via dnf below.
#   EL10+: setup_pcre.sh (called earlier in prepare_tools.sh) builds PCRE 8.45
#          from source; pcre-config will already be in PATH, so we skip here.
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/dnf_utils.sh"

log_mc() { echo "[setup_memcached] $*"; }

pkg_installed() {
    rpm -q "$1" >/dev/null 2>&1
}

EL_VER=$(get_el_version)
log_mc "=== memcached dependency setup (EL${EL_VER}) ==="

wait_for_dnf_lock

# --- PCRE handling ---
# EL9 : pcre-devel available via dnf
# EL10+: already handled by setup_pcre.sh (static build); skip dnf
MEMCACHED_PACKAGES=(
    libevent-devel
    openssl-devel
    zlib-devel
    autoconf
    automake
    libtool
)

if [[ "${EL_VER}" -lt 10 ]] 2>/dev/null; then
    MEMCACHED_PACKAGES+=(pcre-devel)
else
    if command -v pcre-config >/dev/null 2>&1; then
        log_mc "[OK] pcre-config already available (built by setup_pcre.sh): $(pcre-config --version)"
    else
        log_mc "[WARN] pcre-config not found on EL${EL_VER}. setup_pcre.sh should have installed it."
    fi
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
    sudo dnf install -y "${MISSING_PKGS[@]}"
    log_mc "Done."
fi

log_mc "=== memcached dependency setup complete ==="
