#!/bin/bash
#
# setup_llama.sh - llama.cpp System Dependency Setup (Ubuntu/Debian)
#
# Installs missing llama.cpp build dependencies idempotently.
# Already-installed packages are skipped.
#
# Runtime shared libraries (installed automatically via deb dependencies):
#   libllama.so / libggml.so  : built from source by PTS install.sh
#   libopenblas.so.0          : libopenblas-dev (BLAS backend)
#   libgfortran.so.5          : libgfortran5  (OpenBLAS dependency)
#   libquadmath.so.0          : libquadmath0  (OpenBLAS dependency)
#   libgomp.so.1              : libgomp1      (OpenMP, bundled with gcc)
#
# Build dependencies:
#   build-utilities  : build-essential
#   cmake            : cmake
#   curl             : curl
#   blas-dev         : libopenblas-dev
#   gfortran         : gfortran  (pulls libgfortran5, libquadmath0)
#   pkg-config       : pkg-config
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APT_UTILS="${SCRIPT_DIR}/lib/apt_utils.sh"
if [[ -f "${APT_UTILS}" ]]; then
    # shellcheck disable=SC1090
    source "${APT_UTILS}"
fi

log_llama() { echo "[setup_llama] $*"; }

# Check if a dpkg package is already installed
pkg_installed() {
    dpkg -s "$1" >/dev/null 2>&1
}

LLAMA_PACKAGES=(
    build-essential
    cmake
    curl
    libopenblas-dev
    gfortran
    pkg-config
)

log_llama "=== llama.cpp dependency setup (Ubuntu) ==="

# Wait for apt lock if helper is available
if declare -F wait_for_apt_lock >/dev/null 2>&1; then
    wait_for_apt_lock
fi

MISSING_PKGS=()
for pkg in "${LLAMA_PACKAGES[@]}"; do
    if pkg_installed "$pkg"; then
        log_llama "[OK] already installed: $pkg"
    else
        log_llama "[MISS] will install: $pkg"
        MISSING_PKGS+=("$pkg")
    fi
done

if [[ ${#MISSING_PKGS[@]} -eq 0 ]]; then
    log_llama "All llama.cpp dependencies are already installed. Nothing to do."
else
    log_llama "Installing missing packages: ${MISSING_PKGS[*]}"
    sudo DEBIAN_FRONTEND=noninteractive apt-get update -y -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${MISSING_PKGS[@]}"
    log_llama "Done."
fi

log_llama "=== llama.cpp dependency setup complete ==="
