#!/bin/bash
#
# setup_srs.sh - srsRAN System Dependency Setup (Ubuntu/Debian)
#
# Installs missing srsRAN build dependencies idempotently.
# Already-installed packages are skipped.
#
# Dependencies:
#   build-utilities  : build-essential
#   fftw3-dev        : libfftw3-dev
#   cmake            : cmake
#   boost-dev        : libboost-all-dev
#   libconfig++      : libconfig++-dev
#   mbedtls          : libmbedtls-dev
#   libsctp          : libsctp-dev
#   yaml-cpp         : libyaml-cpp-dev
#   libgtest         : libgtest-dev
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APT_UTILS="${SCRIPT_DIR}/lib/apt_utils.sh"
if [[ -f "${APT_UTILS}" ]]; then
    # shellcheck disable=SC1090
    source "${APT_UTILS}"
fi

log_srs() { echo "[setup_srs] $*"; }

# Check if a dpkg package is already installed
pkg_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

# Collect only missing packages from the given list
collect_missing() {
    local missing=()
    for pkg in "$@"; do
        if pkg_installed "$pkg"; then
            log_srs "[OK] already installed: $pkg"
        else
            log_srs "[MISS] will install: $pkg"
            missing+=("$pkg")
        fi
    done
    echo "${missing[@]+"${missing[@]}"}"
}

SRS_PACKAGES=(
    build-essential
    libfftw3-dev
    cmake
    libboost-all-dev
    libconfig++-dev
    libmbedtls-dev
    libsctp-dev
    libyaml-cpp-dev
    libgtest-dev
)

log_srs "=== srsRAN dependency setup (Ubuntu) ==="

# Wait for apt lock if helper is available
if declare -F wait_for_apt_lock >/dev/null 2>&1; then
    wait_for_apt_lock
fi

MISSING_PKGS=()
while IFS= read -r -d '' pkg; do
    MISSING_PKGS+=("$pkg")
done < <(
    for pkg in "${SRS_PACKAGES[@]}"; do
        if ! pkg_installed "$pkg"; then
            printf '%s\0' "$pkg"
        else
            log_srs "[OK] already installed: $pkg"
        fi
    done
)

if [[ ${#MISSING_PKGS[@]} -eq 0 ]]; then
    log_srs "All srsRAN dependencies are already installed. Nothing to do."
else
    log_srs "Installing missing packages: ${MISSING_PKGS[*]}"
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING_PKGS[@]}"
    log_srs "Done."
fi

log_srs "=== srsRAN dependency setup complete ==="
