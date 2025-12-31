#!/bin/bash

# post.sh - Executed by PTS AFTER install.sh completes
# This is a fallback to verify the GCC 14 fix was applied

echo "[POST.SH] Executing post-installation script" >&2
echo "[POST.SH] Working directory: $(pwd)" >&2

# Create execution marker
MARKER_FILE="/tmp/nginx_post_sh_executed_$(date +%Y%m%d_%H%M%S)_$$"
touch "$MARKER_FILE"
echo "[POST.SH] Marker file created: $MARKER_FILE" >&2

# Verify wrk was built with the correct flags
if [ -f "wrk-4.2.0/wrk" ]; then
    WRK_SIZE=$(stat -c%s wrk-4.2.0/wrk 2>/dev/null || stat -f%z wrk-4.2.0/wrk 2>/dev/null)
    echo "[POST.SH] ✓ wrk binary exists (size: $WRK_SIZE bytes)" >&2
else
    echo "[POST.SH] ✗ ERROR: wrk binary not found!" >&2
fi

# Check if Makefile was patched
if [ -f "wrk-4.2.0/Makefile" ]; then
    if grep -q "std=gnu99" wrk-4.2.0/Makefile; then
        echo "[POST.SH] ✓ Makefile contains GCC 14 fix (std=gnu99)" >&2
    else
        echo "[POST.SH] ✗ WARNING: Makefile still has std=c99" >&2
    fi
fi

echo "[POST.SH] Post-installation verification completed" >&2
exit 0
