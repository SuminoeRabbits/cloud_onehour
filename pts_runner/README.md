# introduction
pts_runner/pts_runner_<testname>.pyでやりたいことを書きます。
なおPTSの利用方法は
```
phoronix-test-suite --help
```
を参照。
すべてのpts_runner/pts_runner_<testname>.pyにおいて可能な限り関数名、構造を共通化。個別対応が必要な部分はそれが個別であることがわかるように実装。

## Python version
Python3.10で動作すること。Shebang　で　#!/usr/bin/env python3を指定する。

## Error handle policy
スクリプト内でエラーが出た場合はエラーコードをSTDOUTに表示しつつも実行を続行、終了することを心がける。

## Find <testname> 
<testname>は../test_suite.json内にリストがある。"items": 以下の"pts/<testname>"という形式。../test_suite.jsonに登録されていない<testname>は不正。

## setup
PTSのインストール済みテスト(installed tests)をクリアする。
テストプロファイル(test profiles)は保持される。テストプロファイルにはチェックサム修正などの手動修正が含まれる可能性があるため、削除しない。
なおPTSはInstallされていることが前提(../scripts/setup_pts.sh)。

## clean up
PTSから<testname>をPTSのInstallコマンドを使ってクリーンインストールする、その際にはGcc-14を使って毎回Nativeでコンパイルを行う。環境としてはUbuntu22以上、Hardwareはarm64/amd64の両方を前提とする。

- なおデバッグの為に、このPTSのクリーンインストールコマンドを標準出力に出す。

- クリーンインストール時のBuildは、"THFix_in_compile": trueの場合はNUM_CPU_CORES=vCPU数でBinaryの対応するスレッド数設定、コンパイル時は最大限並列化させたいので -j`nproc` としてビルドを行う。

- クリーンインストール時のBuildは、"THFix_in_compile": falseの場合はNUM_CPU_CORESを指定しない。

- PTSのインストールコマンドはbatch-installを使う事。

### インストールエラー検出と検証（重要）

**問題**: Phoronix Test Suiteは依存ファイルのダウンロード失敗時にもexit code 0を返すことがある。

**対策**: 以下の3段階でインストール失敗を確実に検出する:

1. **stdout/stderrをキャプチャ** (`capture_output=True`)
   ```python
   result = subprocess.run(
       ['bash', '-c', install_cmd],
       capture_output=True,  # ← 重要
       text=True
   )
   ```

2. **エラーメッセージを検出**
   - return codeだけでなく、stdout/stderrの内容もチェック
   - 検出パターン: `'Checksum Failed'`, `'Downloading of needed test files failed'`

3. **インストール検証**
   - `phoronix-test-suite info <testname>` でインストール成功を確認
   - 失敗時は明確なエラーメッセージとsys.exit(1)

**実装例**:
```python
# Execute install command
result = subprocess.run(
    ['bash', '-c', install_cmd],
    capture_output=True,
    text=True
)

# Check for installation failure
# PTS may return 0 even if download failed, so check stdout/stderr for error messages
install_failed = False
if result.returncode != 0:
    install_failed = True
elif result.stdout and ('Checksum Failed' in result.stdout or 'Downloading of needed test files failed' in result.stdout):
    install_failed = True
elif result.stderr and ('Checksum Failed' in result.stderr or 'failed' in result.stderr.lower()):
    install_failed = True

if install_failed:
    print(f"  [ERROR] Installation failed")
    if result.stdout:
        print(f"  [ERROR] stdout: {result.stdout[-500:]}")  # Last 500 chars
    if result.stderr:
        print(f"  [ERROR] stderr: {result.stderr[-500:]}")
    sys.exit(1)

# Verify installation by checking if test is actually installed
verify_cmd = f'phoronix-test-suite info {self.benchmark_full}'
verify_result = subprocess.run(
    ['bash', '-c', verify_cmd],
    capture_output=True,
    text=True
)

if verify_result.returncode != 0 or 'not found' in verify_result.stdout.lower():
    print(f"  [ERROR] Installation verification failed - test not found")
    print(f"  [INFO] This may be due to download/checksum failures")
    print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
    sys.exit(1)

print(f"  [OK] Installation completed and verified")
```

