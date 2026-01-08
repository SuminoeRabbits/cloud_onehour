#!/bin/bash
#
# Rust Toolchain Setup Script
#
# Purpose: Install and configure Rust toolchain for Phoronix Test Suite benchmarks
# Features: Idempotent, error handling, verification, logging
#

set -euo pipefail  # Exit on error, undefined variables, pipe failures

# Configuration
RUST_VERSION="1.85.0"
CARGO_ENV="${HOME}/.cargo/env"
LOG_FILE="/tmp/setup_rust_$(date +%Y%m%d_%H%M%S).log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[INFO]${NC} $*" | tee -a "${LOG_FILE}"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*" | tee -a "${LOG_FILE}"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" | tee -a "${LOG_FILE}"
}

# Check if command exists
command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# Verify Rust installation
verify_rust_installation() {
    log "Verifying Rust installation..."

    if ! command_exists rustc; then
        log_error "rustc not found in PATH"
        return 1
    fi

    if ! command_exists cargo; then
        log_error "cargo not found in PATH"
        return 1
    fi

    if ! command_exists rustup; then
        log_error "rustup not found in PATH"
        return 1
    fi

    local installed_version
    installed_version=$(rustc --version | awk '{print $2}')
    log "Installed Rust version: ${installed_version}"

    if [[ "${installed_version}" != "${RUST_VERSION}" ]]; then
        log_warn "Rust version mismatch: expected ${RUST_VERSION}, got ${installed_version}"
        return 1
    fi

    log "Rust installation verified successfully"
    return 0
}

# Install curl if not present
install_curl() {
    if command_exists curl; then
        log "curl already installed: $(curl --version | head -n1)"
        return 0
    fi

    log "Installing curl..."
    if command_exists apt-get; then
        sudo apt-get update -qq || {
            log_error "Failed to update apt repositories"
            return 1
        }
        sudo apt-get install -y curl || {
            log_error "Failed to install curl"
            return 1
        }
    elif command_exists yum; then
        sudo yum install -y curl || {
            log_error "Failed to install curl"
            return 1
        }
    elif command_exists dnf; then
        sudo dnf install -y curl || {
            log_error "Failed to install curl"
            return 1
        }
    else
        log_error "No supported package manager found (apt-get, yum, dnf)"
        return 1
    fi

    log "curl installed successfully"
    return 0
}

# Install rustup
install_rustup() {
    if command_exists rustup; then
        log "rustup already installed: $(rustup --version)"
        return 0
    fi

    log "Installing rustup..."

    # Download and verify rustup installer
    local rustup_init="/tmp/rustup-init.sh"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs -o "${rustup_init}" || {
        log_error "Failed to download rustup installer"
        return 1
    }

    # Verify the script is not empty
    if [[ ! -s "${rustup_init}" ]]; then
        log_error "Downloaded rustup installer is empty"
        rm -f "${rustup_init}"
        return 1
    fi

    # Run rustup installer
    bash "${rustup_init}" -y --default-toolchain none || {
        log_error "Failed to run rustup installer"
        rm -f "${rustup_init}"
        return 1
    }

    rm -f "${rustup_init}"

    # Source cargo environment
    if [[ -f "${CARGO_ENV}" ]]; then
        # shellcheck disable=SC1090
        source "${CARGO_ENV}"
        log "Sourced ${CARGO_ENV}"
    else
        log_error "Cargo environment file not found: ${CARGO_ENV}"
        return 1
    fi

    log "rustup installed successfully"
    return 0
}

# Install specific Rust version
install_rust_version() {
    local version="$1"

    log "Checking if Rust ${version} is already installed..."

    # Check if toolchain is already installed
    if rustup toolchain list | grep -q "^${version}"; then
        log "Rust ${version} toolchain already installed"
    else
        log "Installing Rust ${version} toolchain..."
        rustup toolchain install "${version}" || {
            log_error "Failed to install Rust ${version}"
            return 1
        }
        log "Rust ${version} installed successfully"
    fi

    # Set as default
    log "Setting Rust ${version} as default..."
    rustup default "${version}" || {
        log_error "Failed to set Rust ${version} as default"
        return 1
    }

    log "Rust ${version} set as default"
    return 0
}

# Update PATH for current session
update_path() {
    if [[ -f "${CARGO_ENV}" ]]; then
        # shellcheck disable=SC1090
        source "${CARGO_ENV}"
        log "Updated PATH with Cargo binaries"
    else
        log_warn "Cargo environment file not found: ${CARGO_ENV}"
    fi
}

# Main execution
main() {
    log "========================================"
    log "Rust Toolchain Setup"
    log "Target Version: ${RUST_VERSION}"
    log "Log File: ${LOG_FILE}"
    log "========================================"

    # Check if Rust is already correctly installed
    if verify_rust_installation 2>/dev/null; then
        log "Rust ${RUST_VERSION} is already correctly installed"
        log "Nothing to do. Exiting."
        return 0
    fi

    # Step 1: Install curl
    log "Step 1: Ensuring curl is installed..."
    install_curl || {
        log_error "Failed to install curl"
        exit 1
    }

    # Step 2: Install rustup
    log "Step 2: Installing rustup..."
    install_rustup || {
        log_error "Failed to install rustup"
        exit 1
    }

    # Step 3: Install specific Rust version
    log "Step 3: Installing Rust ${RUST_VERSION}..."
    install_rust_version "${RUST_VERSION}" || {
        log_error "Failed to install Rust ${RUST_VERSION}"
        exit 1
    }

    # Step 4: Update PATH
    log "Step 4: Updating PATH..."
    update_path

    # Step 5: Final verification
    log "Step 5: Final verification..."
    if verify_rust_installation; then
        log "========================================"
        log "✅ Rust setup completed successfully!"
        log "========================================"
        log ""
        log "Installed components:"
        rustc --version | tee -a "${LOG_FILE}"
        cargo --version | tee -a "${LOG_FILE}"
        rustup --version | tee -a "${LOG_FILE}"
        log ""
        log "To use Rust in new shells, run:"
        log "  source ${CARGO_ENV}"
        log ""
        log "Or add this line to your ~/.bashrc or ~/.zshrc:"
        log "  source ${CARGO_ENV}"
        return 0
    else
        log_error "Rust installation verification failed"
        log_error "Check log file: ${LOG_FILE}"
        exit 1
    fi
}

# Run main function
main "$@"


