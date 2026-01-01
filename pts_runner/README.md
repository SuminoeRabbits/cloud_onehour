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
PTSのCacheをすべてクリアする。
なおPTSはInstallされていることが前提(../scripts/setup_pts.sh)。

## clean up
PTSから<testname>をPTSのInstallコマンドを使ってクリーンインストールする、その際にはGcc-14を使って毎回Nativeでコンパイルを行う。環境としてはUbuntu22以上、Hardwareはarm64/amd64の両方を前提とする。

- なおデバッグの為に、このPTSのクリーンインストールコマンドを標準出力に出す。

- クリーンインストール時のBuildは、"THFix_in_compile": trueの場合はNUM_CPU_CORES=vCPU数でBinaryの対応するスレッド数設定、コンパイル時は最大限並列化させたいので -j`nproc` としてビルドを行う。

- クリーンインストール時のBuildは、"THFix_in_compile": falseの場合はNUM_CPU_CORESを指定しない。

- PTSのインストールコマンドはbatch-installを使う事。

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

- 環境設定はPTS実施の先に来ないとだめ。
例えば $> NUM_CPU_CORES=4 phoronix-test-suite benchmark

## CPU frequency monitoring with perf stat
ベンチマーク実行中のCPU動作周波数とパフォーマンス統計を取得するため、`perf stat`でPTSコマンドをラップする。

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
```
# 終了時サンプルの例
cmd_template = 'grep "cpu MHz" /proc/cpuinfo | \
    awk \'{{printf "%.0f\\n", $4 * 1000}}\' > {file}'
command = cmd_template.format(file=freq_end_file)
```

**重要な注意点**:
1. `perf stat`に`-A`（per-CPU統計）および`-a`（全CPU監視）オプションを使用することで、CPU毎のメトリクスを取得
2. **環境変数は必ず `perf stat` コマンドの前に配置すること**
   - ❌ 間違い: `perf stat -e ... -o output.txt NUM_CPU_CORES=4 phoronix-test-suite ...`
   - ✅ 正しい: `NUM_CPU_CORES=4 perf stat -e ... -o output.txt phoronix-test-suite ...`
   - 理由: `perf stat` は引数として渡された環境変数を実行するコマンドに伝播しない
3. PTSが設定する `$LOG_FILE` などの環境変数が正しく渡されないと "No such file or directory" エラーが発生する

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
