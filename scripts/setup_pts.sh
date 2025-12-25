#!/bin/bash
set -euo pipefail

ARCHIVE="phoronix-test-suite-10.8.4.tar.gz"
DOWNLOAD_URL="https://phoronix-test-suite.com/releases/${ARCHIVE}"
INSTALL_DIR="/opt/phoronix-test-suite"
LAUNCHER="/usr/local/bin/phoronix-test-suite"

wget -O "$ARCHIVE" "$DOWNLOAD_URL"

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

# Verify version output
if ! "$LAUNCHER" version >/dev/null 2>&1; then
    echo "Error: Installation failed - phoronix-test-suite version command failed"
    exit 1
fi

echo "=== Installation successful ==="
"$LAUNCHER" version

echo ""
echo "Phoronix Test Suite installed. Run: phoronix-test-suite version"