#!/bin/bash
set -euo pipefail

VERSION="10.8.4"
ARCHIVE="phoronix-test-suite-${VERSION}.tar.gz"
DOWNLOAD_URL="https://phoronix-test-suite.com/releases/${ARCHIVE}"
INSTALL_DIR="/opt/phoronix-test-suite"
LAUNCHER="/usr/local/bin/phoronix-test-suite"

# Check for --fix-permissions flag
if [[ "${1:-}" == "--fix-permissions" ]]; then
    echo "=== Fixing PTS Permissions Only ==="

    if [ ! -d "$INSTALL_DIR" ]; then
        echo "[ERROR] PTS installation directory not found: $INSTALL_DIR"
        echo "Please run this script without --fix-permissions to install PTS first"
        exit 1
    fi

    echo "Setting ownership to current user: $USER"
    sudo chown -R $USER:$USER "$INSTALL_DIR"

    echo "Setting write permissions for user"
    sudo chmod -R u+w "$INSTALL_DIR"

    echo ""
    echo "[OK] Permissions fixed successfully"
    echo "Current ownership:"
    ls -ld "$INSTALL_DIR"
    echo ""
    echo "Current PHP version: $(php --version | head -1)"
    echo "Note: PTS requires PHP 8.1. If version differs, run ./scripts/setup_pts.sh to reinstall with correct PHP."
    exit 0
fi

# Function to setup user config directories
setup_user_config() {
    PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
    USER_CONFIG_DIR="$PROJECT_ROOT/user_config"
    USER_CONFIG_FILE="$USER_CONFIG_DIR/user-config.xml"
    RESULTS_DIR="$PROJECT_ROOT/results"

    mkdir -p "$USER_CONFIG_DIR"
    mkdir -p "$RESULTS_DIR"

    echo "Project root: $PROJECT_ROOT"
    echo "Config directory: $USER_CONFIG_DIR"
    echo "Results directory: $RESULTS_DIR"

    if [[ -f "$USER_CONFIG_FILE" ]] && [[ ! -w "$USER_CONFIG_FILE" ]]; then
        echo "user-config.xml is already protected (read-only)"
    elif [[ -f "$USER_CONFIG_FILE" ]]; then
        chmod 444 "$USER_CONFIG_FILE"
        echo "Set user-config.xml to read-only mode (444)"
    fi
}

# Function to uninstall existing PTS
uninstall_pts() {
    local version=$1
    echo "=== Uninstalling PTS version ${version} ==="

    # Remove installation directory
    if [ -d "$INSTALL_DIR" ]; then
        echo "Removing installation directory: $INSTALL_DIR"
        sudo rm -rf "$INSTALL_DIR"
    fi

    # Remove launcher
    if [ -f "$LAUNCHER" ]; then
        echo "Removing launcher: $LAUNCHER"
        sudo rm -f "$LAUNCHER"
    fi

    # Remove PTS cache and data directories to ensure clean state
    PTS_HOME_DIR="$HOME/.phoronix-test-suite"
    if [ -d "$PTS_HOME_DIR" ]; then
        echo "Removing PTS home directory and cache: $PTS_HOME_DIR"
        rm -rf "$PTS_HOME_DIR"
    fi

    echo "Uninstall completed (including cache cleanup)"
}

# Function to clean up broken PPA for Ubuntu 25.x
cleanup_broken_ppa() {
    # Detect Ubuntu version
    UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "unknown")
    UBUNTU_CODENAME=$(lsb_release -cs 2>/dev/null || echo "unknown")

    # Clean up broken ondrej/php PPA if exists (for Ubuntu 25.x)
    if [[ "$UBUNTU_VERSION" =~ ^25\. ]] || [[ "$UBUNTU_CODENAME" == "questing" ]]; then
        if ls /etc/apt/sources.list.d/ondrej-ubuntu-php-*.sources 2>/dev/null || ls /etc/apt/sources.list.d/ondrej-ubuntu-php-*.list 2>/dev/null; then
            echo "=== Removing unsupported ondrej/php PPA for Ubuntu 25.x ==="
            sudo rm -f /etc/apt/sources.list.d/ondrej-ubuntu-php-*.sources
            sudo rm -f /etc/apt/sources.list.d/ondrej-ubuntu-php-*.list
            echo "[OK] Broken PPA removed"
        fi
    fi
}

