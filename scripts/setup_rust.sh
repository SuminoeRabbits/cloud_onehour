#!/bin/bash
#
# Rust Toolchain Setup Script
#
# Purpose: Install and configure Rust toolchain for Phoronix Test Suite benchmarks
# Features: Idempotent, error handling, verification, logging
#

set -euo pipefail  # Exit on error, undefined variables, pipe failures

# Script location
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APT_UTILS="${SCRIPT_DIR}/lib/apt_utils.sh"
if [[ -f "${APT_UTILS}" ]]; then
    # shellcheck disable=SC1090
    source "${APT_UTILS}"
fi

# Configuration
RUST_VERSION="1.84.0"
CARGO_ENV="${HOME}/.cargo/env"
CARGO_CONFIG="${HOME}/.cargo/config.toml"
CURLRC="${HOME}/.curlrc"
LOG_FILE="/tmp/setup_rust_$(date +%Y%m%d_%H%M%S).log"
SYSTEM_CA_BUNDLE="/etc/ssl/certs/ca-certificates.crt"
CUSTOM_CA_BUNDLE="${HOME}/.cargo/rust-combined-ca.pem"
ACTIVE_CA_BUNDLE="${SYSTEM_CA_BUNDLE}"

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

# Wait for apt locks if helper is available
wait_for_apt_lock_if_available() {
    if declare -F wait_for_apt_lock >/dev/null 2>&1; then
        log "Waiting for apt locks to be released..."
        if ! wait_for_apt_lock 600; then
            log_error "Timeout waiting for apt locks"
            return 1
        fi
    fi
    return 0
}

# Determine how many parallel jobs Cargo should use
get_parallel_jobs() {
    local jobs=""

    if command_exists nproc; then
        jobs=$(nproc --all 2>/dev/null || true)
    fi

    if [[ -z "${jobs}" ]] && command_exists getconf; then
        jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)
    fi

    if [[ -z "${jobs}" ]]; then
        jobs=1
    elif [[ ! "${jobs}" =~ ^[0-9]+$ ]]; then
        jobs=1
    elif (( jobs < 1 )); then
        jobs=1
    fi

    echo "${jobs}"
}

# Get currently installed Rust version (if any)
get_installed_rust_version() {
    if command_exists rustc; then
        local version_output
        if version_output=$(rustc --version 2>/dev/null); then
            echo "${version_output}" | awk '{print $2}'
        else
            log_warn "rustc command exists but failed to report a version; treating as not installed"
            echo ""
        fi
    else
        echo ""
    fi
}

