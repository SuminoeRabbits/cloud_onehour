#!/bin/bash
set -e

TARGET_VERSION=${1:-17}
echo "--- Java Version Check Process (Target: OpenJDK $TARGET_VERSION) ---"

CURRENT_VERSION=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}' | cut -d'.' -f1 | cut -d'-' -f1) || CURRENT_VERSION=""
echo "Current Java Default Version: ${CURRENT_VERSION:-Not Installed}"

if [ "$CURRENT_VERSION" = "$TARGET_VERSION" ]; then
    echo "OpenJDK $TARGET_VERSION is already the default."
else
    echo "Installing java-$TARGET_VERSION-openjdk-devel..."
    if sudo dnf install -y "java-$TARGET_VERSION-openjdk-devel"; then
        # Switch default Java (RHEL 9 / Standard Repos)
        # RHEL9 JDK paths include full version and arch so we find it dynamically
        JAVA_BIN=$(find /usr/lib/jvm -name "java" -path "*/java-${TARGET_VERSION}-openjdk*/bin/java" 2>/dev/null | head -1)
        if [ -n "$JAVA_BIN" ]; then
            sudo alternatives --set java "$JAVA_BIN"
        else
            echo "[WARN] Could not find java binary for OpenJDK $TARGET_VERSION, skipping alternatives --set"
        fi
    else
        echo "[WARN] Package java-$TARGET_VERSION-openjdk-devel not found (common on RHEL 10)."
        echo "Attempting manual installation of Adoptium Temurin JDK $TARGET_VERSION..."
        
        ARCH=$(uname -m)
        if [ "$ARCH" = "x86_64" ]; then
            ADOPT_ARCH="x64"
        elif [ "$ARCH" = "aarch64" ]; then
            ADOPT_ARCH="aarch64"
        else
            echo "[ERROR] Unsupported architecture for manual install: $ARCH"
            exit 1
        fi
        
        # Download latest GA release for version
        API_URL="https://api.adoptium.net/v3/binary/latest/${TARGET_VERSION}/ga/linux/${ADOPT_ARCH}/jdk/hotspot/normal/eclipse"
        INSTALL_DIR="/opt/jdk-${TARGET_VERSION}-manual"
        
        if [ -d "$INSTALL_DIR" ]; then
            echo "[INFO] Cleaning existing manual install at $INSTALL_DIR"
            sudo rm -rf "$INSTALL_DIR"
        fi
        sudo mkdir -p "$INSTALL_DIR"
        
        echo "Downloading JDK from $API_URL..."
        wget -q -O /tmp/jdk.tar.gz "$API_URL"
        
        echo "Extracting to $INSTALL_DIR..."
        sudo tar -xzf /tmp/jdk.tar.gz -C "$INSTALL_DIR" --strip-components=1
        rm -f /tmp/jdk.tar.gz
        
        JAVA_BIN="$INSTALL_DIR/bin/java"
        JAVAC_BIN="$INSTALL_DIR/bin/javac"
        
        if [ -x "$JAVA_BIN" ]; then
            echo "Registering binaries with alternatives..."
            sudo alternatives --install /usr/bin/java java "$JAVA_BIN" 2000
            sudo alternatives --install /usr/bin/javac javac "$JAVAC_BIN" 2000
            
            sudo alternatives --set java "$JAVA_BIN"
            sudo alternatives --set javac "$JAVAC_BIN"
            echo "[OK] Manual installation successful."
        else
            echo "[ERROR] Manual installation failed: Binary not found at $JAVA_BIN"
            exit 1
        fi
    fi
fi

echo "--- Maven Setup ---"
# Maven is platform-independent tarball install, can mostly reuse
TARGET_MVN_VERSION="3.9.12"
if ! mvn -version 2>&1 | grep -q "$TARGET_MVN_VERSION"; then
    MAVEN_URL="https://dlcdn.apache.org/maven/maven-3/${TARGET_MVN_VERSION}/binaries/apache-maven-${TARGET_MVN_VERSION}-bin.tar.gz"
    sudo mkdir -p /opt/maven
    wget --no-check-certificate "$MAVEN_URL" -O /tmp/maven.tar.gz
    sudo tar -xzf /tmp/maven.tar.gz -C /opt/maven
    sudo alternatives --install /usr/bin/mvn mvn "/opt/maven/apache-maven-${TARGET_MVN_VERSION}/bin/mvn" 300
    sudo alternatives --set mvn "/opt/maven/apache-maven-${TARGET_MVN_VERSION}/bin/mvn"
fi
java -version
mvn -version
