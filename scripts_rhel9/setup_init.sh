#!/bin/bash

# Stop on error
set -e

# In some container environments, /usr/bin/sudo may be unusable
# (e.g., missing setuid bit or nosuid mount). Handle this explicitly.
if [ "$(id -u)" -eq 0 ]; then
    sudo() { "$@"; }
else
    if ! command -v sudo >/dev/null 2>&1; then
        echo "[ERROR] sudo command is not available for user $(id -un)."
        echo "[INFO] Run this script as root or install/configure sudo."
        exit 1
    fi

    if ! sudo -n true >/dev/null 2>&1; then
        echo "[ERROR] sudo is not usable for user $(id -un)."
        echo "[INFO] This often happens in containers when /usr/bin/sudo cannot run (setuid/nosuid issue)."
        echo "[INFO] Workaround: run this script as root in this environment."
        exit 1
    fi
fi

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
if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
    USE_REMI_PHP81=0
fi

# 1. Enable EPEL and CRB (CodeReady Builder)
echo "Enabling EPEL and CRB repositories..."

enable_repo_candidates() {
    local repo_id
    for repo_id in "$@"; do
        if [ -z "$repo_id" ]; then
            continue
        fi
        if sudo dnf config-manager --set-enabled "$repo_id" >/dev/null 2>&1; then
            echo "[OK] Enabled repository: $repo_id"
            return 0
        fi
    done
    return 1
}

try_enable_codeready_autodetect() {
    local repo_ids=()
    local repo_id

    while IFS= read -r repo_id; do
        [ -n "$repo_id" ] && repo_ids+=("$repo_id")
    done < <(
        sudo dnf repolist all 2>/dev/null |
            awk 'NR > 1 {print $1}' |
            grep -Ei 'codeready|code-ready|crb' |
            grep -Evi 'debug|source' |
            sort -u
    )

    if [ "${#repo_ids[@]}" -eq 0 ]; then
        return 1
    fi

    if enable_repo_candidates "${repo_ids[@]}"; then
        echo "[INFO] Auto-detected and enabled CodeReady/CRB-style repo."
        return 0
    fi

    return 1
}

try_enable_crb() {
    local arch
    arch="$(uname -m)"

    if [ "$OS_ID" = "ol" ] || [ "$OS_ID" = "oracle" ]; then
        if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
            enable_repo_candidates \
                "ol${EL_VER}_codeready_builder" \
                "ol9_codeready_builder" \
                crb || true
        else
            enable_repo_candidates \
                ol9_codeready_builder \
                crb || true
        fi
        return 0
    fi

    if [ "$OS_ID" = "rhel" ] && [ "$EL_VER" -ge 10 ] 2>/dev/null; then
        if enable_repo_candidates \
            "codeready-builder-for-rhel-${EL_VER}-${arch}-rhui-rpms" \
            "codeready-builder-for-rhel-${EL_VER}-${arch}-eus-rhui-rpms" \
            "codeready-builder-for-rhel-${EL_VER}-rhui-rpms" \
            "codeready-builder-for-rhel-${EL_VER}-${arch}-rpms" \
            "codeready-builder-for-rhel-${EL_VER}-rpms" \
            "rhel-${EL_VER}-for-${arch}-codeready-builder-rhui-rpms" \
            "rhel-${EL_VER}-codeready-builder-rhui-rpms" \
            crb; then
            return 0
        fi

        if try_enable_codeready_autodetect; then
            return 0
        fi

        echo "[WARN] Could not enable RHEL${EL_VER} CodeReady/CRB repo (continuing)."
        return 1
    fi

    if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
        if enable_repo_candidates crb; then
            return 0
        fi
        if try_enable_codeready_autodetect; then
            return 0
        fi
        echo "[WARN] Could not enable CRB/CodeReady repo (continuing)."
    else
        enable_repo_candidates ol9_codeready_builder crb || \
            echo "[WARN] Could not enable CRB/CodeReady repo (continuing)."
    fi
}

