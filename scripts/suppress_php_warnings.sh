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

# Verify the change
NEW_SETTING=$(grep -E "^error_reporting\s*=" "$PHP_INI")
echo "[INFO] New setting: $NEW_SETTING"

echo "[OK] PHP deprecation warnings suppressed"