# Function to setup download cache
setup_download_cache() {
    echo "=== Setting up PTS download cache ==="

    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    CACHE_SCRIPT="$SCRIPT_DIR/setup_download_cache.sh"

    if [ -f "$CACHE_SCRIPT" ]; then
        echo "Running: $CACHE_SCRIPT all"
        bash "$CACHE_SCRIPT" all
        echo "[OK] Download cache setup completed"
    else
        echo "[WARN] setup_download_cache.sh not found at: $CACHE_SCRIPT"
        echo "[WARN] Skipping download cache setup"
        echo "[INFO] Benchmarks will require internet connectivity to download files"
    fi
    echo ""
}

# Function to install PTS
install_pts() {
    echo "=== Installing Phoronix Test Suite ${VERSION} ==="

    # Download archive
    echo "Downloading PTS ${VERSION}..."
    wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"

    # Install dependencies with PHP version management
    echo "Installing dependencies..."

    # Detect Ubuntu version
    UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "unknown")
    UBUNTU_CODENAME=$(lsb_release -cs 2>/dev/null || echo "unknown")
    CURRENT_PHP_VERSION=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || echo "none")

    echo "Ubuntu version: $UBUNTU_VERSION ($UBUNTU_CODENAME)"
    echo "Current PHP version: $CURRENT_PHP_VERSION"

    # Strategy based on Ubuntu version
    if [[ "$UBUNTU_VERSION" == "22.04" ]]; then
        # Ubuntu 22.04 LTS: Use default PHP 8.1
        echo "Ubuntu 22.04 LTS detected: Using default PHP 8.1"
        sudo apt-get update
        sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip

    elif [[ "$UBUNTU_VERSION" == "24.04" ]]; then
        # Ubuntu 24.04 LTS: Use default PHP 8.3
        echo "Ubuntu 24.04 LTS detected: Using default PHP 8.3"
        echo "Note: PHP 8.3 will be used with compatibility measures"
        sudo apt-get update
        sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip

    elif [[ "$UBUNTU_VERSION" =~ ^25\. ]] || [[ "$UBUNTU_CODENAME" == "questing" ]]; then
        # Ubuntu 25.x: Use system default PHP (8.4) - ondrej PPA doesn't support non-LTS yet
        echo "Ubuntu 25.x detected: Using system default PHP 8.4"
        echo "Note: PHP 8.4 will be used with compatibility measures (ondrej/php PPA not available for non-LTS)"
        sudo apt-get update
        sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip

    else
        # Other Ubuntu versions: Try to install PHP 8.1 from ondrej PPA
        echo "Ubuntu $UBUNTU_VERSION detected: Attempting to install PHP 8.1 from ondrej/php PPA"
        REQUIRED_PHP_VERSION="8.1"

        if ! dpkg -l | grep -q "php${REQUIRED_PHP_VERSION}-cli"; then
            echo "Adding ondrej/php repository..."
            sudo apt-get install -y software-properties-common
            if sudo add-apt-repository -y ppa:ondrej/php; then
                sudo apt-get update
                # Install PHP 8.1 packages
                sudo apt-get install -y \
                    php${REQUIRED_PHP_VERSION}-cli \
                    php${REQUIRED_PHP_VERSION}-xml \
                    php${REQUIRED_PHP_VERSION}-json \
                    php${REQUIRED_PHP_VERSION}-gd \
                    php${REQUIRED_PHP_VERSION}-curl \
                    unzip
                # Set PHP 8.1 as default
                sudo update-alternatives --set php /usr/bin/php${REQUIRED_PHP_VERSION}
                echo "PHP $REQUIRED_PHP_VERSION installed and set as default"
            else
                echo "[WARN] Failed to add ondrej/php PPA, using system default PHP"
                sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip
            fi
        else
            sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip
        fi
    fi

    # Extract and install
    echo "Extracting archive..."
    tmpdir=$(mktemp -d)
    trap 'rm -rf "$tmpdir"' EXIT
    tar -xf "$ARCHIVE" -C "$tmpdir"

    echo "Installing to $INSTALL_DIR..."
    sudo mkdir -p "$INSTALL_DIR"
    sudo cp -R "$tmpdir/phoronix-test-suite/"* "$INSTALL_DIR"

    # Create launcher
    echo "Creating launcher at $LAUNCHER..."
    sudo tee "$LAUNCHER" >/dev/null <<'EOF'
