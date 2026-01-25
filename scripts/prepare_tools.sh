#!/bin/bash
set -euo pipefail

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/apt_utils.sh"

get_ubuntu_version() {
    if command -v lsb_release >/dev/null 2>&1; then
        lsb_release -rs
        return
    fi
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "${VERSION_ID:-unknown}"
        return
    fi
    echo "unknown"
}

install_python_with_ppa_if_needed() {
    local ubuntu_version
    ubuntu_version="$(get_ubuntu_version)"

    case "$ubuntu_version" in
        22.04*)
            echo "Ubuntu $ubuntu_version detected: using OS default Python (3.10)."
            ;;
        24.04*|25.*)
            echo "Ubuntu $ubuntu_version detected: installing Python 3.11 via deadsnakes PPA."
            sudo apt-get update -y
            sudo apt-get install -y software-properties-common
            sudo add-apt-repository -y ppa:deadsnakes/ppa
            sudo apt-get update -y
            sudo apt-get install -y python3.11 python3.11-venv python3.11-distutils
            ;;
        *)
            echo "Ubuntu $ubuntu_version detected: skipping Python PPA setup."
            ;;
    esac
}

# Wait for apt locks before any apt operations
# This is critical for cloud instances where unattended-upgrades runs at boot
echo "=== Waiting for apt locks to be released ==="
wait_for_apt_lock
echo ""

# Temporarily disable unattended upgrades to reduce apt lock contention
disable_unattended_upgrades

# Ensure Python versions per Ubuntu release
install_python_with_ppa_if_needed

# setup gcc14
./setup_gcc14.sh
./setup_binutil244.sh

# setup jdkxx, see the version in setup_jdkxx.sh.
./setup_jdkxx.sh

# build zlib
./build_zlib.sh

# build openssl
#./build_openssh.sh

# build pts
./setup_pts.sh

# build others
./setup_init.sh

# setup rust
./setup_rust.sh

# Re-enable unattended upgrades
enable_unattended_upgrades

