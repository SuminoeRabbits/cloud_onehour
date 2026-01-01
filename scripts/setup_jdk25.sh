#!/bin/bash

# エラーが発生したら停止
set -e

echo "--- Javaバージョン確認プロセスを開始します ---"

# 1. 現在のJavaメジャーバージョンを取得
# javaコマンドがない場合は空文字になる
CURRENT_VERSION=$(java -version 2>&1 | awk -F '"' '/version/ {print $2}' | cut -d'.' -f1 | cut -d'-' -f1) || CURRENT_VERSION=""

echo "現在のデフォルトバージョン: ${CURRENT_VERSION:-未インストール}"

# 2. バージョンが 25 かどうか判定
if [ "$CURRENT_VERSION" = "25" ]; then
    echo "既に OpenJDK 25 がデフォルトとして設定されています。終了します。"
    exit 0
fi

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

# 5. 最終確認
echo "--- 入れ替え後のバージョン確認 ---"
java -version

echo "完了しました。"