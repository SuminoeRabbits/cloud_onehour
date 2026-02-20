#!/bin/bash
set -euo pipefail

if [[ ":$PATH:" != *":/usr/local/bin:"* ]]; then
    export PATH="/usr/local/bin:$PATH"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/dnf_utils.sh"

EL_VER=$(get_el_version)
echo "=== GCC-14 Setup (EL${EL_VER}) ==="

echo "Installing build dependencies for GCC..."
wait_for_dnf_lock
sudo dnf -y groupinstall "Development Tools"

repo_has_pkg() {
    local pkg="$1"
    sudo dnf -q list --available "$pkg" >/dev/null 2>&1 || sudo dnf -q repoquery "$pkg" >/dev/null 2>&1
}

install_required_pkg() {
    local pkg="$1"
    if ! repo_has_pkg "$pkg"; then
        echo "[ERROR] Required GCC dependency '$pkg' is unavailable in enabled repositories."
        sudo dnf repolist --enabled || true
        exit 1
    fi
    sudo dnf -y install "$pkg"
}

GCC_DEP_LIST=(
    gmp-devel
    mpfr-devel
    libmpc-devel
    flex
    bison
    texinfo
    libzstd-devel
    zlib-devel
    wget
    aria2
)

for pkg in "${GCC_DEP_LIST[@]}"; do
    install_required_pkg "$pkg"
done

# ---------------------------------------------------------------
# EL10+: GCC 14.2 is the system default compiler.
# No toolset or source build needed — just verify version.
# ---------------------------------------------------------------
if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
    SYSTEM_GCC_VER=$(gcc -dumpversion 2>/dev/null || echo "0")
    SYSTEM_GCC_MAJOR="${SYSTEM_GCC_VER%%.*}"
    if [ "$SYSTEM_GCC_MAJOR" -ge 14 ] 2>/dev/null; then
        echo "[OK] EL${EL_VER}: System GCC ${SYSTEM_GCC_VER} >= 14, no additional setup needed."
        gcc --version
        exit 0
    fi
    echo "[INFO] EL${EL_VER}: System GCC ${SYSTEM_GCC_VER} < 14, falling through to install..."
fi

# ---------------------------------------------------------------
# EL9: Install GCC-14 via toolset or source build
# ---------------------------------------------------------------

# Check if GCC-14 is already installed
gcc14_installed=false
if [[ -f /usr/bin/gcc-14 ]] || [[ -f /usr/local/bin/gcc-14 ]]; then
    gcc14_installed=true
    echo "GCC-14 is already installed"
fi

if [[ "$gcc14_installed" = false ]]; then
    # Try AppStream gcc-toolset-14 first
    if dnf list gcc-toolset-14-gcc >/dev/null 2>&1; then
        echo "Installing gcc-toolset-14..."
        sudo dnf -y install gcc-toolset-14-gcc gcc-toolset-14-gcc-c++

        # Link to /usr/local/bin/gcc-14 to maintain interface compatibility
        TOOLSET_GCC="/opt/rh/gcc-toolset-14/root/usr/bin/gcc"
        TOOLSET_GXX="/opt/rh/gcc-toolset-14/root/usr/bin/g++"
        if [ -f "$TOOLSET_GCC" ]; then
            sudo ln -sf "$TOOLSET_GCC" /usr/local/bin/gcc-14
            sudo ln -sf "$TOOLSET_GXX" /usr/local/bin/g++-14

            # Setup library/include paths for the toolset runtime.
            # Symlinks alone don't set LD_LIBRARY_PATH or include paths,
            # causing libstdc++ mismatch at runtime.
            ENABLE_SCRIPT="/opt/rh/gcc-toolset-14/enable"
            PROFILE_SCRIPT="/etc/profile.d/gcc-toolset-14.sh"
            if [ -f "$ENABLE_SCRIPT" ] && [ ! -f "$PROFILE_SCRIPT" ]; then
                sudo tee "$PROFILE_SCRIPT" >/dev/null <<EOPROFILE
# Auto-generated: source gcc-toolset-14 environment for all login shells
source /opt/rh/gcc-toolset-14/enable
EOPROFILE
                echo "[OK] Created $PROFILE_SCRIPT for toolset library paths"
            fi
            # Also source it in the current session
            if [ -f "$ENABLE_SCRIPT" ]; then
                source "$ENABLE_SCRIPT"
            fi

            gcc14_installed=true
        fi
    fi
fi

if [[ "$gcc14_installed" = false ]]; then
    echo "GCC-14 not found in toolsets, compiling from source..."
    GCC_VERSION="14.2.0"
    INSTALL_PREFIX="/usr/local"
    BUILD_DIR="/tmp/gcc-${GCC_VERSION}-build"

    cd /tmp
    wget "https://ftp.gnu.org/gnu/gcc/gcc-${GCC_VERSION}/gcc-${GCC_VERSION}.tar.gz"
    tar -xzf "gcc-${GCC_VERSION}.tar.gz"
    mkdir -p "${BUILD_DIR}"
    cd "${BUILD_DIR}"

    /tmp/gcc-${GCC_VERSION}/configure \
        --prefix="${INSTALL_PREFIX}" \
        --enable-languages=c,c++ \
        --disable-multilib \
        --enable-threads=posix \
        --enable-checking=release \
        --program-suffix=-14 \
        --with-system-zlib

    make -j"$(nproc)"
    sudo make install
    rm -rf /tmp/gcc-${GCC_VERSION}* "${BUILD_DIR}"
fi

# Alternatives setup (EL9 only — EL10 uses system gcc directly)
echo ">>> Configuring GCC-14 as default compiler..."
if [[ -f /usr/local/bin/gcc-14 ]]; then
    sudo alternatives --install /usr/bin/gcc gcc /usr/local/bin/gcc-14 100
    sudo alternatives --install /usr/bin/g++ g++ /usr/local/bin/g++-14 100
    sudo alternatives --set gcc /usr/local/bin/gcc-14
    sudo alternatives --set g++ /usr/local/bin/g++-14
fi

# Verify
gcc --version
