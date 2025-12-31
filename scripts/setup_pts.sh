#!/bin/bash
set -euo pipefail

VERSION="10.8.4"
ARCHIVE="phoronix-test-suite-${VERSION}.tar.gz"
DOWNLOAD_URL="https://phoronix-test-suite.com/releases/${ARCHIVE}"
INSTALL_DIR="/opt/phoronix-test-suite"
LAUNCHER="/usr/local/bin/phoronix-test-suite"

echo "=== Phoronix Test Suite Setup ==="
echo "Target version: ${VERSION}"
echo ""

# 1. Uninstall existing PTS and clean all cache
echo "=== Step 1: Uninstalling existing PTS and cleaning cache ==="

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

# Remove PTS cache and data directories
PTS_HOME_DIR="$HOME/.phoronix-test-suite"
if [ -d "$PTS_HOME_DIR" ]; then
    echo "Removing PTS home directory and cache: $PTS_HOME_DIR"
    rm -rf "$PTS_HOME_DIR"
fi

echo "[OK] Uninstall and cleanup completed"
echo ""

# 1.5. Install PHP 8.1 (Ubuntu 22+, with version-specific branching)
echo "=== Step 1.5: Installing PHP 8.1 ==="

# Detect Ubuntu version
UBUNTU_VERSION=$(lsb_release -rs 2>/dev/null || echo "unknown")
UBUNTU_CODENAME=$(lsb_release -cs 2>/dev/null || echo "unknown")
CURRENT_PHP_VERSION=$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null || echo "none")

echo "Ubuntu version: $UBUNTU_VERSION ($UBUNTU_CODENAME)"
echo "Current PHP version: $CURRENT_PHP_VERSION"

# Initialize flag
SKIP_PHP_INSTALL=false

# Function to remove existing PHP installations
remove_existing_php() {
    echo "Removing existing PHP installation..."

    # Remove all PHP packages
    INSTALLED_PHP_PACKAGES=$(dpkg -l | grep -E '^ii\s+php' | awk '{print $2}')
    if [[ -n "$INSTALLED_PHP_PACKAGES" ]]; then
        echo "Found PHP packages: $INSTALLED_PHP_PACKAGES"
        sudo apt-get purge -y $INSTALLED_PHP_PACKAGES
        sudo apt-get autoremove -y
    fi

    echo "[OK] Existing PHP removed"
}

# Check if PHP 8.1 is already installed
if [[ "$CURRENT_PHP_VERSION" == "8.1" ]]; then
    echo "[INFO] PHP 8.1 already installed, skipping installation"
    echo "[OK] PHP installation completed"
    echo "Installed PHP version: $(php --version | head -1)"
    echo ""
    SKIP_PHP_INSTALL=true
fi

# Check if PHP 8.2+ is installed and needs rollback
if [[ "$SKIP_PHP_INSTALL" == "false" && "$CURRENT_PHP_VERSION" != "none" ]]; then
    # Extract major.minor version for comparison
    PHP_MAJOR=$(echo "$CURRENT_PHP_VERSION" | cut -d. -f1)
    PHP_MINOR=$(echo "$CURRENT_PHP_VERSION" | cut -d. -f2)

    # Check if PHP >= 8.2
    if [[ "$PHP_MAJOR" -eq 8 && "$PHP_MINOR" -ge 2 ]] || [[ "$PHP_MAJOR" -gt 8 ]]; then
        echo "[INFO] PHP $CURRENT_PHP_VERSION detected (>= 8.2), rolling back to PHP 8.1..."
        remove_existing_php
        # Continue to install PHP 8.1 below
    fi
fi

