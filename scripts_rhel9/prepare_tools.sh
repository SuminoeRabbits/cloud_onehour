#!/bin/bash
set -euo pipefail

# Fail-safe for Ubuntu/Debian systems
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [[ "$ID" == "ubuntu" || "$ID" == "debian" ]]; then
        echo "================================================================================"
        echo "[Fail-safe] Ubuntu/Debian system detected: $NAME $VERSION_ID"
        echo "Redirecting back to scripts/prepare_tools.sh..."
        echo "================================================================================"
        SCRIPT_DIR_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
        UBUNTU_SCRIPT="$(dirname "$SCRIPT_DIR_EARLY")/scripts/prepare_tools.sh"
        if [ -f "$UBUNTU_SCRIPT" ]; then
            exec "$UBUNTU_SCRIPT" "$@"
        else
            echo "[ERROR] Ubuntu-compatible script not found at $UBUNTU_SCRIPT"
            exit 1
        fi
    fi
fi

SCRIPT_DIR_EARLY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${SCRIPT_DIR_EARLY}/results"
LOG_FILE="${LOG_DIR}/prepare_tools.log"
mkdir -p "$(dirname "$LOG_FILE")"
echo "=== prepare_tools.sh (EL) start: $(date -Is) ===" >> "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

log_step() {
    echo "=== [STEP] $1 @ $(date -Is) ==="
}

DNF_AUTOMATIC_DISABLED=0

cleanup_on_exit() {
    local exit_code=$?
    if [ "$DNF_AUTOMATIC_DISABLED" -eq 1 ]; then
        enable_dnf_automatic || true
    fi
    if [ "$exit_code" -ne 0 ]; then
        echo "[ERROR] prepare_tools.sh aborted with exit code ${exit_code}. See log: ${LOG_FILE}"
    fi
}

trap cleanup_on_exit EXIT

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/dnf_utils.sh"

EL_VER=$(get_el_version)
OS_ID=$(get_os_id)

log_step "Preflight checks"

if ! command -v dnf >/dev/null 2>&1; then
    echo "[ERROR] dnf command is not available. This script supports EL systems only."
    exit 1
fi

if ! sudo -n true >/dev/null 2>&1; then
    echo "[ERROR] sudo -n failed. Configure passwordless sudo for this automation user."
    exit 1
fi

if [ "$EL_VER" -lt 9 ] 2>/dev/null; then
    echo "[ERROR] Unsupported EL version: ${EL_VER}. Expected EL9+ for scripts_rhel9."
    exit 1
fi

for required_script in setup_init.sh setup_gcc14.sh setup_binutil244.sh setup_jdkxx.sh build_zlib.sh setup_pts.sh setup_rust.sh; do
    if [ ! -f "$SCRIPT_DIR/$required_script" ]; then
        echo "[ERROR] Missing required script: $SCRIPT_DIR/$required_script"
        exit 1
    fi
done

echo "=== EL${EL_VER} prepare_tools preflight OK (detected OS: ${OS_ID}) ==="

log_step "Wait for dnf locks"
wait_for_dnf_lock
disable_dnf_automatic
DNF_AUTOMATIC_DISABLED=1

log_step "Ensure Python"
if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
    # EL10 ships Python 3.12; install python3.11 only if explicitly needed
    echo "[INFO] EL${EL_VER}: System Python $(python3 --version 2>/dev/null || echo 'not found')"
    if ! command -v python3.11 >/dev/null 2>&1; then
        sudo dnf install -y python3.11 python3.11-pip python3.11-devel 2>/dev/null || \
            echo "[INFO] python3.11 not available on EL${EL_VER}, using system Python 3.12"
    fi
else
    # EL9 has python3.11 in AppStream
    if ! command -v python3.11 >/dev/null 2>&1; then
        sudo dnf install -y python3.11 python3.11-pip python3.11-devel
    fi
fi

# setup_init.sh enables EPEL and CRB repos, which are required by
# subsequent scripts (e.g. setup_gcc14.sh needs aria2 from EPEL).
# Must run BEFORE setup_gcc14.sh.
log_step "Setup init tools (EPEL/CRB repos)"
"$SCRIPT_DIR/setup_init.sh"

log_step "Validate dnf repositories after setup_init"
if ! sudo dnf repolist --enabled >/dev/null 2>&1; then
    echo "[ERROR] No usable enabled repositories after setup_init."
    echo "[INFO] On RHEL/OL cloud images, verify RHUI/subscription repositories are reachable."
    exit 1
fi

log_step "Setup GCC 14"
"$SCRIPT_DIR/setup_gcc14.sh"
"$SCRIPT_DIR/setup_binutil244.sh"

log_step "Setup JDK"
"$SCRIPT_DIR/setup_jdkxx.sh"

log_step "Build zlib"
"$SCRIPT_DIR/build_zlib.sh"

log_step "Setup PTS"
"$SCRIPT_DIR/setup_pts.sh"

log_step "Setup Rust"
"$SCRIPT_DIR/setup_rust.sh"

log_step "Final verification"
if ! rpm -q libxml2-devel >/dev/null 2>&1; then
    sudo dnf install -y libxml2-devel
fi
if ! command -v pkg-config >/dev/null 2>&1; then
    sudo dnf install -y pkgconf-pkg-config
fi

enable_dnf_automatic
DNF_AUTOMATIC_DISABLED=0

log_step "Disk usage before workloads"
df -h /

echo "=== prepare_tools.sh (EL${EL_VER}) end: $(date -Is) ==="