**よくある失敗例**:
- x264, x265等のマルチメディアコーデック: チェックサムエラー（アップストリームの更新）
- ネットワーク不安定時: ダウンロードタイムアウト
- キャッシュ破損: 古いキャッシュファイルとの不一致

### PTS test profile の install.sh 修正（必要な場合のみ）

**注意**: この修正は通常不要です。ほとんどのテストは install.sh の修正なしで動作します。

**適用が必要なケース**:
- GCC-14 コンパイル互換性問題（例: OpenSSL 1.1.1i のインラインアセンブリエラー）
- チェックサム自動修正（例: FFmpeg の x264 依存関係）
- ビルドオプションの調整が必要な特殊ケース

**修正対象のテスト**:
- `pts/apache-3.0.0`: wrk の OpenSSL ビルドに `no-asm` オプション追加
- `pts/nginx-3.0.1`: wrk の OpenSSL ビルドに `no-asm` オプション追加
- `pts/ffmpeg-7.0.1`: Python スクリプト内で `fix_x264_checksum()` メソッドを実装（install.sh は修正不要）

**実装パターン**:

#### パターン1: wrk の OpenSSL 互換性修正（apache, nginx）

**問題**: wrk が OpenSSL 1.1.1i をビルドする際、GCC-14 でインラインアセンブリエラーが発生

**解決**: install.sh で wrk の Makefile を修正し、OpenSSL に `no-asm` オプションを追加

```bash
# install.sh の wrk ビルドセクションに追加
rm -rf wrk-4.2.0
tar -xf wrk-4.2.0.tar.gz
cd wrk-4.2.0
# GCC-14 Compatibility Fix: Add 'no-asm' to OpenSSL build options
# This disables inline assembly in OpenSSL 1.1.1i which has compatibility issues with GCC-14
sed -i 's/OPENSSL_OPTS = no-shared no-psk no-srp no-dtls no-idea --prefix=$(abspath $(ODIR))/OPENSSL_OPTS = no-shared no-psk no-srp no-dtls no-idea no-asm --prefix=$(abspath $(ODIR))/' Makefile
make -j $NUM_CPU_CORES
echo $? > ~/install-exit-status
cd ~
```

**注意点**:
- `sed -i` でインプレース編集を使用
- `no-asm` を既存の `OPENSSL_OPTS` に追加
- コメントで修正理由を明記

#### パターン2: Python スクリプト内でのチェックサム修正（ffmpeg）

**問題**: x264 のアップストリーム変更により、downloads.xml のチェックサムが不一致

**解決**: pts_runner スクリプト内に専用メソッドを実装し、自動修正と再試行を行う

```python
def fix_x264_checksum(self):
    """
    Fix x264 checksum in downloads.xml due to upstream changes.
    """
    downloads_xml = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'downloads.xml'

    if not downloads_xml.exists():
        print(f"  [WARN] downloads.xml not found, skipping checksum fix")
        return

    try:
        with open(downloads_xml, 'r') as f:
            content = f.read()

        # Replace checksums with correct values
        old_sha256 = '48b9160177774f3efd29a60d722d9eddd6c023c0904a9a4f83377aa6e6845edb'
        new_sha256 = 'd859f2b4b0b70d6f10a33e9b5e0a6bf41d97e4414644c4f85f02b7665c0d1292'

        if old_sha256 in content:
            content = content.replace(old_sha256, new_sha256)
            # ... (MD5, FileSize も同様に置換)

            with open(downloads_xml, 'w') as f:
                f.write(content)
            print(f"  [OK] Checksums updated")
        else:
            print(f"  [INFO] Checksums already correct")

    except Exception as e:
        print(f"  [WARN] Failed to fix checksums: {e}")

def install_benchmark(self):
    # First attempt
    result = subprocess.run(['bash', '-c', install_cmd], capture_output=True, text=True)

    # If checksum failed, fix and retry
    if install_failed and 'x264-7ed753b' in result.stdout:
        print(f"  [WARN] Checksum failure detected, applying fix...")
        self.fix_x264_checksum()

        print(f"  [INFO] Retrying installation...")
        result = subprocess.run(['bash', '-c', install_cmd], capture_output=True, text=True)
```

