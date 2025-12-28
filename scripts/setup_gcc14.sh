#!/bin/bash
set -euo pipefail

# Setup passwordless sudo if not already configured
# This is required for automated benchmark runs
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/setup_passwordless_sudo.sh" ]; then
    if ! sudo -n true 2>/dev/null; then
        echo "=== Sudo requires password. Setting up passwordless sudo... ==="
        echo "You will be asked for your password one last time."
        bash "$SCRIPT_DIR/setup_passwordless_sudo.sh"
    else
        echo "[INFO] Passwordless sudo is already configured"
    fi
fi

# Check and switch to GCC 14 if needed
current_gcc_version=$(gcc -dumpversion 2>/dev/null | cut -d. -f1 || echo "0")
if [[ -z "$current_gcc_version" ]] || [[ "$current_gcc_version" -lt 14 ]] 2>/dev/null; then
    echo "Current GCC version is ${current_gcc_version:-not installed}. Installing GCC 14..."
    
    # Detect Ubuntu version
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        UBUNTU_VERSION="${VERSION_ID}"
        UBUNTU_CODENAME="${VERSION_CODENAME}"
    else
        echo "Error: Cannot detect Ubuntu version"
        exit 1
    fi
    
    echo "Detected Ubuntu ${UBUNTU_VERSION} (${UBUNTU_CODENAME})"
    
    # Check if GCC-14 is available in default repositories
    if apt-cache search --names-only '^gcc-14$' | grep -q '^gcc-14'; then
        # GCC-14 is available in default repositories (Ubuntu 24.04+)
        echo "GCC-14 is available in default repositories"
        sudo apt-get update
        sudo apt-get install -y gcc-14 g++-14
    else
        # GCC-14 not in default repos, compile from source (Ubuntu 22.04)
        echo "GCC-14 not in default repositories, compiling from source..."
        echo "This will take 1-2 hours. Please be patient."
        
        GCC_VERSION="14.2.0"
        INSTALL_PREFIX="/usr/local"
        BUILD_DIR="/tmp/gcc-${GCC_VERSION}-build"
        
        # Install build dependencies
        echo "Installing build dependencies..."
        sudo apt-get update
        sudo apt-get install -y build-essential libgmp-dev libmpfr-dev libmpc-dev \
            flex bison texinfo libzstd-dev wget
        
        # Download GCC source
        echo "Downloading GCC ${GCC_VERSION} source code..."
        cd /tmp
        if [[ ! -f "gcc-${GCC_VERSION}.tar.gz" ]]; then
            wget https://ftp.gnu.org/gnu/gcc/gcc-${GCC_VERSION}/gcc-${GCC_VERSION}.tar.gz
        fi
        
        # Extract source
        echo "Extracting source code..."
        rm -rf gcc-${GCC_VERSION}
        tar -xzf gcc-${GCC_VERSION}.tar.gz
        cd gcc-${GCC_VERSION} || {
            echo "Error: Failed to enter source directory gcc-${GCC_VERSION}"
            exit 1
        }
        
        # Create build directory
        echo "Configuring build..."
        rm -rf "${BUILD_DIR}"
        mkdir -p "${BUILD_DIR}"
        cd "${BUILD_DIR}" || {
            echo "Error: Failed to enter build directory ${BUILD_DIR}"
            exit 1
        }

        # Configure
        /tmp/gcc-${GCC_VERSION}/configure \
            --prefix="${INSTALL_PREFIX}" \
            --enable-languages=c,c++ \
            --disable-multilib \
            --enable-threads=posix \
            --enable-checking=release \
            --program-suffix=-14 \
            --with-system-zlib

        # Build (this takes 1-2 hours)
        echo "Building GCC-14 (this will take 1-2 hours)..."
        NCPUS=$(nproc 2>/dev/null || echo 1)
        make -j"${NCPUS}"
        
        # Install
        echo "Installing GCC-14..."
        sudo make install
        
        # Clean up
        echo "Cleaning up build files..."
        cd /tmp
        rm -rf gcc-${GCC_VERSION} "${BUILD_DIR}"
        
        echo "GCC-14 compiled and installed to ${INSTALL_PREFIX}"
    fi
    
    # Set alternatives
    if [[ -f /usr/bin/gcc-14 ]]; then
        # Package installation
        sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 100
        sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-14 100
        sudo update-alternatives --set gcc /usr/bin/gcc-14
        sudo update-alternatives --set g++ /usr/bin/g++-14
    elif [[ -f /usr/local/bin/gcc-14 ]]; then
        # Source installation
        sudo update-alternatives --install /usr/bin/gcc gcc /usr/local/bin/gcc-14 100
        sudo update-alternatives --install /usr/bin/g++ g++ /usr/local/bin/g++-14 100
        sudo update-alternatives --set gcc /usr/local/bin/gcc-14
        sudo update-alternatives --set g++ /usr/local/bin/g++-14
    fi
    
    echo "Switched to GCC 14"
else
    echo "GCC $current_gcc_version is already >= 14"
fi

# Set compiler variables
export CC=gcc
export CXX=g++

# Detect architecture and set CFLAGS/CXXFLAGS
ARCH=$(uname -m)

case "$ARCH" in
    x86_64)
        OPT_FLAGS="-O3 -march=native"
        echo "Architecture: x86_64, flags: ${OPT_FLAGS}"
        ;;
    aarch64|arm64)
        # Check for SVE2 support with robust pattern matching
        if grep -qE "(^Features\s*:.*\bsve2\b|\bsve2\b)" /proc/cpuinfo 2>/dev/null; then
            OPT_FLAGS="-O3 -march=armv8-a+sve2"
            echo "Architecture: aarch64 with SVE2, flags: ${OPT_FLAGS}"
        else
            OPT_FLAGS="-O3 -march=armv8-a+simd"
            echo "Architecture: aarch64 without SVE2, flags: ${OPT_FLAGS}"
        fi
        ;;
    armv7l|armhf)
        OPT_FLAGS="-O3 -march=armv7-a -mfpu=neon-vfpv4"
        echo "Architecture: armv7l/armhf, flags: ${OPT_FLAGS}"
        ;;
    i686|i386)
        OPT_FLAGS="-O3 -march=native"
        echo "Architecture: i686/i386, flags: ${OPT_FLAGS}"
        ;;
    *)
        echo "Error: Unsupported architecture: $ARCH"
        echo "Supported architectures: x86_64, aarch64/arm64, armv7l/armhf, i686/i386"
        exit 1
        ;;
esac

export CFLAGS="$OPT_FLAGS"
export CXXFLAGS="$OPT_FLAGS"

echo "export CFLAGS='${OPT_FLAGS}'" >> ~/.bashrc
echo "export CXXFLAGS='${OPT_FLAGS}'" >> ~/.bashrc

echo "CC=$CC, CXX=$CXX"
echo "CFLAGS=$CFLAGS"
echo "CXXFLAGS=$CXXFLAGS"