if [[ "$SKIP_PHP_INSTALL" == "false" ]]; then
    if [[ "$UBUNTU_VERSION" == "22.04" ]]; then
        # Ubuntu 22.04 LTS: Use default PHP 8.1
        echo "Ubuntu 22.04 LTS: Installing default PHP 8.1"
        sudo apt-get update
        sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip

    elif [[ "$UBUNTU_VERSION" == "24.04" ]]; then
        # Ubuntu 24.04 LTS: Install PHP 8.1 from ondrej PPA
        echo "Ubuntu 24.04 LTS: Installing PHP 8.1 from ondrej/php PPA"
        sudo apt-get install -y software-properties-common
        sudo add-apt-repository -y ppa:ondrej/php
        sudo apt-get update
        sudo apt-get install -y \
            php8.1-cli \
            php8.1-xml \
            php8.1-json \
            php8.1-gd \
            php8.1-curl \
            unzip
        sudo update-alternatives --set php /usr/bin/php8.1
        echo "[OK] PHP 8.1 installed and set as default"

    elif [[ "$UBUNTU_VERSION" =~ ^25\. ]] || [[ "$UBUNTU_CODENAME" == "questing" ]]; then
        # Ubuntu 25.x: Try ondrej PPA, fallback to system default
        echo "Ubuntu 25.x: Attempting PHP 8.1 installation from ondrej/php PPA"

        # Remove broken ondrej/php PPA if exists
        if ls /etc/apt/sources.list.d/ondrej-ubuntu-php-*.sources 2>/dev/null || \
           ls /etc/apt/sources.list.d/ondrej-ubuntu-php-*.list 2>/dev/null; then
            echo "Removing existing ondrej/php PPA..."
            sudo rm -f /etc/apt/sources.list.d/ondrej-ubuntu-php-*.sources
            sudo rm -f /etc/apt/sources.list.d/ondrej-ubuntu-php-*.list
        fi

        sudo apt-get install -y software-properties-common
        if sudo add-apt-repository -y ppa:ondrej/php 2>/dev/null; then
            sudo apt-get update
            if sudo apt-get install -y php8.1-cli php8.1-xml php8.1-json php8.1-gd php8.1-curl unzip 2>/dev/null; then
                sudo update-alternatives --set php /usr/bin/php8.1
                echo "[OK] PHP 8.1 installed and set as default"
            else
                echo "[WARN] PHP 8.1 not available in PPA, using system default PHP"
                sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip
            fi
        else
            echo "[WARN] Failed to add ondrej/php PPA, using system default PHP"
            sudo apt-get update
            sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip
        fi

    else
        # Other Ubuntu versions: Try ondrej PPA for PHP 8.1
        echo "Ubuntu $UBUNTU_VERSION: Installing PHP 8.1 from ondrej/php PPA"
        sudo apt-get install -y software-properties-common
        if sudo add-apt-repository -y ppa:ondrej/php; then
            sudo apt-get update
            sudo apt-get install -y \
                php8.1-cli \
                php8.1-xml \
                php8.1-json \
                php8.1-gd \
                php8.1-curl \
                unzip
            sudo update-alternatives --set php /usr/bin/php8.1
            echo "[OK] PHP 8.1 installed and set as default"
        else
            echo "[WARN] Failed to add ondrej/php PPA, using system default PHP"
            sudo apt-get install -y php-cli php-xml php-json php-gd php-curl unzip
        fi
    fi
fi

echo "[OK] PHP installation completed"
echo "Installed PHP version: $(php --version | head -1)"
echo ""

# 2. Install PTS
echo "=== Step 2: Installing Phoronix Test Suite ${VERSION} ==="

# Download archive
echo "Downloading PTS ${VERSION}..."
wget --no-check-certificate -O "$ARCHIVE" "$DOWNLOAD_URL"

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

# Fix permissions for current user
echo "Setting permissions..."
sudo chown -R $USER:$USER "$INSTALL_DIR"
sudo chmod -R u+w "$INSTALL_DIR"

# Cleanup
rm -f "$ARCHIVE"

echo "[OK] Installation completed"
echo ""

# Verify installation
echo "=== Verifying installation ==="

if [[ ! -x "$INSTALL_DIR/phoronix-test-suite" ]]; then
    echo "[ERROR] Installation verification failed - executable not found"
    exit 1
fi

if [[ ! -x "$LAUNCHER" ]]; then
    echo "[ERROR] Installation verification failed - launcher not found"
    exit 1
fi

if ! "$LAUNCHER" version >/dev/null 2>&1; then
    echo "[ERROR] Installation verification failed - version command failed"
    exit 1
fi

echo ""

# 3. Configure batch mode
echo "=== Step 3: Configuring batch mode ==="
echo "Setting up batch-mode configuration for automated testing..."

# Run batch-setup with automated responses
# Y - Save test results when in batch mode
# N - Open the web browser automatically
# N - Auto upload to OpenBenchmarking.org
# N - Prompt for test identifier
# N - Prompt for test description
# N - Prompt for saved results file-name
# Y - Run all test options
printf "Y\nN\nN\nN\nN\nN\nY\n" | "$LAUNCHER" batch-setup

echo "[OK] Batch mode configured"
echo ""

echo "=== Setup completed successfully ==="
echo "PHP version: $(php --version | head -1)"
"$LAUNCHER" version
echo ""