**重要な原則**:
- install.sh 修正は最終手段。可能な限り Python スクリプト側で対応
- 修正理由を必ずコメントで文書化
- test_suite.json の notes フィールドにも記載
- clean_pts_cache() は test profiles を削除しないため、install.sh の修正は保持される

## set <N>=number of threads

実行形式として、引数が与えられるのでそれを間違えないように。引数の定義は

- 引数　<N>, Nは1以上の整数の場合
taskset を用いて利用するCPUアフィニティを固定したうえで、NUM_CPU_CORES=<N>としてptsを実行する。
なおアフィニティはamd64系のHyperThread機能を最大限利用するために{0,2,4...1,3,5...}という順番で並べる。

- 引数　<N>, Nは1以上の整数の場合でN>=ｖCPUの場合
Nが環境のvCPU数と同数もしくは超える場合は、N=vCPUとし、NUM_CPU_CORES=<N>
この場合、すべてのvCPUにスレッドを割り当てるとの観点からtasksetは用いる必要はない。

- 引数が与えられない場合
NUM_CPU_CORES=1,2,3,,,,vCPUと<N>を＋１で数を増やしながらベンチマークを行う。この際のtasksetの設定は3-aと同じく、amd64系のHyperThread機能を最大限利用するために{0,2,4...1,3,5...}という順番で並べる。

## set runtime config(common setting)
これらの設定は、PTSでテストを実行時にのみ利用される。
- NUM_CPU_CORES=<N>
- BATCH_MODE=1
- SKIP_ALL_PROMPTS=1

## set runtime config(unique setting)
これらの設定は、<testname>毎に固有でPTSでテストを実行時にのみ利用される。
今の時点では未定。

## --quick フラグ（開発モード）

すべてのpts_runner_*.pyスクリプトは`--quick`フラグをサポートしています。

### 目的
開発・デバッグ時のテスト時間を短縮（60-70%削減）

### 動作
- `FORCE_TIMES_TO_RUN=1`を環境変数に設定
- テストを1回のみ実行（通常は3回以上）
- 統計的信頼性は低下するが、機能確認には十分

### 使用例

#### 開発モード（高速）
```bash
./pts_runner/pts_runner_coremark-1.0.1.py 4 --quick
./pts_runner/pts_runner_build-llvm-1.6.0.py --quick
```

#### 本番モード（通常）
```bash
./pts_runner/pts_runner_coremark-1.0.1.py 4
./pts_runner/pts_runner_build-llvm-1.6.0.py
```

### 期待される時間短縮

| テスト | 通常時間 | Quick時間 | 短縮率 |
|--------|---------|-----------|--------|
| CoreMark | 6分30秒 | 2分 | 69% |
| Build-LLVM | 30-60分 | 10-20分 | 66% |
| Build-GCC | 20-40分 | 7-13分 | 66% |

### 注意事項
- ⚠️ 開発・デバッグ専用
- ⚠️ 本番ベンチマークには使用しない
- ⚠️ 標準偏差が計算できない
- ⚠️ 外れ値の影響を受けやすい

## set output directory
ベンチマーク結果は results/<machinename>/<testcategory>/<testname>/<N>-thread.log
<testcategory>の文字列にスペースがある場合は"_"に置換する。
ベンチマーク結果はRaw dataと、すべての<N>のデータをわかりやすく読みやすく集計したsummary.log, summary.logを別のAI解析に利用するために統一されたJSON formatで記述したsummary.jsonから構成される。
テスト実施前にresults/<machinename>/<testcategory>/<testname>が存在する場合は、このディレクトリ内のファイルをすべて消去してからテストを実施する。


### summary file format
summaryはすべての<N>のデータを集計する必要があるので、テスト終了後に行われることが期待される。
summaryにはデフォルトで生成されるデータに追加し、<N>-thread.jsonに含まれる"test_run_times"もそれぞれのThread/Test毎に統合される。

## run PTS
上記の設定を使って、PTSを走行。
その際のPTSコマンドはデバッグの為に標準出力する。

- 標準出力、標準エラー出力は
results/<machine name>/<test_category>/<testname>/stdout.log
にteeコマンドでダンプしながら、ターミナルにも出力。

- PTSのbatch-runコマンドを利用

### 環境変数の配置（重要）

