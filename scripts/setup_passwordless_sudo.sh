#!/bin/bash
set -euo pipefail

# Setup passwordless sudo for the current user
# This is useful for automated benchmark runs that require sudo privileges

CURRENT_USER=$(whoami)
SUDOERS_FILE="/etc/sudoers.d/99-${CURRENT_USER}-nopasswd"

echo "=== Setting up passwordless sudo for user: ${CURRENT_USER} ==="

# Check if already configured
if sudo test -f "$SUDOERS_FILE"; then
    if sudo grep -q "NOPASSWD:ALL" "$SUDOERS_FILE" 2>/dev/null; then
        echo "[INFO] Passwordless sudo is already configured for ${CURRENT_USER}"
        exit 0
    fi
fi

# Create sudoers configuration
echo "[INFO] Creating passwordless sudo configuration..."

# Use a temporary file for validation
TEMP_SUDOERS=$(mktemp)
trap 'rm -f "$TEMP_SUDOERS"' EXIT

# Write the sudoers rule
cat > "$TEMP_SUDOERS" << EOF
# Allow ${CURRENT_USER} to run sudo commands without password
# Created by cloud_onehour benchmark setup
${CURRENT_USER} ALL=(ALL) NOPASSWD:ALL
EOF

# Validate the sudoers file syntax
if sudo visudo -c -f "$TEMP_SUDOERS" >/dev/null 2>&1; then
    echo "[OK] Sudoers syntax is valid"

    # Install the sudoers file with secure permissions
    sudo install -m 0440 "$TEMP_SUDOERS" "$SUDOERS_FILE"

    echo "[OK] Passwordless sudo configured successfully"
    echo "[INFO] Configuration file: $SUDOERS_FILE"
    echo ""
    echo "To verify, try: sudo -n true"
    echo "To revert, run: sudo rm $SUDOERS_FILE"
else
    echo "[ERROR] Sudoers syntax validation failed"
    echo "[ERROR] Passwordless sudo was NOT configured"
    exit 1
fi

# Verify the configuration
if sudo -n true 2>/dev/null; then
    echo "[OK] Verification successful: sudo works without password"
else
    echo "[WARN] Verification failed: You may need to start a new shell session"
    echo "[WARN] Or the configuration might require a system restart"
fi