# Uninstall existing Rust/Cargo installations to ensure a clean state
uninstall_existing_rust() {
    log "Removing existing Rust/Cargo installation..."

    if command_exists rustup; then
        if rustup self uninstall -y >/dev/null 2>&1; then
            log "rustup self uninstall completed"
        else
            log_warn "rustup self uninstall failed or produced warnings; continuing with manual cleanup"
        fi
    fi

    # Remove standard Rust directories
    rm -rf "${HOME}/.cargo" "${HOME}/.rustup" 2>/dev/null || true
    rm -f "${CARGO_ENV}" 2>/dev/null || true
    hash -r 2>/dev/null || true

    # Warn if system-managed Rust/Cargo binaries still exist
    if command_exists rustc; then
        local rustc_path
        rustc_path=$(command -v rustc || true)
        if [[ -n "${rustc_path}" && "${rustc_path}" != "${HOME}/.cargo/bin/rustc" ]]; then
            log_warn "rustc still available at ${rustc_path} (likely system package). Remove it manually if it conflicts."
        fi
    fi

    if command_exists cargo; then
        local cargo_path
        cargo_path=$(command -v cargo || true)
        if [[ -n "${cargo_path}" && "${cargo_path}" != "${HOME}/.cargo/bin/cargo" ]]; then
            log_warn "cargo still available at ${cargo_path} (likely system package). Remove it manually if it conflicts."
        fi
    fi

    log "Rust directories cleaned (${HOME}/.cargo, ${HOME}/.rustup)"
    return 0
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

    local rustc_output=""
    if ! rustc_output=$(rustc --version 2>&1); then
        log_error "rustc command failed: ${rustc_output}"
        return 1
    fi

    local installed_version
    installed_version=$(awk '{print $2}' <<< "${rustc_output}")
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
        wait_for_apt_lock_if_available || return 1
        sudo apt-get update -qq || {
            log_error "Failed to update apt repositories"
            return 1
        }
        wait_for_apt_lock_if_available || return 1
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

# Build a CA bundle that also trusts the intercepted TLS chain
prepare_active_ca_bundle() {
    if [[ ! -f "${SYSTEM_CA_BUNDLE}" ]]; then
        log_warn "System CA bundle not found at ${SYSTEM_CA_BUNDLE}; skipping custom bundle"
        return 1
    fi

    if ! command_exists openssl; then
        log_warn "openssl not available; cannot capture TLS certificates for custom bundle"
        ACTIVE_CA_BUNDLE="${SYSTEM_CA_BUNDLE}"
        return 1
    fi

    mkdir -p "$(dirname "${CUSTOM_CA_BUNDLE}")"

    local tmp_bundle
    tmp_bundle=$(mktemp)
    cp "${SYSTEM_CA_BUNDLE}" "${tmp_bundle}"

    local hosts=(
        "static.rust-lang.org"
        "sh.rustup.rs"
        "static.crates.io"
        "crates.io"
    )

    local captured=false
    for host in "${hosts[@]}"; do
        local tmp_chain
        tmp_chain=$(mktemp)
        if timeout 15 openssl s_client -showcerts -servername "${host}" -connect "${host}:443" </dev/null 2>/dev/null |
            sed -n '/BEGIN CERTIFICATE/,/END CERTIFICATE/p' > "${tmp_chain}" && [[ -s "${tmp_chain}" ]]; then
            log "Captured TLS chain from ${host} for custom CA bundle"
            cat "${tmp_chain}" >> "${tmp_bundle}"
            captured=true
        else
            log_warn "Failed to capture TLS chain from ${host}; continuing"
        fi
        rm -f "${tmp_chain}"
    done

    mv "${tmp_bundle}" "${CUSTOM_CA_BUNDLE}"
    ACTIVE_CA_BUNDLE="${CUSTOM_CA_BUNDLE}"

    if [[ "${captured}" == "true" ]]; then
        log "Custom CA bundle created at ${CUSTOM_CA_BUNDLE}"
    else
        log_warn "No additional certificates captured; using system store copy at ${CUSTOM_CA_BUNDLE}"
    fi

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
    local rustup_url="https://sh.rustup.rs"
    
    # Attempt 1: Use system CA bundle
    if [[ -f "${ACTIVE_CA_BUNDLE}" ]]; then
        log "Attempting download with active CA bundle (${ACTIVE_CA_BUNDLE})..."
        if curl --proto '=https' --tlsv1.2 -sSf --cacert "${ACTIVE_CA_BUNDLE}" "${rustup_url}" -o "${rustup_init}" 2>/dev/null; then
            log "Download successful with CA bundle"
        else
            log_warn "Download with CA bundle failed, trying insecure mode..."
            # Attempt 2: Use insecure mode (last resort)
            log_warn "⚠️  Using insecure mode to download rustup (SSL verification disabled)"
            curl --proto '=https' --tlsv1.2 -sSf -k "${rustup_url}" -o "${rustup_init}" || {
                log_error "Failed to download rustup installer even with insecure mode"
                return 1
            }
        fi
    else
        log_warn "CA bundle not found, using insecure mode..."
        log_warn "⚠️  Using insecure mode to download rustup (SSL verification disabled)"
        curl --proto '=https' --tlsv1.2 -sSf -k "${rustup_url}" -o "${rustup_init}" || {
            log_error "Failed to download rustup installer"
            return 1
        }
    fi

    # Verify the script is not empty
    if [[ ! -s "${rustup_init}" ]]; then
        log_error "Downloaded rustup installer is empty"
        rm -f "${rustup_init}"
        return 1
    fi

    # Temporarily enable insecure mode in ~/.curlrc for rustup installation
    local curlrc_modified=false
    if [[ -f "${CURLRC}" ]] && ! grep -q "^insecure" "${CURLRC}"; then
        log "Temporarily enabling insecure mode in ~/.curlrc for rustup installation..."
        echo "insecure" >> "${CURLRC}"
        curlrc_modified=true
    elif [[ ! -f "${CURLRC}" ]]; then
        log "Creating temporary ~/.curlrc with insecure mode..."
        echo "insecure" > "${CURLRC}"
        curlrc_modified=true
    fi

    # Run rustup installer with SSL-related environment variables
    log "Running rustup installer..."
    RUSTUP_USE_REQWEST=0 \
    RUSTUP_INIT_SKIP_PATH_CHECK=yes \
    CURL_CA_BUNDLE="${ACTIVE_CA_BUNDLE}" \
    SSL_CERT_FILE="${ACTIVE_CA_BUNDLE}" \
    bash "${rustup_init}" -y --default-toolchain none
    
    local install_status=$?
    
    # Remove temporary insecure mode from ~/.curlrc
    if [[ "${curlrc_modified}" == "true" ]] && [[ -f "${CURLRC}" ]]; then
        log "Removing temporary insecure mode from ~/.curlrc..."
        sed -i '/^insecure$/d' "${CURLRC}"
    fi
    
    if [[ ${install_status} -ne 0 ]]; then
        log_error "Failed to run rustup installer"
        rm -f "${rustup_init}"
        return 1
    fi

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
        
        # Temporarily enable insecure mode in ~/.curlrc for toolchain installation
        local curlrc_modified=false
        if [[ -f "${CURLRC}" ]] && ! grep -q "^insecure" "${CURLRC}"; then
            log "Temporarily enabling insecure mode in ~/.curlrc for toolchain installation..."
            echo "insecure" >> "${CURLRC}"
            curlrc_modified=true
        fi
        
        # Set environment variables to help with SSL issues
        RUSTUP_USE_REQWEST=0 \
        RUSTUP_USE_CURL=1 \
        CARGO_HTTP_CHECK_REVOKE=false \
        CURL_CA_BUNDLE="${ACTIVE_CA_BUNDLE}" \
        SSL_CERT_FILE="${ACTIVE_CA_BUNDLE}" \
        rustup toolchain install "${version}"
        
        local install_status=$?
        
        # Remove temporary insecure mode from ~/.curlrc
        if [[ "${curlrc_modified}" == "true" ]] && [[ -f "${CURLRC}" ]]; then
            log "Removing temporary insecure mode from ~/.curlrc..."
            sed -i '/^insecure$/d' "${CURLRC}"
        fi
        
        if [[ ${install_status} -ne 0 ]]; then
            log_error "Failed to install Rust ${version}"
            log_error "If you see SSL errors, check your network/proxy settings"
            return 1
        fi
        
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

# Configure Cargo for SSL/certificate issues
configure_cargo() {
    log "Configuring Cargo..."

    # Create .cargo directory if it doesn't exist
    mkdir -p "${HOME}/.cargo"

    local cargo_jobs
    cargo_jobs=$(get_parallel_jobs)

    # Check if config already exists
    if [[ -f "${CARGO_CONFIG}" ]]; then
        log "Backing up existing Cargo config to ${CARGO_CONFIG}.backup"
        cp "${CARGO_CONFIG}" "${CARGO_CONFIG}.backup"
    fi

    # Create comprehensive Cargo configuration
    cat > "${CARGO_CONFIG}" << EOF
# Cargo Configuration for Phoronix Test Suite Benchmarks
# Generated by setup_rust.sh

[http]
# Disable certificate revocation checking (helps with corporate proxies)
check-revoke = false
# Use the active CA bundle captured by setup_rust.sh
cainfo = "${ACTIVE_CA_BUNDLE}"

[net]
# Use git CLI for fetching instead of libgit2 (better certificate handling)
git-fetch-with-cli = true

[term]
# Colored output
color = "auto"
verbose = false
EOF

    log "Cargo config created at ${CARGO_CONFIG}"

    # Also configure curl for Rust installations that use it directly
    if [[ ! -f "${CURLRC}" ]]; then
        log "Creating ~/.curlrc for curl SSL configuration..."
        cat > "${CURLRC}" << EOF
# curl configuration for Rust/Cargo
# Generated by setup_rust.sh

# Use system CA bundle (comment out if you have SSL issues)
    cacert = "${ACTIVE_CA_BUNDLE}"

# Uncomment the line below if you encounter SSL certificate errors
# This disables SSL verification (NOT RECOMMENDED for production)
# insecure

# Retry on failure
retry = 3
retry-delay = 2

# Show progress
progress-bar
EOF
        log "curl config created at ${CURLRC}"
    else
        log "~/.curlrc already exists, skipping creation"
    fi

    return 0
}

# Install CA certificates
install_ca_certificates() {
    log "Ensuring CA certificates are installed and updated..."

    if command_exists apt-get; then
        wait_for_apt_lock_if_available || return 1
        sudo apt-get update -qq || {
            log_warn "Failed to update apt repositories"
        }
        wait_for_apt_lock_if_available || return 1
        sudo apt-get install -y ca-certificates || {
            log_error "Failed to install ca-certificates"
            return 1
        }
        sudo update-ca-certificates || {
            log_warn "Failed to update CA certificates"
        }
    elif command_exists yum; then
        sudo yum install -y ca-certificates || {
            log_error "Failed to install ca-certificates"
            return 1
        }
    elif command_exists dnf; then
        sudo dnf install -y ca-certificates || {
            log_error "Failed to install ca-certificates"
            return 1
        }
    else
        log_warn "No supported package manager found, skipping CA certificate installation"
        return 0
    fi

    log "CA certificates installed/updated successfully"
    return 0
}

# Install build dependencies for Rust compilation
install_build_dependencies() {
    log "Installing build dependencies..."

    if command_exists apt-get; then
        wait_for_apt_lock_if_available || return 1
        sudo apt-get update -qq || {
            log_warn "Failed to update apt repositories"
        }
        # Install essential build tools, SSL libraries, and pkg-config
        wait_for_apt_lock_if_available || return 1
        sudo apt-get install -y \
            build-essential \
            pkg-config \
            libssl-dev \
            libgit2-dev \
            git \
            curl \
            wget || {
            log_error "Failed to install build dependencies"
            return 1
        }
    elif command_exists yum; then
        sudo yum groupinstall -y "Development Tools" || log_warn "Failed to install Development Tools group"
        sudo yum install -y \
            pkgconfig \
            openssl-devel \
            libgit2-devel \
            git \
            curl \
            wget || {
            log_error "Failed to install build dependencies"
            return 1
        }
    elif command_exists dnf; then
        sudo dnf groupinstall -y "Development Tools" || log_warn "Failed to install Development Tools group"
        sudo dnf install -y \
            pkgconf-pkg-config \
            openssl-devel \
            libgit2-devel \
            git \
            curl \
            wget || {
            log_error "Failed to install build dependencies"
            return 1
        }
    else
        log_warn "No supported package manager found, skipping build dependencies installation"
        return 0
    fi

    log "Build dependencies installed successfully"
    return 0
}

# Set environment variables for Cargo/Rust
set_cargo_env_vars() {
    log "Setting Cargo environment variables..."

    local cargo_jobs
    cargo_jobs=$(get_parallel_jobs)

    # Export variables for current session
    export CARGO_HTTP_CHECK_REVOKE=false
    export CARGO_NET_GIT_FETCH_WITH_CLI=true
    export GIT_SSL_NO_VERIFY=false  # Don't disable SSL completely, use proper certs
    export CARGO_BUILD_JOBS="${cargo_jobs}"
    export RUSTFLAGS="-C target-cpu=native"  # Optimize for current CPU
    export CURL_CA_BUNDLE="${ACTIVE_CA_BUNDLE}"
    export SSL_CERT_FILE="${ACTIVE_CA_BUNDLE}"

    log "Environment variables set:"
    log "  CARGO_HTTP_CHECK_REVOKE=false"
    log "  CARGO_NET_GIT_FETCH_WITH_CLI=true"
    log "  GIT_SSL_NO_VERIFY=false"
    log "  CARGO_BUILD_JOBS=${cargo_jobs}"
    log "  RUSTFLAGS=-C target-cpu=native"
    log "  CURL_CA_BUNDLE=${ACTIVE_CA_BUNDLE}"
    log "  SSL_CERT_FILE=${ACTIVE_CA_BUNDLE}"

    # Add to cargo env file if it exists
    if [[ -f "${CARGO_ENV}" ]]; then
        # Check if variables are already in the file
        if ! grep -q "CARGO_HTTP_CHECK_REVOKE" "${CARGO_ENV}"; then
            log "Adding Cargo environment variables to ${CARGO_ENV}"
            cat >> "${CARGO_ENV}" << EOF

# Cargo configuration for PTS benchmarks (added by setup_rust.sh)
export CARGO_HTTP_CHECK_REVOKE=false
export CARGO_NET_GIT_FETCH_WITH_CLI=true
export CARGO_BUILD_JOBS="${cargo_jobs}"
export RUSTFLAGS="-C target-cpu=native"
export CURL_CA_BUNDLE="${ACTIVE_CA_BUNDLE}"
export SSL_CERT_FILE="${ACTIVE_CA_BUNDLE}"
EOF
        fi
    fi

    return 0
}

# Test cargo functionality
test_cargo_functionality() {
    log "Testing Cargo functionality..."

    # Create a temporary directory for testing
    local test_dir
    test_dir=$(mktemp -d)
    if [[ ! -d "${test_dir}" ]]; then
        log_error "Failed to create test directory"
        return 1
    fi

    log "Creating test Rust project in ${test_dir}..."

    if ! pushd "${test_dir}" >/dev/null; then
        log_error "Failed to enter test directory"
        rm -rf "${test_dir}"
        return 1
    fi

    # Create a minimal Cargo project
    if ! cargo new --bin cargo_test_project --quiet; then
        log_error "Failed to create test Cargo project"
        popd >/dev/null || true
        rm -rf "${test_dir}"
        return 1
    fi

    if ! pushd cargo_test_project >/dev/null; then
        log_error "Failed to enter test project directory"
        popd >/dev/null || true
        rm -rf "${test_dir}"
        return 1
    fi

    # Try to build the project (this will test crates.io connectivity)
    log "Building test project (this tests crates.io connectivity)..."
    if timeout 120 cargo build --release 2>&1 | tee -a "${LOG_FILE}"; then
        log "✅ Cargo build test successful"
        popd >/dev/null || true
        popd >/dev/null || true
        rm -rf "${test_dir}"
        return 0
    else
        log_error "❌ Cargo build test failed"
        log_error "This may indicate SSL/certificate issues or network problems"
        popd >/dev/null || true
        popd >/dev/null || true
        rm -rf "${test_dir}"
        return 1
    fi
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

    # Determine whether we need to remove an existing installation
    local existing_version=""
    existing_version=$(get_installed_rust_version)

    if [[ -n "${existing_version}" ]]; then
        if [[ "${existing_version}" == "${RUST_VERSION}" ]]; then
            log_warn "Rust ${existing_version} detected but verification failed; reinstalling cleanly."
            uninstall_existing_rust || {
                log_error "Failed to remove existing Rust installation"
                exit 1
            }
        else
            log_warn "Detected Rust ${existing_version} but ${RUST_VERSION} is required; removing existing toolchain."
            uninstall_existing_rust || {
                log_error "Failed to remove existing Rust installation"
                exit 1
            }
        fi
    elif [[ -d "${HOME}/.cargo" || -d "${HOME}/.rustup" ]]; then
        log_warn "Rust directories exist without a working rustc; cleaning them up."
        uninstall_existing_rust || {
            log_error "Failed to remove existing Rust installation"
            exit 1
        }
    else
        log "No existing Rust installation detected; proceeding with fresh install."
    fi

    # Step 1: Install CA certificates
    log "Step 1: Installing/updating CA certificates..."
    install_ca_certificates || {
        log_error "Failed to install CA certificates"
        exit 1
    }

    log "Step 1b: Capturing TLS certificates for custom CA bundle..."
    if ! prepare_active_ca_bundle; then
        log_warn "Continuing with system CA bundle (${SYSTEM_CA_BUNDLE})"
        ACTIVE_CA_BUNDLE="${SYSTEM_CA_BUNDLE}"
    fi

    # Step 2: Install build dependencies
    log "Step 2: Installing build dependencies..."
    install_build_dependencies || {
        log_error "Failed to install build dependencies"
        exit 1
    }

    # Step 3: Install curl
    log "Step 3: Ensuring curl is installed..."
    install_curl || {
        log_error "Failed to install curl"
        exit 1
    }

    # Step 4: Configure Cargo
    log "Step 4: Configuring Cargo..."
    configure_cargo || {
        log_error "Failed to configure Cargo"
        exit 1
    }

    # Step 5: Install rustup
    log "Step 5: Installing rustup..."
    install_rustup || {
        log_error "Failed to install rustup"
        exit 1
    }

    # Step 6: Install specific Rust version
    log "Step 6: Installing Rust ${RUST_VERSION}..."
    install_rust_version "${RUST_VERSION}" || {
        log_error "Failed to install Rust ${RUST_VERSION}"
        exit 1
    }

    # Step 7: Update PATH
    log "Step 7: Updating PATH..."
    update_path

    # Step 8: Set Cargo environment variables
    log "Step 8: Setting Cargo environment variables..."
    set_cargo_env_vars || {
        log_error "Failed to set Cargo environment variables"
        exit 1
    }

    # Step 9: Test Cargo functionality
    log "Step 9: Testing Cargo functionality..."
    if ! test_cargo_functionality; then
        log_warn "Cargo functionality test failed"
        log_warn "You may encounter issues when building Rust projects"
        log_warn "Check your network connection and SSL certificates"
    fi

    # Step 10: Final verification
    log "Step 10: Final verification..."
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
        log "Configuration files created:"
        log "  - ${CARGO_CONFIG} (Cargo configuration)"
        log "  - ${CURLRC} (curl configuration)"
        log "  - ${CARGO_ENV} (environment setup)"
        log ""
        log "To use Rust in new shells, run:"
        log "  source ${CARGO_ENV}"
        log ""
        log "Or add this line to your ~/.bashrc or ~/.zshrc:"
        log "  source ${CARGO_ENV}"
        log ""
        log "Environment variables set for Cargo:"
        log "  CARGO_HTTP_CHECK_REVOKE=false"
        log "  CARGO_NET_GIT_FETCH_WITH_CLI=true"
        log "  CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-$(get_parallel_jobs)} (auto-detected)"
        log "  RUSTFLAGS=-C target-cpu=native"
        log "  CURL_CA_BUNDLE=${ACTIVE_CA_BUNDLE}"
        log "  SSL_CERT_FILE=${ACTIVE_CA_BUNDLE}"
        return 0
    else
        log_error "Rust installation verification failed"
        log_error "Check log file: ${LOG_FILE}"
        exit 1
    fi
}

# Run main function
main "$@"

