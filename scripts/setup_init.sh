#!/bin/bash

# Stop on error
set -e

# install various tools depends on your local needs.
sudo apt-get -y update
sudo apt-get -y install bc
sudo apt-get -y install uuid-dev libxml2-dev
# install cpupower
sudo apt-get -y install linux-tools-common linux-tools-$(uname -r)
sudo apt-get -y install sysstat htop aria2 curl
sudo apt-get -y install flex bison libssl-dev libelf-dev
# 1. Architecture Detection
ARCH=$(uname -m)
OS_ID=$(lsb_release -is)
VERSION_ID=$(lsb_release -rs)

echo "--- System Check ---"
echo "Architecture: $ARCH"
echo "OS: $OS_ID $VERSION_ID"
echo "--------------------"

# 2. x86_64 specific tools
if [ "$ARCH" = "x86_64" ]; then
    echo "[Target: x86_64] Starting installation of NASM and YASM..."

    # Update repositories
    sudo apt-get update -y

    # --- YASM & NASM Installation via apt ---
    # Ubuntu 24.04/25.04 repositories contain recent NASM versions (2.16+)
    # This is more robust than downloading specific .deb files which may disappear.
    echo "Installing YASM and NASM via apt..."
    sudo apt-get install -y yasm nasm

    echo "--------------------------------------"
    echo "Installation Complete!"
    nasm -v
    yasm --version | head -n 1
    echo "--------------------------------------"

else
    # 3. x86_64 以外（aarch64等）の場合
    echo "[Target: $ARCH] NASM/YASM are x86-specific tools. Skipping installation."
    echo "Note: GCC 14 will handle SVE/SVE2 optimizations for this architecture."
fi