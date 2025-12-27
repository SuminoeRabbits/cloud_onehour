#!/bin/bash
set -euo pipefail

# 使用方法を表示
usage() {
    echo "Usage: $0 <benchmark> [max_threads]"
    echo ""
    echo "Arguments:"
    echo "  benchmark    PTS benchmark name (e.g., coremark-1.0.1, openssl-3.0.1)"
    echo "  max_threads  Maximum number of threads to test (default: all cores)"
    echo ""
    echo "Examples:"
    echo "  $0 coremark-1.0.1"
    echo "  $0 coremark-1.0.1 4"
    echo "  $0 openssl-3.0.1 8"
    exit 1
}

# 引数チェック
if [[ $# -lt 1 ]]; then
    echo "[ERROR] Benchmark name is required"
    usage
fi

BENCHMARK="$1"
BENCHMARK_FULL="pts/${BENCHMARK}"

CONFIG_DIR="$(pwd)/user_config"  # 絶対パスで指定
CONFIG_FILE="$CONFIG_DIR/user-config.xml"

# 設定ファイルの存在と内容を確認
if [ -f "$CONFIG_FILE" ]; then
    echo "[OK] Config file found: $CONFIG_FILE"
else
    echo "[ERROR] Config file not found!"
    exit 1
fi

# CPUコア数を検出
AVAILABLE_CORES=$(nproc)
echo "[INFO] Detected $AVAILABLE_CORES CPU cores"

# スレッド数の指定（第2引数または最大コア数）
MAX_THREADS=${2:-$AVAILABLE_CORES}

# 指定されたスレッド数が利用可能なコア数を超えていないか確認
if [[ $MAX_THREADS -gt $AVAILABLE_CORES ]]; then
    echo "[WARN] Requested $MAX_THREADS threads exceeds available $AVAILABLE_CORES cores"
    echo "[WARN] Limiting to $AVAILABLE_CORES threads"
    MAX_THREADS=$AVAILABLE_CORES
fi

echo "[INFO] Benchmark: $BENCHMARK_FULL"
echo "[INFO] Will test from 1 to $MAX_THREADS threads"

# テストを強制的に再ビルド（現在の環境変数とコンパイラ設定を使用）
echo ">>> Forcing rebuild with current compiler settings..."
PTS_USER_PATH="$CONFIG_DIR" phoronix-test-suite force-install "$BENCHMARK_FULL"

# 失敗したテストを記録
failed_tests=()

# テスト実行（1コアから最大スレッド数まで1刻み）
for threads in $(seq 1 $MAX_THREADS); do
    echo ""
    echo ">>> Running with $threads threads (CPU cores 0-$(($threads-1)))"
    # CPUアフィニティで物理的に制限
    cpu_list="0-$(($threads-1))"
    # 環境変数を先に設定してtasksetを実行
    if TEST_RESULTS_NAME="${BENCHMARK}-${threads}threads" \
       TEST_RESULTS_IDENTIFIER="${BENCHMARK}-${threads}threads" \
       TEST_RESULTS_DESCRIPTION="Benchmark with ${threads} thread(s)" \
       PTS_USER_PATH="$CONFIG_DIR" \
       taskset -c $cpu_list \
       phoronix-test-suite batch-benchmark "$BENCHMARK_FULL"; then
        echo "[OK] Test with $threads threads completed successfully"
    else
        echo "[ERROR] Test with $threads threads failed"
        failed_tests+=("$threads")
    fi
done

# ベンチマーク終了後、テストを破棄
# Note: PTS's remove command requires confirmation. Since batch mode for removal
# is not implemented in PTS (pts_user_io::prompt_bool_input has batch mode commented out),
# we use echo "y" to automatically confirm the removal prompt.
echo ">>> Removing test installation..."
echo "y" | PTS_USER_PATH="$CONFIG_DIR" phoronix-test-suite remove-installed-test "$BENCHMARK_FULL" > /dev/null

# 結果サマリー
echo ""
echo "=== Benchmark Summary ==="
echo "Benchmark: $BENCHMARK_FULL"
echo "Threads tested: 1 to $MAX_THREADS"
if [[ ${#failed_tests[@]} -eq 0 ]]; then
    echo "[OK] All tests completed successfully"
else
    echo "[WARN] Failed tests (threads): ${failed_tests[*]}"
fi