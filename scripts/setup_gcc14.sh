#!/bin/bash
set -euo pipefail

# Check and switch to GCC 14 if needed
current_gcc_version=$(gcc -dumpversion 2>/dev/null | cut -d. -f1 || echo "0")
if [[ -z "$current_gcc_version" ]] || [[ "$current_gcc_version" -lt 14 ]] 2>/dev/null; then
    echo "Current GCC version is ${current_gcc_version:-not installed}. Installing GCC 14..."
    sudo apt-get update
    sudo apt-get install -y gcc-14 g++-14
    
    # Set alternatives
    sudo update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-14 100
    sudo update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-14 100
    sudo update-alternatives --set gcc /usr/bin/gcc-14
    sudo update-alternatives --set g++ /usr/bin/g++-14
    
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