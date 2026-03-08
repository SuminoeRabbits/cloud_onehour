#!/bin/bash
#
# setup_llama.sh - llama.cpp System Dependency Setup (RHEL/Oracle Linux EL9+)
#
# Installs missing llama.cpp build dependencies idempotently.
# Already-installed packages are skipped.
#
# Runtime shared libraries (installed automatically via rpm dependencies):
#   libllama.so / libggml.so  : built from source by PTS install.sh
#   libopenblas.so.0          : openblas-devel (BLAS backend)
#   libgfortran.so.5          : gcc-gfortran (pulls libgfortran, libquadmath)
#   libgomp.so.1              : libgomp (bundled with gcc)
#
# Build dependencies:
#   build-utilities  : gcc gcc-c++ make
#   cmake            : cmake
#   curl             : curl (or curl-minimal)
#   blas-devel       : openblas-devel
#   gfortran         : gcc-gfortran  (pulls libgfortran, libquadmath)
#   pkg-config       : pkgconf-pkg-config
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/dnf_utils.sh"

log_llama() { echo "[setup_llama] $*"; }

# Check if an rpm package is already installed
pkg_installed() {
    rpm -q "$1" >/dev/null 2>&1
}

LLAMA_PACKAGES=(
    gcc
    gcc-c++
    make
    cmake
    gcc-gfortran
    pkgconf-pkg-config
    openblas-devel
)

# curl-minimal and curl conflict; install curl only when curl-minimal is absent
CURL_PKG=""
if ! rpm -q curl-minimal >/dev/null 2>&1 && ! rpm -q curl >/dev/null 2>&1; then
    CURL_PKG="curl"
fi

log_llama "=== llama.cpp dependency setup (EL$(get_el_version)) ==="

wait_for_dnf_lock

MISSING_PKGS=()
for pkg in "${LLAMA_PACKAGES[@]}"; do
    if pkg_installed "$pkg"; then
        log_llama "[OK] already installed: $pkg"
    else
        log_llama "[MISS] will install: $pkg"
        MISSING_PKGS+=("$pkg")
    fi
done

# Handle curl separately (conflict avoidance)
if [[ -n "$CURL_PKG" ]]; then
    MISSING_PKGS+=("$CURL_PKG")
    log_llama "[MISS] will install: $CURL_PKG"
fi

if [[ ${#MISSING_PKGS[@]} -eq 0 ]]; then
    log_llama "All llama.cpp dependencies are already installed. Nothing to do."
else
    log_llama "Installing missing packages: ${MISSING_PKGS[*]}"
    # openblas-devel is in EPEL/CRB, enabled by setup_init.sh
    sudo dnf install -y "${MISSING_PKGS[@]}"
    log_llama "Done."
fi

log_llama "=== llama.cpp dependency setup complete ==="
