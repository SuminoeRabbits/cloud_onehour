# about README_results.md
ディレクトリ構造、ファイル構造、ファイル名の命名規則、データ内容を解説。
その後のデータベース作成向けJSONファイル(one_big_json.json)実装仕様を説明する。

# Directory and structure
まず`${PROJECT_ROOT}`に対して
`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>/<files>`
というディレクトリ構造になっている。今後の命名規則として下記の通りにする。
"machinename"=<machinename>
"os"=<os>
"testcategory"=<testcategory>
"benchmark"=<benchmark>
"files"=<files>

# TOC
<ここにTOCを記載してほしい>。

# File details
本件は`Phoronix Test Suite v10.8.4`をベースラインのインフラとして利用しプログラムを実行、結果をまとめて表示している。ここでは主に<files>の詳細について記載する。

## Common rule
データを読むうえで<files>内で利用されている共通の概念を説明する。

### Number of threads in hardware and Thread count in `<benchmark>`
`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>`デイレク取りの`<benchmark>`で利用するスレッド数は`<N>`で表され、ハードウェア資源の持っているスレッド数は`vCPU`として表される。`<benchmark>`毎に下記の3通りが出現することに注意。
 - `<N>=vCPU` : `<N>`は固定。マルチスレッド化によるスケールアウトの恩恵のみを想定している。
 - `<N>=1` : `<N>`は固定。マルチスレッド化が十分にされておらず、1スレッドでのスケールアップを想定している。
 - `<N>={1,2,3...,vCPU}`: `<N>`が最小1から最大`vCPU`までの間のいくつか出現していることがある。これは`<N>`増やすことで、スケールアップ、スケールアウトの両方をベンチマークする目的がある。

### relaionship between CPU affinity and `<N>`-Thread in <benchmark> 
`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>`デイレク取りの`<benchmark>`で利用されるスレッド数`<N>`であるが、実際に利用されるＣＰＵアフィニティの順序はamd64系のHyperThread機能を考慮して、`{0,2,4,6....,1,3,5,7..[vCPU-1]}`と設定している。

例えば`vCPU=4`, `<N>=2`では利用されるCPUアフィニティは`{0,2}`となる。CPUアフィニティはLinux標準コマンドである`taskset`で指定される。なお`<N>=vCPU`の場合はすべてのCPUアフィニティを利用していることになるので`taskset`は適応しない。
このようなCPUアフィニティの分散は`arm64`系プロセッサにおいて`vCPU=physical CPU`である場合は意味がをなさないが、両方のISAで対応できるようにこのような仕様にしている。

### How to distingush `<N>` in `<files>`
スレッド数`<N>`でテストが実行された際は、ログが`<files>`中の`<N>-thread...`で始まるファイル名で保存されている。これらが指定した`<N>`に対してすべて揃っている状態でテスト完了とする。

- `<N>-thread_freq_end.txt`:スレッド数`<N>`テスト終了時のCPUクロックリスト。
- `<N>-thread_freq_end.txt`:スレッド数`<N>`テスト開始時のCPUクロックリスト。
- `<N>-thread_perf_stats.txt`:スレッド数`<N>`テストのperf stat raw value。
- `<N>-thread_perf_summary.json`:スレッド数`<N>`テストのperf stat summary。
- `<N>-thread.csv`:すべてのスレッド数`<N>`テストのCSVまとめ。
- `<N>-thread.json`:すべてのスレッド数`<N>`テストのJSONまとめ。

[注意]そろっていない場合はテスト完了ではないので処理を行わない。

### summary file in `<files>`
`<benchmark>`が完了している場合のみ`<files>`中に`summary.json`,`summary.log`が生成される。この2ファイルはフォーマットの違いだけであり内容に違いはない。

[注意]そろっていない場合はテスト完了ではないので処理を行わない。


次に`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>`デイレク取りの`summary.json`についてデータの読み方を説明する。

#### Multiple "test_name", "description" in one <benchmark>
<benchmark>の中で複数の"test_name"が実行されているケースがある。この際は"description"を見て、それぞれの"test_name"の特徴を理解することが必要である。



## perf_stat output
ここでは独自拡張したOSの`perf stat`の出力ファイルを説明する。

