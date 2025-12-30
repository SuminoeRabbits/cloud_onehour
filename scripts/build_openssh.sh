#!/bin/bash
set -euo pipefail

VERSION="3.5.4"
ARCHIVE="openssl-${VERSION}.tar.gz"
DOWNLOAD_URL="https://www.openssl.org/source/${ARCHIVE}"
INSTALL_PREFIX="/usr/local"

echo "Building OpenSSL ${VERSION} with current CFLAGS: ${CFLAGS:-none}"

# Check if OpenSSL is already installed with the correct version
if [ -x "${INSTALL_PREFIX}/bin/openssl" ]; then
    INSTALLED_VERSION=$("${INSTALL_PREFIX}/bin/openssl" version 2>/dev/null | awk '{print $2}')
    if [ "$INSTALLED_VERSION" = "$VERSION" ]; then
        echo "=== OpenSSL ${VERSION} is already installed ==="
        echo "Skipping installation. To reinstall, remove ${INSTALL_PREFIX}/bin/openssl first."
        exit 0
    else
        echo "Found existing OpenSSL version: ${INSTALLED_VERSION:-unknown}"
        echo "Will upgrade to version ${VERSION}"
    fi
fi

# Download source
wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"

# Extract
tar -xf "$ARCHIVE"
cd "openssl-${VERSION}" || {
    echo "Error: Failed to enter directory openssl-${VERSION}"
    exit 1
}

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
NCPUS=$(nproc 2>/dev/null || echo 1)
echo "Building with ${NCPUS} parallel jobs..."
make -j"${NCPUS}"

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
cd .. || {
    echo "Warning: Failed to return to parent directory"
}
rm -rf "openssl-${VERSION}" "$ARCHIVE"

echo ""
echo "OpenSSL ${VERSION} installed to ${INSTALL_PREFIX}"
echo "To use it, ensure ${INSTALL_PREFIX}/bin is in your PATH"

# Add to bashrc only if not already present
if ! grep -q "export PATH=${INSTALL_PREFIX}/bin:" ~/.bashrc 2>/dev/null; then
    echo "export PATH=${INSTALL_PREFIX}/bin:\$PATH" >> ~/.bashrc
    echo "Added PATH to ~/.bashrc"
fi

if ! grep -q "export LD_LIBRARY_PATH=${LIBDIR}:" ~/.bashrc 2>/dev/null; then
    echo "export LD_LIBRARY_PATH=${LIBDIR}:\$LD_LIBRARY_PATH" >> ~/.bashrc
    echo "Added LD_LIBRARY_PATH to ~/.bashrc"
fi