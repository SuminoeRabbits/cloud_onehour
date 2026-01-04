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
#!/bin/bash

# 1. アーキテクチャの取得
ARCH=$(uname -m)
OS_ID=$(lsb_release -is)
VERSION_ID=$(lsb_release -rs)

echo "--- System Check ---"
echo "Architecture: $ARCH"
echo "OS: $OS_ID $VERSION_ID"
echo "--------------------"

# 2. x86_64 の場合のみ実行
if [ "$ARCH" = "x86_64" ]; then
    echo "[Target: x86_64] Starting installation of NASM and YASM..."

    # aptの更新
    sudo apt-get update -y

    # --- YASM のインストール (リポジトリから) ---
    echo "Installing YASM via apt..."
    sudo apt-get install yasm -y

    # --- NASM のインストール (公式サイトから .deb) ---
    echo "Installing latest NASM from official site..."
    
    # 2026年現在の安定版バージョンを指定 (必要に応じて書き換えてください)
    NASM_VER="2.16.03"
    NASM_DEB="nasm_${NASM_VER}-1_amd64.deb"
    NASM_URL="https://www.nasm.us/pub/nasm/releasebuilds/${NASM_VER}/linux/${NASM_DEB}"

    cd /tmp
    echo "Downloading NASM $NASM_VER..."
    wget -q $NASM_URL

    if [ -f "$NASM_DEB" ]; then
        sudo dpkg -i $NASM_DEB
        # 依存関係の解決（万が一のため）
        sudo apt-get install -f -y
        rm $NASM_DEB
    else
        echo "Error: Failed to download NASM .deb"
        exit 1
    fi

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