#!/usr/bin/env bash
exec /opt/phoronix-test-suite/phoronix-test-suite "$@"
EOF
    sudo chmod +x "$LAUNCHER"

    # Fix permissions for PTS directories to allow current user write access
    # This is necessary for PTS to create cache and temporary files
    echo "Setting appropriate permissions for PTS directories..."
    sudo chown -R $USER:$USER "$INSTALL_DIR"
    sudo chmod -R u+w "$INSTALL_DIR"

    # Cleanup
    rm -f "$ARCHIVE"

    echo "Installation completed"
}

# Main logic: Always perform clean install to ensure cache is cleared
echo "=== Checking PTS installation status ==="

# Clean up broken PPA first (before any apt operations)
cleanup_broken_ppa

if [ -x "$LAUNCHER" ]; then
    # PTS is installed - get version and uninstall
    INSTALLED_VERSION=$("$LAUNCHER" version 2>/dev/null | grep -oP 'v\K[0-9]+\.[0-9]+\.[0-9]+' | head -1)

    echo "=== Found existing PTS installation ==="
    echo "Installed version: ${INSTALLED_VERSION:-unknown}"
    echo "Target version: ${VERSION}"

    if [ "$INSTALLED_VERSION" = "$VERSION" ]; then
        echo "Note: Same version detected, but performing clean install to clear cache"
    else
        echo "Note: Different version detected, performing upgrade"
    fi

    uninstall_pts "${INSTALLED_VERSION:-unknown}"
    install_pts
    setup_download_cache  # Setup cache after clean install
else
    # PTS not installed - clean install
    echo "=== No existing PTS installation found ==="
    install_pts
    setup_download_cache  # Setup cache after first install
fi

# Setup user config directory
setup_user_config

# Verify installation
echo ""
echo "=== Verifying installation ==="

if [[ ! -x "$INSTALL_DIR/phoronix-test-suite" ]]; then
    echo "[ERROR] Installation verification failed - executable not found at $INSTALL_DIR/phoronix-test-suite"
    exit 1
fi

if [[ ! -x "$LAUNCHER" ]]; then
    echo "[ERROR] Installation verification failed - launcher not found at $LAUNCHER"
    exit 1
fi

# Get project root for USER_CONFIG_DIR
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_CONFIG_DIR="$PROJECT_ROOT/user_config"

# Verify version output using custom config directory
if ! PTS_USER_PATH_OVERRIDE="$USER_CONFIG_DIR" "$LAUNCHER" version >/dev/null 2>&1; then
    echo "[ERROR] Installation verification failed - phoronix-test-suite version command failed"
    exit 1
fi

echo ""
echo "=== Installation successful ==="
echo "PHP version: $(php --version | head -1)"
PTS_USER_PATH_OVERRIDE="$USER_CONFIG_DIR" "$LAUNCHER" version

# Suppress PHP deprecation warnings
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/suppress_php_warnings.sh" ]; then
    echo ""
    echo "=== Suppressing PHP deprecation warnings ==="
    "$SCRIPT_DIR/suppress_php_warnings.sh"
else
    echo "[WARNING] suppress_php_warnings.sh not found, skipping PHP warning suppression"
fi

echo ""
echo "=== Setup completed successfully ==="
echo ""
echo "Usage:"
echo "  Install/reinstall PTS: ./scripts/setup_pts.sh"
echo "  Fix permissions only:  ./scripts/setup_pts.sh --fix-permissions"
echo "  Run benchmarks:        ./scripts/run_pts_benchmark.sh <benchmark-name>"