#!/bin/bash
# Common dnf utilities for EL9/EL10 (RHEL, Rocky, AlmaLinux, Oracle Linux)
# Source this file in scripts that use dnf commands

# Detect EL major version (9, 10, etc.) from /etc/os-release
# Returns the major version number, e.g. "9" or "10"
get_el_version() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "${VERSION_ID%%.*}"
    else
        echo "9"  # fallback
    fi
}

# Detect OS ID (rhel, rocky, ol, almalinux, centos, fedora)
get_os_id() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        echo "$ID"
    else
        echo "unknown"
    fi
}

# Function to wait for dnf locks to be released
wait_for_dnf_lock() {
    local max_wait=${1:-300}  # Maximum wait time in seconds (default: 5 minutes)
    local wait_interval=5
    local elapsed=0

    echo "Checking for dnf locks..."

    while true; do
        # Check if dnf/yum lock exists
        # In RHEL9, dnf uses /var/run/dnf.pid or just manages itself via flock
        if ! sudo fuser /var/run/dnf.pid >/dev/null 2>&1 && \
           ! pgrep -x "dnf" >/dev/null 2>&1 && \
           ! pgrep -x "yum" >/dev/null 2>&1; then
            echo "[OK] dnf locks are free"
            return 0
        fi

        if [ $elapsed -ge $max_wait ]; then
            echo "[ERROR] Timeout waiting for dnf locks after ${max_wait}s"
            return 1
        fi

        echo "Waiting for dnf locks... (${elapsed}s / ${max_wait}s)"
        sleep $wait_interval
        elapsed=$((elapsed + wait_interval))
    done
}

# Temporarily disable dnf-automatic for the duration of script execution
disable_dnf_automatic() {
    echo "Temporarily disabling dnf-automatic..."
    sudo systemctl stop dnf-automatic.service 2>/dev/null || true
    sudo systemctl stop dnf-automatic-install.timer 2>/dev/null || true
}

# Re-enable dnf-automatic
enable_dnf_automatic() {
    echo "Re-enabling dnf-automatic..."
    sudo systemctl start dnf-automatic-install.timer 2>/dev/null || true
}
