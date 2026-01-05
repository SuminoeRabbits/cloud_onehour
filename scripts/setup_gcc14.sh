#!/bin/bash
set -euo pipefail

# Ensure /usr/local/bin is in PATH (for source-compiled GCC)
if [[ ":$PATH:" != *":/usr/local/bin:"* ]]; then
    export PATH="/usr/local/bin:$PATH"
fi

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

# Install essential build dependencies (always needed)
echo "Installing essential build dependencies..."
sudo apt-get update
sudo apt-get install -y build-essential libgmp-dev libmpfr-dev libmpc-dev \
    flex bison texinfo libzstd-dev wget

# Check if GCC-14 is already installed
gcc14_installed=false
if [[ -f /usr/bin/gcc-14 ]] || [[ -f /usr/local/bin/gcc-14 ]]; then
    gcc14_installed=true
    echo "GCC-14 is already installed"
fi

# Check and switch to GCC 14 if needed
current_gcc_version=$(gcc -dumpversion 2>/dev/null | cut -d. -f1 || echo "0")
if [[ "$gcc14_installed" = false ]]; then
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
        echo "Building with ${NCPUS} parallel jobs..."
        make -j"${NCPUS}"
        
        # Install
        echo "Installing GCC-14..."
        sudo make install

        # Verify installation
        if [[ -f "${INSTALL_PREFIX}/bin/gcc-14" ]]; then
            echo "[OK] GCC-14 installed to ${INSTALL_PREFIX}/bin/gcc-14"

            # Ensure /usr/local/bin is in PATH
            if [[ ":$PATH:" != *":/usr/local/bin:"* ]]; then
                echo "Adding /usr/local/bin to PATH..."
                export PATH="/usr/local/bin:$PATH"

                # Add to .bashrc if not already there
                if ! grep -q 'export PATH="/usr/local/bin:$PATH"' ~/.bashrc; then
                    echo 'export PATH="/usr/local/bin:$PATH"' >> ~/.bashrc
                fi
            fi
        else
            echo "[ERROR] GCC-14 installation failed - binary not found at ${INSTALL_PREFIX}/bin/gcc-14"
            exit 1
        fi

        # Clean up
        echo "Cleaning up build files..."
        cd /tmp
        rm -rf gcc-${GCC_VERSION} "${BUILD_DIR}"

        echo "GCC-14 compiled and installed to ${INSTALL_PREFIX}"
    fi
fi

# Always set/verify alternatives (even if GCC-14 was already installed)
echo ">>> Configuring GCC-14 as default compiler..."

# Ensure /usr/local/bin is in PATH before checking
if [[ ":$PATH:" != *":/usr/local/bin:"* ]]; then
    export PATH="/usr/local/bin:$PATH"
fi

if [[ -f /usr/bin/gcc-14 ]]; then
    # Package installation
    sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 100 || true
    sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-14 100 || true
    sudo update-alternatives --set gcc /usr/bin/gcc-14
    sudo update-alternatives --set g++ /usr/bin/g++-14
    echo "[OK] Set GCC-14 from /usr/bin/gcc-14 as default"
elif [[ -f /usr/local/bin/gcc-14 ]]; then
    # Source installation
    sudo update-alternatives --install /usr/bin/gcc gcc /usr/local/bin/gcc-14 100 || true
    sudo update-alternatives --install /usr/bin/g++ g++ /usr/local/bin/g++-14 100 || true
    sudo update-alternatives --set gcc /usr/local/bin/gcc-14
    sudo update-alternatives --set g++ /usr/local/bin/g++-14
    echo "[OK] Set GCC-14 from /usr/local/bin/gcc-14 as default"
else
    echo "[ERROR] GCC-14 binary not found in /usr/bin or /usr/local/bin"
    exit 1
fi

# Verify GCC version after setting alternatives
echo ">>> Verifying GCC installation..."
echo "[DEBUG] PATH: $PATH"
echo "[DEBUG] which gcc: $(which gcc 2>/dev/null || echo 'not found')"
echo "[DEBUG] which gcc-14: $(which gcc-14 2>/dev/null || echo 'not found')"

new_gcc_version=$(gcc -dumpversion 2>/dev/null | cut -d. -f1 || echo "0")
if [[ "$new_gcc_version" -ge 14 ]]; then
    echo "[OK] GCC $new_gcc_version is now active"
    gcc --version | head -1
else
    echo "[WARN] GCC version is still $new_gcc_version (expected >= 14)"
    echo "[WARN] Current gcc location: $(which gcc 2>/dev/null || echo 'not found')"
    echo "[WARN] You may need to restart your shell or run: hash -r"
    echo ""
    echo "Diagnostic information:"
    echo "  - /usr/bin/gcc-14 exists: $(test -f /usr/bin/gcc-14 && echo 'yes' || echo 'no')"
    echo "  - /usr/local/bin/gcc-14 exists: $(test -f /usr/local/bin/gcc-14 && echo 'yes' || echo 'no')"
    echo "  - update-alternatives gcc: $(update-alternatives --query gcc 2>/dev/null | grep 'Value:' || echo 'not set')"
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