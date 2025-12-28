#!/bin/bash
set -euo pipefail

# Suppress PHP deprecation warnings for Phoronix Test Suite
# This script modifies PHP CLI configuration to hide E_DEPRECATED and E_STRICT warnings

echo ">>> Suppressing PHP deprecation warnings for PTS..."

# Find PHP CLI configuration file
PHP_INI=$(php --ini | grep "Loaded Configuration File" | cut -d: -f2 | xargs)

if [ -z "$PHP_INI" ] || [ ! -f "$PHP_INI" ]; then
    echo "[Warning] PHP configuration file not found, skipping PHP warning suppression"
    exit 0
fi

echo "[INFO] PHP config file: $PHP_INI"

# Backup original php.ini
if [ ! -f "${PHP_INI}.backup" ]; then
    sudo cp "$PHP_INI" "${PHP_INI}.backup"
    echo "[INFO] Created backup: ${PHP_INI}.backup"
fi

# Check current error_reporting setting
CURRENT_SETTING=$(grep -E "^error_reporting\s*=" "$PHP_INI" || echo "")

if [ -n "$CURRENT_SETTING" ]; then
    echo "[INFO] Current setting: $CURRENT_SETTING"

    # Replace error_reporting to exclude E_DEPRECATED and E_STRICT
    sudo sed -i 's/^error_reporting\s*=.*/error_reporting = E_ALL \& ~E_DEPRECATED \& ~E_STRICT/' "$PHP_INI"
    echo "[OK] Updated error_reporting in $PHP_INI"
else
    # Add error_reporting if not exists
    echo "error_reporting = E_ALL & ~E_DEPRECATED & ~E_STRICT" | sudo tee -a "$PHP_INI" > /dev/null
    echo "[OK] Added error_reporting to $PHP_INI"
fi

# Also disable display_errors to prevent any error output to stdout/stderr
# This is the most reliable way to suppress ALL error messages
CURRENT_DISPLAY=$(grep -E "^display_errors\s*=" "$PHP_INI" || echo "")

if [ -n "$CURRENT_DISPLAY" ]; then
    echo "[INFO] Current display_errors: $CURRENT_DISPLAY"
    sudo sed -i 's/^display_errors\s*=.*/display_errors = Off/' "$PHP_INI"
    echo "[OK] Updated display_errors to Off"
else
    echo "display_errors = Off" | sudo tee -a "$PHP_INI" > /dev/null
    echo "[OK] Added display_errors = Off"
fi

# Disable display_startup_errors as well
CURRENT_STARTUP=$(grep -E "^display_startup_errors\s*=" "$PHP_INI" || echo "")

if [ -n "$CURRENT_STARTUP" ]; then
    sudo sed -i 's/^display_startup_errors\s*=.*/display_startup_errors = Off/' "$PHP_INI"
    echo "[OK] Updated display_startup_errors to Off"
else
    echo "display_startup_errors = Off" | sudo tee -a "$PHP_INI" > /dev/null
    echo "[OK] Added display_startup_errors = Off"
fi

# Verify the changes
echo ""
echo "=== Final PHP Configuration ==="
grep -E "^error_reporting\s*=" "$PHP_INI"
grep -E "^display_errors\s*=" "$PHP_INI"
grep -E "^display_startup_errors\s*=" "$PHP_INI"

echo ""
echo "[OK] PHP deprecation warnings completely suppressed"
