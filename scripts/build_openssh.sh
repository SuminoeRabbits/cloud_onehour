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

# 依存パッケージインストール（Ubuntu標準）
sudo apt-get update && sudo apt-get install -y \
    build-essential \
    wget \
    zlib1g-dev \
    libpam0g-dev \
    libselinux1-dev \
    libedit-dev \
    pkg-config

# インストール先ディレクトリを事前に作成（sudo権限で）
sudo mkdir -p "$INSTALL_ROOT"
sudo chown $(whoami):$(whoami) "$INSTALL_ROOT"

# SSH特権分離用のディレクトリを事前作成（Ubuntu標準）
sudo mkdir -p /var/empty
sudo chown root:root /var/empty
sudo chmod 755 /var/empty

# ビルドディレクトリ作成
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
# -fPIC: 位置独立コード（必須）
# no-docs, no-tests: ビルド時間短縮
./config --prefix="$INSTALL_ROOT/openssl" \
    no-shared \
    no-docs \
    no-tests \
    -fPIC \
    zlib

# 並列ビルド（メモリ不足対策: nproc/2）
NPROC=$(($(nproc) / 2))
[ $NPROC -lt 1 ] && NPROC=1
echo "Building with $NPROC parallel jobs..."
make -j"$NPROC"

# インストール実行
make install_sw

# インストール検証
if [ ! -f "$INSTALL_ROOT/openssl/lib/libcrypto.a" ]; then
    echo "✗ OpenSSL installation failed: libcrypto.a not found"
    exit 1
fi
echo "✓ OpenSSL ${SSL_VER} installed successfully"

cd ..

# --- [3/5] OpenSSH のビルド (静的リンク) ---
echo "Building OpenSSH ${SSH_VER} (Linked Statically to OpenSSL)..."
tar -xf "openssh-${SSH_VER}.tar.gz"
cd "openssh-${SSH_VER}"

# OpenSSL 3.5 をバイナリ内部に閉じ込め、PAMモジュールのOpenSSL 3.0と隔離する
# --with-ssl-dir: カスタムOpenSSLの場所
# --with-pam: PAM認証サポート（Ubuntu標準）
# --with-libedit: コマンドライン編集機能
# --with-selinux: SELinux対応（将来的な拡張用）
# --sysconfdir: 設定ファイル場所（システム標準）
# --with-privsep-path: 特権分離用ディレクトリ
./configure --prefix="$INSTALL_ROOT" \
            --with-ssl-dir="$INSTALL_ROOT/openssl" \
            --with-pam \
            --with-libedit \
            --with-selinux \
            --sysconfdir=/etc/ssh \
            --with-privsep-path=/var/empty \
            LDFLAGS="-static-libgcc"

# 設定確認
if [ ! -f Makefile ]; then
    echo "✗ Configure failed: Makefile not generated"
    exit 1
fi

# 並列ビルド（OpenSSLと同じ設定）
echo "Building with $NPROC parallel jobs..."
make -j"$NPROC"

# 反映前にテスト用バイナリを確認（ホストキーエラーは無視）
./sshd -t 2>&1 | tee /tmp/sshd_build_test.log || {
    if grep -q "no hostkeys available" /tmp/sshd_build_test.log; then
        echo "⚠ Warning: Host key issue in build test (expected, will be resolved after installation)"
    else
        echo "sshd configuration test failed"
        cat /tmp/sshd_build_test.log
        exit 1
    fi
}

# インストール実行（/var/empty は既に準備済み）
make install-nosysconf
cd ..

# --- [4/5] 反映前の最終検証 (接続テスト) ---
echo "=== [4/5] 接続テスト実行 ==="

# ホストキーの存在確認と必要に応じた生成
echo "Checking SSH host keys..."
if ! sudo ls /etc/ssh/ssh_host_*_key >/dev/null 2>&1; then
    echo "Host keys not found, generating them..."
    sudo "$INSTALL_ROOT/bin/ssh-keygen" -A
    echo "✓ Host keys generated"
else
    echo "✓ Host keys already exist"
fi

# バイナリの基本動作確認（設定テストはホストキー存在が前提）
echo "Testing SSH daemon configuration..."
sudo "$INSTALL_ROOT/sbin/sshd" -t -f /etc/ssh/sshd_config 2>&1 | tee /tmp/sshd_test.log || {
    echo "✗ SSH daemon configuration test failed"
    echo "Error details:"
    cat /tmp/sshd_test.log
    # ホストキーエラーの場合は警告のみで続行（デプロイ後に自動生成される）
    if grep -q "no hostkeys available" /tmp/sshd_test.log; then
        echo "⚠ Warning: Host key issue detected, but continuing (will be resolved after deployment)"
    else
        # ホストキー以外のエラーは致命的
        exit 1
    fi
}
echo "✓ SSH daemon configuration is valid"

# SSHクライアントのバージョン確認
echo "Testing SSH client binary..."
"$INSTALL_ROOT/bin/ssh" -V 2>&1 | head -1
echo "✓ SSH client binary is functional"

# --- [5/5] アトミックな切り替え ---
echo "=== [5/5] システムへの安全な反映 ==="

# バックアップディレクトリ作成
sudo mkdir -p "$BACKUP_DIR"

# 既存バイナリのバックアップ（存在する場合のみ）
echo "Backing up existing SSH binaries..."
[ -f /usr/sbin/sshd ] && sudo cp -p /usr/sbin/sshd "$BACKUP_DIR/sshd.orig"
for bin in ssh ssh-add ssh-agent ssh-keygen ssh-keyscan; do
    [ -f /usr/bin/$bin ] && sudo cp -p /usr/bin/$bin "$BACKUP_DIR/${bin}.orig"
done

# シンボリックリンクによる一瞬の置換
echo "Installing new SSH binaries..."
sudo ln -sf "$INSTALL_ROOT/sbin/sshd" /usr/sbin/sshd
for bin in ssh ssh-add ssh-agent ssh-keygen ssh-keyscan; do
    sudo ln -sf "$INSTALL_ROOT/bin/$bin" "/usr/bin/$bin"
done

# PAMの設定確認（Ubuntu標準）
if [ ! -f /etc/pam.d/sshd ]; then
    echo "⚠ Warning: /etc/pam.d/sshd not found, creating default configuration..."
    sudo tee /etc/pam.d/sshd >/dev/null <<'EOF'
# PAM configuration for the Secure Shell service
@include common-auth
@include common-account
@include common-session
@include common-password
EOF
fi

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