try_enable_epel() {
    local epel_url_candidates=()
    local epel_url

    enable_epel_repo_candidates() {
        local repo_ids=()
        local repo_id

        # Known EPEL-style repo IDs across EL variants
        repo_ids=(
            epel
            epel-testing
            epel-next
            "ol${EL_VER}_developer_EPEL"
            "ol${EL_VER}_developer_EPEL_aarch64"
            "ol${EL_VER}_developer_EPEL_x86_64"
        )

        enable_repo_candidates "${repo_ids[@]}" || true

        # Auto-detect any additional EPEL-like repo IDs and enable them.
        while IFS= read -r repo_id; do
            [ -n "$repo_id" ] || continue
            sudo dnf config-manager --set-enabled "$repo_id" >/dev/null 2>&1 || true
        done < <(
            sudo dnf repolist all 2>/dev/null |
                awk 'NR > 1 {print $1}' |
                grep -Ei '(^|[-_])epel($|[-_])|developer_epel' |
                grep -Evi 'debug|source' |
                sort -u
        )
    }

    if rpm -q epel-release >/dev/null 2>&1; then
        echo "[OK] epel-release is already installed"
        return 0
    fi

    # 1) Package-based attempts (works on Oracle Linux and some derivatives)
    if sudo dnf install -y "oracle-epel-release-el${EL_VER}" 2>/dev/null; then
        echo "[OK] Installed oracle-epel-release-el${EL_VER}"
        enable_epel_repo_candidates
        return 0
    fi

    if sudo dnf install -y epel-release 2>/dev/null; then
        echo "[OK] Installed epel-release from configured repositories"
        enable_epel_repo_candidates
        return 0
    fi

    # 2) URL bootstrap fallback (important for RHEL RHUI where epel-release is not in default repos)
    if [[ "$OS_ID" == "rhel" || "$OS_ID" == "rocky" || "$OS_ID" == "almalinux" || "$OS_ID" == "centos" ]]; then
        epel_url_candidates=(
            "https://dl.fedoraproject.org/pub/epel/epel-release-latest-${EL_VER}.noarch.rpm"
            "https://www.mirrorservice.org/sites/dl.fedoraproject.org/pub/epel/epel-release-latest-${EL_VER}.noarch.rpm"
        )

        for epel_url in "${epel_url_candidates[@]}"; do
            if sudo dnf install -y "$epel_url" 2>/dev/null; then
                echo "[OK] Installed epel-release from URL: $epel_url"
                enable_epel_repo_candidates
                return 0
            fi
        done
    fi

    echo "[WARN] EPEL release package is unavailable on this host (continuing without EPEL)."
    return 1
}

case "$OS_ID" in
    ol|oracle)
        try_enable_epel
        try_enable_crb
        ;;
    rocky|almalinux|rhel|centos)
        try_enable_epel
        try_enable_crb || true
        ;;
    *)
        echo "[WARN] Unknown OS ID: $OS_ID, attempting EL defaults..."
        try_enable_epel
        try_enable_crb || true
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
    echo "[INFO] EL${EL_VER}: skipping Remi PHP 8.1 setup (EL10 remi-release often depends on unavailable epel-release)."
fi

# 3. Install core tools and libraries
echo "Installing core tools and libraries..."

repo_has_package() {
    local pkg="$1"
    # dnf list/repoquery can return confusing statuses on some EL10 images.
    # Use a test transaction to verify the package is actually installable.
    sudo dnf -y install --setopt=tsflags=test "$pkg" >/dev/null 2>&1
}

install_required_from_candidates() {
    local logical_name="$1"
    local candidates_csv="$2"
    local required_mode="${3:-required}"
    local candidate

    IFS=',' read -r -a candidates <<< "$candidates_csv"
    for candidate in "${candidates[@]}"; do
        if repo_has_package "$candidate"; then
            echo "[OK] ${logical_name}: using package '${candidate}'"
            sudo dnf -y install "$candidate"
            return 0
        fi
    done

    if [ "$logical_name" = "jansson-devel" ]; then
        echo "[WARN] ${logical_name} not found. Retrying after CodeReady/CRB enable attempt..."
        try_enable_crb || true
        for candidate in "${candidates[@]}"; do
            if repo_has_package "$candidate"; then
                echo "[OK] ${logical_name}: using package '${candidate}' after CRB remediation"
                sudo dnf -y install "$candidate"
                return 0
            fi
        done
    fi

    if [ "$required_mode" = "optional" ]; then
        echo "[WARN] Optional package '${logical_name}' is unavailable in enabled repositories; continuing."
        echo "[WARN] Tried candidates: ${candidates_csv}"
        return 0
    fi

    echo "[ERROR] Required package '${logical_name}' is unavailable in enabled repositories."
    echo "[ERROR] Tried candidates: ${candidates_csv}"
    echo "[INFO] Enabled repositories:" 
    sudo dnf repolist --enabled || true
    return 1
}

