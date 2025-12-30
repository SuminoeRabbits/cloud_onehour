#!/bin/bash
set -euo pipefail

# Load compiler environment settings
SCRIPT_DIR_INIT="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_DIR_INIT/setup_compiler_env.sh" ]; then
    echo ">>> Loading compiler environment settings..."
    source "$SCRIPT_DIR_INIT/setup_compiler_env.sh"
else
    echo "[WARN] Compiler environment file not found, using default settings"
fi

# 使用方法を表示
usage() {
    echo "Usage: $0 <benchmark> [threads]"
    echo ""
    echo "Arguments:"
    echo "  benchmark    PTS benchmark name (e.g., coremark-1.0.1, openssl-3.0.1)"
    echo "  threads      Number of threads to use (optional):"
    echo "               - If omitted: Test all thread counts from 1 to vCPU max (default)"
    echo "               - If N > 0:   Test only with N threads (capped at vCPU max)"
    echo ""
    echo "Examples:"
    echo "  $0 coremark-1.0.1           # Test with 1, 2, 3, ... up to max vCPUs"
    echo "  $0 coremark-1.0.1 4         # Test only with 4 threads"
    echo "  $0 openssl-3.0.1 8          # Test only with 8 threads (capped at vCPU max)"
    exit 1
}

# 引数チェック
if [[ $# -lt 1 ]]; then
    echo "[ERROR] Benchmark name is required"
    usage
fi

BENCHMARK="$1"
BENCHMARK_FULL="pts/${BENCHMARK}"

# プロジェクトルートディレクトリを取得（BASH_SOURCEではなく$0を使用）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/user_config"
CONFIG_FILE="$CONFIG_DIR/user-config.xml"

BENCHMARK_NAME="${BENCHMARK%%-*}"  # ベンチマーク名を取得（例: coremark-1.0.1 -> coremark）

# Validate test-specific XML config file exists early (before expensive setup)
# Config file name uses underscore instead of slash: pts_coremark-1.0.1.config
BENCHMARK_CONFIG_NAME="${BENCHMARK_FULL//\//_}"  # pts/coremark-1.0.1 -> pts_coremark-1.0.1
REPO_TEST_CONFIG="$PROJECT_ROOT/user_config/test-options/${BENCHMARK_CONFIG_NAME}.config"
if [ ! -f "$REPO_TEST_CONFIG" ]; then
    echo "[ERROR] Test-specific config file not found: $REPO_TEST_CONFIG"
    echo "[ERROR] All benchmarks must have a corresponding XML config file in user_config/test-options/"
    echo "[ERROR] Config file should be in XML format with test-specific PTS settings"
    echo ""
    echo "Example XML format:"
    echo '<?xml version="1.0"?>'
    echo '<PhoronixTestSuite>'
    echo '  <Options>'
    echo '    <TestResultValidation>'
    echo '      <DynamicRunCount>FALSE</DynamicRunCount>'
    echo '      <LimitDynamicToTestLength>20</LimitDynamicToTestLength>'
    echo '    </TestResultValidation>'
    echo '  </Options>'
    echo '  <TestOptions>'
    echo '    <Test>'
    echo '      <Identifier>pts/coremark-1.0.1</Identifier>'
    echo '      <Option>1</Option>'
    echo '    </Test>'
    echo '  </TestOptions>'
    echo '</PhoronixTestSuite>'
    exit 1
fi
echo "[OK] Test-specific config file found: $REPO_TEST_CONFIG"

# Validate python3 is available for XML merging
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] python3 is required to merge XML config files"
    exit 1
fi

# 設定ファイルの存在と内容を確認
if [ -f "$CONFIG_FILE" ]; then
    echo "[OK] Config file found: $CONFIG_FILE"
else
    echo "[ERROR] Config file not found!"
    exit 1
fi

# user-config.xmlからResultsDirectoryを読み取り（相対パスから絶対パスに変換）
RESULTS_DIR_RELATIVE=$(grep -oP '<ResultsDirectory>\K[^<]+' "$CONFIG_FILE" | sed 's:/$::')
if [ -z "$RESULTS_DIR_RELATIVE" ]; then
    echo "[ERROR] ResultsDirectory not found in $CONFIG_FILE"
    exit 1
fi

# 相対パスを絶対パスに変換（CONFIG_DIRを基準とする）
# まず、ディレクトリが存在するか確認し、なければ作成
RESULTS_DIR_PATH="$CONFIG_DIR/$RESULTS_DIR_RELATIVE"
if [ ! -d "$RESULTS_DIR_PATH" ]; then
    echo "[INFO] Results directory does not exist, creating: $RESULTS_DIR_PATH"
    mkdir -p "$RESULTS_DIR_PATH"
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create results directory: $RESULTS_DIR_PATH"
        exit 1
    fi
    echo "[OK] Results directory created"
fi

# 絶対パスに変換
RESULTS_BASE_DIR="$(cd "$RESULTS_DIR_PATH" && pwd)"
if [ -z "$RESULTS_BASE_DIR" ]; then
    echo "[ERROR] Failed to resolve results directory path: $RESULTS_DIR_PATH"
    exit 1
fi

# マシン名の取得（環境変数MACHINE_NAMEが指定されていない場合はhostnameを使用）
MACHINE_NAME="${MACHINE_NAME:-$(hostname)}"
echo "[INFO] Machine name: $MACHINE_NAME"
echo "[INFO] Results directory: $RESULTS_BASE_DIR"

# CPUコア数を検出
AVAILABLE_CORES=$(nproc 2>/dev/null || echo 1)
echo "[INFO] Detected $AVAILABLE_CORES CPU cores"

# CPU scaling governorを保存して、performanceに設定
echo ">>> Setting CPU scaling governor to performance..."
ORIGINAL_GOVERNORS=()
GOVERNOR_SET_SUCCESS=false

# 各CPUのgovernorを保存
for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    if [ -f "$cpu" ]; then
        ORIGINAL_GOVERNORS+=("$(cat "$cpu")")
    fi
done

# performanceに設定を試みる
if command -v cpupower >/dev/null 2>&1; then
    # cpupowerコマンドが利用可能な場合
    if sudo cpupower frequency-set -g performance >/dev/null 2>&1; then
        echo "[OK] CPU governor set to performance using cpupower"
        GOVERNOR_SET_SUCCESS=true
    fi
elif [ -f /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor ]; then
    # 直接sysfsに書き込む方法
    cpu_num=0
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        if echo performance | sudo tee "$cpu" >/dev/null 2>&1; then
            ((cpu_num++))
        fi
    done
    if [ $cpu_num -gt 0 ]; then
        echo "[OK] CPU governor set to performance for $cpu_num cores"
        GOVERNOR_SET_SUCCESS=true
    fi
fi

if [ "$GOVERNOR_SET_SUCCESS" = false ]; then
    echo "[WARN] Could not set CPU governor to performance. Benchmarks may not reflect maximum performance."
    echo "[WARN] Install 'cpupower' (linux-tools-common) or run with sudo for better performance."
fi

# スレッド数の処理
# - 引数なし（Null）: 1からAVAILABLE_CORESまで全てテスト
# - 引数あり（N > 0）: Nのみテスト（上限はAVAILABLE_CORES）
if [[ $# -ge 2 ]]; then
    # 第2引数が指定されている場合
    FIXED_THREADS="$2"

    # 数値チェック
    if ! [[ "$FIXED_THREADS" =~ ^[0-9]+$ ]] || [[ "$FIXED_THREADS" -le 0 ]]; then
        echo "[ERROR] Thread count must be a positive integer (got: $FIXED_THREADS)"
        usage
    fi

    # vCPU数を上限とする
    if [[ $FIXED_THREADS -gt $AVAILABLE_CORES ]]; then
        echo "[WARN] Requested $FIXED_THREADS threads exceeds available $AVAILABLE_CORES cores"
        echo "[WARN] Limiting to $AVAILABLE_CORES threads"
        FIXED_THREADS=$AVAILABLE_CORES
    fi

    echo "[INFO] Benchmark: $BENCHMARK_FULL"
    echo "[INFO] Will test with FIXED thread count: $FIXED_THREADS"

    # For loop compatibility: set range to single value
    THREAD_START=$FIXED_THREADS
    THREAD_END=$FIXED_THREADS
else
    # 引数なし: 1からAVAILABLE_CORESまで全てテスト
    echo "[INFO] Benchmark: $BENCHMARK_FULL"
    echo "[INFO] Will test from 1 to $AVAILABLE_CORES threads"

    THREAD_START=1
    THREAD_END=$AVAILABLE_CORES
fi

# Verify batch mode is configured
echo ">>> Verifying batch mode configuration..."
BATCH_CONFIGURED=$(grep -oP '<Configured>\K[^<]+' "$CONFIG_FILE")
if [ "$BATCH_CONFIGURED" != "TRUE" ]; then
    echo "[ERROR] Batch mode is not configured in user-config.xml"
    echo "Please ensure <Configured>TRUE</Configured> is set in $CONFIG_FILE"
    exit 1
fi
echo "[OK] Batch mode is configured in $CONFIG_FILE"

# Also ensure the global config (~/.phoronix-test-suite/user-config.xml) has batch mode configured
# and uploads disabled. This is a workaround for PTS sometimes checking global config
# even when PTS_USER_PATH_OVERRIDE is set
GLOBAL_CONFIG="$HOME/.phoronix-test-suite/user-config.xml"
if [ -f "$GLOBAL_CONFIG" ]; then
    # Check batch mode configuration
    GLOBAL_BATCH_CONFIGURED=$(grep -oP '<Configured>\K[^<]+' "$GLOBAL_CONFIG" 2>/dev/null || echo "FALSE")
    if [ "$GLOBAL_BATCH_CONFIGURED" != "TRUE" ]; then
        echo "[WARN] Global config at $GLOBAL_CONFIG does not have batch mode configured"
        echo "[INFO] Configuring batch mode in global config (non-interactive)..."
        echo -e "Y\nN\nN\nN\nN\nN\nY" | phoronix-test-suite batch-setup >/dev/null 2>&1 || true
        echo "[OK] Global batch mode configured"
    fi

    # Check and fix NoInternetCommunication setting
    GLOBAL_NO_INTERNET=$(grep -oP '<NoInternetCommunication>\K[^<]+' "$GLOBAL_CONFIG" 2>/dev/null || echo "FALSE")
    if [ "$GLOBAL_NO_INTERNET" != "TRUE" ]; then
        echo "[WARN] Global config allows internet communication (NoInternetCommunication=$GLOBAL_NO_INTERNET)"
        echo "[INFO] Disabling internet communication in global config..."

        # Use sed to change NoInternetCommunication to TRUE
        sed -i 's/<NoInternetCommunication>FALSE<\/NoInternetCommunication>/<NoInternetCommunication>TRUE<\/NoInternetCommunication>/g' "$GLOBAL_CONFIG"
        sed -i 's/<AlwaysUploadResultsToOpenBenchmarking>TRUE<\/AlwaysUploadResultsToOpenBenchmarking>/<AlwaysUploadResultsToOpenBenchmarking>FALSE<\/AlwaysUploadResultsToOpenBenchmarking>/g' "$GLOBAL_CONFIG"
        sed -i 's/<AllowResultUploadsToOpenBenchmarking>TRUE<\/AllowResultUploadsToOpenBenchmarking>/<AllowResultUploadsToOpenBenchmarking>FALSE<\/AllowResultUploadsToOpenBenchmarking>/g' "$GLOBAL_CONFIG"

        echo "[OK] Internet communication disabled in global config"
    fi
else
    echo "[INFO] No global config found at $GLOBAL_CONFIG - creating one now..."
    echo "[INFO] Configuring batch mode in global config (non-interactive)..."

    # Create global config with batch mode enabled and uploads disabled
    echo -e "Y\nN\nN\nN\nN\nN\nY" | phoronix-test-suite batch-setup >/dev/null 2>&1 || true

    # After creation, disable internet communication
    if [ -f "$GLOBAL_CONFIG" ]; then
        sed -i 's/<NoInternetCommunication>FALSE<\/NoInternetCommunication>/<NoInternetCommunication>TRUE<\/NoInternetCommunication>/g' "$GLOBAL_CONFIG"
        sed -i 's/<AlwaysUploadResultsToOpenBenchmarking>TRUE<\/AlwaysUploadResultsToOpenBenchmarking>/<AlwaysUploadResultsToOpenBenchmarking>FALSE<\/AlwaysUploadResultsToOpenBenchmarking>/g' "$GLOBAL_CONFIG"
        sed -i 's/<AllowResultUploadsToOpenBenchmarking>TRUE<\/AllowResultUploadsToOpenBenchmarking>/<AllowResultUploadsToOpenBenchmarking>FALSE<\/AllowResultUploadsToOpenBenchmarking>/g' "$GLOBAL_CONFIG"
        echo "[OK] Global config created and configured"
    else
        echo "[ERROR] Failed to create global config"
        exit 1
    fi
fi

# Disable perf_tips module to prevent interactive prompts during SSH execution
echo ">>> Disabling perf_tips module to ensure non-interactive execution..."
phoronix-test-suite unload-module perf_tips >/dev/null 2>&1 || true
echo "[OK] perf_tips module disabled"

# テストを強制的に再ビルド（現在の環境変数とコンパイラ設定を使用）
echo ">>> Forcing rebuild with current compiler settings..."
echo "[INFO] Using compiler: ${CC:-gcc} with CFLAGS: ${CFLAGS:-default}"
echo "[INFO] Using CXXFLAGS: ${CXXFLAGS:-default}"

# Coremarkなど一部のベンチマークは独自のMakefileでCFLAGSを上書きするため、
# 複数の環境変数を設定して最適化フラグを確実に渡す
# Note: Coremarkは内部で -O2 を指定するが、後に指定した最適化フラグが優先されるため、
#       CFLAGSの最後に最適化フラグを追加する必要がある
if [ -n "${CFLAGS:-}" ]; then
    # 既存のCFLAGSに追加の変数として渡す
    export XCFLAGS="${CFLAGS}"
    export EXTRA_CFLAGS="${CFLAGS}"
    export FLAGS="${CFLAGS}"

    # Coremarkの場合、FLAGSFULLやCFLAGS_FULLも設定を試みる
    export FLAGSFULL="${CFLAGS}"
    export CFLAGS_FULL="${CFLAGS}"
fi

if [ -n "${CXXFLAGS:-}" ]; then
    export EXTRA_CXXFLAGS="${CXXFLAGS}"
fi

PTS_USER_PATH_OVERRIDE="$CONFIG_DIR" phoronix-test-suite force-install "$BENCHMARK_FULL"

# 失敗したテストを記録
failed_tests=()

# テスト実行（THREAD_STARTからTHREAD_ENDまで）
for threads in $(seq $THREAD_START $THREAD_END); do
    echo ""
    echo ">>> Running with $threads threads"

    # CPUアフィニティで物理的に制限
    # x86ハイパーバイザー環境では偶数IDが物理コア、奇数IDが論理コア（HT）となることが多い
    # 線形に性能向上させるため、nproc/2までは偶数IDを優先し、その後奇数IDを追加
    # 例: threads=1 -> 0
    #     threads=2 -> 0,2
    #     threads=3 -> 0,2,4
    #     threads=4 -> 0,2,4,1  (nproc/2を超えたら奇数IDを追加開始)
    #     threads=5 -> 0,2,4,1,3
    cpu_list=""
    nproc_total=$(nproc)
    half_cores=$((nproc_total / 2))

    if [ $threads -le $half_cores ]; then
        # 物理コアのみ使用（偶数ID）: 0,2,4,...
        for ((i=0; i<threads; i++)); do
            if [ $i -gt 0 ]; then
                cpu_list="${cpu_list},"
            fi
            cpu_list="${cpu_list}$((i * 2))"
        done
    else
        # 物理コア全て + 論理コア（奇数ID）
        # まず偶数IDを全て追加
        for ((i=0; i<half_cores; i++)); do
            if [ $i -gt 0 ]; then
                cpu_list="${cpu_list},"
            fi
            cpu_list="${cpu_list}$((i * 2))"
        done
        # 次に奇数IDを必要数追加
        logical_cores=$((threads - half_cores))
        for ((i=0; i<logical_cores; i++)); do
            cpu_list="${cpu_list},$((i * 2 + 1))"
        done
    fi

    echo ">>> CPU affinity: $cpu_list"
    # 環境変数を先に設定してtasksetを実行
    # SKIP_ALL_TEST_OPTION_CHECKS=1 を追加してバッチモードチェックをスキップ
    # TEST_RESULTS_NAME等で結果の保存先を指定
    # user-config.xmlの<ShowResultsAfterTest>FALSE</ShowResultsAfterTest>でプロンプトを回避
    # AUTO_UPLOAD_RESULTS_TO_OPENBENCHMARKING=FALSE でアップロードプロンプトを回避
    # Note: FORCE_TIMES_TO_RUNを削除し、user-config.xmlの設定を使用（DynamicRunCount=FALSE, LimitDynamicToTestLength=20）
    # Prepare per-run results directory (ensure exists so we can save pre/post samples)
    BENCHMARK_RESULTS_DIR="$RESULTS_BASE_DIR/$MACHINE_NAME/$BENCHMARK_NAME"
    mkdir -p "$BENCHMARK_RESULTS_DIR"

    # Capture pre-run CPU frequency snapshot (no polling during run)
    FREQ_FILE="$BENCHMARK_RESULTS_DIR/${BENCHMARK}-${threads}threads-cpufreq.txt"
    {
        echo "=== PRE-RUN SNAPSHOT ==="
        echo "timestamp: $(date --iso-8601=seconds)"
        lscpu 2>/dev/null || true
        grep -H "cpu MHz" /proc/cpuinfo 2>/dev/null || true
        for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do
            if [ -f "$f" ]; then
                cpu_idx=$(basename "$(dirname "$f")" | sed 's/cpu//')
                val=$(cat "$f" 2>/dev/null || echo)
                echo "cpu${cpu_idx}: ${val} kHz"
            fi
        done
        echo
    } > "$FREQ_FILE" 2>/dev/null || true

    # Configure PTS user-config.xml by merging base config with test-specific config
    PTS_CONFIG_DIR=~/.phoronix-test-suite
    PTS_USER_CONFIG="$PTS_CONFIG_DIR/user-config.xml"
    mkdir -p "$PTS_CONFIG_DIR"

    # Merge base user-config.xml with test-specific XML config using Python
    echo "[INFO] Merging base config with test-specific config..."
    TEST_OPTION=$(python3 << PYTHON_EOF
import xml.etree.ElementTree as ET

def merge_xml_elements(base_elem, override_elem):
    """Recursively merge override_elem into base_elem"""
    base_children = {child.tag: child for child in base_elem}
    for override_child in override_elem:
        if override_child.tag in base_children:
            base_child = base_children[override_child.tag]
            if len(override_child) > 0:
                merge_xml_elements(base_child, override_child)
            else:
                base_child.text = override_child.text
                base_child.attrib.update(override_child.attrib)
        else:
            base_elem.append(override_child)

# Read and merge configs
base_tree = ET.parse('$CONFIG_FILE')
base_root = base_tree.getroot()
test_tree = ET.parse('$REPO_TEST_CONFIG')
test_root = test_tree.getroot()
merge_xml_elements(base_root, test_root)

# Extract test option
test_option = "1"
for test_opts in test_root.findall('.//TestOptions/Test'):
    opt = test_opts.find('Option')
    if opt is not None:
        test_option = opt.text
        break

# Write merged config
base_tree.write('$PTS_USER_CONFIG', encoding='utf-8', xml_declaration=True)
print(test_option)
PYTHON_EOF
)

    echo "[OK] Merged config written to $PTS_USER_CONFIG"
    echo "[INFO] Test option: $TEST_OPTION"

    # Pre-configure test options to avoid interactive prompts
    # Strategy: Instead of using test-options file, feed responses directly via FIFO
    # This handles both single and multiple selection tests reliably
    TEST_OPTION_DIR=~/.phoronix-test-suite/test-options
    mkdir -p "$TEST_OPTION_DIR"

    # Remove any existing test-options file to prevent conflicts
    rm -f "$TEST_OPTION_DIR/${BENCHMARK_CONFIG_NAME}.config"

    echo "[INFO] Using test option '$TEST_OPTION' for $BENCHMARK_FULL"

    # Create a named pipe for providing responses
    input_fifo=$(mktemp -u)
    mkfifo "$input_fifo"

    # Feed test option followed by newlines to handle any prompts
    # Use timeout to prevent infinite blocking if FIFO isn't being read
    # Background process will be killed after benchmark completes
    (
        echo "$TEST_OPTION"
        # Send 'n' responses to decline all prompts (view results, upload, etc.)
        for i in {1..100}; do
            echo "n"
            sleep 0.1
        done
    ) > "$input_fifo" 2>/dev/null &
    yes_pid=$!

    # Run benchmark with clean output
    # - Remove ANSI/ESC sequences for cleaner logs
    # - PHP deprecation warnings are suppressed at system level (via suppress_php_warnings.sh)
    # - All test settings are configured via merged user-config.xml
    # - DISPLAY_COMPACT_RESULTS=1 prevents "Do you want to view results" prompt
    # - SKIP_TEST_RESULT_PARSE=1 prevents result parsing prompts
    # - PHP_ERROR_REPORTING forces PHP to ignore deprecation warnings (critical for Ubuntu 25/PHP 8.3+)
    if TEST_RESULTS_NAME="${BENCHMARK}-${threads}threads" \
       TEST_RESULTS_IDENTIFIER="${BENCHMARK}-${threads}threads" \
       TEST_RESULTS_DESCRIPTION="Benchmark with ${threads} thread(s)" \
       PTS_USER_PATH_OVERRIDE="$CONFIG_DIR" \
       SKIP_ALL_TEST_OPTION_CHECKS=1 \
       SKIP_TEST_OPTION_HANDLING=1 \
       AUTO_UPLOAD_RESULTS_TO_OPENBENCHMARKING=FALSE \
       DISPLAY_COMPACT_RESULTS=1 \
       SKIP_TEST_RESULT_PARSE=1 \
       SKIP_ALL_PROMPTS=1 \
       NO_COLOR=1 \
       PHP_ERROR_REPORTING=0 \
       taskset -c $cpu_list \
       phoronix-test-suite benchmark "$BENCHMARK_FULL" < "$input_fifo" 2>&1 | \
       sed -r 's/\x1B\[[0-9;]*[mK]//g' | \
       grep -v "trim(): Passing null" | \
       grep -v "Deprecated" | \
       grep -v "\[8192\]"; then
        benchmark_result=0
    else
        benchmark_result=1
    fi

    # Clean up
    kill $yes_pid 2>/dev/null || true
    rm -f "$input_fifo"

    if [ $benchmark_result -eq 0 ]; then
        echo "[OK] Test with $threads threads completed successfully"
    else
        echo "[ERROR] Test with $threads threads failed"
        failed_tests+=("$threads")
    fi

    # Capture post-run CPU frequency snapshot (append to same file)
    {
        echo "=== POST-RUN SNAPSHOT ==="
        echo "timestamp: $(date --iso-8601=seconds)"
        lscpu 2>/dev/null || true
        grep -H "cpu MHz" /proc/cpuinfo 2>/dev/null || true
        for f in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do
            if [ -f "$f" ]; then
                cpu_idx=$(basename "$(dirname "$f")" | sed 's/cpu//')
                val=$(cat "$f" 2>/dev/null || echo)
                echo "cpu${cpu_idx}: ${val} kHz"
            fi
        done
        echo
    } >> "$FREQ_FILE" 2>/dev/null || true
done

# 結果をベンチマーク毎のフォルダに整理してエクスポート（マシン名/ベンチマーク名の階層構造）
echo ">>> Organizing and exporting results..."
BENCHMARK_RESULTS_DIR="$RESULTS_BASE_DIR/$MACHINE_NAME/$BENCHMARK_NAME"
mkdir -p "$BENCHMARK_RESULTS_DIR"

# 各スレッド数の結果をエクスポート（CSVのみ）
# PTSはベンチマーク実行時に~/にCSVファイルを直接生成する
# これらのファイルを適切な場所に移動する

echo ">>> Exporting and organizing results..."
for threads in $(seq $THREAD_START $THREAD_END); do
    RESULT_IDENTIFIER="${BENCHMARK}-${threads}threads"

    # PTSが生成するtest-resultsディレクトリから結果を取得
    # 例: ~/.phoronix-test-suite/test-results/coremark-101-1threads/
    PTS_RESULTS_DIR=~/.phoronix-test-suite/test-results
    RESULT_DIR_PATTERN="${BENCHMARK_NAME}-*-${threads}threads"

    # 最新の結果ディレクトリを検索
    RESULT_DIR=$(ls -td $PTS_RESULTS_DIR/${RESULT_DIR_PATTERN} 2>/dev/null | head -1)

    if [ -n "$RESULT_DIR" ] && [ -d "$RESULT_DIR" ]; then
        echo "  Processing results for $threads thread(s): $(basename $RESULT_DIR)"

        # composite.xmlをコピー
        if [ -f "$RESULT_DIR/composite.xml" ]; then
            cp "$RESULT_DIR/composite.xml" "$BENCHMARK_RESULTS_DIR/${RESULT_IDENTIFIER}.xml"
            echo "    Saved: ${RESULT_IDENTIFIER}.xml"
        fi

        # PTSのCSVエクスポート機能を使ってCSVを生成
        # result-file-to-csvコマンドで結果をCSV形式に変換
        if phoronix-test-suite result-file-to-csv "$(basename $RESULT_DIR)" > "$BENCHMARK_RESULTS_DIR/${RESULT_IDENTIFIER}.csv" 2>/dev/null; then
            echo "    Exported: ${RESULT_IDENTIFIER}.csv"
        else
            # CSVエクスポートが失敗した場合、XMLから直接パース
            echo "    [INFO] Parsing results from XML..."
            python3 << PYTHON_EOF
import xml.etree.ElementTree as ET
import csv

try:
    tree = ET.parse('$RESULT_DIR/composite.xml')
    root = tree.getroot()

    # Extract result data
    results = []
    for result in root.findall('.//Result'):
        title = result.find('Title')
        description = result.find('Description')
        scale = result.find('Scale')

        title_text = title.text if title is not None else 'Unknown'
        desc_text = description.text if description is not None else 'Unknown'
        scale_text = scale.text if scale is not None else 'Unknown'

        for entry in result.findall('.//Data/Entry'):
            identifier = entry.find('Identifier')
            value = entry.find('Value')
            raw_string = entry.find('RawString')

            if value is not None:
                results.append({
                    'test': title_text,
                    'description': desc_text,
                    'identifier': identifier.text if identifier is not None else '',
                    'value': value.text,
                    'unit': scale_text,
                    'raw_values': raw_string.text if raw_string is not None else ''
                })

    # Write CSV
    if results:
        with open('$BENCHMARK_RESULTS_DIR/${RESULT_IDENTIFIER}.csv', 'w', newline='') as csvfile:
            fieldnames = ['test', 'description', 'identifier', 'value', 'unit', 'raw_values']
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in results:
                writer.writerow(row)
        print("    Exported: ${RESULT_IDENTIFIER}.csv (from XML)")
    else:
        print("    [WARN] No results found in XML")
except Exception as e:
    print(f"    [ERROR] Failed to parse XML: {e}")
PYTHON_EOF
        fi

        # 人間が読みやすいテキストサマリーを生成
        echo "    Generating summary..."
        python3 << PYTHON_EOF > "$BENCHMARK_RESULTS_DIR/${RESULT_IDENTIFIER}-summary.txt" 2>/dev/null
import xml.etree.ElementTree as ET

try:
    tree = ET.parse('$RESULT_DIR/composite.xml')
    root = tree.getroot()

    # Print header
    gen = root.find('Generated')
    if gen is not None:
        title = gen.find('Title')
        desc = gen.find('Description')
        last_mod = gen.find('LastModified')
        print("=" * 80)
        print(f"Benchmark: {title.text if title is not None else 'Unknown'}")
        print(f"Description: {desc.text if desc is not None else 'Unknown'}")
        print(f"Date: {last_mod.text if last_mod is not None else 'Unknown'}")
        print("=" * 80)
        print()

    # Print system info
    system = root.find('System')
    if system is not None:
        hw = system.find('Hardware')
        sw = system.find('Software')
        if hw is not None:
            print(f"Hardware: {hw.text}")
        if sw is not None:
            print(f"Software: {sw.text}")
        print()

    # Print results
    for result in root.findall('.//Result'):
        title = result.find('Title')
        description = result.find('Description')
        scale = result.find('Scale')

        print("-" * 80)
        print(f"Test: {title.text if title is not None else 'Unknown'}")
        print(f"Metric: {description.text if description is not None else 'Unknown'}")
        print(f"Unit: {scale.text if scale is not None else 'Unknown'}")
        print()

        for entry in result.findall('.//Data/Entry'):
            identifier = entry.find('Identifier')
            value = entry.find('Value')
            raw_string = entry.find('RawString')

            if value is not None:
                print(f"  Result: {value.text}")
                if raw_string is not None and raw_string.text:
                    print(f"  Raw values: {raw_string.text}")
                print()

    print("=" * 80)
except Exception as e:
    print(f"Error parsing XML: {e}")
PYTHON_EOF
        echo "    Saved: ${RESULT_IDENTIFIER}-summary.txt"

    else
        echo "  [WARN] Result directory not found for $threads thread(s) (pattern: ${RESULT_DIR_PATTERN})"
    fi
done

# ベンチマーク終了後、テストを破棄
# Note: PTS's remove command requires confirmation. Since batch mode for removal
# is not implemented in PTS (pts_user_io::prompt_bool_input has batch mode commented out),
# we use echo "y" to automatically confirm the removal prompt.
echo ">>> Removing test installation..."
echo "y" | PTS_USER_PATH_OVERRIDE="$CONFIG_DIR" phoronix-test-suite remove-installed-test "$BENCHMARK_FULL" > /dev/null

# CPU governorを元に戻す
if [ "$GOVERNOR_SET_SUCCESS" = true ] && [ ${#ORIGINAL_GOVERNORS[@]} -gt 0 ]; then
    echo ">>> Restoring original CPU governor settings..."
    cpu_idx=0
    for cpu in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
        if [ -f "$cpu" ] && [ $cpu_idx -lt ${#ORIGINAL_GOVERNORS[@]} ]; then
            original_gov="${ORIGINAL_GOVERNORS[$cpu_idx]}"
            if echo "$original_gov" | sudo tee "$cpu" >/dev/null 2>&1; then
                ((cpu_idx++))
            fi
        fi
    done
    if [ $cpu_idx -gt 0 ]; then
        echo "[OK] Restored CPU governor for $cpu_idx cores"
    fi
fi

# 結果サマリー
echo ""
echo "=== Benchmark Summary ==="
echo "Benchmark: $BENCHMARK_FULL"
if [[ $THREAD_START -eq $THREAD_END ]]; then
    echo "Threads tested: $THREAD_START (fixed)"
else
    echo "Threads tested: $THREAD_START to $THREAD_END"
fi
if [[ ${#failed_tests[@]} -eq 0 ]]; then
    echo "[OK] All tests completed successfully"
else
    echo "[WARN] Failed tests (threads): ${failed_tests[*]}"
fi
echo ""
echo "Results saved to: $BENCHMARK_RESULTS_DIR/"