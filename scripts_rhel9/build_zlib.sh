#!/bin/bash
set -euo pipefail

# Check for RHEL 10 / Oracle Linux 10
if [ -f /etc/os-release ]; then
    . /etc/os-release
    if [[ "$VERSION_ID" == 10* ]] && [[ "$ID" =~ ^(rhel|ol)$ ]]; then
        echo "RHEL/Oracle Linux 10 detected. Using system default zlib (zlib-ng)."
        echo "Installing zlib-devel..."
        sudo dnf -y install zlib-devel
        exit 0
    fi
fi

VERSION="1.3.1"
ARCH=$(uname -m)
LIBSUBDIR=$([ "$ARCH" = "x86_64" ] && echo "lib64" || echo "lib")
INSTALL_PREFIX="/usr/local"
LIBDIR="${INSTALL_PREFIX}/${LIBSUBDIR}"

if [ -f "${INSTALL_PREFIX}/include/zlib.h" ] && grep -q "$VERSION" "${INSTALL_PREFIX}/include/zlib.h"; then
    echo "zlib $VERSION already installed."
    exit 0
fi

sudo dnf -y install make wget

WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cd "$WORK_DIR"

wget "https://www.zlib.net/zlib-${VERSION}.tar.gz"
tar -xzf "zlib-${VERSION}.tar.gz"
cd "zlib-${VERSION}"
export LDFLAGS="-Wl,-rpath,${LIBDIR}"
./configure --prefix="${INSTALL_PREFIX}" --libdir="${LIBDIR}"
make -j"$(nproc)"
sudo make install
sudo ldconfig
echo "zlib $VERSION installed to $LIBDIR"
