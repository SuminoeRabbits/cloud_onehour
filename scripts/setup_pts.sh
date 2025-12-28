#!/bin/bash
set -euo pipefail

VERSION="10.8.4"
ARCHIVE="phoronix-test-suite-${VERSION}.tar.gz"
DOWNLOAD_URL="https://phoronix-test-suite.com/releases/${ARCHIVE}"
INSTALL_DIR="/opt/phoronix-test-suite"
LAUNCHER="/usr/local/bin/phoronix-test-suite"

# Check if PTS is already installed with the correct version
if [ -x "$LAUNCHER" ]; then
    INSTALLED_VERSION=$("$LAUNCHER" version 2>/dev/null | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    if [ "$INSTALLED_VERSION" = "$VERSION" ]; then
        echo "=== Phoronix Test Suite ${VERSION} is already installed ==="
        echo "Skipping installation. To reinstall, remove ${INSTALL_DIR} first."

        # Still setup user config directory even if PTS is already installed
        PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
        USER_CONFIG_DIR="$PROJECT_ROOT/user_config"
        USER_CONFIG_FILE="$USER_CONFIG_DIR/user-config.xml"
        RESULTS_DIR="$PROJECT_ROOT/results"

        mkdir -p "$USER_CONFIG_DIR"
        mkdir -p "$RESULTS_DIR"

        if [[ -f "$USER_CONFIG_FILE" ]] && [[ ! -w "$USER_CONFIG_FILE" ]]; then
            echo "user-config.xml is already protected (read-only)"
        elif [[ -f "$USER_CONFIG_FILE" ]]; then
            chmod 444 "$USER_CONFIG_FILE"
            echo "Set user-config.xml to read-only mode (444)"
        fi

        exit 0
    else
        echo "Found existing PTS version: ${INSTALLED_VERSION:-unknown}"
        echo "Will upgrade to version ${VERSION}"
    fi
fi

wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"

sudo apt-get update
sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip

tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
tar -xf "$ARCHIVE" -C "$tmpdir"

sudo rm -rf "$INSTALL_DIR"
sudo mkdir -p "$INSTALL_DIR"
sudo cp -R "$tmpdir/phoronix-test-suite/"* "$INSTALL_DIR"

sudo tee "$LAUNCHER" >/dev/null <<'EOF'
#!/usr/bin/env bash
exec /opt/phoronix-test-suite/phoronix-test-suite "$@"
EOF
sudo chmod +x "$LAUNCHER"

rm -f "$ARCHIVE"

# Setup user config directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_CONFIG_DIR="$PROJECT_ROOT/user_config"
USER_CONFIG_FILE="$USER_CONFIG_DIR/user-config.xml"
RESULTS_DIR="$PROJECT_ROOT/results"

# Ensure directories exist
mkdir -p "$USER_CONFIG_DIR"
mkdir -p "$RESULTS_DIR"

echo "Project root: $PROJECT_ROOT"
echo "Config directory: $USER_CONFIG_DIR"
echo "Results directory: $RESULTS_DIR"

# Verify installation
echo "=== Verifying installation ==="

if [[ ! -x "$INSTALL_DIR/phoronix-test-suite" ]]; then
    echo "Error: Installation failed - executable not found at $INSTALL_DIR/phoronix-test-suite"
    exit 1
fi

if [[ ! -x "$LAUNCHER" ]]; then
    echo "Error: Installation failed - launcher not found at $LAUNCHER"
    exit 1
fi

# Verify version output using custom config directory
if ! PTS_USER_PATH_OVERRIDE="$USER_CONFIG_DIR" "$LAUNCHER" version >/dev/null 2>&1; then
    echo "Error: Installation failed - phoronix-test-suite version command failed"
    exit 1
fi

echo "=== Installation successful ==="
PTS_USER_PATH_OVERRIDE="$USER_CONFIG_DIR" "$LAUNCHER" version

# Protect existing user-config.xml from being overwritten
if [[ -f "$USER_CONFIG_FILE" ]]; then
    echo "=== Protecting existing user-config.xml from overwrite ==="
    # Make the file read-only to prevent PTS from overwriting it
    chmod 444 "$USER_CONFIG_FILE"
    echo "Set user-config.xml to read-only mode (444)"
    echo "To modify the config, run: chmod 644 $USER_CONFIG_FILE"
else
    echo "=== No existing user-config.xml found ==="
    echo "PTS will create a new one on first run at: $USER_CONFIG_FILE"
fi

echo ""
echo "Phoronix Test Suite installed. Run: phoronix-test-suite version"

# Suppress PHP deprecation warnings
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/suppress_php_warnings.sh" ]; then
    echo ""
    echo "=== Suppressing PHP deprecation warnings ==="
    "$SCRIPT_DIR/suppress_php_warnings.sh"
else
    echo "[Warning] suppress_php_warnings.sh not found, skipping PHP warning suppression"
fi