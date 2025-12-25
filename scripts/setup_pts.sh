#!/
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