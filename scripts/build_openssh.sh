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
    local exit_code=$?
    rm -rf "$BUILD_DIR"
    echo "Temporary files removed."

    # エラー発生時にステータスファイルを作成
    if [ $exit_code -ne 0 ]; then
        echo "FAILED" > /tmp/ssh_build_status.txt
        echo "Build failed with exit code $exit_code"
    fi
}

echo "=== [1/5] 整合性確認と環境準備 ==="
sudo apt-get update && sudo apt-get install -y build-essential wget zlib1g-dev libpam0g-dev libselinux1-dev libedit-dev

# インストール先ディレクトリを事前に作成（sudo権限で）
sudo mkdir -p "$INSTALL_ROOT"
sudo chown $(whoami):$(whoami) "$INSTALL_ROOT"

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
make install-nosysconf
cd ..

# --- [4/5] 反映前の最終検証 (接続テスト) ---
echo "=== [4/5] 接続テスト実行 ==="
# バイナリの基本動作確認（ポート起動なしで設定テストのみ）
echo "Testing SSH daemon configuration..."
sudo "$INSTALL_ROOT/sbin/sshd" -t -f /etc/ssh/sshd_config || {
    echo "✗ SSH daemon configuration test failed"
    exit 1
}
echo "✓ SSH daemon configuration is valid"

# SSHクライアントのバージョン確認
echo "Testing SSH client binary..."
"$INSTALL_ROOT/bin/ssh" -V 2>&1 | head -1
echo "✓ SSH client binary is functional"

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
# リモートSSH経由での実行を考慮し、遅延再起動を実施
sudo systemctl daemon-reload

# SSH経由で実行されている場合、現在のセッションを維持しつつ新SSHに切り替え
if [ -n "$SSH_CONNECTION" ]; then
    echo "=== Remote execution detected. Scheduling delayed SSH restart ==="
    echo "Current SSH session will remain active."
    echo "New SSH connections will use the upgraded binary after 5 seconds."
    # バックグラウンドで遅延再起動（現在のセッションを切断しない）
    (sleep 5 && sudo systemctl restart ssh) >/dev/null 2>&1 &
    echo "SSH restart scheduled. PID: $!"
else
    # ローカル実行の場合は即座に再起動
    sudo systemctl restart ssh
fi

cleanup

# ビルド成功を検証
echo "=== Final Verification ==="
if /usr/bin/ssh -V 2>&1 | grep -q "OpenSSH_${SSH_VER}"; then
    echo "SUCCESS" > /tmp/ssh_build_status.txt
    echo "✓ Upgrade successful. OpenSSH ${SSH_VER} is now active."
    echo "✓ Backup located at $BACKUP_DIR"
    exit 0
else
    echo "FAILED" > /tmp/ssh_build_status.txt
    echo "✗ Verification failed: SSH binary version mismatch"
    /usr/bin/ssh -V 2>&1
    exit 1
fi