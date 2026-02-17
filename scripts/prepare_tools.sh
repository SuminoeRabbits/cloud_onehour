#!/bin/bash
set -euo pipefail

# Fail-safe for RHEL-based systems (RedHat, Rocky, Oracle, Fedora, Alma)
if [ -f /etc/os-release ]; then
    . /etc/os-release
    # ID can be "rocky", "rhel", "fedora", "ol" (Oracle Linux), "almalinux", etc.
    if [[ "$ID" =~ ^(fedora|rhel|rocky|ol|almalinux)$ ]]; then
        echo "================================================================================"
        echo "[Fail-safe] RHEL-based system detected: $NAME $VERSION_ID"
        echo "Redirecting to scripts_rhel9/prepare_tools.sh..."
        echo "================================================================================"
        SCRIPT_DIR_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        RHEL9_SCRIPT="$(dirname "$SCRIPT_DIR_EARLY")/scripts_rhel9/prepare_tools.sh"
        if [ -f "$RHEL9_SCRIPT" ]; then
            exec "$RHEL9_SCRIPT" "$@"
        else
            echo "[ERROR] RHEL9-compatible script not found at $RHEL9_SCRIPT"
            exit 1
        fi
    fi
fi

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
                sudo apt-get install -y python3.11 python3.11-venv python3.11-distutils
                # On noble, python3.11-pip may not exist; bootstrap pip via ensurepip
                if command -v python3.11 >/dev/null 2>&1; then
                    python3.11 -m ensurepip --upgrade || true
                    python3.11 -m pip install --upgrade pip || true
                fi
            else
                echo "Ubuntu $ubuntu_version ($ubuntu_codename) detected: skipping Python PPA setup."
                echo "  [INFO] deadsnakes PPA is not available for this release."
            fi
            ;;
    esac
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

# Ensure Python versions per Ubuntu release
log_step "Ensure Python versions"
install_python_with_ppa_if_needed

# setup gcc14
log_step "Setup GCC 14"
./setup_gcc14.sh
./setup_binutil244.sh

# setup jdkxx, see the version in setup_jdkxx.sh.
log_step "Setup JDK"
./setup_jdkxx.sh

# build zlib
log_step "Build zlib"
./build_zlib.sh

# build openssl
#./build_openssh.sh

# build pts
log_step "Setup PTS"
./setup_pts.sh

# build others
log_step "Setup init tools"
./setup_init.sh

# setup rust
log_step "Setup Rust"
./setup_rust.sh

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

echo "=== prepare_tools.sh end: $(date -Is) ==="
