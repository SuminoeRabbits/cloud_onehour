#!/bin/bash
# Common apt utilities for cloud instances
# Source this file in scripts that use apt commands

# Function to wait for apt locks to be released
# This is essential for cloud instances where unattended-upgrades runs at boot
wait_for_apt_lock() {
    local max_wait=${1:-300}  # Maximum wait time in seconds (default: 5 minutes)
    local wait_interval=5
    local elapsed=0

    echo "Checking for apt locks..."

    while true; do
        # Check if any apt/dpkg locks are held
        if ! sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 && \
           ! sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1 && \
           ! sudo fuser /var/lib/dpkg/lock >/dev/null 2>&1; then
            echo "[OK] apt locks are free"
            return 0
        fi

        if [ $elapsed -ge $max_wait ]; then
            echo "[ERROR] Timeout waiting for apt locks after ${max_wait}s"
            echo "Attempting to identify blocking processes..."
            sudo fuser -v /var/lib/dpkg/lock-frontend 2>/dev/null || true
            sudo fuser -v /var/lib/apt/lists/lock 2>/dev/null || true
            return 1
        fi

        echo "Waiting for apt locks... (${elapsed}s / ${max_wait}s)"
        sleep $wait_interval
        elapsed=$((elapsed + wait_interval))
    done
}

# Optionally disable unattended-upgrades for the duration of script execution
disable_unattended_upgrades() {
    echo "Temporarily disabling unattended-upgrades..."
    sudo systemctl stop unattended-upgrades.service 2>/dev/null || true
    sudo systemctl stop apt-daily.service 2>/dev/null || true
    sudo systemctl stop apt-daily-upgrade.service 2>/dev/null || true
}

# Re-enable unattended-upgrades
enable_unattended_upgrades() {
    echo "Re-enabling unattended-upgrades..."
    sudo systemctl start unattended-upgrades.service 2>/dev/null || true
}
