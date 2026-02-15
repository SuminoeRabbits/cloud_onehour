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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/dnf_utils.sh"

EL_VER=$(get_el_version)

log_step "Wait for dnf locks"
wait_for_dnf_lock
disable_dnf_automatic

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
echo "=== prepare_tools.sh (EL${EL_VER}) end: $(date -Is) ==="
