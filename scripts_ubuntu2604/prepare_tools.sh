#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/os_guard.sh"
set -euo pipefail

# Always log output for postmortem (collected in /tmp/reports.tar.gz)
LOG_FILE="$HOME/cloud_onehour/results/prepare_tools.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "=== prepare_tools.sh start: $(date -Is) ===" >> "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

log_step() {
    echo "=== [STEP] $1 @ $(date -Is) ==="
}

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/apt_utils.sh"

# Force non-interactive apt behavior for automation (e.g., SSH BatchMode)
export DEBIAN_FRONTEND=noninteractive
export TZ=Etc/UTC
export NEEDRESTART_MODE=a

ensure_noninteractive_timezone() {
    wait_for_apt_lock
    sudo ln -fs /usr/share/zoneinfo/Etc/UTC /etc/localtime
    sudo DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Lock::Timeout=300 -o Acquire::Retries=3 update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get -o Dpkg::Lock::Timeout=300 -o Acquire::Retries=3 install -y tzdata
    sudo DEBIAN_FRONTEND=noninteractive dpkg-reconfigure -f noninteractive tzdata >/dev/null 2>&1 || true
}

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

ensure_system_python() {
    local ubuntu_version
    local ubuntu_codename
    ubuntu_version="$(get_ubuntu_version)"
    ubuntu_codename="$(get_ubuntu_codename)"

    echo "Ubuntu $ubuntu_version ($ubuntu_codename) detected: using OS default Python."
    sudo apt-get update -y
    sudo apt-get install -y python3 python3-pip python3-venv
    python3 --version
}

# Wait for apt locks before any apt operations
# This is critical for cloud instances where unattended-upgrades runs at boot
log_step "Wait for apt locks"
echo "=== Waiting for apt locks to be released ==="
wait_for_apt_lock
echo ""

# Temporarily disable unattended upgrades to reduce apt lock contention
log_step "Disable unattended upgrades"
disable_unattended_upgrades

# Pre-configure timezone to prevent tzdata interactive prompt
log_step "Ensure non-interactive timezone"
ensure_noninteractive_timezone

# Ensure Python from Ubuntu 26.04 repositories
log_step "Ensure system Python"
ensure_system_python

# setup jdkxx, see the version in setup_jdkxx.sh.
log_step "Setup JDK"
./setup_jdkxx.sh

# build openssl
#./build_openssh.sh

# build pts
log_step "Setup PTS"
./setup_pts.sh

log_step "Setup FPU benchmark dependencies"
./setup_fpu.sh

# build others
log_step "Setup init tools"
./setup_init.sh

# setup rust
log_step "Setup Rust"
./setup_rust.sh

# setup srsRAN dependencies
log_step "Setup srsRAN dependencies"
./setup_srs.sh

# setup memcached/memtier_benchmark dependencies
log_step "Setup memcached dependencies"
./setup_memcached.sh

# setup llama.cpp dependencies
log_step "Setup llama.cpp dependencies"
./setup_llama.sh

# Verify libxml2-dev is installed (for pmbench xmlgen build)
log_step "Ensure libxml2-dev"
if ! dpkg -s libxml2-dev >/dev/null 2>&1; then
    echo "[WARN] libxml2-dev not installed. Installing..."
    sudo apt-get update -y
    sudo apt-get install -y libxml2-dev
else
    echo "[OK] libxml2-dev is installed"
fi

# Ensure pkg-config is available for libxml2 flags
log_step "Ensure pkg-config"
if ! command -v pkg-config >/dev/null 2>&1; then
    echo "[WARN] pkg-config not installed. Installing pkgconf..."
    sudo apt-get update -y
    sudo apt-get install -y pkgconf
else
    echo "[OK] pkg-config is available"
fi

# Re-enable unattended upgrades
log_step "Enable unattended upgrades"
enable_unattended_upgrades

log_step "Disk usage before workloads"
df -h /

echo "=== prepare_tools.sh end: $(date -Is) ==="