### Performance summary file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/<N>-thread_perf_summary.json`　
    <N>スレッド毎にファイルが存在する。
    もしこのファイルが特定の<N>だけ存在しない場合は、そのスレッド数のテストは行っていない、もしくは失敗していることを意味する。

### Frequency file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/<N>-thread_freq_*.txt`　
    <N>スレッド毎にファイルが存在する。
    ファイルが存在していてもテストは失敗していることがあるので注意。
    ベンチマーク開始地点は<N>-threa_freq_start.txt。
    ベンチマーク終了地点は<N>-threa_freq_end.txt。
    CPUアフィニティ順に[Hz]単位で記録されている。

## pts output
ここではPTSの標準ベンチマーク出力を説明する。
### Benchmark summary file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/summary.json`
    すべての<N>-thread_perf_summary.jsonを<N>毎に1つにまとめたファイルであり、1つだけ存在。存在しない場合はテストが失敗している。
    1つのベンチマークに複数の<test_name>が存在する場合があり、その時は<test_name>毎にベンチマーク結果が記載される。。

# Extracting one big JSON
まずデータ構造を説明し、その後にそれぞれに入力されるべきデータを説明する。

## Definition of one big JSON structure
データ構造の定義は以下の通り。
```json
{
    "<machinename>": {
        "CSP": "<csp>",
        "total_vcpu": "<vcpu>",
        "cpu_name": "<cpu_name>",
        "cpu_isa": "<cpu_isa>",
        "os": {
            "<os>": {
                "testcategory": {
                    "<testcategory>": {
                        "benchmark": {
                            "<benchmark>": {
                                "thread": {
                                    "<N>": {
                                        "perf_stat": {
                                            "start_freq": {
                                                "freq_0": <freq_0>,
                                                "freq_1": <freq_1>,
                                                "freq_2": <freq_2>
                                            },
                                            "end_freq": {
                                                "freq_0": <freq_0>,
                                                "freq_1": <freq_1>,
                                                "freq_2": <freq_2>
                                            },
                                            "ipc": {
                                                "ipc_0": <ipc_0>,
                                                "ipc_1": <ipc_1>,
                                                "ipc_2": <ipc_2>
                                            },
                                            "total_cycles": {
                                                "total_cycles_0": <total_cycles_0>,
                                                "total_cycles_1": <total_cycles_1>,
                                                "total_cycles_2": <total_cycles_2>
                                            },
                                            "total_instructions": {
                                                "total_instructions_0": <total_instructions_0>,
                                                "total_instructions_1": <total_instructions_1>,
                                                "total_instructions_2": <total_instructions_2>
                                            },
                                            "cpu_utilization_percent": <cpu_utilization_percent>,
                                            "elapsed_time_sec": <elapsed_time_sec>
                                        },
                                        "test_name": {
                                            "<test_name>": {
                                                "description": "<description>",
                                                "values": "<values>",
                                                "unit": "<unit>",
                                                "time": "<time>",
                                                "cost": "<cost>"
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
```
## Definition data source
データ構造に挿入すべきデータソースを説明する。

### Look-Up-Table

`<machinename>`に含まれる文字列で決定される数字である。対応表は下記の通り。
ここに記載がない場合は、`<machinename>`はそのまま利用するが他の`<machinename>`向け情報は`N/A`とする。