> [!CAUTION]
> **環境変数は必ず `perf stat` コマンドの前に配置すること**
> 
> `perf stat` でPTSコマンドをラップする場合、環境変数を `perf stat` の後に配置すると、`perf stat` が環境変数を実行するコマンドに伝播しないため、PTSが必要とする `$LOG_FILE` などの環境変数が正しく渡されず、**"No such file or directory" エラーが発生します**。

**間違った例（エラーが発生）:**
```bash
perf stat -e cycles,instructions -o output.txt NUM_CPU_CORES=4 BATCH_MODE=1 phoronix-test-suite batch-run pts/testname
```

**正しい例:**
```bash
NUM_CPU_CORES=4 BATCH_MODE=1 perf stat -e cycles,instructions -o output.txt phoronix-test-suite batch-run pts/testname
```

### 完全なコマンド例

#### 1. 全vCPU使用（taskset不要）
```bash
NUM_CPU_CORES=4 BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 \
TEST_RESULTS_NAME=testname-4threads TEST_RESULTS_IDENTIFIER=testname-4threads \
perf stat -e cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations \
-A -a -o /path/to/4-thread_perf_stats.txt \
phoronix-test-suite batch-run pts/testname
```

#### 2. 一部vCPU使用（tasksetでCPUアフィニティ固定）
```bash
NUM_CPU_CORES=2 BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 \
TEST_RESULTS_NAME=testname-2threads TEST_RESULTS_IDENTIFIER=testname-2threads \
perf stat -e cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations \
-A -a -o /path/to/2-thread_perf_stats.txt \
taskset -c 0,2 phoronix-test-suite batch-run pts/testname
```

#### 3. perf statを使わない場合（シンプル）
```bash
NUM_CPU_CORES=4 BATCH_MODE=1 phoronix-test-suite batch-run pts/testname
```

### Pythonでの実装例

#### パターン1: 全vCPU使用（taskset不要）
```python
if num_threads >= self.vcpu_count:
    # All vCPUs mode - no taskset needed
    cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
    pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
    cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
else:
    # Partial vCPU mode - use taskset with affinity
    cpu_list = self.get_cpu_affinity_list(num_threads)
    pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
    cpu_info = f"CPU affinity (taskset): {cpu_list}"

# CRITICAL: Environment variables MUST come BEFORE perf stat
pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} perf stat -e cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations -A -a -o {perf_stats_file} {pts_base_cmd}'
```

#### パターン2: Single-threaded benchmark（apache等）
```python
# Single-threaded: use CPU 0 only with taskset
cpu_list = '0'
pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
cpu_info = f"Single-threaded benchmark: CPU affinity (taskset): {cpu_list}"

# Environment variables BEFORE perf stat
pts_cmd = f'{batch_env} perf stat -e cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations -A -a -o {perf_stats_file} {pts_base_cmd}'
```

**重要**: `pts_base_cmd`には環境変数を含めず、PTSコマンドのみを記述すること。

## 実装チェックリスト（新規テスト作成時の必須確認事項）

新しいpts_runner_*.pyを作成する際は、以下を必ず確認してください：

### ✅ 必須実装項目

1. **環境変数の配置**
   - [ ] `NUM_CPU_CORES`と`batch_env`が`perf stat`の**前**に配置されている
   - [ ] `pts_base_cmd`には環境変数を含めない（PTSコマンドのみ）
   - [ ] 正しいパターン: `NUM_CPU_CORES=N batch_env perf stat ... pts_base_cmd`

2. **CPU周波数監視**
   - [ ] `/proc/cpuinfo`方式を使用（sysfsは使用禁止）
   - [ ] 開始時と終了時の2箇所で実装
   - [ ] awkコマンド: `grep "cpu MHz" /proc/cpuinfo | awk '{printf "%.0f\\n", $4 * 1000}'`

3. **perf_event_paranoid設定**
   - [ ] `check_and_setup_perf_permissions()`メソッドを実装
   - [ ] 起動時に自動チェック・調整を実行

4. **コマンド構築ロジック**
   - [ ] 全vCPU使用時: taskset不要、環境変数のみ
   - [ ] 一部vCPU使用時: tasksetでアフィニティ固定
   - [ ] CPUリスト: HyperThread最適化 `{0,2,4...1,3,5...}`

