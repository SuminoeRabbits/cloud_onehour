#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/os_guard.sh"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/apt_utils.sh"

apt_get() {
    wait_for_apt_lock
    sudo apt-get -o Dpkg::Lock::Timeout=300 "$@"
}

VERSION="10.8.4"
ARCHIVE="phoronix-test-suite-${VERSION}.tar.gz"
DOWNLOAD_URL="https://phoronix-test-suite.com/releases/${ARCHIVE}"
INSTALL_DIR="/opt/phoronix-test-suite"
LAUNCHER="/usr/local/bin/phoronix-test-suite"

echo "=== Phoronix Test Suite Setup ==="
echo "Target version: ${VERSION}"
echo ""

# 1. Uninstall existing PTS and clean all cache
echo "=== Step 1: Uninstalling existing PTS and cleaning cache ==="

# Remove installation directory
if [ -d "$INSTALL_DIR" ]; then
    echo "Removing installation directory: $INSTALL_DIR"
    sudo rm -rf "$INSTALL_DIR"
fi

# Remove launcher
if [ -f "$LAUNCHER" ]; then
    echo "Removing launcher: $LAUNCHER"
    sudo rm -f "$LAUNCHER"
fi

# Remove PTS cache and data directories
PTS_HOME_DIR="$HOME/.phoronix-test-suite"
if [ -d "$PTS_HOME_DIR" ]; then
    echo "Removing PTS home directory and cache: $PTS_HOME_DIR"
    rm -rf "$PTS_HOME_DIR"
fi

echo "[OK] Uninstall and cleanup completed"
echo ""

# 1.5. Install PHP from Ubuntu 26.04 repositories
echo "=== Step 1.5: Installing system PHP ==="

# Detect Ubuntu version
UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "unknown")
UBUNTU_CODENAME=$(lsb_release -cs 2>/dev/null || echo "unknown")
CURRENT_PHP_VERSION=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || echo "none")

echo "Ubuntu version: $UBUNTU_VERSION ($UBUNTU_CODENAME)"
echo "Current PHP version: $CURRENT_PHP_VERSION"

if [[ "$UBUNTU_VERSION" != "26.04" ]]; then
    echo "[ERROR] setup_pts.sh in scripts_ubuntu2604 supports Ubuntu 26.04 only"
    exit 1
fi

echo "Ubuntu 26.04: installing system default PHP packages"
apt_get update
if ! apt_get install -y php-cli php-xml php-gd php-curl unzip; then
    echo "[ERROR] Failed to install PHP on Ubuntu 26.04"
    exit 1
fi

echo "[OK] PHP installation completed"
echo "Installed PHP version: $(php --version | head -1)"
echo ""

# 1.6. Suppress PHP warnings for compatibility
echo "=== Step 1.6: Suppressing PHP warnings ==="

# Get script directory to find suppress_php_warnings.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUPPRESS_SCRIPT="$SCRIPT_DIR/suppress_php_warnings.sh"

if [ -f "$SUPPRESS_SCRIPT" ]; then
    echo "Running suppress_php_warnings.sh to prevent PHP compatibility issues..."
    bash "$SUPPRESS_SCRIPT"
    if [ $? -eq 0 ]; then
        echo "[OK] PHP warnings suppressed successfully"
    else
        echo "[WARN] Failed to suppress PHP warnings, continuing anyway..."
    fi
else
    echo "[WARN] suppress_php_warnings.sh not found at: $SUPPRESS_SCRIPT"
    echo "[WARN] PHP warnings may appear during PTS execution"
fi

echo ""

# 1.7. Install build dependencies for PTS benchmarks
echo "=== Step 1.7: Installing build dependencies ==="
echo "Installing essential build tools for benchmark compilation..."

# Essential build tools required by most PTS benchmarks.
# ninja-build is required by the Ninja variant of pts/build-llvm.
BUILD_DEPS="build-essential pkg-config autoconf automake libtool cmake ninja-build git libgtest-dev libgmock-dev"

# Linux kernel build requirements
KERNEL_DEPS="flex bison bc libelf-dev libssl-dev"

