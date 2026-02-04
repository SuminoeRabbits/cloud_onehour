#!/bin/bash
set -euo pipefail

# Always log output for postmortem (collected in /tmp/reports.tar.gz)
LOG_FILE="/tmp/prepare_tools.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "=== prepare_tools.sh start: $(date -Is) ===" >> "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

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

get_ubuntu_codename() {
    if command -v lsb_release >/dev/null 2>&1; then
        lsb_release -sc
        return
    fi
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "${VERSION_CODENAME:-unknown}"
        return
    fi
    echo "unknown"
}

install_python_with_ppa_if_needed() {
    local ubuntu_version
    local ubuntu_codename
    ubuntu_version="$(get_ubuntu_version)"
    ubuntu_codename="$(get_ubuntu_codename)"

    case "$ubuntu_version" in
        22.04*)
            echo "Ubuntu $ubuntu_version detected: using OS default Python (3.10)."
            ;;
        *)
            if [ "$ubuntu_codename" = "noble" ] || [ "$ubuntu_codename" = "plucky" ]; then
                echo "Ubuntu $ubuntu_version ($ubuntu_codename) detected: installing Python 3.11 via deadsnakes PPA."
                sudo apt-get update -y
                sudo apt-get install -y software-properties-common
                sudo add-apt-repository -y ppa:deadsnakes/ppa
                sudo apt-get update -y
                sudo apt-get install -y python3.11 python3.11-venv python3.11-pip python3.11-distutils
            else
                echo "Ubuntu $ubuntu_version ($ubuntu_codename) detected: skipping Python PPA setup."
                echo "  [INFO] deadsnakes PPA is not available for this release."
            fi
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

# Verify libxml2-dev is installed (for pmbench xmlgen build)
if ! dpkg -s libxml2-dev >/dev/null 2>&1; then
    echo "[WARN] libxml2-dev not installed. Installing..."
    sudo apt-get update -y
    sudo apt-get install -y libxml2-dev
else
    echo "[OK] libxml2-dev is installed"
fi

# Ensure pkg-config is available for libxml2 flags
if ! command -v pkg-config >/dev/null 2>&1; then
    echo "[WARN] pkg-config not installed. Installing pkgconf..."
    sudo apt-get update -y
    sudo apt-get install -y pkgconf
else
    echo "[OK] pkg-config is available"
fi

# Re-enable unattended upgrades
enable_unattended_upgrades

echo "=== prepare_tools.sh end: $(date -Is) ==="

# Copy log into cloud reports directory for collection
RESULTS_LOG_DIR="$HOME/cloud_onehour/results"
mkdir -p "$RESULTS_LOG_DIR"
cp -f "$LOG_FILE" "$RESULTS_LOG_DIR/prepare_tools.log"
