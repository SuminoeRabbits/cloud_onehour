#!/bin/bash
# Rust Toolchain Setup Script for RHEL9
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/dnf_utils.sh"

RUST_VERSION="1.84.0"
SYSTEM_CA_BUNDLE="/etc/pki/tls/certs/ca-bundle.crt"
LOG_FILE="/tmp/setup_rust_$(date +%Y%m%d_%H%M%S).log"

# Install build dependencies
echo "Installing build dependencies for Rust..."
wait_for_dnf_lock
sudo dnf groupinstall -y "Development Tools"
# curl: skip if curl-minimal is present (Docker minimal images)
# libgit2-devel: in CRB, not needed for rustup itself; install if available
RUST_DEPS="pkgconf-pkg-config openssl-devel git wget ca-certificates"
if ! rpm -q curl-minimal >/dev/null 2>&1; then
    RUST_DEPS="$RUST_DEPS curl"
fi
sudo dnf install -y $RUST_DEPS
sudo dnf install -y libgit2-devel 2>/dev/null || echo "[INFO] libgit2-devel not available, skipping (not required for rustup)"

# Rest of the Rust installation is platform-independent (using rustup)
# To avoid duplication, we could theoretically source setup_rust.sh from original,
# but for total isolation, we'll create a RHEL-optimized version.
# Actually, the original script is quite portable. We'll simplify here.

if ! command -v rustup >/dev/null 2>&1; then
    echo "Installing rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain "${RUST_VERSION}"
fi

source "$HOME/.cargo/env"
rustup toolchain install "${RUST_VERSION}"
rustup default "${RUST_VERSION}"
rustc --version
