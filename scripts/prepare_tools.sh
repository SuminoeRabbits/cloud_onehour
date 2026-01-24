#!/bin/bash
set -euo pipefail

# Source common utilities
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/apt_utils.sh"

# Wait for apt locks before any apt operations
# This is critical for cloud instances where unattended-upgrades runs at boot
echo "=== Waiting for apt locks to be released ==="
wait_for_apt_lock
echo ""

# Temporarily disable unattended upgrades to reduce apt lock contention
disable_unattended_upgrades

# setup gcc14
./setup_gcc14.sh
./setup_binutil244.sh

# setup jdkxx, see the version in setup_jdkxx.sh.
./setup_jdkxx.sh

# build zlib
./build_zlib.sh

# build openssl
#./build_openssh.sh

# build pts
./setup_pts.sh

# build others
./setup_init.sh

# setup rust
./setup_rust.sh

# Re-enable unattended upgrades
enable_unattended_upgrades