# GCC source build requirements (pts/build-gcc)
GCC_BUILD_DEPS="libgmp-dev libmpfr-dev libmpc-dev"

# Additional common dependencies
EXTRA_DEPS="libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev"
EXTRA_DEPS="$EXTRA_DEPS libncurses-dev libffi-dev liblzma-dev"
EXTRA_DEPS="$EXTRA_DEPS libuv1-dev libhwloc-dev"

echo "Installing: $BUILD_DEPS $KERNEL_DEPS $GCC_BUILD_DEPS $EXTRA_DEPS"
if apt_get install -y $BUILD_DEPS $KERNEL_DEPS $GCC_BUILD_DEPS $EXTRA_DEPS; then
    echo "[OK] Build dependencies installed"
else
    echo "[WARN] Some dependencies failed to install, continuing anyway..."
fi

echo ""

# 2. Install PTS
echo "=== Step 2: Installing Phoronix Test Suite ${VERSION} ==="

# Download archive
echo "Downloading PTS ${VERSION}..."
wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"

# Extract and install
echo "Extracting archive..."
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
tar -xf "$ARCHIVE" -C "$tmpdir"

echo "Installing to $INSTALL_DIR..."
sudo mkdir -p "$INSTALL_DIR"
sudo cp -R "$tmpdir/phoronix-test-suite/"* "$INSTALL_DIR"

# Create launcher with forced user-local installation path
echo "Creating launcher at $LAUNCHER..."
sudo tee "$LAUNCHER" >/dev/null <<'EOF'
#!/usr/bin/env bash
# Force PTS to use user home directory instead of system-wide /var/lib/
export PTS_USER_PATH="$HOME/.phoronix-test-suite"
exec /opt/phoronix-test-suite/phoronix-test-suite "$@"
EOF
sudo chmod +x "$LAUNCHER"

# Fix permissions for current user
echo "Setting permissions..."
sudo chown -R $USER:$USER "$INSTALL_DIR"
sudo chmod -R u+w "$INSTALL_DIR"

# Cleanup
rm -f "$ARCHIVE"

echo "[OK] Installation completed"
echo ""

# Verify installation
echo "=== Verifying installation ==="

if [[ ! -x "$INSTALL_DIR/phoronix-test-suite" ]]; then
    echo "[ERROR] Installation verification failed - executable not found"
    exit 1
fi

if [[ ! -x "$LAUNCHER" ]]; then
    echo "[ERROR] Installation verification failed - launcher not found"
    exit 1
fi

if ! "$LAUNCHER" version >/dev/null 2>&1; then
    echo "[ERROR] Installation verification failed - version command failed"
    exit 1
fi

echo ""

# 3. Configure batch mode
echo "=== Step 3: Configuring batch mode ==="
echo "Setting up batch-mode configuration for automated testing..."

# Run batch-setup with automated responses
# Force PTS to use user home directory instead of system-wide /var/lib/ (for root user)
export PTS_USER_PATH="$HOME/.phoronix-test-suite"
# Y - Save test results when in batch mode
# N - Open the web browser automatically
# N - Auto upload to OpenBenchmarking.org
# N - Prompt for test identifier
# N - Prompt for test description
# N - Prompt for saved results file-name
# Y - Run all test options
printf "Y\nN\nN\nN\nN\nN\nY\n" | "$LAUNCHER" batch-setup

# Force disable UploadResults to prevent accidental uploads
USER_CONFIG="$HOME/.phoronix-test-suite/user-config.xml"
if [ -f "$USER_CONFIG" ]; then
    echo "Enforcing UploadResults=FALSE in user-config.xml..."
    sed -i 's|<UploadResults>TRUE</UploadResults>|<UploadResults>FALSE</UploadResults>|g' "$USER_CONFIG"
fi

echo "[OK] Batch mode configured"
echo ""

echo "=== Step 4: Verifying installation and runtime ==="
# Configure verification target user similar to EL script behavior.
TARGET_USER="$(id -un)"
if [ "$(id -u)" -eq 0 ]; then
    if id -u ubuntu >/dev/null 2>&1; then
        TARGET_USER="ubuntu"
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
echo "PHP version: $(php --version | head -1)"
"$LAUNCHER" version
echo ""
