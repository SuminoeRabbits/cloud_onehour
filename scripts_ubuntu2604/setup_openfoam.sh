#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/os_guard.sh"
#
# setup_openfoam.sh - OpenFOAM System Dependency Setup (Ubuntu/Debian)
#
# Installs OpenFOAM-specific missing dependencies idempotently.
# This script is intentionally narrow: dependencies already handled by other
# project setup scripts are NOT duplicated here.
#
# Already covered elsewhere:
#   - openmpi-development : setup_fpu.sh
#   - boost-development   : setup_srs.sh (via libboost-all-dev)
#   - zlib-development    : setup_pts.sh / Ubuntu 26.04 repositories
#   - fftw3-development   : setup_fpu.sh
#   - flex / bison        : setup_init.sh / setup_pts.sh
#   - ncurses-development : setup_pts.sh
#
# OpenFOAM-specific gap handled here:
#   - scotch/scotch.h
#   - scotch/ptscotch.h
#     On Ubuntu/Debian these headers are typically provided by:
#       * libscotch-dev
#       * libptscotch-dev
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APT_UTILS="${SCRIPT_DIR}/lib/apt_utils.sh"
if [[ -f "${APT_UTILS}" ]]; then
    # shellcheck disable=SC1090
    source "${APT_UTILS}"
fi

log_openfoam() { echo "[setup_openfoam] $*"; }

pkg_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

apt_get_install() {
    if declare -F wait_for_apt_lock >/dev/null 2>&1; then
        wait_for_apt_lock
    fi
    sudo DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Lock::Timeout=300 install -y "$@"
}

OPENFOAM_PACKAGES=(
    libscotch-dev
    libptscotch-dev
)

log_openfoam "=== OpenFOAM dependency setup (Ubuntu/Debian) ==="
log_openfoam "[INFO] OpenMPI / Boost / FFTW / flex / bison / ncurses / zlib are expected from existing setup scripts"

MISSING_PKGS=()
for pkg in "${OPENFOAM_PACKAGES[@]}"; do
    if pkg_installed "$pkg"; then
        log_openfoam "[OK] already installed: $pkg"
    else
        log_openfoam "[MISS] will install: $pkg"
        MISSING_PKGS+=("$pkg")
    fi
done

if [[ ${#MISSING_PKGS[@]} -eq 0 ]]; then
    log_openfoam "All OpenFOAM-specific packages are already installed. Nothing to do."
else
    log_openfoam "Installing missing packages: ${MISSING_PKGS[*]}"
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y -qq
    apt_get_install "${MISSING_PKGS[@]}"
    log_openfoam "Done."
fi

log_openfoam "=== Post-install validation ==="

SCOTCH_HEADERS=(
    /usr/include/scotch/scotch.h
    /usr/include/scotch/ptscotch.h
)

for hdr in "${SCOTCH_HEADERS[@]}"; do
    if [[ -f "$hdr" ]]; then
        log_openfoam "[OK] Found header: $hdr"
    else
        log_openfoam "[WARN] Missing header: $hdr"
    fi
done

if command -v mpirun >/dev/null 2>&1; then
    log_openfoam "[OK] mpirun available (provided by existing scripts): $(mpirun --version 2>/dev/null | head -1)"
else
    log_openfoam "[WARN] mpirun not found in PATH"
fi

if command -v mpicc >/dev/null 2>&1; then
    log_openfoam "[OK] mpicc available"
else
    log_openfoam "[WARN] mpicc not found in PATH"
fi

log_openfoam "=== OpenFOAM dependency setup complete ==="
