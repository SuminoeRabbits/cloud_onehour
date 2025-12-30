#!/bin/bash
set -euo pipefail

VERSION="10.8.4"
ARCHIVE="phoronix-test-suite-${VERSION}.tar.gz"
DOWNLOAD_URL="https://phoronix-test-suite.com/releases/${ARCHIVE}"
INSTALL_DIR="/opt/phoronix-test-suite"
LAUNCHER="/usr/local/bin/phoronix-test-suite"

# Function to setup user config directories
setup_user_config() {
    PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    USER_CONFIG_DIR="$PROJECT_ROOT/user_config"
    USER_CONFIG_FILE="$USER_CONFIG_DIR/user-config.xml"
    RESULTS_DIR="$PROJECT_ROOT/results"

    mkdir -p "$USER_CONFIG_DIR"
    mkdir -p "$RESULTS_DIR"

    echo "Project root: $PROJECT_ROOT"
    echo "Config directory: $USER_CONFIG_DIR"
    echo "Results directory: $RESULTS_DIR"

    if [[ -f "$USER_CONFIG_FILE" ]] && [[ ! -w "$USER_CONFIG_FILE" ]]; then
        echo "user-config.xml is already protected (read-only)"
    elif [[ -f "$USER_CONFIG_FILE" ]]; then
        chmod 444 "$USER_CONFIG_FILE"
        echo "Set user-config.xml to read-only mode (444)"
    fi
}

# Function to uninstall existing PTS
uninstall_pts() {
    local version=$1
    echo "=== Uninstalling PTS version ${version} ==="

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

    # Remove PTS cache and data directories to ensure clean state
    PTS_HOME_DIR="$HOME/.phoronix-test-suite"
    if [ -d "$PTS_HOME_DIR" ]; then
        echo "Removing PTS home directory and cache: $PTS_HOME_DIR"
        rm -rf "$PTS_HOME_DIR"
    fi

    echo "Uninstall completed (including cache cleanup)"
}

# Function to install PTS
install_pts() {
    echo "=== Installing Phoronix Test Suite ${VERSION} ==="

    # Download archive
    echo "Downloading PTS ${VERSION}..."
    wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"

    # Install dependencies
    echo "Installing dependencies..."
    sudo apt-get update
    sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip

    # Extract and install
    echo "Extracting archive..."
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT
    tar -xf "$ARCHIVE" -C "$tmpdir"

    echo "Installing to $INSTALL_DIR..."
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp -R "$tmpdir/phoronix-test-suite/"* "$INSTALL_DIR"

    # Create launcher
    echo "Creating launcher at $LAUNCHER..."
    sudo tee "$LAUNCHER" >/dev/null <<'EOF'
#!/usr/bin/env bash
exec /opt/phoronix-test-suite/phoronix-test-suite "$@"
EOF
    sudo chmod +x "$LAUNCHER"

    # Cleanup
    rm -f "$ARCHIVE"

    echo "Installation completed"
}

# Main logic: Always perform clean install to ensure cache is cleared
echo "=== Checking PTS installation status ==="

if [ -x "$LAUNCHER" ]; then
    # PTS is installed - get version and uninstall
    INSTALLED_VERSION=$("$LAUNCHER" version 2>/dev/null | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+' | head -1)

    echo "=== Found existing PTS installation ==="
    echo "Installed version: ${INSTALLED_VERSION:-unknown}"
    echo "Target version: ${VERSION}"

    if [ "$INSTALLED_VERSION" = "$VERSION" ]; then
        echo "Note: Same version detected, but performing clean install to clear cache"
    else
        echo "Note: Different version detected, performing upgrade"
    fi

    uninstall_pts "${INSTALLED_VERSION:-unknown}"
    install_pts
else
    # PTS not installed - clean install
    echo "=== No existing PTS installation found ==="
    install_pts
fi

# Setup user config directory
setup_user_config

# Verify installation
echo ""
echo "=== Verifying installation ==="

if [[ ! -x "$INSTALL_DIR/phoronix-test-suite" ]]; then
    echo "[ERROR] Installation verification failed - executable not found at $INSTALL_DIR/phoronix-test-suite"
    exit 1
fi

if [[ ! -x "$LAUNCHER" ]]; then
    echo "[ERROR] Installation verification failed - launcher not found at $LAUNCHER"
    exit 1
fi

# Get project root for USER_CONFIG_DIR
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_CONFIG_DIR="$PROJECT_ROOT/user_config"

# Verify version output using custom config directory
if ! PTS_USER_PATH_OVERRIDE="$USER_CONFIG_DIR" "$LAUNCHER" version >/dev/null 2>&1; then
    echo "[ERROR] Installation verification failed - phoronix-test-suite version command failed"
    exit 1
fi

echo ""
echo "=== Installation successful ==="
PTS_USER_PATH_OVERRIDE="$USER_CONFIG_DIR" "$LAUNCHER" version

# Suppress PHP deprecation warnings
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/suppress_php_warnings.sh" ]; then
    echo ""
    echo "=== Suppressing PHP deprecation warnings ==="
    "$SCRIPT_DIR/suppress_php_warnings.sh"
else
    echo "[WARNING] suppress_php_warnings.sh not found, skipping PHP warning suppression"
fi

echo ""
echo "=== Setup completed successfully ==="
echo "To run benchmarks, use: ./scripts/run_pts_benchmark.sh <benchmark-name>"