|<machinename>が含む文字列|	csp  |vcpu  |cpu_name|cpu_isa|
| :---                   | :---:| :---:| :---: |:---: |
|"rpi5"                   |local|	4  |Cortex-A76|	Armv8.2-A|
|"t3" and "medium"|	AWS|	2|	Intel Xeon Platinum (8000 series)|	x86-64 (AVX-512)|
|"m8a" and "2xlarge"|	AWS|8|	AMD EPYC 9R45 (Zen 5 "Turin")|	x86-64 (AMX + AVX-512)|
|"m8i" and "2xlarge"|	AWS|8|	Intel Xeon 6 (6th Granite Rapids)|	x86-64 (AMX + AVX-512)|
|"i7ie" and "2xlarge"|	AWS|8|	Intel Xeon 5 Metal(5th Emerald Rapids)|	x86-64 (AMX + AVX-512)|
|"m7i" and "2xlarge"|	AWS|8|	Intel Xeon 4 (4th Sapphire Rapids)|	x86-64 (AMX + AVX-512)|
|"m8g" and "2xlarge"|	AWS|8|	Neoverse-V2 (Graviton4)|	Armv9.0-A (SVE2-128)|
|"e2-standard-2"|	GCP|	2|	Intel Xeon / AMD EPYC(Variable)|	x86-64|
|"c4d-standard-8"|	GCP|	8|	AMD EPYC 9B45 (Zen 5 "Turin")|	x86-64 (AMX + AVX-512)|
|"c4-standard-8"|	GCP|	8|	Intel Xeon Platinum 8581C (5th Emerald Rapids)|	x86-64 (AMX + AVX-512)|
|"c4a-standard-8"|	GCP|	8|	Neoverse-V2 (Google Axion)|	Armv9.0-A (SVE2-128) |
|"VM.Standard.E5.Flex"|	OCI|	8|	AMD EPYC 9J14 (Zen 4 "Genoa")|	x86-64 (AMX + AVX-512)|
|"VM.Standard.E6.Flex"|	OCI|	8|	AMD EPYC 9J45 (Zen 5 "Turin")|	x86-64 (AMX + AVX-512)|
|"VM.Standard.A1.Flex"|	OCI|	8|	Ampere one (v8.6A)|	Armv8.6 (NEON-128)|


#### "machinename":"\<machinename\>"
`${PROJECT_ROOT}`直下にあるディレクトリ名が<machinename>に相当する。複数ある場合はアルファベット順に登録する。

#### "total_vcpu":"\<vcpu\>"
`<machinename>`から決定される。(###Look-Up-Table)参照。`<machinename>`が決定されたら`${PROJECT_ROOT}/<machinename>`に移動する。

#### "cpu_name":"\<cpu_name\>"
`<machinename>`から決定される。(###Look-Up-Table)参照。

#### "cpu_isa":"\<cpu_isa\>"
`<machinename>`から決定される。(###Look-Up-Table)参照。

#### "CSP":"\<csp\>"
`<machinename>`から決定される。(###Look-Up-Table)参照。

#### "os":"\<os\>"
`${PROJECT_ROOT}/<machinename>`直下にあるディレクトリ名が`<os>`に相当する。複数ある場合はアルファベット順に登録する。

#### "testcategory":"\<testcategory\>"
`${PROJECT_ROOT}/<machinename>/<os>`直下にあるディレクトリ名が`<testcategory>`に相当する。
複数ある場合はアルファベット順に登録する。

#### "benchmark":"\<benchmark\>"
`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>`の直下にあるディレクトリ名が`<benchmark>`に相当する。
複数ある場合はアルファベット順に登録する。

#### data extract from "\<benchmark\>"
以下、`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>`からのデータ抽出法である。以下、このDirectoryを便宜上`${BENCHMARK}`と定義する。

##### "threads":"\<N\>"
まず`${BENCHMARK}`中に[How to distingush `<N>` in `<files>`](### How to distingush `<N>` in `<files>`)で`<N>`が特定されているとする。それぞれの `<N>` に対して個別に子ノードを生成する。

##### "\<N\>":"perf_stat"
[perf_stat output](## perf_stat output)を参照。

##### "\<N\>":"test_name"

一つの`<N>` に対して複数の`<test_name>`が存在しうる。`${BENCHMARK}/summary.json`より抽出する。


# make_one_big_json.py specification
ここでは、`one_big_json.json`を生成するPythonスクリプト`make_one_big_json.py`を実装する際の仕様について記す。

## requirement
Python3.10で動作すること。`make_one_big_json.py`自分自身と出力ファイルである`one_big_json.json`に対してSyntax Errorを検出する機能を有する。

## argument parameters
オプションは下記の通りとする。  
- `--dir`(省略可能) :　
    `${PROJECT_ROOT}`を指定する。なおここで指定される`${PROJECT_ROOT}`は複数あっても構わない。その場合でも`one_big_json.json`内でマージされることとする。省略された場合は`${PROJECT_ROOT}=${PWD}`と解釈する
- `--output`(省略可能):
    生成される`one_big_json.json`のDirectoryとファイル名を変更したいときに利用する。省略された場合は`${PWD}/one_big_json.json`と解釈され、もしすでに同名のファイルが存在する場合は上書き保存するかを確認する。
