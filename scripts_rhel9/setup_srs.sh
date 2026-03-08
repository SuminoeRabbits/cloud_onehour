#!/bin/bash
#
# setup_srs.sh - srsRAN System Dependency Setup (RHEL/Oracle Linux EL9+)
#
# Installs missing srsRAN build dependencies idempotently.
# Already-installed packages are skipped.
#
# Dependencies:
#   build-utilities  : gcc gcc-c++ make
#   fftw3-devel      : fftw-devel
#   cmake            : cmake
#   boost-devel      : boost-devel
#   libconfig-devel  : libconfig-devel
#   mbedtls-devel    : mbedtls-devel
#   lksctp-tools     : lksctp-tools-devel
#   yaml-cpp-devel   : yaml-cpp-devel
#   gtest-devel      : gtest-devel
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/dnf_utils.sh"

log_srs() { echo "[setup_srs] $*"; }

# Check if an rpm package is already installed
pkg_installed() {
    rpm -q "$1" >/dev/null 2>&1
}

SRS_PACKAGES=(
    gcc
    gcc-c++
    make
    fftw-devel
    cmake
    boost-devel
    libconfig-devel
    mbedtls-devel
    lksctp-tools-devel
    yaml-cpp-devel
    gtest-devel
)

log_srs "=== srsRAN dependency setup (EL$(get_el_version)) ==="

wait_for_dnf_lock

MISSING_PKGS=()
for pkg in "${SRS_PACKAGES[@]}"; do
    if pkg_installed "$pkg"; then
        log_srs "[OK] already installed: $pkg"
    else
        log_srs "[MISS] will install: $pkg"
        MISSING_PKGS+=("$pkg")
    fi
done

if [[ ${#MISSING_PKGS[@]} -eq 0 ]]; then
    log_srs "All srsRAN dependencies are already installed. Nothing to do."
else
    log_srs "Installing missing packages: ${MISSING_PKGS[*]}"
    # Some packages (boost-devel, yaml-cpp-devel, gtest-devel) may reside in CRB/EPEL.
    # Those repos are enabled by setup_init.sh which runs before this script.
    sudo dnf install -y "${MISSING_PKGS[@]}"
    log_srs "Done."
fi

log_srs "=== srsRAN dependency setup complete ==="
