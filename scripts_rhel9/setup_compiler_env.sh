#!/bin/bash
# Compiler environment setup for benchmarks
# Source this file before running benchmarks to ensure compiler flags are set

# Set compiler variables
export CC=gcc
export CXX=g++

# Detect architecture and set CFLAGS/CXXFLAGS
ARCH=$(uname -m)

case "$ARCH" in
    x86_64)
        export CFLAGS="-O3 -march=native"
        export CXXFLAGS="-O3 -march=native"
        echo "Architecture: x86_64, flags: ${CFLAGS}"
        ;;
    aarch64|arm64)
        # Check for SVE2 support
        if grep -qE "(^Features\s*:.*\bsve2\b|\bsve2\b)" /proc/cpuinfo 2>/dev/null; then
            export CFLAGS="-O3 -march=armv8-a+sve2"
            export CXXFLAGS="-O3 -march=armv8-a+sve2"
            echo "Architecture: aarch64 with SVE2, flags: ${CFLAGS}"
        else
            export CFLAGS="-O3 -march=armv8-a+simd"
            export CXXFLAGS="-O3 -march=armv8-a+simd"
            echo "Architecture: aarch64 without SVE2, flags: ${CFLAGS}"
        fi
        ;;
    armv7l|armhf)
        export CFLAGS="-O3 -march=armv7-a -mfpu=neon-vfpv4"
        export CXXFLAGS="-O3 -march=armv7-a -mfpu=neon-vfpv4"
        echo "Architecture: armv7l/armhf, flags: ${CFLAGS}"
        ;;
    i686|i386)
        export CFLAGS="-O3 -march=native"
        export CXXFLAGS="-O3 -march=native"
        echo "Architecture: i686/i386, flags: ${CFLAGS}"
        ;;
    *)
        echo "Error: Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

echo "CC=$CC, CXX=$CXX"
echo "CFLAGS=$CFLAGS"
echo "CXXFLAGS=$CXXFLAGS"
