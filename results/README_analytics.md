# README_analytics.md
`README_results.md` から生成された `one_big_json.json` を用いたデータ解析の仕様、および解析スクリプト `one_big_json_analytics.py` の実装要件を定義します。

---

# TOC
- [README\_analytics.md](#readme_analyticsmd)
- [TOC](#toc)
- [1. Performance comparison (絶対性能比較)](#1-performance-comparison-絶対性能比較)
- [2. Cost comparison (コスト効率比較)](#2-cost-comparison-コスト効率比較)
- [3. Thread scaling comparison (スレッドスケーリング特性比較)](#3-thread-scaling-comparison-スレッドスケーリング特性比較)
- [4. CSP instance comparison (CSPインスタンス比較)](#4-csp-instance-comparison-cspインスタンス比較)
- [5. one\_big\_json\_analytics.py 仕様](#5-one_big_json_analyticspy-仕様)

---

# 1. Performance comparison (絶対性能比較)
**目的**: 同一 OS 環境下で、プロセッサ世代（CPU 名/ISA）ごとの絶対的な処理能力を比較し、順位付け（リーダーボード）を行います。

## 基準データの設定
- **性能値の選択**: 
  - `test_name` に `values` が存在する場合：その数値を性能値とします。
        "unit": "Microseconds", "Seconds"などと`values`の単位が時間の場合、小さいほうが優位。
        それ以外は大きいほうが優位。
  - `values` が存在しない場合：`time`（秒）を性能値（低スコアほど良好）とします。
- **相対性能**: 各 OS・スレッド数における最速機を基準（1.0）とした比率（`relative_performance`）を算出します。

## Output JSON 構造
```json
{
  "description": "Performance comparison leaderboard by OS",
  "workload": {
    "<testcategory>": {
      "<benchmark>": {
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

# 2. Cost comparison (コスト効率比較)
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

# 3. Thread scaling comparison (スレッドスケーリング特性比較)
**目的**: 同一の Workload において、スレッド数増加に伴う性能向上率（スケーリング耐性）がアーキテクチャやマシンによってどのように異なるかを比較・可視化します。

## 基準データの設定
- **正規化**: 各マシンの最大スレッド実行時（通常は `nproc`）の `values` 値を基準（**100**）とします。
- **スケーリングスコア**: 基準値（100）に対する各スレッド数 `<N>` での相対値を算出します。
  - **計算**: `(各スレッド数の values / 最大スレッド数の values) * 100`（※高いほど性能が良い指標の場合）
  - 例：8スレッドで100の時、1スレッドのスコアが12.5なら理想的な線形スケール。25ならスケーリング効率が悪い（並列化の恩恵が少ない）ことを示します。
- **制約**: スレッド数 `<N>` が 1 種類しか存在しない Workload は解析対象外とします。

## Output JSON 構造
```json
{
  "description": "Thread scaling comparison by workload",
  "workload": {
    "<testcategory>": {
      "<benchmark>": {
        "<test_name>": {
          "unit": "<unit>",
          "curves": {
            "<machinename_1> (<arch>)": {
              "1": 15.5,
              "2": 31.0,
              "4": 62.1,
              "8": 100.0
            },
            "<machinename_2> (<arch>)": {
              "1": 25.0,
              "2": 48.2,
              "4": 82.5,
              "8": 100.0
            }
          },
          "insight": {
            "scaling_efficiency": "Higher on <machinename_1>",
            "saturation_point": "Observed on <machinename_2> above 4 threads"
          }
        }
      }
    }
  }
}
```

## 例外処理
- 基準となる最大スレッド数の性能値が取得できない場合、そのマシンの解析はスキップし Warning を出力します。
- 全てのマシンでデータが不十分な Workload 自体は出力に含めません。

---

# 4. CSP instance comparison (CSPインスタンス比較)
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
| `--all` | 任意 | すべての結果を出力 |
| `--output` | 任意 | 出力先ファイル名。デフォルトは `${PWD}/one_big_json_analytics_<type>.json` |
| `--help` | 任意 | ヘルプメッセージを表示 |