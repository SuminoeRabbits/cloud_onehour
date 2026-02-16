#!/bin/bash

# Stop on error
set -e

# Source dnf utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/dnf_utils.sh"

wait_for_dnf_lock

# Detect OS family
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    else
        echo "unknown"
    fi
}

OS_ID=$(detect_os)
EL_VER=$(get_el_version)
echo "=== EL${EL_VER} Initialization (detected OS: $OS_ID) ==="

USE_REMI_PHP81=1
if [[ ("$OS_ID" == "ol" || "$OS_ID" == "oracle") && "$EL_VER" -ge 10 ]]; then
    USE_REMI_PHP81=0
fi

# 1. Enable EPEL and CRB (CodeReady Builder)
echo "Enabling EPEL and CRB repositories..."
case "$OS_ID" in
    ol|oracle)
        sudo dnf install -y "oracle-epel-release-el${EL_VER}"
        if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
            sudo dnf config-manager --set-enabled "ol${EL_VER}_codeready_builder" 2>/dev/null || \
                sudo dnf config-manager --set-enabled crb 2>/dev/null || true
        else
            sudo dnf config-manager --set-enabled ol9_codeready_builder
        fi
        ;;
    rocky|almalinux|rhel|centos)
        sudo dnf install -y epel-release
        sudo dnf config-manager --set-enabled crb
        ;;
    *)
        echo "[WARN] Unknown OS ID: $OS_ID, attempting EL defaults..."
        sudo dnf install -y epel-release
        sudo dnf config-manager --set-enabled crb
        ;;
esac

# 2. Enable Remi repository for PHP 8.1 (skip on Oracle Linux 10+)
if [ "$USE_REMI_PHP81" -eq 1 ]; then
    echo "Enabling Remi repository for PHP 8.1..."
    if ! rpm -q remi-release >/dev/null 2>&1; then
        sudo dnf install -y "https://rpms.remirepo.net/enterprise/remi-release-${EL_VER}.rpm"
    fi
    if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
        # EL10: dnf module system may differ; try module first, fall back to direct install
        sudo dnf module reset php -y 2>/dev/null || true
        sudo dnf module enable php:remi-8.1 -y 2>/dev/null || \
            echo "[INFO] EL${EL_VER}: dnf module not available for PHP, using default Remi config"
    else
        sudo dnf module reset php -y
        sudo dnf module enable php:remi-8.1 -y
    fi
else
    echo "[INFO] Oracle Linux ${EL_VER}: skipping Remi PHP 8.1 setup (remi-release dependency mismatch on EL10)."
fi

# 3. Install core tools and libraries
echo "Installing core tools and libraries..."

# perf package: on Oracle Linux with UEK, perf may not be available
# as a standalone package. Try installing, but don't fail if unavailable.
PERF_PKG="perf"
if ! sudo dnf install -y "$PERF_PKG" 2>/dev/null; then
    echo "[WARN] '$PERF_PKG' package not available. Trying kernel-uek-tools..."
    sudo dnf install -y kernel-uek-tools 2>/dev/null || \
        echo "[WARN] perf not available on this system (benchmark will run without perf)"
fi

# Minimal Docker images ship curl-minimal which conflicts with full curl.
# Install full curl only if curl-minimal is not present.
if ! rpm -q curl-minimal >/dev/null 2>&1; then
    sudo dnf -y install curl
fi

sudo dnf -y install \
    bc \
    libuuid-devel \
    libxml2-devel \
    pkgconf-pkg-config \
    libcurl-devel \
    jansson-devel \
    sysstat \
    htop \
    aria2 \
    flex \
    bison \
    openssl-devel \
    elfutils-libelf-devel \
    libevent-devel \
    python3-tabulate \
    expat-devel \
    pcre2-devel \
    p7zip \
    p7zip-plugins \
    glibc-devel \
    numactl \
    which \
    wget \
    tar \
    gzip

# Some EL10 variants do not provide pcre-devel (PCRE1). Install only when available.
if ! sudo dnf -y install pcre-devel 2>/dev/null; then
    echo "[INFO] pcre-devel is not available on this system. Continuing with pcre2-devel only."
fi

# 4. Architecture Detection
ARCH=$(uname -m)
OS_NAME=$(. /etc/os-release && echo "$NAME $VERSION_ID")

echo ""
echo "--- System Check ---"
echo "Architecture: $ARCH"
echo "OS: $OS_NAME"
echo "--------------------"

# 5. x86_64 specific tools
if [ "$ARCH" = "x86_64" ]; then
    echo "[Target: x86_64] Installing NASM and YASM..."
    sudo dnf install -y yasm nasm
    echo "--------------------------------------"
    echo "Installation Complete!"
    nasm -v
    yasm --version | head -n 1
    echo "--------------------------------------"
else
    echo "[Target: $ARCH] NASM/YASM are x86-specific. Skipping."
fi

echo "setup_init.sh completed successfully."
