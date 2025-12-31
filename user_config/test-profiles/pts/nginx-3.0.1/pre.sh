#!/bin/bash

# pre.sh - Executed by PTS BEFORE install.sh
# This script patches the wrk Makefile to fix GCC 14 compatibility

echo "[PRE.SH] Executing pre-installation script" >&2
echo "[PRE.SH] Working directory: $(pwd)" >&2
echo "[PRE.SH] Timestamp: $(date)" >&2

# Create execution marker to prove this script runs
MARKER_FILE="/tmp/nginx_pre_sh_executed_$(date +%Y%m%d_%H%M%S)_$$"
touch "$MARKER_FILE"
echo "[PRE.SH] Marker file created: $MARKER_FILE" >&2

# Wait for wrk-4.2.0 to be extracted by PTS
# PTS extracts tarballs before running pre.sh
if [ -d "wrk-4.2.0" ]; then
    echo "[PRE.SH] Found wrk-4.2.0 directory" >&2

    if [ -f "wrk-4.2.0/Makefile" ]; then
        echo "[PRE.SH] Found wrk-4.2.0/Makefile" >&2
        echo "[PRE.SH] Original CFLAGS:" >&2
        grep "^CFLAGS" wrk-4.2.0/Makefile >&2

        # Apply GCC 14 fix: Change -std=c99 to -std=gnu99
        # This allows inline assembly (asm keyword) in OpenSSL
        sed -i 's/-std=c99/-std=gnu99/g' wrk-4.2.0/Makefile

        echo "[PRE.SH] Patched CFLAGS:" >&2
        grep "^CFLAGS" wrk-4.2.0/Makefile >&2

        # Verify the fix was applied
        if grep -q "std=gnu99" wrk-4.2.0/Makefile; then
            echo "[PRE.SH] ✓ GCC 14 fix successfully applied to wrk Makefile" >&2
        else
            echo "[PRE.SH] ✗ ERROR: Failed to apply GCC 14 fix!" >&2
            exit 1
        fi
    else
        echo "[PRE.SH] WARNING: wrk-4.2.0/Makefile not found yet" >&2
    fi
else
    echo "[PRE.SH] WARNING: wrk-4.2.0 directory not found yet" >&2
    echo "[PRE.SH] PTS may extract it after pre.sh runs" >&2
fi

echo "[PRE.SH] Pre-installation script completed" >&2
exit 0
