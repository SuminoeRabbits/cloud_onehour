#!/bin/bash
set -e

TARGET_VERSION="2.44"
THRESHOLD_VERSION="2.42"
PREFIX="/opt/binutils-${TARGET_VERSION}"
TOOLS=("as" "ld" "ld.gold" "nm" "objcopy" "objdump" "readelf" "strip" "ar" "ranlib" "size" "strings")

is_version_older_than_threshold() {
    [ "$1" = "$2" ] && return 1
    [ "$(printf "%s\n%s" "$1" "$2" | sort -V | head -n 1)" = "$1" ] && return 0 || return 1
}

if command -v as >/dev/null 2>&1; then
    CURRENT_VER=$(as --version | head -n 1 | grep -oP '\d+\.\d+(\.\d+)?' | head -n 1)
    if ! is_version_older_than_threshold "${CURRENT_VER}" "${THRESHOLD_VERSION}"; then
        echo "Binutils is already up to date: ${CURRENT_VER}"
        exit 0
    fi
fi

echo "Installing build dependencies..."
sudo dnf -y groupinstall "Development Tools"
sudo dnf -y install wget texinfo bison flex

echo "Building Binutils ${TARGET_VERSION} from source..."
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cd "$WORK_DIR"
wget "https://ftp.gnu.org/gnu/binutils/binutils-${TARGET_VERSION}.tar.gz"
tar -xf "binutils-${TARGET_VERSION}.tar.gz"
cd "binutils-${TARGET_VERSION}"
mkdir build && cd build
../configure --prefix="${PREFIX}" --disable-werror --enable-gold --enable-plugins --enable-threads
make -j"$(nproc)"
sudo make install

echo "Setting up symlinks..."
for tool in "${TOOLS[@]}"; do
    if [ -f "${PREFIX}/bin/${tool}" ]; then
        sudo ln -sf "${PREFIX}/bin/${tool}" "/usr/local/bin/${tool}"
    fi
done
as --version | head -1