### ⚠️ よくある間違い

**間違い1: 環境変数を`pts_base_cmd`に含める**
```python
# ❌ 間違い
pts_base_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} phoronix-test-suite ...'
pts_cmd = f'perf stat ... {pts_base_cmd}'
```

**正解:**
```python
# ✅ 正しい
pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} perf stat ... {pts_base_cmd}'
```

**間違い2: sysfs方式でCPU周波数を取得**
```python
# ❌ 間違い（ハードウェア依存）
subprocess.run(['bash', '-c', f'cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq > {file}'])
```

**正解:**
```python
# ✅ 正しい（ハードウェア非依存）
cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \\'{{printf "%.0f\\\\n", $4 * 1000}}\\' > {file}'
command = cmd_template.format(file=freq_start_file)
subprocess.run(['bash', '-c', command])
```

## CPU frequency monitoring with perf stat
ベンチマーク実行中のCPU動作周波数とパフォーマンス統計を取得するため、`perf stat`でPTSコマンドをラップする。

> [!NOTE]
> 環境変数の配置については、[run PTS セクションの「環境変数の配置（重要）」](#環境変数の配置重要)を参照してください。

### 取得するメトリクス
以下のイベントを使用（amd64/arm64互換）:
- `cycles`: CPUクロックサイクル数
- `instructions`: 実行命令数
- `cpu-clock`: CPU時間（ミリ秒）
- `task-clock`: タスク実行時間
- `context-switches`: コンテキストスイッチ回数
- `cpu-migrations`: CPUコア間マイグレーション回数

### 周波数サンプルの実装
/proc/cpuinfo で得られた周波数データをAwkで整形して各コアの周波数サンプルとする。それ以外の方法、例えば
`cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq`
などはハードウェア依存性があるので利用しない。サンプルのタイミングは`perf stat`の実行前後。
```python
# 終了時サンプルの例
cmd_template = 'grep "cpu MHz" /proc/cpuinfo | \
    awk \'{{printf "%.0f\\n", $4 * 1000}}\' > {file}'
command = cmd_template.format(file=freq_end_file)
```

### 完全な実装例

```python
def run_benchmark(self, num_threads):
    # ... (省略)
    
    # Record CPU frequency before benchmark
    # Use /proc/cpuinfo method to avoid hardware dependencies (as per README)
    print(f"[INFO] Recording CPU frequency before benchmark...")
    cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \\'{{printf "%.0f\\\\n", $4 * 1000}}\\' > {file}'
    command = cmd_template.format(file=freq_start_file)
    result = subprocess.run(
        ['bash', '-c', command],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"  [WARN] Failed to record start frequency: {result.stderr}")
    else:
        print(f"  [OK] Start frequency recorded")
    
    # Execute PTS command
    # ... (省略)
    
    # Record CPU frequency after benchmark
    # Use /proc/cpuinfo method to avoid hardware dependencies (as per README)
    print(f"\n[INFO] Recording CPU frequency after benchmark...")
    cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \\'{{printf "%.0f\\\\n", $4 * 1000}}\\' > {file}'
    command = cmd_template.format(file=freq_end_file)
    result = subprocess.run(
        ['bash', '-c', command],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"  [WARN] Failed to record end frequency: {result.stderr}")
    else:
        print(f"  [OK] End frequency recorded")
```

**注意事項**:
- 必ず**開始時と終了時の2箇所**で実装すること
- `freq_start_file`と`freq_end_file`のファイル名を間違えないこと
- エラーハンドリングを含めること（`returncode`チェック）

### perf stat オプション
1. `perf stat`に`-A`（per-CPU統計）および`-a`（全CPU監視）オプションを使用することで、CPU毎のメトリクスを取得
2. `-e` オプションでイベントを指定: `cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations`
3. `-o` オプションで出力ファイルを指定

### 出力ファイル
results/<machine name>/<test_category>/<testname>/に以下を保存:
- `<N>-thread_perf_stats.txt`: perf statの生出力（CPU毎）
- `<N>-thread_freq_start.txt`: ベンチマーク開始時のCPU周波数（全CPU）
- `<N>-thread_freq_end.txt`: ベンチマーク終了時のCPU周波数（全CPU）
- `<N>-thread_perf_summary.json`: パース済み統計データ（JSON）

### 計算メトリクス（perf_summary.json）
`parse_perf_stats_and_freq()`関数で生成。以下のフォーマット:

```json
{
  "avg_frequency_ghz": {
    "0": 2.856,
    "1": 2.912,
    "2": 2.834
  },
  "start_frequency_ghz": {
    "0": 2.500,
    "1": 2.500,
    "2": 2.500
  },
  "end_frequency_ghz": {
    "0": 3.100,
    "1": 3.200,
    "2": 3.050
  },
  "ipc": {
    "0": 1.51,
    "1": 1.48,
    "2": 1.53
  },
  "total_cycles": {
    "0": 45234567890,
    "1": 46123456789,
    "2": 44987654321
  },
  "total_instructions": {
    "0": 68456789012,
    "1": 68234567890,
    "2": 68876543210
  },
  "cpu_utilization_percent": 198.5,
  "elapsed_time_sec": 15.88
}
```

**計算方法**:
- `avg_frequency_ghz[cpu]`: CPU毎の平均周波数 = cycles[cpu] / (cpu-clock[cpu] / 1000) / 1e9
- `start_frequency_ghz[cpu]`: 開始時周波数（sysfsから取得、kHz → GHz変換）
- `end_frequency_ghz[cpu]`: 終了時周波数（sysfsから取得、kHz → GHz変換）
- `ipc[cpu]`: CPU毎のInstructions Per Cycle = instructions[cpu] / cycles[cpu]
- `cpu_utilization_percent`: 全CPUの利用率合計

### 実装モジュール
すべてのpts_runnerスクリプトで共通関数を使用:
- **関数名**: `parse_perf_stats_and_freq(perf_stats_file, freq_start_file, freq_end_file, cpu_list)`
- **配置**: 各pts_runner_<testname>.pyのクラス内メソッド
- **引数**:
  - `perf_stats_file`: perf statの出力ファイルパス
  - `freq_start_file`: 開始時周波数ファイルパス
  - `freq_end_file`: 終了時周波数ファイルパス
  - `cpu_list`: 使用したCPUリスト（例: "0,2,4"）
- **戻り値**: dict型のperf_summary

### システム要件
**perf_event_paranoid の設定**:
- `perf stat -A -a`（per-CPU統計 + system-wide監視）を使用するため、`perf_event_paranoid <= 0` が必要
- 各pts_runnerスクリプトは起動時に自動的に以下を実行:
  1. `/proc/sys/kernel/perf_event_paranoid` の値を確認
  2. 値が 1 以上の場合、`sudo sysctl -w kernel.perf_event_paranoid=0` で一時的に変更
  3. sudo権限がない場合は警告を表示して制限モードで続行（per-CPUメトリクスなし）

**perf_event_paranoid の値の意味**:
- `-1`: ほぼ全てのイベントをユーザーが使用可能（最も緩い）
- `0`: rawイベントとftraceトレースポイントを無効化（system-wide監視が可能）
- `1`: system-wideイベントアクセスを無効化（デフォルト）
- `2`: カーネルプロファイリングを無効化（最も厳しい）

### 注意事項
- パフォーマンス影響: < 0.01%（無視できるレベル）
- 時系列データは取得できない（ベンチマーク全体の平均値のみ）
- アーキテクチャ固有イベント（cache-misses等）は使用しない（互換性のため）
- `perf stat -A`はCPU毎の統計を出力（フォーマット: `CPU<n>`プレフィックス付き）
- CPU周波数はkHz単位で保存されているため、GHzへの変換が必要（÷ 1,000,000）
- 設定変更は一時的（再起動で元に戻る）、永続化は /etc/sysctl.conf に追加

---

## 新規テスト作成時の参考資料

新しいpts_runner_*.pyを作成する際は、以下を参照してください：

### コード構造テンプレート
詳細なテンプレートとサンプルコードは [`CODE_TEMPLATE.md`](./CODE_TEMPLATE.md) を参照。

### 参考実装
- `pts_runner_build-llvm-1.6.0.py` - 最も完全で正しい実装
- `pts_runner_coremark-1.0.1.py` - シンプルな実装例
- `pts_runner_apache-3.0.0.py` - Single-threaded benchmarkの例