# perf package: on Oracle Linux with UEK, perf may not be available
# as a standalone package. Try installing, but don't fail if unavailable.
PERF_PKG="perf"
if ! sudo dnf install -y "$PERF_PKG" 2>/dev/null; then
    echo "[WARN] '$PERF_PKG' package not available. Trying kernel-uek-tools..."
    if ! sudo dnf install -y kernel-uek-tools 2>/dev/null; then
        echo "[ERROR] Neither '$PERF_PKG' nor 'kernel-uek-tools' is available."
        exit 1
    fi
fi

# Minimal Docker images ship curl-minimal which conflicts with full curl.
# Install full curl only if curl-minimal is not present.
if ! rpm -q curl-minimal >/dev/null 2>&1; then
    sudo dnf -y install curl
fi

echo "Installing required packages with repository-backed name resolution..."
PACKAGE_SPECS=(
    "bc:bc"
    "libuuid-devel:libuuid-devel"
    "libxml2-devel:libxml2-devel"
    "pkgconf-pkg-config:pkgconf-pkg-config"
    "libcurl-devel:libcurl-devel"
    "jansson-devel:jansson-devel"
    "sysstat:sysstat"
    "aria2:aria2"
    "flex:flex"
    "bison:bison"
    "openssl-devel:openssl-devel"
    "elfutils-libelf-devel:elfutils-libelf-devel"
    "libevent-devel:libevent-devel"
    "python3-tabulate:python3-tabulate,python3.12-tabulate"
    "expat-devel:expat-devel"
    "pcre2-devel:pcre2-devel"
    "p7zip:p7zip,7zip"
    "p7zip-plugins:p7zip-plugins,7zip-plugins"
    "glibc-devel:glibc-devel"
    "numactl:numactl"
    "which:which"
    "wget:wget"
    "tar:tar"
    "gzip:gzip"
)

OPTIONAL_PACKAGE_SPECS=(
    "htop:htop"
)

for spec in "${PACKAGE_SPECS[@]}"; do
    logical_name="${spec%%:*}"
    candidates_csv="${spec#*:}"
    install_required_from_candidates "$logical_name" "$candidates_csv"
done

for spec in "${OPTIONAL_PACKAGE_SPECS[@]}"; do
    logical_name="${spec%%:*}"
    candidates_csv="${spec#*:}"
    install_required_from_candidates "$logical_name" "$candidates_csv" optional
done

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

persist_pkg_config_path_profile() {
    local profile_file="/etc/profile.d/cloud_onehour-pkgconfig.sh"
    local profile_content

    profile_content='export PKG_CONFIG_PATH="/usr/local/lib64/pkgconfig:/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH}"'

    if sudo test -f "$profile_file" && sudo grep -Fq "/usr/local/lib64/pkgconfig:/usr/local/lib/pkgconfig" "$profile_file"; then
        echo "[OK] PKG_CONFIG_PATH profile already configured: ${profile_file}"
        return 0
    fi

    echo "[INFO] Persisting PKG_CONFIG_PATH for login shells: ${profile_file}"
    echo "$profile_content" | sudo tee "$profile_file" >/dev/null
    sudo chmod 0644 "$profile_file"
}

