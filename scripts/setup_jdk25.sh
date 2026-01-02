#!/bin/bash

# エラーが発生したら停止
set -e

echo "--- Javaバージョン確認プロセスを開始します ---"

# 1. 現在のJavaメジャーバージョンを取得
# javaコマンドがない場合は空文字になる
CURRENT_VERSION=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}' | cut -d'.' -f1 | cut -d'-' -f1) || CURRENT_VERSION=""

echo "現在のデフォルトバージョン: ${CURRENT_VERSION:-未インストール}"

# 2. バージョンが 25 かどうか判定
# 2. バージョンが 25 かどうか判定
if [ "$CURRENT_VERSION" = "25" ]; then
    echo "既に OpenJDK 25 がデフォルトとして設定されています。Javaのインストールはスキップします。"
else
    echo "OpenJDK 25 への入れ替えを開始します..."

    # 3. リポジトリの更新とインストール
    sudo apt update
    sudo apt install -y openjdk-25-jdk
    
    # 4. デフォルトの Java を 25 に切り替える
    # update-java-alternatives はインストールされている全Javaの中から特定のものを一括設定するコマンドです
    # アーキテクチャ(amd64/arm64)を自動判定して設定します
    JAVA_25_NAME=$(update-java-alternatives -l | grep "25" | awk '{print $1}' | head -n 1)
    
    if [ -n "$JAVA_25_NAME" ]; then
        echo "システム設定を $JAVA_25_NAME に切り替えます..."
        sudo update-java-alternatives -s "$JAVA_25_NAME"
    else
        echo "警告: update-java-alternatives で Java 25 が見つかりませんでした。"
        echo "手動で update-alternatives を設定します。"
        # 個別に優先度を設定する場合（念のためのフォールバック）
        sudo update-alternatives --set java /usr/lib/jvm/java-25-openjdk-$(dpkg --print-architecture)/bin/java
    fi
fi


# 5. 最終確認 (Java)
echo "--- 入れ替え後のJavaバージョン確認 ---"
java -version

echo ""
echo "--- Apache Maven 3.9.12 の設定を開始します ---"

# Maven バージョン確認
CURRENT_MVN_VERSION=$(mvn -version 2>&1 | head -n 1 | awk '{print $3}') || CURRENT_MVN_VERSION=""
TARGET_MVN_VERSION="3.9.12"

if [ "$CURRENT_MVN_VERSION" = "$TARGET_MVN_VERSION" ]; then
    echo "既に Apache Maven $TARGET_MVN_VERSION がインストールされています。"
else
    echo "Apache Maven $TARGET_MVN_VERSION をインストールします..."
    
    # ダウンロードと配置
    MAVEN_URL="https://dlcdn.apache.org/maven/maven-3/${TARGET_MVN_VERSION}/binaries/apache-maven-${TARGET_MVN_VERSION}-bin.tar.gz"
    INSTALL_DIR="/opt/maven"
    TARGET_DIR="${INSTALL_DIR}/apache-maven-${TARGET_MVN_VERSION}"
    
    # wget がない場合は入れる (通常はあるはずだが念のため)
    if ! command -v wget &> /dev/null; then
        sudo apt install -y wget
    fi

    # 既存のディレクトリ確認
    if [ ! -d "$INSTALL_DIR" ]; then
        sudo mkdir -p "$INSTALL_DIR"
    fi
    
    # 既にディレクトリがある場合はスキップ(再ダウンロード回避)
    if [ ! -d "$TARGET_DIR" ]; then
        echo "ダウンロード中: $MAVEN_URL"
        # --no-check-certificate を追加 (SSLエラー回避)
        wget --no-check-certificate "$MAVEN_URL" -O /tmp/maven.tar.gz
        
        echo "展開中..."
        sudo tar -xzf /tmp/maven.tar.gz -C "$INSTALL_DIR"
        rm /tmp/maven.tar.gz
    fi
    
    echo "update-alternatives を設定します..."
    # 優先度を高めに設定(300くらい)
    sudo update-alternatives --install /usr/bin/mvn mvn "${TARGET_DIR}/bin/mvn" 300
    sudo update-alternatives --set mvn "${TARGET_DIR}/bin/mvn"
    
    echo "Maven の設定が完了しました。"
fi

# 6. 最終確認 (Maven)
echo "--- 入れ替え後のMavenバージョン確認 ---"
mvn -version

echo "全て完了しました。"