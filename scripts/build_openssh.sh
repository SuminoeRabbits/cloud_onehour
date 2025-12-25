#!/bin/bash
set -euo pipefail

VERSION="3.5.4"
ARCHIVE="openssl-${VERSION}.tar.gz"
DOWNLOAD_URL="https://www.openssl.org/source/${ARCHIVE}"
INSTALL_PREFIX="/usr/local"

echo "Building OpenSSL ${VERSION} with current CFLAGS: ${CFLAGS:-none}"

# Download source
wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"

# Extract
tar -xf "$ARCHIVE"
cd "openssl-${VERSION}"

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

echo "Detected architecture: $ARCH -> Using library directory: $LIBSUBDIR"

# Set LDFLAGS to ensure RPATH is embedded
export LDFLAGS="-Wl,-rpath,${LIBDIR}"

# Configure with CFLAGS/CXXFLAGS from environment
# OpenSSL's config script respects CC and CFLAGS
./config --prefix="${INSTALL_PREFIX}" \
         --openssldir="${INSTALL_PREFIX}/ssl" \
         --libdir="${LIBSUBDIR}" \
         shared \
         zlib

# Build
make -j$(nproc)

# Skip tests (optional - QUIC tests may fail in some environments)
# make test

# Install
sudo make install

# Update shared library cache
sudo ldconfig

# Add lib directory to ldconfig if not already present
if [[ ! -f /etc/ld.so.conf.d/local-${LIBSUBDIR}.conf ]]; then
    echo "${LIBDIR}" | sudo tee /etc/ld.so.conf.d/local-${LIBSUBDIR}.conf
    sudo ldconfig
fi

# Verify installation and flags used
echo "=== OpenSSL Version ==="
"${INSTALL_PREFIX}/bin/openssl" version -a

# Cleanup
cd ..
rm -rf "openssl-${VERSION}" "$ARCHIVE"

echo ""
echo "OpenSSL ${VERSION} installed to ${INSTALL_PREFIX}"
echo "To use it, ensure ${INSTALL_PREFIX}/bin is in your PATH"
echo "export PATH=${INSTALL_PREFIX}/bin:\$PATH" >> ~/.bashrc
echo "export LD_LIBRARY_PATH=${INSTALL_PREFIX}/lib:\$LD_LIBRARY_PATH" >> ~/.bashrc