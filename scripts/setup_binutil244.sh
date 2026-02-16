#!/bin/bash

# エラー発生時に即座に終了
set -e

# --- 設定 ---
TARGET_VERSION="2.44"
THRESHOLD_VERSION="2.41"  # これより古ければインストールを実行
PREFIX="/opt/binutils-${TARGET_VERSION}"
SOURCE_URL="https://ftp.gnu.org/gnu/binutils/binutils-${TARGET_VERSION}.tar.gz"
WORK_DIR="/tmp/binutils_build"
TOOLS=("as" "ld" "ld.gold" "nm" "objcopy" "objdump" "readelf" "strip" "ar" "ranlib" "size" "strings")

# --- 関数定義 ---

# バージョン比較関数
# 現在のバージョンが閾値より小さければ 0 (true) を返す
is_version_older_than_threshold() {
    local current_ver=$1
    local threshold_ver=$2
    
    # 2つのバージョンを並べてソートし、先頭（小さい方）が現在のものであれば「古い」と判定
    # ただし同じ場合は「古くない」とする
    if [ "$current_ver" = "$threshold_ver" ]; then
        return 1
    fi
    
    local older_ver=$(printf "%s\n%s" "$current_ver" "$threshold_ver" | sort -V | head -n 1)
    if [ "$older_ver" = "$current_ver" ]; then
        return 0 # 古い
    else
        return 1 # 新しい
    fi
}

# 0. バージョンチェック
check_current_as() {
    echo ">>> [0/6] 現在のアセンブラ（as）のバージョンを確認中..."
    
    if ! command -v as &> /dev/null; then
        echo "as が見つかりません。インストールを開始します。"
        return 0
    fi

    CURRENT_VER=$(as --version | head -n 1 | grep -oP '\d+\.\d+(\.\d+)?' | head -n 1)
    echo "現在のバージョン: ${CURRENT_VER}"

    if is_version_older_than_threshold "${CURRENT_VER}" "${THRESHOLD_VERSION}"; then
        echo "バージョン ${CURRENT_VER} は ${THRESHOLD_VERSION} より古いため、${TARGET_VERSION} をインストールします。"
        return 0 # インストール実行
    else
        echo "バージョン ${CURRENT_VER} は十分に新しいため、処理をスキップします。"
        return 1 # インストール不要
    fi
}

# 1. 依存パッケージのインストール
install_dependencies() {
    echo ">>> [1/6] 依存パッケージをインストール中..."
    sudo apt update
    sudo apt install -y build-essential wget texinfo bison flex
}

# 2. ソースのダウンロードと展開
download_source() {
    echo ">>> [2/6] ソースコードをダウンロード中..."
    mkdir -p ${WORK_DIR}
    cd ${WORK_DIR}
    if [ ! -f "binutils-${TARGET_VERSION}.tar.gz" ]; then
        wget --no-check-certificate ${SOURCE_URL}
    fi
    tar -xf binutils-${TARGET_VERSION}.tar.gz
}

# 3. ビルドとインストール
build_and_install() {
    echo ">>> [3/6] コンパイルを開始します..."
    cd ${WORK_DIR}/binutils-${TARGET_VERSION}
    mkdir -p build
    cd build
    
    ../configure --prefix=${PREFIX} --disable-werror --enable-gold --enable-plugins --enable-threads
    make -j$(nproc)
    
    sudo make install
}

# 4. シンボリックリンクの作成
setup_links() {
    echo ">>> [4/6] /usr/local/bin にシンボリックリンクを作成中..."
    for tool in "${TOOLS[@]}"; do
        if [ -f "${PREFIX}/bin/${tool}" ]; then
            sudo ln -sf "${PREFIX}/bin/${tool}" "/usr/local/bin/${tool}"
        fi
    done
}

# 5. インストール後の確認
verify_installation() {
    echo ">>> [5/6] インストール結果の最終確認..."
    hash -r
    NEW_AS_PATH=$(which as)
    NEW_AS_VER=$(as --version | head -n 1)
    
    echo "------------------------------------------------"
    echo "パス: ${NEW_AS_PATH}"
    echo "バージョン: ${NEW_AS_VER}"
    echo "------------------------------------------------"
}

# 6. 一時ファイルのクリーンアップ
cleanup() {
    echo ">>> [6/6] 一時ファイルを削除中..."
    rm -rf ${WORK_DIR}
}

# --- メイン処理 ---
main() {
    if check_current_as; then
        install_dependencies
        download_source
        build_and_install
        setup_links
        verify_installation
        cleanup
        echo "すべての工程が正常に完了しました。"
    else
        # インストール不要な場合
        exit 0
    fi
}

main