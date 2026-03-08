# README_analytics.md
`README_results.md` から生成された `one_big_json.json` を用いたデータ解析の仕様、および解析スクリプト `one_big_json_analytics.py` の実装要件を定義します。

---

# TOC
- [README\_analytics.md](#readme_analyticsmd)
- [TOC](#toc)
- [1. Benchmark information (ベンチマーク情報)](#1-benchmark-information-ベンチマーク情報)
- [2. Performance comparison (絶対性能比較)](#2-performance-comparison-絶対性能比較)
- [3. Cost comparison (コスト効率比較)](#3-cost-comparison-コスト効率比較)
- [4. Thread scaling comparison (スレッドスケーリング特性比較)](#4-thread-scaling-comparison-スレッドスケーリング特性比較)
- [5. CSP instance comparison (CSPインスタンス比較)](#5-csp-instance-comparison-cspインスタンス比較)
- [6. one\_big\_json\_analytics.py 仕様](#6-one_big_json_analyticspy-仕様)

---
# 1. Benchmark information
`<benchmark>`の付加情報を下記のTableに作成する。なおこれは `${PWD}/../test_suite.json` の情報を利用しているので、更新時には変更されるものとする。

| testcategory | benchmark | test_snippet | gcc_ver |
| ------------ | --------- | ------------ | ------- |
| Processor | coremark-1.0.1 | Coremark-1.0 | 14.2-system |
| Processor | simdjson-2.1.0 | simdjson-3.10 | 14.2-system |
| Processor | cpuminer-opt-1.8.0 | Cpuminer-Opt-24.3 | 14.2-system |
| System | sysbench-1.1.0 | Sysbench-1.0.20 | 14.2-system |
| System | glibc-bench-1.9.0 | Glibc Benchmarks-2.39 | 14.2-system |
| Build Process | build-linux-kernel-1.17.1 | Timed Linux Kernel Compilation-6.15 | 14.2-system |
| Build Process | build-llvm-1.6.0 | Timed LLVM Compilation-21.1 | 14.2-system |
| Build Process | build-gcc-1.5.0 | Timed GCC Compilation-15.2 | 14.2-system |
| Compression | compress-7zip-1.12.0 | 7-Zip Compression-25.00 | 14.2-system |
| Compression | compress-zstd-1.6.0 | Zstd Compression-1.5.4 | 14.2-system |
| Compression | compress-xz-1.1.0 | XZ Compression-5.2.4 | 14.2-system |
| Compression | compress-lz4-1.10.0 | LZ4 Compression-1.10 | 14.2-system |
| Cryptography and TLS | rustls-1.0.0 | Rustls-0.23.17 | 14.2-system |
| Cryptography and TLS | openssl-3.6.0 | OpenSSL-3.6 | 14.2-system |
| Cryptography and TLS | nginx-3.0.1 | nginx-1.23.2 | 14.2-system |
| Java Applications | renaissance-1.4.0 | Renaissance-0.16 | 14.2-system |
| Java Applications | java-jmh-1.0.1 | Java JMH-1.0.1 | 14.2-system |
| Network | phpbench-1.1.6 | PHPBench-0.8.1 | 14.2-system |
| Multimedia | ffmpeg-7.0.1 | FFmpeg-7.0 | 14.2-system |
| Multimedia | x264-2.7.0 | x264-2022-02-22 | 14.2-system |
| Multimedia | x265-1.5.0 | x265-4.1 | 14.2-system |
| Multimedia | kvazaar-1.2.0 | Kvazaar-2.2 | 14.2-system |
| Multimedia | avifenc-1.4.1 | libavif avifenc-1.0 | 14.2-system |
| Multimedia | webp-1.4.0 | WebP Image Encode-1.4 | 14.2-system |
| Multimedia | svt-av1-2.17.0 | SVT-AV1-4.0 | 14.2-system |
| Database | pgbench-1.17.0 | PostgreSQL-18.1 | 14.2-system |
| Database | pgbench-1.11.1 | PostgreSQL pgbench-14.0 | 14.2-system |
| Database | redis-1.5.0 | Redis-8.6 | 14.2-system |
| Database | valkey-1.1.0 | Valkey-9.0.2 | 14.2-system |
| Database | cassandra-1.3.1 | Apache Cassandra-5.0 | 14.2-system |
| Memory Access | stream-1.3.4 | Stream-2013-01-17 | 14.2-system |
| Memory Access | tinymembench-1.0.2 | Tinymembench-2018-05-28 | 14.2-system |
| Memory Access | memcached-1.2.0 | Memcached-1.6.19 | 14.2-system |
| Memory Access | cachebench-1.2.0 | CacheBench-1.2.0 | 14.2-system |
| AI | tensorflow-lite-1.1.0 | TensorFlow Lite-2022-05-18 | 14.2-system |


# 2. Performance comparison (絶対性能比較)
**目的**: 同一 OS 環境下で、プロセッサ世代（CPU 名/ISA）ごとの絶対的な処理能力を比較し、順位付け（リーダーボード）を行います。

## 基準データの設定
- **性能値の選択**: 
  - `test_name` に `values` が存在する場合：その数値を性能値とします。
        "unit": "Microseconds", "Seconds"などと`values`の単位が時間の場合、小さいほうが優位。
        それ以外は大きいほうが優位。"Frames Per Second", "Requests Per Second"などは大きいほうが優位なことに注意。
  - `values` が存在しない場合：`time`（秒）を性能値（低スコアほど良好）とします。
- **相対性能**: 各 OS・スレッド数における最速機を基準（1.0）とした比率（`relative_performance`）を算出します。

## Output JSON 構造
```json
{
  "description": "Performance comparison leaderboard by OS",
  "workload": {
        "<testcategory>": {
      "<benchmark>": {
        "test_snippet": "<test_snippet>",
        "gcc_ver": "<gcc_ver>",
        "<test_name>": {
          "os": {
            "<os>": {
              "thread": {
                "<N>": {
                  "unit": "<unit>",
                  "leaderboard": [
                    {
                      "rank": 1,
                      "machinename": "<machinename>",
                      "cpu_name": "<cpu_name>",
                      "cpu_isa": "<cpu_isa>",
                      "score": "<score>",
                      "relative_performance": "<relative_performance>"
                    }
                  ]
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

## 例外処理
- 該当する OS / スレッド数 `<N>` のデータが存在しないエントリは出力に含めません。

---

# 3. Cost comparison (コスト効率比較)
**目的**: 同一 OS 環境下で、コストパフォーマンス（1ドルあたりの性能）を比較し、経済性ランキングを作成します。

## 基準データの設定
- **スループット (T)**: 
  - `get_performance_score` により算出された性能値を使用。
  - 時間単位の場合は逆数（`1 / score`）、それ以外はそのままの値をスループットとします。
- **コスト効率 (Efficiency)**: `T / hourly_rate` (1ドルあたりの処理量)
- **相対コスト効率**: 各 OS・スレッド数における最高効率機を基準（1.0）とした比率（`relative_cost_efficiency`）を算出します。
- **ランキング**: 各条件でコスト効率が高い順（降順）にランク付けします。

## Output JSON 構造
```json
{
  "description": "Cost efficiency ranking by OS",
  "workload": {
        "<testcategory>": {
      "<benchmark>": {
        "test_snippet": "<test_snippet>",
        "gcc_ver": "<gcc_ver>",
        "<test_name>": {
          "os": {
            "<os>": {
              "thread": {
                "<N>": {
                  "unit": "Efficiency (Throughput/USD)",
                  "ranking": [
                    {
                      "rank": 1,
                      "machinename": "gcp-c4a-standard-8-arm64",
                      "cpu_name": "Neoverse-V2 (Google Axion)",
                      "cpu_isa": "Armv9.0-A (SVE2-128)",
                      "efficiency_score": 12500.5,
                      "relative_cost_efficiency": 1.0
                    },
                    {
                      "rank": 2,
                      "machinename": "aws-m8g-2xlarge-arm64",
                      "cpu_name": "Neoverse-V2 (Graviton4)",
                      "cpu_isa": "Armv9.0-A (SVE2-128)",
                      "efficiency_score": 11000.2,
                      "relative_cost_efficiency": 0.88
                    }
                  ]
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

## 例外処理
- `hourly_rate` が `"unknown"` または 0 以下のマシンは、ランキングから除外します。

---

# 4. Thread scaling comparison (スレッドスケーリング特性比較)
**目的**: 同一の Workload において、スレッド数増加に伴う性能向上率（スケーリング耐性）がアーキテクチャやマシンによってどのように異なるかを比較・可視化します。

## 基準データの設定
- **正規化**: 各マシンの最大スレッド実行時（通常は `nproc`）の `values` 値を基準（**100**）とします。
- **スケーリングスコア**: 基準値（100）に対する各スレッド数 `<N>` での相対値を算出します。
  - **計算**: 高速化方向の場合は `(各スレッド数の values / 最大スレッド数の values) * 100`、時間最小化方向の場合は `(最大スレッド数の values / 各スレッド数の values) * 100`。
  - 例：8スレッドで100の時、1スレッドのスコアが12.5なら理想的な線形スケール。25ならスケーリング効率が悪い（並列化の恩恵が少ない）ことを示します。
- **制約**: スレッド数 `<N>` が 1 種類しか存在しない Workload は解析対象外とします。

## ランキング
- **指標**: 理想線形スケーリング曲線からの乖離合計（`linear_deviation_total`）が小さいほど良好。
  - **理想スコア**: `ideal_score(N) = (N / N_max) × 100`
  - **乖離**: `dev(N) = ideal_score(N) - actual_score(N)`（スケーリングが悪いほど正の値が大きくなる）
  - **合計乖離**: `D_total = Σ dev(N)` （N_max を除く全スレッド数に対して合計）
- **ランキング**: `D_total` が小さい順（昇順）に rank 付けします。
- **補助情報**: `linear_deviation_per_thread` として各スレッド数での乖離値も出力します。突出して大きい値を持つスレッド数が飽和点の目安となります。

## Output JSON 構造
```json
{
  "description": "Thread scaling comparison by workload",
  "workload": {
        "<testcategory>": {
      "<benchmark>": {
        "test_snippet": "<test_snippet>",
        "gcc_ver": "<gcc_ver>",
        "<test_name>": {
          "unit": "<unit>",
          "curves": {
            "<machinename_1> (<cpu_isa>)": {
              "1": 15.5,
              "2": 31.0,
              "4": 62.1,
              "8": 100.0
            },
            "<machinename_2> (<cpu_isa>)": {
              "1": 25.0,
              "2": 48.2,
              "4": 82.5,
              "8": 100.0
            }
          },
          "ranking": [
            {
              "rank": 1,
              "machinename": "<machinename_1>",
              "cpu_name": "<cpu_name>",
              "cpu_isa": "<cpu_isa>",
              "linear_deviation_total": 21.1,
              "linear_deviation_per_thread": {
                "1": 3.0,
                "2": 6.0,
                "4": 12.1
              }
            },
            {
              "rank": 2,
              "machinename": "<machinename_2>",
              "cpu_name": "<cpu_name>",
              "cpu_isa": "<cpu_isa>",
              "linear_deviation_total": 68.2,
              "linear_deviation_per_thread": {
                "1": 12.5,
                "2": 23.2,
                "4": 32.5
              }
            }
          ]
        }
      }
    }
  }
}
```

## 例外処理
- 基準となる最大スレッド数の性能値が取得できない場合、そのマシンの解析はスキップし Warning を出力します。
- 全てのマシンでデータが不十分な Workload 自体は出力に含めません。
- **非スケーリング検出**: 最小スレッド数 `N_min`（`N_max` を除く最小の `N`）での正規化スコアが `flat_threshold`（デフォルト: **80**）を超えるマシンは、スケーリング特性を持たないとみなし `curves` および `ranking` から除外し Warning を出力します。
  - 除外条件: `score(N_min) > 80`
  - 除外後に `curves` 内の全エントリが空になった `test_name` は出力から削除します。

---

# 5. CSP instance comparison (CSPインスタンス比較)
**目的**: 同一 CSP 内で、アーキテクチャ間（x86 vs Arm）のコスト効率とスケーリング特性の推移（トレンド）を比較します。

## 基準データの設定
- **基準点 (100)**: 各 CSP の Arm64 インスタンス（下記）のコスト効率を **100** とします。
  - **AWS**: `"m8g"`
  - **GCP**: `"c4a"`
  - **OCI**: `"A1.Flex"`
- **比較指標**: 各マシンのコスト効率（Performance/USD）を `(current_eff / ref_eff) * 100` で算出します。
  - **100超**: Arm64 よりコスト効率が良い（x86優位）
  - **100未満**: Arm64 よりコスト効率が悪い（Arm優位）
- **トレンド分析**: スレッド数 `<N>` ごとのスコアを並べることで、有利・不利の逆転（クロスオーバー）を可視化します。

## Output JSON 構造
```json
{
  "description": "CSP instance comparison (Trend Analysis)",
  "workload": {
        "<testcategory>": {
      "<benchmark>": {
        "test_snippet": "<test_snippet>",
        "gcc_ver": "<gcc_ver>",
        "<test_name>": {
          "baseline": {
            "machinename": "<machinename_arm>",
            "arch": "arm64",
            "os": "<os>"
          },
          "trends": {
            "<machinename_x86> (x86_64)": {
              "scores": {
                "1": 115.2,
                "2": 105.4,
                "4": 92.1,
                "8": 85.3
              },
              "insight": {
                "max_advantage": {"thread": "1", "score": 115.2},
                "crossover_point": "thread 4",
                "scaling_efficiency": "declining_relative_to_arm"
              }
            }
          }
        }
      }
    }
  }
}
```

# 6. one_big_json_analytics.py 仕様
`one_big_json_analytics.py` の実行時の引数と出力仕様を定義します。  
実体の実装は以下を前提とします。

## 1) 想定入力
- `--input`: 入力 JSON のパス（既定: `${PWD}/one_big_json.json`）。
- 形式: `one_big_json.json`（`make_one_big_json.py` 由来。`generation_log` を除く各マシン配下に `os` / `testcategory` / `benchmark` / `thread` / `test_name` が入る構造）。

## 2) 実行オプション
- `--perf`: パフォーマンス比較のみ生成（`performance_comparison`）
- `--cost`: コスト効率比較のみ生成（`cost_comparison`）
- `--th`: スレッドスケーリング比較のみ生成（`thread_scaling_comparison`）
- `--csp`: CSP比較のみ生成（`csp_instance_comparison`）
- `--all`: 1〜4 の全比較を生成
- `--testcategory`: 対象 testcategory をフィルタ（`--testcategory cpu,mem`、`--testcategory [cpu,mem]` に対応）
- `--rhel-os-merge`: `testcategory` 指定時のみ有効。`rhel_10_family`（例: `Red_10_*`, `Oracle_10_*`）を1群として集約（実装上 `rhel_os_merge`）
- `--no_arm64`: 出力 JSON から arm64 を除外
- `--no_amd64`: 出力 JSON から amd64/x86_64 を除外
- `--output`: 出力ファイル名を指定。未指定時は  
  `one_big_json_analytics_<type><arch_suffix>[_rhel_os_merge].json`

`<type>` は `perf|cost|th|csp|all|mixed`（`mixed` は複数指定時）。

## 3) 出力 JSON 構造
トップレベルは `generation log` を必ず含み、`generation log.version info` には `VERSION-g<git-hash>` を格納する。  
`one_big_json_analytics.py` は指定された比較結果を以下のキーへ展開する。
- `performance_comparison`
- `cost_comparison`
- `thread_scaling_comparison`
- `csp_instance_comparison`

各キーの中身は 1〜4 の各節で記載した output JSON 構造を参照する。

## 例外処理
- 基準値（Arm インスタンス）が生成できない場合は、解析を中断し Error を出力します。
- 基準値には存在するが、比較対象に存在しない項目がある場合は `"unknown"` とし、Warning を出力します。
- 基準値または比較対象の性能値が `0` の場合、除算エラーを避けるため `"unknown"` と記載し Warning を出力します。
- 処理継続が可能な警告時は、入力 JSON の該当行数を明記します。

---

# 5. one_big_json_analytics.py 仕様

## 実装要件
- **言語**: Python 3.10 以上。
- **堅牢性**: 入力された `one_big_json.json` の構文エラーを検出し、適切なエラーメッセージを表示すること。

## バージョン管理
JSON 出力の先頭に `generation log` を含めます。
- **version info**: `v<major>.<minor>.<patch>-g<git-hash>` 形式。
```json
{
  "generation log": {
    "version info": "v1.2.0-gabc123",
    "date": "20260216-123456"
  }
}
```

## 引数パラメータ
| オプション | 必須/任意 | 説明 |
| :--- | :--- | :--- |
| `--input` | 任意 | 入力ファイルのパス。デフォルトは `${PWD}/one_big_json.json` |
| `--perf` | 任意 | Performance comparison のみ出力（指定なき場合のデフォルト） |
| `--cost` | 任意 | Cost comparison のみ出力 |
| `--th` | 任意 | Thread scaling comparison のみ出力 |
| `--csp` | 任意 | CSP instance comparison のみ出力 |
| `--no_arm64` | 任意 | arm64インスタンスの結果を出力から取り除く。JSON生成完了後の Post Process でのみ作用する。ランキング項目は取り除いたうえで再集計する。 |
| `--no_amd64` | 任意 | amd64/x86_64インスタンスの結果を出力から取り除く。JSON生成完了後の Post Process でのみ作用する。ランキング項目は取り除いたうえで再集計する。 |
| `--testcategory` | 任意 | 入力ファイル中の"testcategory"に対して`--testcategory=[<testcategory>]`とリスト指定された場合はそこに含まれるデータのみを出力。もしリスト内で存在しない`<testcategory>`を指定された場合は、そのリスト要素のみWaringを出して処理をスキップ。 |
|`--rhel-os-merge` | 任意かつ`--testcategory`指定時にのみ有効 | RHEL系の2つの<os>を1にマージする。マージの対象は"Red_10_x"と "Oracle_10_y"のみで、マージ後の<os>は"RHEL_10"とする。 |
| `--all` | 任意 | すべての結果を出力 |
| `--output` | 任意 | 出力先ファイル名。デフォルトは `${PWD}/one_big_json_analytics_<type>.json` |
| `--help` | 任意 | ヘルプメッセージを表示 |

### アーキ除外フラグの適用方針
- `--no_arm64` / `--no_amd64` の有無にかかわらず、解析ロジックは同一ルールで JSON を生成します。
- これらのフラグは **出力直前の Post Process** としてのみ適用し、該当アーキのインスタンス情報を削除してから出力します。
- 生成ロジック本体（Performance/Cost/Thread/CSP の計算処理）に分岐は入れません。
- 再集計対象はランキング項目（`performance_comparison.leaderboard` / `cost_comparison.ranking`）です。除外後のデータで `rank` と相対値（`relative_performance` / `relative_cost_efficiency`）を再計算します。
- 除外後に要素が空になった `thread` / `os` / `test_name` / `benchmark` / `testcategory` は出力から削除します。
- `thread_scaling_comparison` / `csp_instance_comparison` は該当アーキのエントリを除外し、結果が空になったノードは同様に削除します。
