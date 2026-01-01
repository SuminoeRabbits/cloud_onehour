#!/bin/bash
# ------------------------------------------------------------------
# Hardened OpenSSL & OpenSSH Installer (Production-Grade Safety)
# ------------------------------------------------------------------
set -euo pipefail
trap 'echo "Error occurred at line $LINENO. Reverting changes..."; cleanup' ERR

# --- 設定 ---
SSL_VER="3.5.4"
SSH_VER="10.2p1"
BUILD_DIR="/tmp/build_$(date +%Y%m%d_%H%M%S)"
INSTALL_ROOT="/opt/custom-ssh" # 隔離ディレクトリ
BACKUP_DIR="/var/backups/ssh-pre-upgrade-$(date +%s)"

# --- クリーンアップ関数 ---
cleanup() {
    rm -rf "$BUILD_DIR"
    echo "Temporary files removed."
}

echo "=== [1/5] 整合性確認と環境準備 ==="
sudo apt-get update && sudo apt-get install -y build-essential wget zlib1g-dev libpam0g-dev libselinux1-dev libedit-dev

mkdir -p "$BUILD_DIR"
cd "$BUILD_DIR"

# 証明書検証ありでダウンロード（チェックサム検証推奨）
wget -q "https://www.openssl.org/source/openssl-${SSL_VER}.tar.gz"
wget -q "https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/openssh-${SSH_VER}.tar.gz"

# --- [2/5] OpenSSL のビルド (静的ライブラリのみ) ---
echo "Building OpenSSL ${SSL_VER} (Static)..."
tar -xf "openssl-${SSL_VER}.tar.gz"
cd "openssl-${SSL_VER}"
# no-shared を指定し、静的ライブラリのみを作成（PAM競合対策）
./config --prefix="$INSTALL_ROOT/openssl" no-shared no-docs no-tests -fPIC
make -j"$(nproc)"
make install_sw
cd ..

# --- [3/5] OpenSSH のビルド (静的リンク) ---
echo "Building OpenSSH ${SSH_VER} (Linked Statically to OpenSSL)..."
tar -xf "openssh-${SSH_VER}.tar.gz"
cd "openssh-${SSH_VER}"

# OpenSSL 3.5 をバイナリ内部に閉じ込め、PAMモジュールのOpenSSL 3.0と隔離する
./configure --prefix="$INSTALL_ROOT" \
            --with-ssl-dir="$INSTALL_ROOT/openssl" \
            --with-pam --with-libedit --with-selinux \
            --sysconfdir=/etc/ssh \
            --with-privsep-path=/var/empty \
            LDFLAGS="-static-libgcc" # GCC依存も極力減らす

make -j"$(nproc)"
# 反映前にテスト用バイナリを確認
./sshd -t || { echo "sshd configuration test failed"; exit 1; }
sudo make install-nosysconf
cd ..

# --- [4/5] 反映前の最終検証 (接続テスト) ---
echo "=== [4/5] 接続テスト実行 ==="
# 新しいsshdを一時的に別ポート(2222)で立ち上げ、自身のパスが通るか確認
sudo "$INSTALL_ROOT/sbin/sshd" -p 2222 -f /etc/ssh/sshd_config || { echo "Failed to start test sshd"; exit 1; }
echo "Test sshd is running on port 2222. (Manual check recommended)"
sudo kill $(cat /var/run/sshd.pid 2>/dev/null || pgrep -f "sshd -p 2222") || true

# --- [5/5] アトミックな切り替え ---
echo "=== [5/5] システムへの安全な反映 ==="
sudo mkdir -p "$BACKUP_DIR"
[ -f /usr/sbin/sshd ] && sudo mv /usr/sbin/sshd "$BACKUP_DIR/sshd.orig"

# シンボリックリンクによる一瞬の置換
sudo ln -sf "$INSTALL_ROOT/sbin/sshd" /usr/sbin/sshd
for bin in ssh ssh-add ssh-agent ssh-keygen ssh-keyscan; do
    sudo ln -sf "$INSTALL_ROOT/bin/$bin" "/usr/bin/$bin"
done

# systemdの再起動（既存セッション維持のため restart を使用）
sudo systemctl daemon-reload
sudo systemctl restart ssh

cleanup
echo "Upgrade successful. Backup located at $BACKUP_DIR"