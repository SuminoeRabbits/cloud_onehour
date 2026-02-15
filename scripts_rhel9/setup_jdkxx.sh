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
    sudo dnf install -y "java-$TARGET_VERSION-openjdk-devel"
    
    # Switch default Java
    # RHEL9 JDK paths include full version and arch (e.g. java-17-openjdk-17.0.13.0.11-5.el9.x86_64)
    # so we dynamically find the actual binary path registered with alternatives.
    JAVA_BIN=$(find /usr/lib/jvm -name "java" -path "*/java-${TARGET_VERSION}-openjdk*/bin/java" 2>/dev/null | head -1)
    if [ -n "$JAVA_BIN" ]; then
        sudo alternatives --set java "$JAVA_BIN"
    else
        echo "[WARN] Could not find java binary for OpenJDK $TARGET_VERSION, skipping alternatives --set"
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
