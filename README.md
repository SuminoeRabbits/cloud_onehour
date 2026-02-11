# Cloud one hour project
このProjectはphoronix-test-suite(https://www.phoronix-test-suite.com/)を利用してオープンソースソフトウェアのベンチマークを簡易的に行います。実行環境としてLocalのUbuntu LinuxもしくはCloud環境VMのUbuntu Instance（AWS,GCP,OCI）を設定することが可能です。Cloud環境VMを利用する際は、アカウント作成とCLIのインストールを前提とします。

## TOC
- [Cloud one hour project](#cloud-one-hour-project)
  - [TOC](#toc)
  - [Features](#features)
  - [Prerequisites](#prerequisites)
    - [Local環境](#local環境)
    - [Cloud環境](#cloud環境)
  - [Project Structure](#project-structure)
  - [Getting Started](#getting-started)
    - [初期セットアップ](#初期セットアップ)
    - [Local環境での実行](#local環境での実行)
    - [Cloud環境での実行](#cloud環境での実行)
  - [Configuration](#configuration)
    - [test\_suite.json](#test_suitejson)
    - [cloud\_config.json](#cloud_configjson)
    - [cloud\_instances.json](#cloud_instancesjson)
  - [Usage](#usage)
    - [pts\_regression.py](#pts_regressionpy)
    - [cloud\_exec\_para.py](#cloud_exec_parapy)
  - [Results](#results)
    - [Results構造](#results構造)
    - [データ分析](#データ分析)
  - [Development](#development)
    - [新しいベンチマークの追加](#新しいベンチマークの追加)
  - [License](#license)

## Features

- **多様なOSSベンチマーク**: Phoronix Test Suite (PTS)を活用した30種類以上のベンチマーク
  - プロセッサ性能テスト（CoreMark, SIMDJSON, cpuminer-opt等）
  - システム性能テスト（sysbench, stream等）
  - ビルド性能テスト（Linux Kernel, LLVM, GCC等）
  - 圧縮テスト（7zip, lz4, xz, zstd等）
  - データベース（PostgreSQL, Redis, Memcached等）
  - Webサービス（Apache, Nginx等）
  - 機械学習（TensorFlow Lite等）
  - Java（JMH, Renaissance, Spark等）

- **柔軟な実行環境**:
  - Local Ubuntu Linux環境での実行
  - AWS/GCP/OCI クラウドVM環境での並列実行
  - アーキテクチャ対応: amd64 (x86_64) / arm64 (aarch64)

- **自動化**:
  - テストコマンド自動生成 (`pts_regression.py`)
  - クラウド環境での並列実行とリソース管理 (`cloud_exec_para.py`)
  - perfカウンタによる性能メトリクス収集
  - CPU周波数トラッキング

- **結果分析**:
  - JSON形式での構造化データ出力
  - パフォーマンスサマリー生成
  - クラウドコスト分析
  - リグレッション分析

## Prerequisites

### Local環境

- **OS**: Ubuntu 22.04 LTS / 24.04 LTS
- **Python**: 3.10以上
- **必須ツール**: git, curl, pip
- **推奨**: sudo権限（パッケージインストールとperf設定に必要）

### Cloud環境

上記に加えて:

- **AWS**:
  - AWSアカウント
  - AWS CLI設定済み (`aws configure`)
  - 適切なIAM権限（EC2インスタンス作成・削除等）

- **GCP**:
  - GCPプロジェクト
  - gcloud CLI設定済み (`gcloud init`)
  - Compute Engine API有効化

- **OCI**:
  - OCIテナンシー
  - OCI CLI設定済み (`oci setup config`)
  - Compute権限

- **SSH**:
  - SSH鍵ペアの生成と配置
  - 鍵のパスは `cloud_config.json` の `ssh_key_path` で指定

## Project Structure

```
cloud_onehour/
├── pts_regression.py          # テストコマンド生成・実行スクリプト
├── cloud_exec_para.py          # クラウド並列実行スクリプト
├── test_suite.json             # ベンチマーク定義
├── cloud_config.json           # クラウド実行設定
├── cloud_instances.json        # クラウドインスタンス定義
├── pts_runner/                 # ベンチマーク実行スクリプト群
│   ├── pts_runner_*.py         # 各ベンチマーク用ランナー
│   ├── CODE_TEMPLATE.md        # ランナー実装テンプレート
│   └── check_compliance.py     # ランナー準拠性チェック
├── scripts/                    # 環境セットアップスクリプト
│   ├── prepare_tools.sh        # 総合セットアップスクリプト
│   ├── setup_gcc14.sh          # GCC 14セットアップ
│   ├── setup_jdkxx.sh          # JDKセットアップ
│   └── setup_pts.sh            # PTS セットアップ
├── results/                    # ベンチマーク結果格納
│   ├── make_one_big_json.py    # 結果統合スクリプト
│   ├── one_big_json_analytics.py  # データ分析スクリプト
│   ├── regression_summary.py   # リグレッション分析
│   └── README_results.md       # 結果データ仕様
└── bench_results/              # クラウド実行結果収集先
```

## Getting Started

### 初期セットアップ

1. **リポジトリのクローン**:
```bash
git clone https://github.com/SuminoeRabbits/cloud_onehour.git
cd cloud_onehour
```

2. **環境セットアップ**:
```bash
cd scripts
./prepare_tools.sh
```

このスクリプトは以下を自動実行します:
- Pythonパッケージのインストール
- GCC 14のセットアップ
- JDK/Mavenのセットアップ
- Phoronix Test Suiteのインストール
- 必要な開発ツールのインストール

### Local環境での実行

1. **テストコマンド生成（dry-run）**:
```bash
./pts_regression.py --dry-run
```

2. **クイックテストの実行**:
```bash
./pts_regression.py --quick --run
```

3. **特定の範囲のテスト実行**:
```bash
# 短時間のテストのみ
./pts_regression.py --regression --short --run

# 中程度の時間のテスト
./pts_regression.py --regression --run

# 長時間のテスト
./pts_regression.py --regression --long --run
```

4. **分割実行（大量のテストを複数回に分ける）**:
```bash
./pts_regression.py --split-1st --run
./pts_regression.py --split-2nd --run
# ... --split-5th まで
```

### Cloud環境での実行

1. **SSH鍵の準備**:
```bash
# SSH鍵ペアを生成（まだの場合）
# 鍵のパスは cloud_config.json の ssh_key_path に合わせてください
ssh-keygen -t rsa -b 4096 -f ~/.ssh/<your-key-name>.pem -N ""
chmod 600 ~/.ssh/<your-key-name>.pem

# cloud_config.json で ssh_key_name と ssh_key_path を設定
# 例: "ssh_key_path": "${HOME}/.ssh/<your-key-name>.pem"
```

2. **cloud_instances.jsonの設定**:
実行したいインスタンスの `"enable": true` に設定します。

3. **クラウド実行**:
```bash
# AWS環境のみ実行
./cloud_exec_para.py --csp aws

# GCP環境のみ実行
./cloud_exec_para.py --csp gcp

# OCI環境のみ実行
./cloud_exec_para.py --csp oci

# 並列実行数を指定
./cloud_exec_para.py --csp aws --max-workers 3

# Dry-run（実際には実行しない）
./cloud_exec_para.py --csp aws --dry-run
```

4. **結果の回収**:
実行完了後、結果は自動的に `bench_results/<instance-name>/` に保存されます。

## Configuration

### test_suite.json

ベンチマークテストの定義ファイル。各テストは以下の構造を持ちます:

```json
{
  "test_category": {
    "Processor": {
      "enabled": true,
      "items": {
        "pts/coremark-1.0.1": {
          "enabled": true,
          "THFix_in_compile": true,
          "THChange_at_runtime": false,
          "TH_scaling": "N/A",
          "exe_time_v8cpu": "1.0"
        }
      }
    }
  }
}
```

**主要フィールド**:
- `enabled`: テストの有効/無効
- `THFix_in_compile`: コンパイル時にスレッド数を固定するか
- `THChange_at_runtime`: 実行時にスレッド数を変更可能か
- `TH_scaling`: スレッドスケーリング方式
- `exe_time_v8cpu`: 8vCPU環境での想定実行時間（秒）

### cloud_config.json

クラウド実行の共通設定:

- `os_version`: Ubuntu バージョン（24.04推奨）
- `ssh_user`: SSH接続ユーザー（デフォルト: ubuntu）
- `ssh_key_name`: SSH鍵の名前（任意の名前を設定）
- `ssh_key_path`: SSH秘密鍵のパス（例: `${HOME}/.ssh/<key-name>.pem`）
- `testloads`: テスト用軽量ワークロード
- `workloads`: 本番ワークロードコマンド一覧
- `workload_timeout`: ワークロードタイムアウト（秒）

### cloud_instances.json

クラウドインスタンスの定義:

```json
{
  "aws": {
    "enable": true,
    "regions": {
      "ap-northeast-3": {
        "enable": true,
        "instances": [
          {
            "enable": true,
            "type": "m7i.2xlarge",
            "arch": "amd64",
            "hostname": "aws-m7i-2xlarge-amd64",
            "vcpus": 8,
            "cpu_cost_hour[730h-mo]": 0.5208
          }
        ]
      }
    }
  }
}
```

**主要フィールド**:
- `enable`: インスタンスを有効化
- `testloads`: テストモード（軽量チェック）
- `type`: インスタンスタイプ
- `arch`: アーキテクチャ（amd64/arm64）
- `vcpus`: 仮想CPU数
- `cpu_cost_hour`: 時間あたりコスト（USD）

## Usage

### pts_regression.py

テストコマンドの生成・実行スクリプト。

**基本オプション**:
```bash
./pts_regression.py --help         # ヘルプ表示
./pts_regression.py --dry-run      # テストコマンド生成のみ
./pts_regression.py --run          # テストコマンド生成・実行
./pts_regression.py --no-execute   # 実行せずコマンド表示のみ
```

**テスト範囲制御**:
```bash
--quick              # クイックモード（高速）
--regression         # リグレッションテストモード
--regression --short # 短時間テストのみ（< 15.25秒）
--regression         # 中時間テスト（15.25 - 120秒）
--regression --long  # 長時間テストのみ（>= 120秒）
```

**並列度制御**:
```bash
--max                # 最大スレッド数（288）を使用
```

**分割実行**:
```bash
--split-1st          # 全体の1/5（最初）
--split-2nd          # 全体の2/5
--split-3rd          # 全体の3/5
--split-4th          # 全体の4/5
--split-5th          # 全体の5/5（最後）
```

**動作詳細**:
1. `test_suite.json` を読み込み
2. 有効なテストを抽出
3. 各テストのスレッド数を決定（`nproc` or 固定値）
4. `pts_runner/pts_runner_<testname>.py` コマンドを生成
5. `--run` 指定時は実際に実行

### cloud_exec_para.py

クラウド環境での並列実行スクリプト。

**基本使用法**:
```bash
./cloud_exec_para.py --csp aws              # AWS環境のみ
./cloud_exec_para.py --csp gcp              # GCP環境のみ
./cloud_exec_para.py --csp oci              # OCI環境のみ
```

**オプション**:
```bash
--max-workers N      # 並列実行数（デフォルト: 2）
--dry-run            # dry-run（実際には実行しない）
--debug              # デバッグログ出力
```

**実行フロー**:
1. クラウドプロバイダの初期化（SG/VPC等の共有リソース作成）
2. 有効なインスタンスの並列起動
3. 各インスタンスでワークロード実行
4. 結果の回収（`bench_results/` ディレクトリへ）
5. インスタンスの削除（クリーンアップ）

**注意**: Ctrl+Cで中断した場合も確実にインスタンスをクリーンアップします。

## Results

### Results構造

ベンチマーク結果は以下の階層で保存されます:

```
results/ または bench_results/
└── <machinename>/              # マシン名（例: aws-m7i-2xlarge-amd64）
    └── <os>/                   # OS（例: Ubuntu-2404）
        └── <testcategory>/     # テストカテゴリ（例: Processor）
            └── <benchmark>/    # ベンチマーク名（例: coremark-101）
                ├── <N>-thread.json                # PTSベンチマーク結果
                ├── <N>-thread_perf_stats.txt      # perf統計
                ├── <N>-thread_perf_summary.json   # perfサマリー
                ├── <N>-thread_freq_start.txt      # 開始時CPU周波数
                ├── <N>-thread_freq_end.txt        # 終了時CPU周波数
                └── summary.json                   # 総合サマリー
```

**<N>**: スレッド数（1, 2, 4, 8, 12, 16等）

### データ分析

1. **個別結果の統合**:
```bash
cd results
./make_one_big_json.py
```
これにより `one_big_json.json` が生成されます。

2. **データ分析**:
```bash
./one_big_json_analytics.py
```
分析レポートが生成されます。

3. **リグレッション分析**:
```bash
cd bench_results
./regression_summary.py
```

詳細は [results/README_results.md](results/README_results.md) を参照してください。

## Development

### 新しいベンチマークの追加

1. **テスト情報の確認**:
```bash
phoronix-test-suite info pts/<test-name>
```

2. **pts_runnerスクリプトの作成**:
```bash
cd pts_runner
# CODE_TEMPLATE.md を参照して新規スクリプトを作成
cp pts_runner_coremark-1.0.1.py pts_runner_<new-test>.py
# 編集...
```

3. **準拠性チェック**:
```bash
./check_compliance.py pts_runner_<new-test>.py
```

4. **test_suite.jsonへの追加**:
該当するカテゴリに新しいテストエントリを追加します。

5. **テスト**:
```bash
cd ..
./pts_runner/pts_runner_<new-test>.py 1 --quick
```

詳細は [pts_runner/CODE_TEMPLATE.md](pts_runner/CODE_TEMPLATE.md) を参照してください。

## License

このプロジェクトはApache License 2.0のもとで公開されています。詳細は [LICENSE](LICENSE) ファイルを参照してください。

