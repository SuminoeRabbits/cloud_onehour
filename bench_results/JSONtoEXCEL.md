# JSON to Excel

ベンチマーク結果 JSON を分析用の Excel（`.xlsx`）形式に変換します。
JSONファイルはDefaultで特に指定がない限り${PWD}/globalの下に`testcategory`毎のディレクトリ内に存在しているとします。

## TOC

- [Python バージョン](#python-バージョン)
- [Excel 列（A–L）](#excel-列al)
- [Excel の内容](#excel-の内容)
- [列 L: `performance`](#列-l-performance)
- [ソート順](#ソート順)
- [グラフ生成](#グラフ生成)
  - [線の色](#線の色)
  - [CSP マーカー表（折れ線グラフ）](#csp-マーカー表折れ線グラフ)
  - [グラフタイトルレイアウト](#グラフタイトルレイアウト)
  - [スレッド数が複数のとき（折れ線グラフ）](#スレッド数が複数のとき折れ線グラフ)
  - [スレッド数が 1 つのとき（棒グラフ）](#スレッド数が-1-つのとき棒グラフ)
- [Excel 出力先](#excel-出力先)
- [変換ルール](#変換ルール)
- [JSONtoEXCEL.py](#jsontoexcelpy)
  - [必須環境](#必須環境)
  - [使い方](#使い方)
  - [オプション](#オプション)
  - [ノート](#ノート)
  - [実行環境メモ（Debian/Ubuntu）](#実行環境メモdebianoubuntu)

## Python バージョン

Python 3.12 以上が必要です。

## Excel 列（A–L）

1 行目はヘッダー行です。

| 列 | ヘッダー | 型 |
|-----|--------|------|
| A | `benchmark` | string |
| B | `test_snippet` | string |
| C | `test_name` | string |
| D | `os` | string |
| E | `gcc_ver` | string |
| F | `thread` | integer |
| G | `unit` | string |
| H | `machinename` | string |
| I | `cpu_name` | string |
| J | `score` | float（小数 2 桁） |
| K | `relative_performance` | float（小数 2 桁） |
| L | `performance` | float（小数 2 桁） |

## Excel の内容

データは 2 行目から始まり、入力 JSON の構造は次のようになります。

```json
{
  "performance_comparison": {
    "workload": {
      "<workload-key>": {
        "<benchmark>": {
          "test_snippet": "SVT-AV1-4.0",
          "gcc_ver": "14.2-system",
          "<test_name>": {
            "os": {
              "<OS>": {
                "thread": {
                  "<thread>": {
                    "unit": "Microseconds",
                    "leaderboard": [
                      {
                        "rank": 1,
                        "machinename": "aws-m8a-4xlarge-amd64",
                        "cpu_name": "AMD EPYC 9R45 (Zen 5 \"Turin\")",
                        "score": 2271.33,
                        "relative_performance": 1.0
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
}
```

`leaderboard`（または `ranking`）の各エントリに対する列の対応は次のとおりです。

| 列 | 元フィールド |
|--------|-------------|
| A `benchmark` | ベンチマーク名のキー |
| B `test_snippet` | ベンチマークレベルの `test_snippet` |
| C `test_name` | ベンチマークレベルの `test_name` |
| D `os` | OS キー |
| E `gcc_ver` | ベンチマークレベルの `gcc_ver` |
| F `thread` | thread キー（文字列の場合は整数に変換） |
| G `unit` | `unit` |
| H `machinename` | `machinename` |
| I `cpu_name` | `cpu_name` |
| J `score` | `score`（`efficiency_score` を代替値として使用） |
| K `relative_performance` | `relative_performance`（`relative_cost_efficiency` を代替値として使用） |
| L `performance` | 別途計算（下記参照） |

## 列 L: `performance`

列 L は `*performance_analysis*` のファイルのみ埋められ、他のファイルでは空になります。

各（`benchmark`, `test_name`, `OS`, `unit`）グループについて、`rank=1` のエントリの中で最小スレッド数を用いた `score` をベースライン（100）として定義します。

他の行はこのベースラインに対する比率で算出します。

- `unit` が `Microseconds` の場合: `performance = (baseline_score / score) × 100`
- それ以外: `performance = (score / baseline_score) × 100`

## ソート順

並び順は次の優先順位です。

1. **A** `benchmark` — ナチュラルソート
2. **H** `machinename` — ナチュラルソート
3. **F** `thread` — 昇順

> **ナチュラルソート**: 数値は文字列比較ではなく整数として比較します。
> 例: `1 < 20 < 100 < 200 < 1000 < 4000`

## グラフ生成

グラフは `*performance_analysis*` ファイルのみに対して生成されます。
`test_name`ごとに 1 つのグラフにまとめ、系列は`machinename`。１グラフあたり1ページの PDF にまとめて出力します。

### 線の色

色は **H** `cpu_name` から判定します。

| アーキテクチャ | 色 |
|---|---|
| ARM64（Graviton, Neoverse, Ampere, …） | 青系 |
| AMD（EPYC, …） | 黒 / ダークグレー系 |
| Intel（Xeon, …） | 赤系 |

同一アーキテクチャ内では線種（実線、破線、点線、破線点線）を循環させ、識別性を高めます。色と線種は凡例の表記とも対応しています。

### CSP マーカー表（折れ線グラフ）

ラインマーカーは `machinename` の接頭辞（大文字小文字を区別しない）で決まります。
`JSONtoEXCEL.py` の `CSP_MARKER_TABLE` と整合してください。

| CSP   | `machinename` 接頭辞 | `marker` | `fillstyle` | 図形 |
|-------|----------------------|----------|-------------|------|
| AWS   | `aws-`               | `"o"`    | `"none"`    | ○ 白抜き円 |
| GCP   | `gcp-`               | `"o"`    | `"full"`    | ● 塗り円 |
| OCI   | `oci-`               | `"^"`    | `"none"`    | △ 白抜き三角 |
| Azure | `azure-`             | `"^"`    | `"full"`    | ▲ 塗り三角 |
| Other | 上記以外            | `"s"`    | `"full"`    | ■ 正方形 |

### グラフタイトルレイアウト

各グラフは 2 階層のタイトルを持ちます。

| レベル | 内容 | 参照元 |
|-------|---------|--------|
| **メインタイトル**（大） | `test_snippet` | Excel 列 **B** (`test_snippet`) |
| **サブタイトル**（小） | `test_name (unit)` または `test_name (unit), Thread=N` | test_name + 単位 |

`test_snippet` が空の場合は、サブタイトルのみを `axis.set_title()` で表示します。

### スレッド数が複数のとき（折れ線グラフ）

- X 軸: `thread`（整数）
- Y 軸: `performance`
- 系列: `machinename` ごとに 1 本の線
- メインタイトル: Excel 列 **B** `test_snippet`
- サブタイトル: `test_name (unit)`
- マーカー: CSP マーカー表（上記）

各系列は凡例とグラフ上に番号（1, 2, …）で表示されます。
重なり防止のため、番号ラベルは各線の左右端を交互に使用します。

### スレッド数が 1 つのとき（棒グラフ）

- Y 軸: `performance`（通常は 0–100）
- 系列: `machinename` ごとに 1 本の棒
- メインタイトル: Excel 列 **B** `test_snippet`
- サブタイトル: `test_name (unit), Thread=<thread>`
- X 軸ラベル: 短縮した `machinename`

棒は左側が高い性能、右側が低い性能の順に並びます。
系列番号は表示しません。

## Excel 出力先

出力は元の JSON と同じディレクトリに保存され、拡張子 `.json` を `.xlsx` に置き換えます。
同名ファイルが既に存在する場合は上書きされます。

- 処理対象カテゴリ:
`AI`, `Compression`, `Cryptography_and_TLS`, `Database`, `Java_Applications`,
`Memory_Access`, `Multimedia`, `Network`,`Telecom`,`Processor`, `System`

各カテゴリ `<testcategory>` 配下の `<testcategory>/<testcategory>_*.json` という形式のファイルを
`<testcategory>/<testcategory>_*.xlsx` に変換します。

## 変換ルール

- 比較ブロック: `workload` サブキーを持つ、トップレベルで `*_comparison` で終わるキーを使用します。
- エントリ順: `leaderboard` を優先し、なければ `ranking` を使用します。
- スコアフィールド: `score` を優先し、なければ `efficiency_score` を使用します。
- 相対性能フィールド: `relative_performance` を優先し、なければ `relative_cost_efficiency` を使用します。
- `test_snippet` と `gcc_ver` は可能な場合、JSON の `<benchmark>` レベルから読み取ります。
- スレッド値: 数値文字列なら整数へ変換します（例: `"4"` → `4`）。

## 変換後の網羅性チェック
変換後、処理対象カテゴリ内の `<testcategory>_performance_analysis.json` からテスト結果の網羅性を抽出する。抽出結果は **NOG（欠損あり）のみ** を JSON で出力する。
出力ファイル名はデフォルトで `coverage_nog_all_<timestamp>.json`。同じ JSON は `stdout` にも出力する。

- とある"machinename"で一つでもデータ点がある場合は、ほかの"machinename"でも等しく情報が存在していなくてはならない。
- "machinename"の母集団は、<testcategory>_performance_analysis.jsonの中で必ず１度は現れる"machinename"を積算したもの。
- 判定単位は `benchmark + thread`。その配下の `test_name` のいずれかで欠損があれば `nog` に含める。

出力フォーマットは以下の通りとする。

```
{
  "schema_version": "1.0",
  "generated_at": "2026-03-08T19:30:00+09:00",
  "results":{
      "testcategory":{
         "System":{
            "nog_count": 1,
            "nog":{
                "glibc-bench-1.9.0":{
                  "thread": "12",
                  "missing_count": 3,
                  "missing_test_names": ["..."],
                  "csp_breakdown": {"gcp": ["..."]}
                  }
            }
          }
        }
    }
}
```

## JSONtoEXCEL.py

### 必須環境

- Python 3.12 以上
- `openpyxl`（`python3-openpyxl`）
- `matplotlib`（`python3-matplotlib`）。PDF 生成が不要なら任意

### 使い方

```bash
python JSONtoEXCEL.py [options]
```

### オプション

| オプション | デフォルト | 説明 |
|--------|---------|-------------|
| `--root PATH` | スクリプトのあるディレクトリ | ワークスペースルート。`global/` 配下を検索対象とします。 |
| `--ext .xlsx` | `.xlsx` | 出力拡張子（現在は `.xlsx` 固定） |
| `--log DIR` | `$PWD/log` | ログ保存先ディレクトリ。なければ自動作成 |
| `--graph` | — | 既存の Excel を読み込み PDF を再生成します。**Excel は再作成・上書きされません。**
JSON を再実行せず、手動編集した Excel のグラフだけを更新したい場合に使用します。 |
| `--coverage-out PATH` | `<root>/coverage_nog_all_<timestamp>.json` | 網羅性チェック（NOG）JSON の出力先。指定しない場合はデフォルト名で保存され、同じ内容が `stdout` にも出力されます。 |

### ノート

- すべての処理は `JSONtoEXCEL.py` 単体で実装されています。
- 以前使われていた PowerShell の中間ファイル（`*.ps1`）は非推奨です。

### 実行環境メモ（Debian/Ubuntu）

- `pip install --user` で `externally-managed-environment` エラーが出る場合は、システムパッケージでインストールします。

```bash
sudo apt install python3-openpyxl python3-matplotlib
```

- `matplotlib` が利用できない場合でも、`JSONtoEXCEL.py` は Excel の生成だけは続けます。
  PDF 生成は警告付きでスキップされます。
