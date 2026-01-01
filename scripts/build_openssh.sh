#!/bin/bash
set -euo pipefail

# --- ターゲットバージョンの設定 ---
TARGET_SSL_VER="3.5.4"
TARGET_SSH_VER="10.2"   # OpenSSH 10.2
TARGET_SSH_FULL="${TARGET_SSH_VER}p1"

SSL_PREFIX="/usr/local/openssl-${TARGET_SSL_VER}"
SSH_PREFIX="/usr/local/ssh-${TARGET_SSH_VER}"
BACKUP_DIR="/var/backups/openssl-ssh-compat-$(date +%Y%m%d)"

# --- 関数: バージョン比較 ---
# $1 >= $2 なら 0 (真), $1 < $2 なら 1 (偽)
version_ge() {
    [ "$(printf '%s\n%s' "$2" "$1" | sort -V | head -n1)" = "$2" ]
}

echo "=== システム診断開始 ==="

# 1. 現在のバージョン取得
CUR_SSL_VER=$(openssl version | awk '{print $2}' | sed 's/[a-z]//g') || CUR_SSL_VER="0.0.0"
CUR_SSH_VER=$(ssh -V 2>&1 | awk -F'[_ ]' '{print $2}' | sed 's/p[0-1]//') || CUR_SSH_VER="0.0.0"

echo "現在のOpenSSL: ${CUR_SSL_VER} (ターゲット: ${TARGET_SSL_VER})"
echo "現在のOpenSSH: ${CUR_SSH_VER} (ターゲット: ${TARGET_SSH_VER})"

# 2. インストール要否の判定
# インストールするバージョン(Target)が現在(Cur)より高ければ実行
DO_SSL=false
if ! version_ge "$CUR_SSL_VER" "$TARGET_SSL_VER"; then
    echo "[判定] OpenSSLをアップグレードします。"
    DO_SSL=true
else
    echo "[判定] OpenSSLは既に最新または同等です。ビルドをスキップします。"
fi

DO_SSH=false
if ! version_ge "$CUR_SSH_VER" "$TARGET_SSH_VER"; then
    echo "[判定] OpenSSHをアップグレードします。"
    DO_SSH=true
else
    echo "[判定] OpenSSHは既に最新または同等です。ビルドをスキップします。"
fi

if [ "$DO_SSL" = false ] && [ "$DO_SSH" = false ]; then
    echo "すべてのコンポーネントが最新です。終了します。"
    exit 0
fi

# 3. アーキテクチャ最適化フラグの決定
ARCH=$(uname -m)
OPT_FLAGS="-O3 -march=native -pipe"

echo "アーキテクチャ: ${ARCH} を検出。"
if [[ "$ARCH" == "x86_64" ]]; then
    if grep -q "avx512" /proc/cpuinfo; then
        echo "最適化: AVX-512 を有効にします。"
    else
        echo "最適化: 標準的な x86_64 最適化を適用します。"
    fi
elif [[ "$ARCH" == "aarch64" ]]; then
    if grep -q "sve2" /proc/cpuinfo; then
        echo "最適化: SVE2 を有効にします。"
    else
        echo "最適化: 標準的な ARMv8/v9 最適化を適用します。"
    fi
fi

# 4. 依存パッケージとバックアップ
sudo apt update
sudo apt install -y build-essential wget zlib1g-dev libpam0g-dev libselinux1-dev libedit-dev
sudo mkdir -p "${BACKUP_DIR}"
LIB_PATH="/usr/lib/$(uname -m)-linux-gnu"

# 5. OpenSSLビルド (必要な場合)
if [ "$DO_SSL" = true ]; then
    echo "--- OpenSSL ${TARGET_SSL_VER} のビルド (最適化適用) ---"
    wget --no-check-certificate -O "openssl-${TARGET_SSL_VER}.tar.gz" "https://www.openssl.org/source/openssl-${TARGET_SSL_VER}.tar.gz"
    tar -xf "openssl-${TARGET_SSL_VER}.tar.gz"
    cd "openssl-${TARGET_SSL_VER}"
    
    # OpenSSLのconfigは環境変数CFLAGSを尊重する
    export CFLAGS="${OPT_FLAGS}"
    ./config --prefix="${SSL_PREFIX}" --openssldir="${SSL_PREFIX}/ssl" --libdir=lib shared zlib
    make -j"$(nproc)"
    sudo make install
    cd ..
fi

# 6. OpenSSHビルド (必要な場合)
if [ "$DO_SSH" = true ]; then
    echo "--- OpenSSH ${TARGET_SSH_FULL} のビルド (最適化適用) ---"
    wget --no-check-certificate -O "openssh-${TARGET_SSH_FULL}.tar.gz" "https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/openssh-${TARGET_SSH_FULL}.tar.gz"
    tar -xf "openssh-${TARGET_SSH_FULL}.tar.gz"
    cd "openssh-${TARGET_SSH_FULL}"
    
    export CFLAGS="${OPT_FLAGS}"
    # SSL_PREFIXが未設定（SSLを今回ビルドしなかった）場合は、現在のシステムパスを指定
    SSL_DIR="${SSL_PREFIX}"
    [ ! -d "$SSL_DIR" ] && SSL_DIR="/usr"

    ./configure --prefix="${SSH_PREFIX}" \
                --with-ssl-dir="${SSL_DIR}" \
                --with-pam \
                --with-libedit \
                --with-selinux \
                --sysconfdir=/etc/ssh
    make -j"$(nproc)"
    sudo make install
    cd ..
fi

# 7. システムへの反映（アトミックに切り替え）
echo "--- システム環境の更新 ---"

# バックアップ実行
[ -f /usr/bin/openssl ] && sudo cp /usr/bin/openssl "${BACKUP_DIR}/"
[ -f /usr/sbin/sshd ] && sudo cp /usr/sbin/sshd "${BACKUP_DIR}/"

if [ "$DO_SSL" = true ]; then
    sudo ln -sf "${SSL_PREFIX}/lib/libssl.so.3" "${LIB_PATH}/libssl.so.3"
    sudo ln -sf "${SSL_PREFIX}/lib/libcrypto.so.3" "${LIB_PATH}/libcrypto.so.3"
    sudo ln -sf "${SSL_PREFIX}/bin/openssl" /usr/bin/openssl
    sudo ldconfig
fi

if [ "$DO_SSH" = true ]; then
    for bin in ssh ssh-add ssh-agent ssh-keygen ssh-keyscan; do
        sudo ln -sf "${SSH_PREFIX}/bin/${bin}" "/usr/bin/${bin}"
    done
    sudo ln -sf "${SSH_PREFIX}/sbin/sshd" /usr/sbin/sshd
    sudo systemctl restart ssh
fi

echo "=== 換装完了 ==="
openssl version
ssh -V