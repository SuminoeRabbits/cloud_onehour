#!/bin/bash
set -euo pipefail

VERSION="1.3.1"
ARCHIVE="zlib-${VERSION}.tar.gz"
DOWNLOAD_URL="https://www.zlib.net/${ARCHIVE}"
INSTALL_PREFIX="/usr/local"

# dependencies
sudo apt-get update
sudo apt-get install -y make cmake

# Detect lib directory based on architecture
ARCH=$(uname -m)
case "$ARCH" in
    x86_64)
        LIBSUBDIR="lib64"
        ;;
    aarch64|arm64)
        LIBSUBDIR="lib"
        ;;
    armv7l|armhf)
        LIBSUBDIR="lib"
        ;;
    i686|i386)
        LIBSUBDIR="lib"
        ;;
    *)
        echo "Error: Unsupported architecture: $ARCH"
        echo "Supported architectures: x86_64, aarch64/arm64, armv7l/armhf, i686/i386"
        exit 1
        ;;
esac
LIBDIR="${INSTALL_PREFIX}/${LIBSUBDIR}"

echo "Building zlib ${VERSION} with current CFLAGS: ${CFLAGS:-none}"
echo "Detected architecture: $ARCH -> Using library directory: $LIBSUBDIR"

# Set LDFLAGS to ensure RPATH is embedded
export LDFLAGS="-Wl,-rpath,${LIBDIR}"

# Download source
wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"

# Extract
tar -xf "$ARCHIVE"
cd "zlib-${VERSION}"

# Configure with lib directory
./configure --prefix="${INSTALL_PREFIX}" --libdir="${LIBDIR}"

# Build
make -j$(nproc)

# Test
make test

# Install
sudo make install

# Update shared library cache
sudo ldconfig

# Add lib directory to ldconfig if not already present
if [[ ! -f /etc/ld.so.conf.d/local-${LIBSUBDIR}.conf ]]; then
    echo "${LIBDIR}" | sudo tee /etc/ld.so.conf.d/local-${LIBSUBDIR}.conf
    sudo ldconfig
fi

# Verify
echo "=== Zlib installed ==="
ls -l "${LIBDIR}/libz.so"*

# Cleanup
cd ..
rm -rf "zlib-${VERSION}" "$ARCHIVE"

echo ""
echo "zlib ${VERSION} installed to ${LIBDIR}"