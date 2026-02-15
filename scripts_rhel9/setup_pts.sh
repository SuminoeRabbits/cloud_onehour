#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/dnf_utils.sh"

EL_VER=$(get_el_version)

dnf_install() {
    wait_for_dnf_lock
    sudo dnf -y install "$@"
}

VERSION="10.8.4"
ARCHIVE="phoronix-test-suite-${VERSION}.tar.gz"
DOWNLOAD_URL="https://phoronix-test-suite.com/releases/${ARCHIVE}"
INSTALL_DIR="/opt/phoronix-test-suite"
LAUNCHER="/usr/local/bin/phoronix-test-suite"

echo "=== Phoronix Test Suite Setup (EL${EL_VER}) ==="
echo "Target version: ${VERSION}"
echo ""

# 1. Uninstall existing PTS and clean all cache
echo "=== Step 1: Uninstalling existing PTS and cleaning cache ==="
sudo rm -rf "$INSTALL_DIR"
sudo rm -f "$LAUNCHER"
rm -rf "$HOME/.phoronix-test-suite"
echo "[OK] Uninstall and cleanup completed"

# 1.5. Install PHP 8.1 via Remi Repo (RHEL9 target)
echo "=== Step 1.5: Installing PHP 8.1 via Remi ==="

# Check current PHP version
CURRENT_PHP_VERSION=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || echo "none")
echo "Current PHP version: $CURRENT_PHP_VERSION"

if [[ "$CURRENT_PHP_VERSION" != "8.1" ]]; then
    echo "Configuring Remi repo for PHP 8.1..."
    # epel and remi-release should be installed by setup_init.sh, but verify
    if ! rpm -q remi-release >/dev/null 2>&1; then
        sudo dnf install -y "https://rpms.remirepo.net/enterprise/remi-release-${EL_VER}.rpm"
    fi
    if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
        sudo dnf module reset php -y 2>/dev/null || true
        sudo dnf module enable php:remi-8.1 -y 2>/dev/null || \
            echo "[INFO] EL${EL_VER}: dnf module not available for PHP, using default Remi config"
    else
        sudo dnf module reset php -y
        sudo dnf module enable php:remi-8.1 -y
    fi
    
    echo "Installing PHP 8.1 packages..."
    # Note: php-json is not needed on PHP 8.0+ (JSON is bundled in core)
    dnf_install php-cli php-xml php-gd php-curl unzip
else
    echo "[OK] PHP 8.1 is already installed"
fi

# 1.6. Suppress PHP warnings
SUPPRESS_SCRIPT="$SCRIPT_DIR/suppress_php_warnings.sh"
if [ -f "$SUPPRESS_SCRIPT" ]; then
    bash "$SUPPRESS_SCRIPT"
fi

# 1.7. Install build dependencies
echo "=== Step 1.7: Installing build dependencies ==="
# RHEL9 equivalents for build-essential and others
BUILD_DEPS="pkgconf-pkg-config autoconf automake libtool cmake git"
# Development Tools group is often safer for build-essential equivalent
sudo dnf -y groupinstall "Development Tools"

# Libraries and headers
LIB_DEPS="flex bison bc elfutils-libelf-devel openssl-devel zlib-devel bzip2-devel readline-devel sqlite-devel ncurses-devel libffi-devel xz-devel"

echo "Installing: $BUILD_DEPS $LIB_DEPS"
dnf_install $BUILD_DEPS $LIB_DEPS

# 2. Install PTS
echo "=== Step 2: Installing Phoronix Test Suite ${VERSION} ==="
wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
tar -xf "$ARCHIVE" -C "$tmpdir"
sudo mkdir -p "$INSTALL_DIR"
sudo cp -R "$tmpdir/phoronix-test-suite/"* "$INSTALL_DIR"

# Create launcher
sudo tee "$LAUNCHER" >/dev/null <<'EOF'
#!/usr/bin/env bash
exec /opt/phoronix-test-suite/phoronix-test-suite "$@"
EOF
sudo chmod +x "$LAUNCHER"
sudo chown -R "$(whoami):$(whoami)" "$INSTALL_DIR"
rm -f "$ARCHIVE"

# 3. Configure batch mode
echo "=== Step 3: Configuring batch mode ==="
# Force PTS to use user home directory instead of system-wide /var/lib/
export PTS_USER_PATH="$HOME/.phoronix-test-suite"
printf "Y\nN\nN\nN\nN\nN\nY\n" | "$LAUNCHER" batch-setup

USER_CONFIG="$HOME/.phoronix-test-suite/user-config.xml"
if [ -f "$USER_CONFIG" ]; then
    sed -i 's|<UploadResults>TRUE</UploadResults>|<UploadResults>FALSE</UploadResults>|g' "$USER_CONFIG"
fi

echo "=== Setup completed successfully ==="
"$LAUNCHER" version
