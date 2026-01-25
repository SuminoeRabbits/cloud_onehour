# about README_results.md
ディレクトリ構造、ファイル構造、ファイル名の命名規則、データ内容を解説。
その後のデータベース作成向けJSONファイル(one_big_json.json)実装仕様を説明する。

# TOC
- [about README\_results.md](#about-readme_resultsmd)
- [TOC](#toc)
- [Directory and structure](#directory-and-structure)
- [File details](#file-details)
  - [Common rule](#common-rule)
    - [Number of threads in hardware and Thread count in `<benchmark>`](#number-of-threads-in-hardware-and-thread-count-in-benchmark)
    - [relaionship between CPU affinity and `<N>`-Thread in ](#relaionship-between-cpu-affinity-and-n-thread-in-)
    - [How to distingush `<N>` in `<files>`](#how-to-distingush-n-in-files)
      - [ファイル一覧](#ファイル一覧)
    - [summary file in `<files>`](#summary-file-in-files)
      - [ケース1: `summary.json`、`<N>-thread_perf_stats.txt`と`<N>-thread.json`が存在](#ケース1-summaryjsonn-thread_perf_statstxtとn-threadjsonが存在)
      - [ケース2: `summary.json`と`<N>-thread_perf_stats.txt`はないが`<N>-thread.json`が存在](#ケース2-summaryjsonとn-thread_perf_statstxtはないがn-threadjsonが存在)
      - [ケース3: `<N>-thread.json`と`<N>-thread_perf_stats.txt`はないが`<N>-thread_perf_summary.json`が存在](#ケース3-n-threadjsonとn-thread_perf_statstxtはないがn-thread_perf_summaryjsonが存在)
      - [ケース4: このケースは将来の拡張の為に確保されている。](#ケース4-このケースは将来の拡張の為に確保されている)
      - [ケース5: 特殊ベンチマーク（`summary.json`も`<N>-thread_perf_summary.json`も`<N>-thread.json`も存在しない）](#ケース5-特殊ベンチマークsummaryjsonもn-thread_perf_summaryjsonもn-threadjsonも存在しない)
      - [Multiple "test\_name", "description" in one ](#multiple-test_name-description-in-one-)
        - ["test\_name"のキー生成ルール](#test_nameのキー生成ルール)
  - [perf\_stat output](#perf_stat-output)
    - [Performance summary file](#performance-summary-file)
    - [Performance summary file format](#performance-summary-file-format)
    - [Frequency file](#frequency-file)
    - [Frequency file format](#frequency-file-format)
  - [pts output](#pts-output)
    - [Benchmark summary file](#benchmark-summary-file)
    - [N-thread.json format](#n-threadjson-format)
- [Extracting one big JSON](#extracting-one-big-json)
  - [Definition of one big JSON structure](#definition-of-one-big-json-structure)
  - [Definition data source](#definition-data-source)
    - [Look-Up-Table](#look-up-table)
    - [Cost at Look-Up-Table](#cost-at-look-up-table)
      - ["machinename":"\<machinename\>"](#machinenamemachinename)
      - ["total\_vcpu":"\<vcpu\>"](#total_vcpuvcpu)
      - ["cpu\_name":"\<cpu\_name\>"](#cpu_namecpu_name)
      - ["cpu\_isa":"\<cpu\_isa\>"](#cpu_isacpu_isa)
      - ["CSP":"\<csp\>"](#cspcsp)
      - ["os":"\<os\>"](#osos)
      - ["testcategory":"\<testcategory\>"](#testcategorytestcategory)
      - ["benchmark":"\<benchmark\>"](#benchmarkbenchmark)
      - [data extract from "\<benchmark\>"](#data-extract-from-benchmark)
        - ["threads":"\<N\>"](#threadsn)
        - ["\<N\>":"perf\_stat"](#nperf_stat)
        - ["\<N\>":"test\_name"](#ntest_name)
          - [データソース](#データソース)
          - [ケース３ データソース 例外処理](#ケース３-データソース-例外処理)
          - [ケース５ データソース 例外処理](#ケース５-データソース-例外処理)
          - [descriptionによるマッチング](#descriptionによるマッチング)
        - ["\<N\>":"test\_name":"values", "raw\_values", "time", "test\_run\_times"](#ntest_namevalues-raw_values-time-test_run_times)
        - ["\<N\>":"test\_name":"cost"](#ntest_namecost)
- [make\_one\_big\_json.py specification](#make_one_big_jsonpy-specification)
  - [requirement](#requirement)
  - [script version info](#script-version-info)
  - [argument parameters](#argument-parameters)


# Directory and structure
まず`${PROJECT_ROOT}`に対して
`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>/<files>`
というディレクトリ構造になっている。今後の命名規則として下記の通りにする。
"machinename"=<machinename>
"os"=<os>
"testcategory"=<testcategory>
"benchmark"=<benchmark>
"files"=<files>

ファイルでANSIエスケープシーケンスを一斉除去する必要がある場合は、データ処理前にこのコマンドを適応させること。
```
find results -name "*.log" -type f -exec sed -i 's/\x1b\[[0-9;]*m//g' {} \;
```

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
スレッド数`<N>`でテストが実行された際は、ログが`<files>`中の`<N>-thread...`で始まるファイル名で保存されている。

#### ファイル一覧
- `<N>-thread_freq_end.txt`:スレッド数`<N>`テスト終了時のCPUクロックリスト。
- `<N>-thread_freq_start.txt`:スレッド数`<N>`テスト開始時のCPUクロックリスト。
- `<N>-thread_perf_stats.txt`:スレッド数`<N>`テストのperf stat raw value。
- `<N>-thread_perf_summary.json`:スレッド数`<N>`テストのperf stat summary。
- `<N>-thread.json`:すべてのスレッド数`<N>`テストのJSONまとめ。
- `<N>-thread.log`:スレッド数`<N>`テストの実行ログ。
- `stdout.log`:テスト実施時のSTDOUT。

[注意]上記ファイルの必須/オプションの区別は下記ケース毎に異なる。

### summary file in `<files>`
`<benchmark>`が完了している条件は４つのケースがありうる。必須ファイルはケース毎に異なる。

#### ケース1: `summary.json`、`<N>-thread_perf_stats.txt`と`<N>-thread.json`が存在
**必須ファイル:**
- `<N>-thread_freq_end.txt`
- `<N>-thread_freq_start.txt`
- `<N>-thread_perf_stats.txt`
- `<N>-thread.json`
- `summary.json`

#### ケース2: `summary.json`と`<N>-thread_perf_stats.txt`はないが`<N>-thread.json`が存在
**必須ファイル:**
- `<N>-thread_freq_end.txt`
- `<N>-thread_freq_start.txt`
- `<N>-thread.json`

#### ケース3: `<N>-thread.json`と`<N>-thread_perf_stats.txt`はないが`<N>-thread_perf_summary.json`が存在
**必須ファイル:**
- `<N>-thread_freq_end.txt`
- `<N>-thread_freq_start.txt`
- `<N>-thread_perf_summary.json`

#### ケース4: このケースは将来の拡張の為に確保されている。

#### ケース5: 特殊ベンチマーク（`summary.json`も`<N>-thread_perf_summary.json`も`<N>-thread.json`も存在しない）
以下のベンチマークはPTSの出力形式が異なるため、`<N>-thread.log`から直接結果を抽出する。
- `<benchmark>="build-gcc-1.5.0"`
- `<benchmark>="build-linux-kernel-1.17.1"`
- `<benchmark>="build-llvm-1.6.0"`
- `<benchmark>="coremark-1.0.1"`
- `<benchmark>="sysbench-1.1.0"`
- `<benchmark>="java-jmh-1.0.1"`
- `<benchmark>="ffmpeg-7.0.1"`
- `<benchmark>="apache-3.0.0"`

**必須ファイル:**
- `<N>-thread.log`（結果抽出に必須）

**オプションファイル:**
- `<N>-thread_freq_end.txt`（存在すれば読み込む）
- `<N>-thread_freq_start.txt`（存在すれば読み込む）

[注意]これら４条件のいづれかに合致しない場合はテスト完了ではないので処理を行わない。


次に`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>`デイレク取りの`summary.json`についてデータの読み方を説明する。

#### Multiple "test_name", "description" in one <benchmark>
<benchmark>の中で複数の"test_name"が実行されているケースがある。この際、同じ"test_name"でも"description"が異なる場合は、別々のテスト結果として扱う必要がある。

##### "test_name"のキー生成ルール
`one_big_json.json`の`"test_name"`ノードのキーは以下のルールで生成する：
1. `summary.json`内の`"test_name"`と`"description"`が両方とも同一の場合、同じキーとして扱う（上書き）
2. `"test_name"`は同じだが`"description"`が異なる場合は、`"<test_name> - <description>"`という形式で別のキーとして登録する
3. これにより、例えば"7-Zip Compression"という同じ`test_name`でも、"Test: Compression Rating"と"Test: Decompression Rating"という異なる`description`を持つ場合は、それぞれ別のノードとして登録される

例：
```json
"test_name": {
  "7-Zip Compression - Test: Compression Rating": {
    "description": "Test: Compression Rating",
    "values": 3951,
    "raw_values": [4302, 3873, 3759, 3598, 5147, 3880, 3938, 4476, 3794, 3262, 3736, 3644],
    "unit": "MIPS",
    "time": 42.43,
    "test_run_times": [42.43, 40.53, 38.42, 40.07, 39.75, 40.13, 41.04, 38.91, 43.08, 43.85, 40.96, 40.9],
    "cost": 0.000805
  },
  "7-Zip Compression - Test: Decompression Rating": {
    "description": "Test: Decompression Rating",
    "values": 3575,
    "raw_values": [3353, 3474, 4155, 3909, 3491, 3378, 3382, 3430, 3142, 3571, 3958, 3658],
    "unit": "MIPS",
    "time": 42.43,
    "test_run_times": [42.43, 40.53, 38.42, 40.07, 39.75, 40.13, 41.04, 38.91, 43.08, 43.85, 40.96, 40.9],
    "cost": 0.000805
  }
}
```



## perf_stat output
ここでは独自拡張したOSの`perf stat`の出力ファイルを説明する。

### Performance summary file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/<N>-thread_perf_summary.json`
    <N>スレッド毎にファイルが存在する。
    もしこのファイルが特定の<N>だけ存在しない場合は、そのスレッド数のテストは行っていない、もしくは失敗していることを意味する。

### Performance summary file format
`<N>-thread_perf_summary.json`のフォーマットは以下の通り。キーはCPUコア番号（文字列）で、値は数値。
```json
{
  "avg_frequency_ghz": {
    "0": 0.0,
    "1": 0.0,
    "2": 0.0,
    "3": 0.0
  },
  "start_frequency_ghz": {
    "0": 3.193,
    "1": 3.193,
    "2": 3.193,
    "3": 3.193
  },
  "end_frequency_ghz": {
    "0": 3.193,
    "1": 3.193,
    "2": 3.193,
    "3": 3.193
  },
  "ipc": {
    "0": 0.0,
    "1": 0.0,
    "2": 0.0,
    "3": 0.0
  },
  "total_cycles": {
    "0": 0,
    "1": 0,
    "2": 0,
    "3": 0
  },
  "total_instructions": {
    "0": 0,
    "1": 0,
    "2": 0,
    "3": 0
  },
  "cpu_utilization_percent": 100.0,
  "elapsed_time_sec": 1383.7
}
```

**フィールド説明:**
- `avg_frequency_ghz`: 各CPUコアの平均周波数（GHz）
- `start_frequency_ghz`: テスト開始時の各CPUコアの周波数（GHz）
- `end_frequency_ghz`: テスト終了時の各CPUコアの周波数（GHz）
- `ipc`: 各CPUコアのIPC（Instructions Per Cycle）
- `total_cycles`: 各CPUコアの総サイクル数
- `total_instructions`: 各CPUコアの総命令数
- `cpu_utilization_percent`: CPU使用率（%）
- `elapsed_time_sec`: 経過時間（秒）

**one_big_json.jsonへのマッピング:**
`<N>-thread_perf_summary.json`から`one_big_json.json`の`perf_stat`ノードへは以下のようにマッピングする:
- `start_freq.freq_<i>`: `start_frequency_ghz["<i>"]` × 1000000000（GHzからHzに変換）
- `end_freq.freq_<i>`: `end_frequency_ghz["<i>"]` × 1000000000（GHzからHzに変換）
- `ipc.ipc_<i>`: `ipc["<i>"]`
- `total_cycles.total_cycles_<i>`: `total_cycles["<i>"]`
- `total_instructions.total_instructions_<i>`: `total_instructions["<i>"]`
- `cpu_utilization_percent`: `cpu_utilization_percent`
- `elapsed_time_sec`: `elapsed_time_sec`

### Frequency file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/<N>-thread_freq_*.txt`
    <N>スレッド毎にファイルが存在する。
    ファイルが存在していてもテストは失敗していることがあるので注意。
    ベンチマーク開始地点は`<N>-thread_freq_start.txt`。
    ベンチマーク終了地点は`<N>-thread_freq_end.txt`。
    CPUアフィニティ順に[KHz]単位で記録されている。

### Frequency file format
`<N>-thread_freq_start.txt`および`<N>-thread_freq_end.txt`のフォーマットは以下の通り。
各行がCPUコア（0から順番）の周波数（KHz単位）を表す。
```
3192614
3192614
3192614
3192614

```

**注意:**
- ファイル末尾に空行が含まれることがある
- 行数はvCPU数と一致する
- 単位はKHz（キロヘルツ）
- `<N>-thread_perf_summary.json`が存在する場合、そちらの`start_frequency_ghz`/`end_frequency_ghz`を優先して使用する（GHz単位で記録されているため変換が容易）

## pts output
ここではPTSの標準ベンチマーク出力を説明する。
### Benchmark summary file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/summary.json`
    すべての<N>-thread_perf_summary.jsonを<N>毎に1つにまとめたファイルであり、1つだけ存在。ただし存在しない場合もある。
    1つのベンチマークに複数の<test_name>が存在する場合があり、その時は<test_name>毎にベンチマーク結果が記載される。

### N-thread.json format
`<N>-thread.json`はPhoronix Test Suiteが出力する詳細な結果ファイルである。以下にフォーマットを示す。

```json
{
    "title": "nginx-3.0.1-4threads",
    "last_modified": "2026-01-17 17:33:42",
    "description": "wsl testing on Ubuntu 22.04 via the Phoronix Test Suite.",
    "systems": {
        "<system_identifier>": {
            "identifier": "<system_identifier>",
            "hardware": {
                "Processor": "Intel Core i5-4460 (4 Cores)",
                "Memory": "8GB",
                "Disk": "...",
                "Graphics": "llvmpipe"
            },
            "software": {
                "OS": "Ubuntu 22.04",
                "Kernel": "6.6.87.2-microsoft-standard-WSL2 (x86_64)",
                "Compiler": "GCC 14.2.0 + Clang 14.0.0-1ubuntu1.1",
                "File-System": "ext4"
            },
            "user": "snakajim",
            "timestamp": "2026-01-17 17:21:30",
            "client_version": "10.8.4"
        }
    },
    "results": {
        "<result_hash>": {
            "identifier": "pts/<benchmark_name>",
            "title": "<test_title>",
            "app_version": "<version>",
            "arguments": "<test_arguments>",
            "description": "<test_description>",
            "scale": "<unit>",
            "proportion": "HIB|LIB",
            "display_format": "BAR_GRAPH",
            "results": {
                "<system_identifier>": {
                    "value": 2195.02,
                    "raw_values": [2195.02, 2200.15, 2189.88],
                    "test_run_times": [86.99, 85.50, 87.20],
                    "details": {}
                }
            }
        }
    }
}
```

**重要なフィールド:**
- `results.<result_hash>.title`: テスト名（`test_name`として使用）
- `results.<result_hash>.description`: テストの説明（`description`として使用）
- `results.<result_hash>.scale`: 単位（`unit`として使用）
- `results.<result_hash>.results.<system_identifier>.value`: 代表値（`values`として使用）
- `results.<result_hash>.results.<system_identifier>.raw_values`: 生データ配列（存在しない場合は`value`を配列として使用）
- `results.<result_hash>.results.<system_identifier>.test_run_times`: 各実行の実行時間配列

**注意:**
- `raw_values`が存在しない場合、`value`の値を単一要素の配列`[value]`として使用する
- `test_run_times`が存在しない場合、`<N>-thread_perf_summary.json`の`elapsed_time_sec`を使用する
- 同じ`<N>-thread.json`内に複数のテスト結果（異なる`<result_hash>`）が存在することがある

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
        "cost_hour[730h-mo]":"<cost>",
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
                                                "raw_values": [<raw_value_0>, <raw_value_1>, ...],
                                                "unit": "<unit>",
                                                "time": "<time>",
                                                "test_run_times": [<time_0>, <time_1>, ...],
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

|<machinename>が含む文字列|	csp  |vcpu  |cpu_name|cpu_isa| cost_hour[730h-mo] |
| :---                   | :---:| :---:| :---:  |:---:  | :---:      |
|"rpi5"                   |local|	4  |Cortex-A76|	Armv8.2-A| 0.0 |
|"t3" and "medium"|	AWS|	2|	Intel Xeon Platinum (8000 series)|	x86-64 (AVX-512)| 0.0183 |
|"m8a" and "2xlarge"|	AWS|8|	AMD EPYC 9R45 (Zen 5 "Turin")|	x86-64 (AMX + AVX-512)| 0.3164 |
|"m8i" and "2xlarge"|	AWS|8|	Intel Xeon 6 (6th Granite Rapids)|	x86-64 (AMX + AVX-512)| 0.2594 |
|"i7ie" and "2xlarge"|	AWS|8|	Intel Xeon 5 Metal(5th Emerald Rapids)|	x86-64 (AMX + AVX-512)| 1.2433 |
|"m7i" and "2xlarge"|	AWS|8|	Intel Xeon 4 (4th Sapphire Rapids)|	x86-64 (AMX + AVX-512)| 0.5405 |
|"m8g" and "2xlarge"|	AWS|8|	Neoverse-V2 (Graviton4)|	Armv9.0-A (SVE2-128)| 0.2274 |
|"e2-standard-2"|	GCP|	2|	Intel Xeon / AMD EPYC(Variable)|	x86-64| 0.0683 |
|"c4d-standard-8"|	GCP|	8|	AMD EPYC 9B45 (Zen 5 "Turin")|	x86-64 (AMX + AVX-512)| 0.4057 |
|"c4-standard-8"|	GCP|	8|	Intel Xeon Platinum 8581C (5th Emerald Rapids)|	x86-64 (AMX + AVX-512)| 0.4231 |
|"c4a-standard-8"|	GCP|	8|	Neoverse-V2 (Google Axion)|	Armv9.0-A (SVE2-128) | 0.3869 |
|"E5" and "Flex"|	OCI|	8|	AMD EPYC 9J14 (Zen 4 "Genoa")|	x86-64 (AMX + AVX-512)| 0.1925 |
|"E6" and "Flex"|	OCI|	8|	AMD EPYC 9J45 (Zen 5 "Turin")|	x86-64 (AMX + AVX-512)| 0.1925 |
|"A1" and "Flex"|	OCI|	8|	Ampere one (v8.6A)|	Armv8.6 (NEON-128)| 0.0599 |
|"A2" and "Flex"|	OCI|	8|	Ampere one (v8.6A)|	Armv8.6 (NEON-128)| 0.1845 |
|"A4" and "Flex"|	OCI|	8|	Ampere one (v8.6A)|	Armv8.6 (NEON-128)| 0.2053 |


### Cost at Look-Up-Table
`"cost_hour[730h-mo]":"<cost>"`は1時間当たりの利用コストである（USD/時間）。この値はLook-Up-Tableから直接取得する。Look-Up-Tableに記載がない`<machinename>`の場合は`0.0`とする。

**コスト計算の注意:**
- Look-Up-Tableの`cost_hour[730h-mo]`はCPUコストと追加ストレージコストの合計値として事前計算されている
- 新しいインスタンスタイプを追加する場合は、Look-Up-Tableに直接エントリを追加すること

#### "machinename":"\<machinename\>"
`${PROJECT_ROOT}`直下にあるディレクトリ名が`<machinename>`に相当する。複数ある場合はアルファベット順に登録する。
`${PROJECT_ROOT}/<machinename>`直下にあるディレクトリ名を`<os>`、さらに`${PROJECT_ROOT}/<machinename>/<os>`直下にあるディレクトリを`<testcategory>`とするので、ディレクトリ構造が2層以上下位にない場合は`<machinename>`としない。
`<machinename>`が決定されたら`${PROJECT_ROOT}/<machinename>`に移動する。

#### "total_vcpu":"\<vcpu\>"
`<machinename>`から決定される。(###Look-Up-Table)参照。

#### "cpu_name":"\<cpu_name\>"
`<machinename>`から決定される。(###Look-Up-Table)参照。

#### "cpu_isa":"\<cpu_isa\>"
`<machinename>`から決定される。(###Look-Up-Table)参照。

#### "CSP":"\<csp\>"
`<machinename>`から決定される。(###Look-Up-Table)参照。

#### "os":"\<os\>"
`${PROJECT_ROOT}/<machinename>`直下にあるディレクトリ名が`<os>`に相当する。複数ある場合はアルファベット順に登録する。
`${PROJECT_ROOT}/<machinename>/<os>`直下にあるディレクトリ名を`<testcategory>`、さらに`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>`直下にあるディレクトリを`<benchmark>`とするので、ディレクトリ構造が2層以上下位にない場合は`<os>`としない。
`<os>`が決定されたら`${PROJECT_ROOT}/<machinename>/<os>`に移動する。

#### "testcategory":"\<testcategory\>"
`${PROJECT_ROOT}/<machinename>/<os>`直下にあるディレクトリ名が`<testcategory>`に相当する。
複数ある場合はアルファベット順に登録する。
`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>`直下にあるディレクトリ名を`<benchmark>`とするので、ディレクトリ構造が1層以上下位にない場合は`<testcategory>`としない。
`<testcategory>`が決定されたら`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>`に移動する。

#### "benchmark":"\<benchmark\>"
`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>`の直下にあるディレクトリ名が`<benchmark>`に相当する。
複数ある場合はアルファベット順に登録する。
`<benchmark>`が決定されたら`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>`に移動する。

#### data extract from "\<benchmark\>"
以下、`${PROJECT_ROOT}/<machinename>/<os>/<testcategory>/<benchmark>`からのデータ抽出法である。このDirectoryを環境変数`${BENCHMARK}`と定義して参照する。

##### "threads":"\<N\>"
まず`${BENCHMARK}`中に[How to distingush `<N>` in `<files>`](#how-to-distingush-n-in-files)で`<N>`が特定されているとする。それぞれの `<N>` に対して個別に子ノードを生成する。

##### "\<N\>":"perf_stat"
[perf_stat output](#perf_stat-output)を参照。

##### "\<N\>":"test_name"

一つの`<N>` に対して複数の`<test_name>`が存在しうる。**重要**: データは`${BENCHMARK}/<N>-thread.json`から直接取得する。`summary.json`は平均化されたデータなので使用しない。

###### データソース
[summary file](#summary-file-in-files)のケース１，２の場合、すべてのデータは`${BENCHMARK}/<N>-thread.json`から取得する:
- **values**: ベンチマーク結果の代表値（通常は平均値）
- **raw_values**: ベンチマーク実行の全生データ配列
- **unit**: 単位（MIPS, MB/s等）
- **test_run_times**: 各実行の実行時間の配列（秒単位）
- **description**: テストの詳細説明

[summary file](#summary-file-in-files)のケース3の場合、すべてのデータは`${BENCHMARK}/<N>-thread_perf_summary.json`と`${BENCHMARK}/<N>-thread.json`から取得する。基本異なるフォーマットが利用されているので、**test_run_times**以外はN/Aとして`<test_name>`以下の子ノードを生成する。:
- **values**: N/A
- **raw_values**: N/A
- **unit**: N/A
- **test_run_times**: <N>-thread_perf_summary.json内の"elapsed_time_sec"を採用（秒単位）
- **description**: テストの詳細説明

データソースとして、ケース１，２，３のどれを適応させたか、`<test_name>`毎に明確にする。

###### ケース３ データソース 例外処理
ケース３でのデータソース例外処理について説明する。

ケース３は`<N>-thread.json`が存在せず、`<N>-thread_perf_summary.json`のみが存在する状況である。
この場合、ベンチマーク結果の詳細情報（values, raw_values, unit等）は取得できないため、以下のように処理する：

**処理方法:**
1. `test_name`は`<benchmark>`名から生成する（例: `openssl-3.6.0` → `"OpenSSL"`）
2. `description`は`"Benchmark: <benchmark>"`形式とする
3. `values`, `raw_values`, `unit`は`"N/A"`とする
4. `test_run_times`は`<N>-thread_perf_summary.json`の`elapsed_time_sec`を単一要素配列`[elapsed_time_sec]`として使用
5. `time`は`elapsed_time_sec`をそのまま使用
6. `cost`は通常通り計算する

**例:**
```json
"test_name": {
  "OpenSSL": {
    "description": "Benchmark: openssl-3.6.0",
    "values": "N/A",
    "raw_values": "N/A",
    "unit": "N/A",
    "time": 1383.7,
    "test_run_times": [1383.7],
    "cost": 0.026234
  }
}
```

**注意:**
- ケース３が発生するのは、PTSが正常に結果JSONを出力しなかったが、perf statの計測は完了した場合
- このケースでは性能値は取得できないが、実行時間とコストは計算可能

###### ケース５ データソース 例外処理
ケース５でのデータソース例外処理について、各`<benchmark>`特有の特殊事情を説明する。

- `<benchmark>="build-gcc-1.5.0"`:
    `<N>-thread.log`内の `Average: XXXX.XXXX Seconds`が存在しなければならない。`<N>`が複数あるのでそれらすべてに適応。
    - **values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **raw_values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **unit**: `"Seconds"`
    - **test_run_times**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **description**: "Timed GCC Compilation 15.2"

- `<benchmark>="build-linux-kernel-1.17.1"`:
    `<N>-thread.log`内の `Average: XXXX.XXXX Seconds`が存在しなければならない。`<N>`が複数あるのでそれらすべてに適応。
    - **values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **raw_values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **unit**: `"Seconds"`
    - **test_run_times**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **description**: "Timed Linux Kernel Compilation 6.15"

- `<benchmark>="build-llvm-1.6.0"`:
    `<N>-thread.log`内の `Average: XXXX.XXXX Seconds`が存在しなければならない。`<N>`が複数あるのでそれらすべてに適応。
    - **values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **raw_values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **unit**: `"Seconds"`
    - **test_run_times**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **description**: "Timed LLVM Compilation 21.1"

- `<benchmark>="coremark-1.0.1"`:
    `<N>-thread.log`内の `Average: XXXX.XXXX Iterations/Sec`が存在しなければならない。
    - **values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **raw_values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **unit**: `"Iterations/Sec"`
    - **test_run_times**: "N/A"
    - **description**: "Coremark 1.0"

- `<benchmark>="sysbench-1.1.0"`:
    このベンチマークは1つの`<N>-thread.log`内に**2つの独立したテスト**が含まれる。
    それぞれを別の`<test_name>`として登録する。

    **テスト1: RAM_Memory**
    - ログ内のセクション: `Test: RAM / Memory:`
    - パターン: `Average: XXXX.XXXX MiB/sec`
    - **values**: `XXXX.XXXX` を抽出
    - **raw_values**: `XXXX.XXXX` を抽出
    - **unit**: `"MiB/sec"`
    - **test_run_times**: `"N/A"`
    - **description**: `"Sysbench 1.0.20 Memory"`

    **テスト2: CPU**
    - ログ内のセクション: `Test: CPU:`
    - パターン: `Average: XXXX.XXXX Events Per Second`
    - **values**: `XXXX.XXXX` を抽出
    - **raw_values**: `XXXX.XXXX` を抽出
    - **unit**: `"Events Per Second"`
    - **test_run_times**: `"N/A"`
    - **description**: `"Sysbench 1.0.20 CPU"`

    **出力例**:
    ```json
    "test_name": {
        "RAM_Memory": {
            "description": "Sysbench 1.0.20 Memory",
            "values": 5293.59,
            "raw_values": [5289.24, 5298.85, 5292.69],
            "unit": "MiB/sec",
            "test_run_times": "N/A",
            "cost": 0.000XXX
        },
        "CPU": {
            "description": "Sysbench 1.0.20 CPU",
            "values": 826.48,
            "raw_values": [829.78, 827.55, 822.12],
            "unit": "Events Per Second",
            "test_run_times": "N/A",
            "cost": 0.000XXX
        }
    }
    ```

- `<benchmark>="java-jmh-1.0.1"`:
    `<N>-thread.log`内の `Average: XXXX.XXXX Ops/s`が存在しなければならない。
    - **values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **raw_values**: `<N>-thread.log`から`XXXX.XXXX` を抽出
    - **unit**: `"Ops/s"`
    - **test_run_times**: "N/A"
    - **description**: "Java JMH"

- `<benchmark>="ffmpeg-7.0.1"`:
    `<N>-thread.log`内に複数のテストが存在する。各テストは`Encoder: <encoder> - Scenario: <scenario>`の形式でヘッダーがあり、その後に`Average: XXXX.XX FPS`の形式で結果が出力される。
    - **test_name**: `"FFmpeg 7.0 - Encoder: <encoder> - Scenario: <scenario>"`（複数存在）
    - **values**: `<N>-thread.log`から`XXXX.XX` を抽出
    - **raw_values**: `<N>-thread.log`から`XXXX.XX` を抽出
    - **unit**: `"FPS"`
    - **test_run_times**: "N/A"
    - **description**: `"Encoder: <encoder> - Scenario: <scenario>"`

    例えば以下のテストが存在する：
    - "FFmpeg 7.0 - Encoder: libx264 - Scenario: Live"
    - "FFmpeg 7.0 - Encoder: libx265 - Scenario: Live"
    - "FFmpeg 7.0 - Encoder: libx264 - Scenario: Upload"
    - "FFmpeg 7.0 - Encoder: libx265 - Scenario: Upload"
    - "FFmpeg 7.0 - Encoder: libx264 - Scenario: Platform"
    - "FFmpeg 7.0 - Encoder: libx265 - Scenario: Platform"
    - "FFmpeg 7.0 - Encoder: libx264 - Scenario: Video On Demand"
    - "FFmpeg 7.0 - Encoder: libx265 - Scenario: Video On Demand"

- `<benchmark>="apache-3.0.0"`:
    このベンチマークは1つの`<N>-thread.log`内に**複数の独立したテスト**（異なるConcurrent Requests数）が含まれる。
    それぞれを別の`<test_name>`として登録する。
    
    各テストは`pts/apache-3.0.0 [Concurrent Requests: XXX]`の形式でヘッダーがあり、その後に`Average: XXXX.XX Requests Per Second`の形式で結果が出力される。
    
    - **test_name**: `"Apache HTTP Server 2.4.56 - Concurrent Requests: XXX"`（複数存在）
    - **values**: `<N>-thread.log`から`Average: XXXX.XX Requests Per Second`の`XXXX.XX`を抽出
    - **raw_values**: `<N>-thread.log`から`Average: XXXX.XX Requests Per Second`の`XXXX.XX`を抽出（quickモードでは1回のみ実行のため単一要素配列）
    - **unit**: `"Requests Per Second"`
    - **test_run_times**: `"N/A"`
    - **description**: `"Concurrent Requests: XXX"`
    
    例えば以下のテストが存在する：
    - "Apache HTTP Server 2.4.56 - Concurrent Requests: 4"
    - "Apache HTTP Server 2.4.56 - Concurrent Requests: 20"
    - "Apache HTTP Server 2.4.56 - Concurrent Requests: 100"
    - "Apache HTTP Server 2.4.56 - Concurrent Requests: 200"
    - "Apache HTTP Server 2.4.56 - Concurrent Requests: 500"
    - "Apache HTTP Server 2.4.56 - Concurrent Requests: 1000"
    
    **出力例**:
    ```json
    "test_name": {
        "Apache HTTP Server 2.4.56 - Concurrent Requests: 4": {
            "description": "Concurrent Requests: 4",
            "values": 652.06,
            "raw_values": [652.06],
            "unit": "Requests Per Second",
            "test_run_times": "N/A",
            "cost": 0.000XXX
        },
        "Apache HTTP Server 2.4.56 - Concurrent Requests: 200": {
            "description": "Concurrent Requests: 200",
            "values": 1168.48,
            "raw_values": [1168.48],
            "unit": "Requests Per Second",
            "test_run_times": "N/A",
            "cost": 0.000XXX
        }
    }
    ```

###### descriptionによるマッチング
[summary file](#summary-file-in-files)のケース１，２の場合は
`<N>-thread.json`には複数のテスト結果が含まれ、同じスレッド数`<N>`で同じ`test_name`でも`description`が異なる場合がある。正しいデータを選択するためのマッチングルール:

**マッチングルール**:
1. `<N>-thread.json`から`test_name`と`description`の組み合わせを取得
2. `<N>-thread.json`内で同じ`test_name`と`description`を持つエントリを検索
3. 一致したエントリから以下を取得:
   - `raw_values`配列全体
   - `test_run_times`配列全体
   - `value`（代表値）
   - `unit`（単位）
4. `time`フィールドには`test_run_times`の中央値（median）を使用
   - **理由**: ベンチマーク実行時にたまに外れ値が出るため、中央値が最も信頼性の高い代表値となる
   - 平均値ではなく中央値を使用することで、異常値の影響を受けにくいコスト計算が可能

##### "\<N\>":"test_name":"values", "raw_values", "time", "test_run_times"

これらのフィールドはすべて`${BENCHMARK}/<N>-thread.json`から取得する。

**取得方法**:
1. `${BENCHMARK}/<N>-thread.json`を開く
2. 該当する`test_name`と`description`の組み合わせに対応するエントリを探す
3. そのエントリから以下を取得:
   - `values`: `value`フィールドの値（ベンチマークスコアの代表値）
   - `raw_values`: `raw_values`配列全体（各実行のベンチマークスコア）
   - `test_run_times`: `test_run_times`配列全体（各実行の実行時間、秒単位）
   - `time`: `test_run_times[0]`（最初の実行時間、コスト計算に使用）

**例**:
```json
// <N>-thread.jsonから取得したデータ
{
  "value": 3951,
  "raw_values": [4302, 3873, 3759, 3598, 5147, 3880, 3938, 4476, 3794, 3262, 3736, 3644],
  "test_run_times": [42.43, 40.53, 38.42, 40.07, 39.75, 40.13, 41.04, 38.91, 43.08, 43.85, 40.96, 40.9]
}

// one_big_json.jsonへの出力
{
  "values": 3951,
  "raw_values": [4302, 3873, 3759, 3598, 5147, 3880, 3938, 4476, 3794, 3262, 3736, 3644],
  "test_run_times": [42.43, 40.53, 38.42, 40.07, 39.75, 40.13, 41.04, 38.91, 43.08, 43.85, 40.96, 40.9],
  "time": 42.43  // test_run_times[0]
}
```

##### "\<N\>":"test_name":"cost"

`cost`はテスト実行にかかったコストをドル単位で表す。

**計算式**:
```
cost = cost_hour[730h-mo] × time / 3600
```

**計算手順**:
1. `<machinename>`から決定される`cost_hour[730h-mo]`を取得（1時間あたりのコスト、ドル単位）
2. 上記で取得した`time`（秒単位）を3600で割って時間単位に変換
3. 両者を乗算してコストを算出
4. JSONでの表記は小数点6桁までで7桁を四捨五入。例：0.023420

**例**:
- `cost_hour[730h-mo]` = 0.0683 ドル/時間
- `time` = 42.43 秒
- `cost` = 0.0683 × (42.43 / 3600) = 0.0683 × 0.01179 ≈ 0.000805 ドル

# make_one_big_json.py specification
ここでは、`one_big_json.json`を生成するPythonスクリプト`make_one_big_json.py`を実装する際の仕様について記す。

## requirement
Python3.10で動作すること。`make_one_big_json.py`自分自身と出力ファイルである`one_big_json.json`に対してSyntax Errorを検出する機能を有する。

## script version info
スクリプト前段コメント欄にこのスクリプトの生成時刻を明記し、それを`version info`とする。生成されるJSONファイルの先頭に対しても、下記の生成データログを入れる。`version info`のフォーマットとしては`v<major>.<minor>.<patch>-g<git-hash>`とする。

{
    "generation log":{
        "version info": "<version>"
        "date": "<yyyymmdd-hhmmss>"
    }
}

## argument parameters
オプションは下記の通りとする。  
- `--dir` or `-D`(省略可能) :　
    `${PROJECT_ROOT}`を指定する。なおここで指定される`${PROJECT_ROOT}`は複数あっても構わない。その場合でも`one_big_json.json`内でマージされることとする。省略された場合は`${PROJECT_ROOT}=${PWD}`と解釈する。
- `--output` or `-O`(省略可能):
    生成される`one_big_json.json`のDirectoryとファイル名を変更したいときに利用する。省略された場合は`${PWD}/one_big_json_${HOSTNAME}.json`と解釈され、もしすでに同名のファイルが存在する場合は上書き保存するかを確認する。なおHOSTNAME=`hostname`とする。
- `--force` or `-F`(省略可能):
    `--output` が指定されたときに上書き確認をおこなわない。
- `--merge` or `-M`（省略可能）:
    `one_big_json.json`をDiretoryから生成するのではなく、複数のJSONをマージして１つのＪＳＯＮを作成する。マージの際は与えられた各ＪＳＯＮファイルの階層が一致するようにマージする。このオプションが指定された際は`--output`によりマージ先ファイルがデフォルト以外に指定されなければならない。よって利用方法は、例えば下記の様になる。
    make_one_big_json.py --merge ./1.JSON ./2.JSON .... --output ./New.JSON
    マージされるJSON同士はスクリプトの`"version info":<version>`が一致していなければならない。一致していない場合はErrorを出して終了する。
    マージされるJSONが指定されていない場合は、`${PWD}/one_big_json_*.json`のリストを引数とする。