ensure_codec_pc_visibility() {
    local pc
    local src
    local dst_dirs=("/usr/lib64/pkgconfig" "/usr/lib/pkgconfig")
    local dst_dir
    for pc in x264.pc x265.pc; do
        if [ -f "/usr/local/lib64/pkgconfig/${pc}" ]; then
            src="/usr/local/lib64/pkgconfig/${pc}"
        elif [ -f "/usr/local/lib/pkgconfig/${pc}" ]; then
            src="/usr/local/lib/pkgconfig/${pc}"
        else
            continue
        fi

        for dst_dir in "${dst_dirs[@]}"; do
            if [ -d "${dst_dir}" ]; then
                sudo ln -sf "${src}" "${dst_dir}/${pc}" || true
            fi
        done
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
    sudo dnf -y install git gcc gcc-c++ make pkgconf-pkg-config 2>/dev/null || true

    # Ensure CMake and Ninja are available for faster x265 build
    if ! command -v cmake >/dev/null 2>&1; then
        echo "[INFO] Installing cmake..."
        sudo dnf -y install cmake 2>/dev/null || true
    fi
    if ! command -v ninja >/dev/null 2>&1; then
        echo "[INFO] Installing ninja-build..."
        sudo dnf -y install ninja-build 2>/dev/null || sudo dnf -y install ninja 2>/dev/null || true
    fi

    if ! command -v cmake >/dev/null 2>&1; then
        echo "[WARN] cmake is still unavailable; skipping x265 source build"
        return 1
    fi
    if ! command -v ninja >/dev/null 2>&1; then
        echo "[WARN] ninja is still unavailable; skipping x265 source build"
        return 1
    fi

    rm -rf /tmp/cloud_onehour-x265
    git clone --depth 1 https://github.com/videolan/x265.git /tmp/cloud_onehour-x265
    pushd /tmp/cloud_onehour-x265 >/dev/null
    cmake -G Ninja -S source -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local -DENABLE_SHARED=ON
    cmake --build build
    sudo cmake --install build
    popd >/dev/null
}

generate_x265_pc_if_missing() {
    local existing_pc=""
    local libdir=""
    local pkgconfig_dir=""
    local pc_file=""

    if [ -f "/usr/local/lib64/pkgconfig/x265.pc" ]; then
        existing_pc="/usr/local/lib64/pkgconfig/x265.pc"
    elif [ -f "/usr/local/lib/pkgconfig/x265.pc" ]; then
        existing_pc="/usr/local/lib/pkgconfig/x265.pc"
    fi

    if [ -n "$existing_pc" ]; then
        echo "[INFO] x265.pc already present: ${existing_pc}"
        return 0
    fi

    if compgen -G "/usr/local/lib64/libx265.so*" >/dev/null || [ -f "/usr/local/lib64/libx265.a" ]; then
        libdir="/usr/local/lib64"
        pkgconfig_dir="/usr/local/lib64/pkgconfig"
    elif compgen -G "/usr/local/lib/libx265.so*" >/dev/null || [ -f "/usr/local/lib/libx265.a" ]; then
        libdir="/usr/local/lib"
        pkgconfig_dir="/usr/local/lib/pkgconfig"
    else
        echo "[WARN] libx265 library (.so/.a) not found under /usr/local/lib{,64}; skipping x265.pc generation"
        return 1
    fi

    pc_file="${pkgconfig_dir}/x265.pc"
    sudo mkdir -p "$pkgconfig_dir"
    sudo tee "$pc_file" >/dev/null <<EOF
prefix=/usr/local
exec_prefix=\${prefix}
libdir=${libdir}
includedir=\${prefix}/include

Name: x265
Description: H.265/HEVC video encoder
Version: unknown
Libs: -L\${libdir} -lx265
Cflags: -I\${includedir}
EOF
    sudo chmod 0644 "$pc_file"
    echo "[INFO] Generated x265 pkg-config file: ${pc_file}"
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
        if build_x265_from_source; then
            generate_x265_pc_if_missing || true
        else
            echo "[WARN] x265 source build failed"
        fi
    fi

    ensure_codec_pc_visibility
    sudo ldconfig 2>/dev/null || true
}

ensure_codec_pkgconfig_ready

if ! codec_pkgconfig_ready; then
    echo "[ERROR] x264/x265 are still unavailable via pkg-config after repository/source attempts."
    exit 1
fi

persist_pkg_config_path_profile

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
    sudo dnf install -y nasm || { echo "[ERROR] nasm package is required but unavailable"; exit 1; }
    sudo dnf install -y yasm || { echo "[ERROR] yasm package is required but unavailable"; exit 1; }
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
