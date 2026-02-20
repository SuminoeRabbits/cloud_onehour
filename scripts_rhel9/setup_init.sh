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

# FFmpeg (PTS) requires libx264/libx265 via pkg-config when
# FFMPEG_CONFIGURE_EXTRA_OPTS enables those encoders.
# On some EL/OL variants, package names differ or are unavailable.
echo "Installing FFmpeg codec development dependencies (x264/x265)..."

# Toggle for third-party repo attempts (default: enabled)
ENABLE_THIRD_PARTY_CODEC_REPOS="${ENABLE_THIRD_PARTY_CODEC_REPOS:-1}"

codec_pkgconfig_ready() {
    command -v pkg-config >/dev/null 2>&1 && pkg-config --exists x264 && pkg-config --exists x265
}

try_install_codec_packages() {
    sudo dnf -y install x264 x264-devel x265 x265-devel 2>/dev/null
}

try_enable_additional_codec_repos() {
    echo "[INFO] Trying additional repositories for x264/x265 packages..."

    # Oracle Linux specific optional repos (may or may not exist by release)
    if [[ "$OS_ID" == "ol" || "$OS_ID" == "oracle" ]]; then
        sudo dnf config-manager --set-enabled "ol${EL_VER}_developer_EPEL" 2>/dev/null || true
        sudo dnf config-manager --set-enabled "ol${EL_VER}_addons" 2>/dev/null || true
        sudo dnf config-manager --set-enabled "ol${EL_VER}_appstream" 2>/dev/null || true
    fi

    # Try RPM Fusion (often provides x264/x265 on EL family)
    if [ "$ENABLE_THIRD_PARTY_CODEC_REPOS" = "1" ]; then
        echo "[INFO] Trying RPM Fusion repositories..."
        sudo dnf -y install \
            "https://mirrors.rpmfusion.org/free/el/rpmfusion-free-release-${EL_VER}.noarch.rpm" \
            "https://mirrors.rpmfusion.org/nonfree/el/rpmfusion-nonfree-release-${EL_VER}.noarch.rpm" \
            2>/dev/null || true
    else
        echo "[INFO] Skipping third-party codec repositories (ENABLE_THIRD_PARTY_CODEC_REPOS=0)"
    fi
}

if ! try_install_codec_packages; then
    echo "[WARN] x264/x265 devel packages not available via currently-enabled repos."
    try_enable_additional_codec_repos
    if ! try_install_codec_packages; then
        echo "[WARN] x264/x265 packages are still unavailable from package repositories."
    fi
fi

# Ensure pkg-config can find /usr/local installs as well.
export PKG_CONFIG_PATH="/usr/local/lib64/pkgconfig:/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH}"

ensure_codec_pc_visibility() {
    local pc
    local src
    local dst_dir="/usr/lib64/pkgconfig"
    for pc in x264.pc x265.pc; do
        if [ -f "/usr/local/lib64/pkgconfig/${pc}" ]; then
            src="/usr/local/lib64/pkgconfig/${pc}"
        elif [ -f "/usr/local/lib/pkgconfig/${pc}" ]; then
            src="/usr/local/lib/pkgconfig/${pc}"
        else
            continue
        fi

        if [ -d "${dst_dir}" ]; then
            sudo ln -sf "${src}" "${dst_dir}/${pc}" || true
        fi
    done
}

build_x264_from_source() {
    echo "[INFO] Building x264 from source..."
    sudo dnf -y install git gcc gcc-c++ make nasm yasm pkgconf-pkg-config 2>/dev/null || true
    rm -rf /tmp/cloud_onehour-x264
    git clone --depth 1 https://code.videolan.org/videolan/x264.git /tmp/cloud_onehour-x264
    pushd /tmp/cloud_onehour-x264 >/dev/null
    ./configure --prefix=/usr/local --enable-shared --enable-pic
    make -j"$(nproc)"
    sudo make install
    popd >/dev/null
}

build_x265_from_source() {
    echo "[INFO] Building x265 from source..."
    sudo dnf -y install git gcc gcc-c++ make cmake pkgconf-pkg-config 2>/dev/null || true
    rm -rf /tmp/cloud_onehour-x265
    git clone --depth 1 https://github.com/videolan/x265.git /tmp/cloud_onehour-x265
    pushd /tmp/cloud_onehour-x265 >/dev/null
    cmake -S source -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local -DENABLE_SHARED=ON
    cmake --build build -j"$(nproc)"
    sudo cmake --install build
    popd >/dev/null
}

ensure_codec_pkgconfig_ready() {
    if codec_pkgconfig_ready; then
        return
    fi

    echo "[WARN] x264/x265 are still missing from pkg-config. Falling back to source builds..."

    if ! (command -v pkg-config >/dev/null 2>&1 && pkg-config --exists x264); then
        build_x264_from_source || echo "[WARN] x264 source build failed"
    fi

    if ! (command -v pkg-config >/dev/null 2>&1 && pkg-config --exists x265); then
        build_x265_from_source || echo "[WARN] x265 source build failed"
    fi

    ensure_codec_pc_visibility
    sudo ldconfig 2>/dev/null || true
}

ensure_codec_pkgconfig_ready

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

echo ""
echo "--- FFmpeg Codec Dependency Check ---"
if command -v pkg-config >/dev/null 2>&1; then
    ensure_codec_pc_visibility
    if pkg-config --exists x264; then
        echo "[OK] x264 detected via pkg-config"
    else
        echo "[WARN] x264 NOT detected via pkg-config (ffmpeg PTS install may fail)"
    fi

    if pkg-config --exists x265; then
        echo "[OK] x265 detected via pkg-config"
    else
        echo "[WARN] x265 NOT detected via pkg-config (ffmpeg PTS install may fail)"
    fi
else
    echo "[WARN] pkg-config command not found"
fi
echo "--------------------------------------"

# 5. NASM/YASM tools (required by ffmpeg/x264 build path in PTS)
echo "[Target: $ARCH] Installing NASM and YASM..."

# Try bulk install first, then retry per package for distro/repo differences
if ! sudo dnf install -y nasm yasm; then
    echo "[WARN] Bulk install (nasm yasm) failed. Retrying individually..."
    sudo dnf install -y nasm || echo "[WARN] nasm package is not available on this system"
    sudo dnf install -y yasm || echo "[WARN] yasm package is not available on this system"
fi

echo "--------------------------------------"
echo "NASM/YASM installation check"
if command -v nasm >/dev/null 2>&1; then
    nasm -v
else
    echo "[WARN] nasm is not installed"
fi

if command -v yasm >/dev/null 2>&1; then
    yasm --version | head -n 1
else
    echo "[WARN] yasm is not installed"
fi
echo "--------------------------------------"

echo "setup_init.sh completed successfully."
