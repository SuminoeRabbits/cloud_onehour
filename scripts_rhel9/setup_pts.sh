#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/dnf_utils.sh"

EL_VER=$(get_el_version)
OS_ID=$(. /etc/os-release && echo "${ID:-unknown}")

USE_REMI_PHP81=1
if [ "$EL_VER" -ge 10 ] 2>/dev/null; then
    USE_REMI_PHP81=0
fi

dnf_install() {
    wait_for_dnf_lock
    sudo dnf -y install "$@"
}

repo_has_package() {
    local pkg="$1"
    sudo dnf -q list --available "$pkg" >/dev/null 2>&1 || sudo dnf -q repoquery "$pkg" >/dev/null 2>&1
}

install_required_from_candidates() {
    local logical_name="$1"
    local candidates_csv="$2"
    local candidate

    IFS=',' read -r -a candidates <<< "$candidates_csv"
    for candidate in "${candidates[@]}"; do
        if repo_has_package "$candidate"; then
            echo "[OK] ${logical_name}: using package '${candidate}'"
            dnf_install "$candidate"
            return 0
        fi
    done

    echo "[ERROR] Required package '${logical_name}' not found in enabled repositories."
    echo "[ERROR] Tried candidates: ${candidates_csv}"
    sudo dnf repolist --enabled || true
    exit 1
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

# 1.5. Install PHP runtime
echo "=== Step 1.5: Installing PHP runtime ==="

# Check current PHP version
CURRENT_PHP_VERSION=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || echo "none")
echo "Current PHP version: $CURRENT_PHP_VERSION"

if [[ "$CURRENT_PHP_VERSION" != "8.1" ]]; then
    if [ "$USE_REMI_PHP81" -eq 1 ]; then
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
        install_required_from_candidates "php-cli" "php-cli"
        install_required_from_candidates "php-xml" "php-xml"
        install_required_from_candidates "php-gd" "php-gd"
        install_required_from_candidates "php-curl" "php-curl"
        install_required_from_candidates "unzip" "unzip"
    else
        echo "[INFO] EL${EL_VER}: using distro PHP packages (skip Remi PHP 8.1)."
        install_required_from_candidates "php" "php"
        install_required_from_candidates "php-cli" "php-cli"
        install_required_from_candidates "php-xml" "php-xml"
        install_required_from_candidates "php-gd" "php-gd"
        install_required_from_candidates "php-curl" "php-curl"
        install_required_from_candidates "unzip" "unzip"
    fi
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
for pkg in $BUILD_DEPS $LIB_DEPS; do
    install_required_from_candidates "$pkg" "$pkg"
done

# 2. Install PTS
echo "=== Step 2: Installing Phoronix Test Suite ${VERSION} ==="
wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
tar -xf "$ARCHIVE" -C "$tmpdir"
sudo mkdir -p "$INSTALL_DIR"
sudo cp -R "$tmpdir/phoronix-test-suite/"* "$INSTALL_DIR"

# Create launcher with forced user-local installation path
sudo tee "$LAUNCHER" >/dev/null <<'EOF'
#!/usr/bin/env bash
# Force PTS to use user home directory instead of system-wide /var/lib/
export PTS_USER_PATH="$HOME/.phoronix-test-suite"
exec /opt/phoronix-test-suite/phoronix-test-suite "$@"
EOF
sudo chmod +x "$LAUNCHER"
sudo chown -R "$(whoami):$(whoami)" "$INSTALL_DIR"
rm -f "$ARCHIVE"

# 3. Configure batch mode
echo "=== Step 3: Configuring batch mode ==="
# Configure batch mode for the user who will run benchmarks.
# If setup is executed as root in OCI-like environments, prefer 'opc'.
TARGET_USER="$(id -un)"
if [ "$(id -u)" -eq 0 ]; then
    if id -u opc >/dev/null 2>&1; then
        TARGET_USER="opc"
    elif [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
        TARGET_USER="${SUDO_USER}"
    else
        TARGET_USER="root"
    fi
fi

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
if [ -z "$TARGET_HOME" ]; then
    TARGET_HOME="$HOME"
fi

echo "Configuring PTS batch mode for user: ${TARGET_USER}"
if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ]; then
    su - "$TARGET_USER" -c "export PTS_USER_PATH=\"$TARGET_HOME/.phoronix-test-suite\"; printf 'Y\\nN\\nN\\nN\\nN\\nN\\nY\\n' | '$LAUNCHER' batch-setup"
else
    export PTS_USER_PATH="$TARGET_HOME/.phoronix-test-suite"
    printf "Y\nN\nN\nN\nN\nN\nY\n" | "$LAUNCHER" batch-setup
fi

USER_CONFIG="$TARGET_HOME/.phoronix-test-suite/user-config.xml"
if [ -f "$USER_CONFIG" ]; then
    sed -i 's|<UploadResults>TRUE</UploadResults>|<UploadResults>FALSE</UploadResults>|g' "$USER_CONFIG"
fi

echo "=== Step 4: Verifying installation and runtime ==="
echo "Launcher: $LAUNCHER"
echo "Target user: $TARGET_USER"
echo "Target home: $TARGET_HOME"
echo "Expected PTS_USER_PATH: $TARGET_HOME/.phoronix-test-suite"

if [ ! -x "$LAUNCHER" ]; then
    echo "[ERROR] Launcher is missing or not executable: $LAUNCHER"
    exit 1
fi

if [ ! -d "$INSTALL_DIR" ]; then
    echo "[ERROR] Install directory is missing: $INSTALL_DIR"
    exit 1
fi

VERIFY_CMD="export PTS_USER_PATH=\"$TARGET_HOME/.phoronix-test-suite\"; test -d \"$PTS_USER_PATH\""
if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ]; then
    su - "$TARGET_USER" -c "$VERIFY_CMD"
else
    export PTS_USER_PATH="$TARGET_HOME/.phoronix-test-suite"
    test -d "$PTS_USER_PATH"
fi

VERSION_CMD="export PTS_USER_PATH=\"$TARGET_HOME/.phoronix-test-suite\"; '$LAUNCHER' --v >/dev/null 2>&1 || '$LAUNCHER' version"
if [ "$(id -u)" -eq 0 ] && [ "$TARGET_USER" != "root" ]; then
    su - "$TARGET_USER" -c "$VERSION_CMD"
else
    eval "$VERSION_CMD"
fi

echo "=== Setup completed successfully ==="
"$LAUNCHER" version
