#!/bin/bash

# Stop on error
set -e

# install various tools depends on your local needs.
sudo apt-get -y update
sudo apt-get -y install bc
sudo apt-get -y install uuid-dev libxml2-dev pkg-config libcurl4-openssl-dev libjansson-dev
# install cpupower
sudo apt-get -y install linux-tools-common linux-tools-$(uname -r)
sudo apt-get -y install sysstat htop aria2 curl
sudo apt-get -y install flex bison libssl-dev libelf-dev libevent-dev
sudo apt-get install -y python3-tabulate libexpat1-dev
sudo apt-get install -y cl-ppcre libpcre3-dev
sudo apt-get -y install p7zip-full
sudo apt-get install -y libc6-dev numactl
sudo apt-get install -y gawk
# libpng (required by pts/avifenc libavif cmake build; FindPNG requires >= 1.6.32)
sudo apt-get install -y libpng-dev
# libsharpyuv (required by pts/avifenc; split from libwebp >= 1.3.0)
# On Ubuntu 24.04+, libwebp-dev depends on libsharpyuv-dev automatically.
# On Ubuntu 22.04, libwebp-dev bundles sharpyuv headers.
sudo apt-get install -y libwebp-dev
# libyuv (required by pts/avifenc libavif cmake build)
# Note: libyuv-dev does NOT ship a .pc file; create one so pkg-config can find it.
sudo apt-get install -y libyuv-dev
if ! pkg-config --exists libyuv 2>/dev/null; then
    ARCH_TRIPLE=$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo "$(uname -m)-linux-gnu")
    LIBYUV_LIBDIR="/usr/lib/${ARCH_TRIPLE}"
    LIBYUV_VER=$(dpkg-query -W -f='${Version}' libyuv-dev 2>/dev/null | grep -oP '^\d+' || echo "0")
    sudo mkdir -p "${LIBYUV_LIBDIR}/pkgconfig"
    sudo tee "${LIBYUV_LIBDIR}/pkgconfig/libyuv.pc" >/dev/null <<EOF
prefix=/usr
exec_prefix=\${prefix}
libdir=${LIBYUV_LIBDIR}
includedir=\${prefix}/include

Name: libyuv
Description: YUV conversion and scaling library
Version: ${LIBYUV_VER}
Libs: -L\${libdir} -lyuv
Cflags: -I\${includedir}
EOF
    echo "[OK] Created ${LIBYUV_LIBDIR}/pkgconfig/libyuv.pc"
fi

# 1. Architecture Detection
ARCH=$(uname -m)
OS_ID=$(lsb_release -is)
VERSION_ID=$(lsb_release -rs)

echo "--- System Check ---"
echo "Architecture: $ARCH"
echo "OS: $OS_ID $VERSION_ID"
echo "--------------------"

# 2. NASM/YASM tools (required by ffmpeg/x264 build path in PTS)
echo "[Target: $ARCH] Installing NASM and YASM..."

# Update repositories
sudo apt-get update -y

# Try bulk install first, then retry per package for distro differences
if ! sudo apt-get install -y nasm yasm; then
    echo "[WARN] Bulk install (nasm yasm) failed. Retrying individually..."
    sudo apt-get install -y nasm || echo "[WARN] nasm package is not available on this system"
    sudo apt-get install -y yasm || echo "[WARN] yasm package is not available on this system"
fi

echo "--------------------------------------"
echo "NASM/YASM installation check"
if command -v nasm >/dev/null 2>&1; then
    nasm -v
else
    echo "[WARN] nasm is not installed"
fi

if command -v yasm >/dev/null 2>&1; then
    yasm --version | head -n 1
else
    echo "[WARN] yasm is not installed"
fi
echo "--------------------------------------"
