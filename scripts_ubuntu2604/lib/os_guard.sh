#!/bin/bash
# Shared guard for Ubuntu 26.04-only setup scripts.

require_ubuntu_2604() {
    local os_id=""
    local version_id=""
    local pretty_name=""

    if [[ -f /etc/os-release ]]; then
        # shellcheck disable=SC1091
        source /etc/os-release
        os_id="${ID:-}"
        version_id="${VERSION_ID:-}"
        pretty_name="${PRETTY_NAME:-${NAME:-unknown}}"
    fi

    if [[ "${os_id}" != "ubuntu" || "${version_id}" != "26.04" ]]; then
        echo "[ERROR] scripts_ubuntu2604 supports Ubuntu 26.04 only."
        echo "[ERROR] Detected: ${pretty_name} (ID=${os_id:-unknown}, VERSION_ID=${version_id:-unknown})"
        exit 1
    fi
}

require_ubuntu_2604
