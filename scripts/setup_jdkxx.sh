#!/bin/bash

# Stop on error
set -e

# Default to 21 if not supplied
# 将来的にデフォルトのJDKバージョンを変更したい場合は、以下の "21" を変更してください。
# To change the future default JDK version, modify "21" below.
TARGET_VERSION=${1:-21}

echo "--- Java Version Check Process (Target: OpenJDK $TARGET_VERSION) ---"

# 1. Get current major version
CURRENT_VERSION=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}' | cut -d'.' -f1 | cut -d'-' -f1) || CURRENT_VERSION=""

echo "Current Java Default Version: ${CURRENT_VERSION:-Not Installed}"
echo "Target Version: $TARGET_VERSION"

# 2. Check if target version is active
if [ "$CURRENT_VERSION" = "$TARGET_VERSION" ]; then
    echo "OpenJDK $TARGET_VERSION is already the default. Skipping Java installation."
else
    echo "Switching to OpenJDK $TARGET_VERSION..."

    # 3. Update and Install
    sudo apt update
    if ! dpkg -l | grep -q "openjdk-$TARGET_VERSION-jdk"; then
        echo "Installing openjdk-$TARGET_VERSION-jdk..."
        sudo apt install -y "openjdk-$TARGET_VERSION-jdk"
    else
        echo "openjdk-$TARGET_VERSION-jdk is already installed."
    fi
    
    # 4. Switch default Java
    # Find the name used by update-java-alternatives (e.g., java-1.21.0-openjdk-amd64)
    # We look for a line containing the version number.
    JAVA_NAME=$(update-java-alternatives -l | grep "$TARGET_VERSION" | awk '{print $1}' | head -n 1)
    
    if [ -n "$JAVA_NAME" ]; then
        echo "Switching system configuration to $JAVA_NAME..."
        sudo update-java-alternatives -s "$JAVA_NAME"
    else
        echo "WARNING: Could not find strict match for version $TARGET_VERSION in update-java-alternatives."
        echo "Attempting manual update-alternatives fallback..."
        ARCH=$(dpkg --print-architecture)
        sudo update-alternatives --set java "/usr/lib/jvm/java-$TARGET_VERSION-openjdk-$ARCH/bin/java"
    fi
fi


# 5. Final Confirmation
echo "--- Post-Switch Java Version Check ---"
java -version

echo ""
echo "--- Apache Maven Setup (Ensuring 3.9.12) ---"
# Maven setup logic remains useful for benchmarks

CURRENT_MVN_VERSION=$(mvn -version 2>&1 | head -n 1 | awk '{print $3}') || CURRENT_MVN_VERSION=""
TARGET_MVN_VERSION="3.9.12"

if [ "$CURRENT_MVN_VERSION" = "$TARGET_MVN_VERSION" ]; then
    echo "Apache Maven $TARGET_MVN_VERSION is already installed."
else
    echo "Installing Apache Maven $TARGET_MVN_VERSION..."
    
    MAVEN_URL="https://dlcdn.apache.org/maven/maven-3/${TARGET_MVN_VERSION}/binaries/apache-maven-${TARGET_MVN_VERSION}-bin.tar.gz"
    INSTALL_DIR="/opt/maven"
    TARGET_DIR="${INSTALL_DIR}/apache-maven-${TARGET_MVN_VERSION}"
    
    # Ensure wget
    if ! command -v wget &> /dev/null; then
        sudo apt install -y wget
    fi

    if [ ! -d "$INSTALL_DIR" ]; then
        sudo mkdir -p "$INSTALL_DIR"
    fi
    
    if [ ! -d "$TARGET_DIR" ]; then
        echo "Downloading: $MAVEN_URL"
        wget --no-check-certificate "$MAVEN_URL" -O /tmp/maven.tar.gz
        
        echo "Extracting..."
        sudo tar -xzf /tmp/maven.tar.gz -C "$INSTALL_DIR"
        rm /tmp/maven.tar.gz
    fi
    
    echo "Setting update-alternatives for Maven..."
    sudo update-alternatives --install /usr/bin/mvn mvn "${TARGET_DIR}/bin/mvn" 300
    sudo update-alternatives --set mvn "${TARGET_DIR}/bin/mvn"
    
    echo "Maven setup complete."
fi

echo "--- Post-Switch Maven Version Check ---"
mvn -version

echo "All tasks completed."