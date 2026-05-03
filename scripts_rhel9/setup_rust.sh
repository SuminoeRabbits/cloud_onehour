#!/bin/bash
# Rust Toolchain Setup Script for RHEL9/EL10
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/dnf_utils.sh"

RUST_VERSION="1.84.0"
SYSTEM_CA_BUNDLE="/etc/pki/tls/certs/ca-bundle.crt"
LOG_FILE="/tmp/setup_rust_$(date +%Y%m%d_%H%M%S).log"
CARGO_ENV="${HOME}/.cargo/env"
RUSTUP_SETTINGS="${HOME}/.rustup/settings.toml"

log() {
    echo "[setup_rust] $*" | tee -a "${LOG_FILE}"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

show_rust_state() {
    log "PATH=${PATH}"
    if [[ -f "${RUSTUP_SETTINGS}" ]]; then
        log "Existing rustup settings detected at ${RUSTUP_SETTINGS}"
        sed 's/^/[setup_rust] settings: /' "${RUSTUP_SETTINGS}" | tee -a "${LOG_FILE}" >/dev/null
    else
        log "No existing rustup settings file"
    fi

    if command_exists rustup; then
        log "rustup binary: $(command -v rustup)"
        rustup --version 2>&1 | sed 's/^/[setup_rust] /' | tee -a "${LOG_FILE}" >/dev/null || true
        rustup toolchain list 2>&1 | sed 's/^/[setup_rust] toolchain: /' | tee -a "${LOG_FILE}" >/dev/null || true
        rustup show 2>&1 | sed 's/^/[setup_rust] show: /' | tee -a "${LOG_FILE}" >/dev/null || true
    else
        log "rustup is not currently available"
    fi

    if command_exists rustc; then
        rustc --version 2>&1 | sed 's/^/[setup_rust] /' | tee -a "${LOG_FILE}" >/dev/null || true
    fi
}

get_installed_rust_version() {
    if command_exists rustc; then
        rustc --version 2>/dev/null | awk '{print $2}'
    else
        echo ""
    fi
}

ensure_rust_env_loaded() {
    if [[ -f "${CARGO_ENV}" ]]; then
        # shellcheck disable=SC1090
        source "${CARGO_ENV}"
        hash -r 2>/dev/null || true
    fi
}

install_build_dependencies() {
    log "Installing build dependencies for Rust..."
    wait_for_dnf_lock
    sudo dnf groupinstall -y "Development Tools"

    # curl: skip if curl-minimal is present (minimal images)
    # libgit2-devel is optional for rustup itself.
    local rust_deps="pkgconf-pkg-config openssl-devel git wget ca-certificates"
    if ! rpm -q curl-minimal >/dev/null 2>&1; then
        rust_deps="${rust_deps} curl"
    fi

    sudo dnf install -y ${rust_deps}
    sudo dnf install -y libgit2-devel 2>/dev/null || log "libgit2-devel not available, skipping"
}

install_rustup_if_needed() {
    if command_exists rustup; then
        log "rustup already installed: $(command -v rustup)"
        return 0
    fi

    log "Installing rustup bootstrapper..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain none
    ensure_rust_env_loaded

    if ! command_exists rustup; then
        log "rustup bootstrap installation completed, but rustup is still unavailable"
        return 1
    fi
}

ensure_target_toolchain() {
    local version="$1"

    if rustup toolchain list | grep -Eq "^${version}([[:space:]-]|$)"; then
        log "Rust toolchain ${version} already installed"
    else
        log "Installing Rust toolchain ${version}..."
        local attempt
        for attempt in 1 2; do
            if RUSTUP_USE_CURL=1 \
               RUSTUP_USE_REQWEST=0 \
               CARGO_HTTP_CHECK_REVOKE=false \
               CURL_CA_BUNDLE="${SYSTEM_CA_BUNDLE}" \
               SSL_CERT_FILE="${SYSTEM_CA_BUNDLE}" \
               rustup toolchain install "${version}" 2>&1 | tee -a "${LOG_FILE}"; then
                log "Rust toolchain ${version} installed successfully on attempt ${attempt}"
                break
            fi

            if [[ "${attempt}" -eq 2 ]]; then
                log "Rust toolchain ${version} installation failed after ${attempt} attempts"
                return 1
            fi

            log "rustup install failed on attempt ${attempt}; retrying after a short wait"
            sleep 5
        done
    fi

    log "Setting Rust ${version} as default..."
    rustup default "${version}" 2>&1 | tee -a "${LOG_FILE}"
}

main() {
    log "=== setup_rust start ==="
    log "Target Rust version: ${RUST_VERSION}"
    log "CA bundle: ${SYSTEM_CA_BUNDLE}"

    install_build_dependencies
    ensure_rust_env_loaded

    local installed_version
    installed_version="$(get_installed_rust_version)"
    if [[ "${installed_version}" == "${RUST_VERSION}" ]] && command_exists rustup; then
        log "Rust ${RUST_VERSION} is already installed; refreshing default toolchain only"
        ensure_target_toolchain "${RUST_VERSION}"
        rustc --version
        log "=== setup_rust end ==="
        return 0
    fi

    show_rust_state
    install_rustup_if_needed
    ensure_rust_env_loaded
    ensure_target_toolchain "${RUST_VERSION}"

    installed_version="$(get_installed_rust_version)"
    if [[ "${installed_version}" != "${RUST_VERSION}" ]]; then
        log "Installed Rust version mismatch: expected ${RUST_VERSION}, got ${installed_version:-<none>}"
        show_rust_state
        return 1
    fi

    rustc --version | tee -a "${LOG_FILE}"
    log "=== setup_rust end ==="
}

trap 'status=$?; if [[ $status -ne 0 ]]; then log "setup_rust failed with exit code ${status}"; show_rust_state; log "Detailed log: ${LOG_FILE}"; fi; exit $status' EXIT

main "$@"
