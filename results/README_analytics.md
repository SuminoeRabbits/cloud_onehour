# about README_analytics
`README_results.md`とそこから生成された`one_big_json.json`のデータ解析法。

# TOC

- [about README\_analytics](#about-readme_analytics)
- [TOC](#toc)
- [Performance comparison](#performance-comparison)
  - [set reference point](#set-reference-point)
  - [output metrics](#output-metrics)
  - [exception handling](#exception-handling)
- [Cost comparison](#cost-comparison)
  - [set reference point](#set-reference-point-1)
    - [cost\_scoreの計算式](#cost_scoreの計算式)
  - [output metrics](#output-metrics-1)
  - [exception handling](#exception-handling-1)
- [Thread scaling comparison](#thread-scaling-comparison)
  - [set reference point](#set-reference-point-2)
  - [output metrics](#output-metrics-2)
  - [exception handling](#exception-handling-2)
- [CSP instance comparison](#csp-instance-comparison)
  - [set reference point](#set-reference-point-3)
  - [output metrics](#output-metrics-3)
  - [exception handling](#exception-handling-3)
- [one\_big\_json\_analytics.py specification](#one_big_json_analyticspy-specification)
  - [input data format](#input-data-format)
  - [requirement](#requirement)
  - [script version info](#script-version-info)
  - [argument parameters](#argument-parameters)


# Performance comparison
絶対性能比較の目的は、同一のWorkloadを異なる<machinename>で実施した際に、どれだけ短い時間で完了させることができるか、もしくは特定の性能仕様でどれだけ高い数値を出せるか、を比較することである。
- "test_name"に"values"値が存在する場合はその数字を性能値とする。この場合は高いほど良い。
- "test_name"に"values"値が存在しない場合は"time"を性能値とする。この場合は低いほど良い。

## set reference point
各workload毎の`<values>`値を`<benchmark_score>`とする。経過時間[s]を`<time>`とする。`<time>`が不明もしくは空白時は"unknown"とする。

## output metrics
{
    description:"Performance comparison by machine_name",
    workload:{
        <testcategory>:
        {
            <benchmark>:
            {
                <test_name>:
                {
                    <os>:{
                        <machinename>:{
                            "time_score":"<time_score>",
                            "benchmark_score":"<benchmark_score>",
                            "unit":"<unit>"
                            },
                        <machinename>:{
                            "time_score":"<time_score>",
                            "benchmark_score":"<benchmark_score>",
                            "unit":"<unit>"
                            },
                        .....
                    }
                    .....
                }
                .....
            }
            .....
        }
        .....
    }
}

## exception handling
例外がある場合はここに記す。

# Cost comparison
コスト比較の目的は同一のWorkloadを異なる`<machinename>`で実施した際に、Workloadを完了させるのに必要な計算機利用料（ベンチマーク時間ｘ時間当たり利用料）を比較することである。

## set reference point
各workload毎の`<values>`値を`<benchmark_score>`とする。上記で定義される計算機利用料を`<cost_score>`とする。`<cost_score>`が不明な場合は"unknown"とする。

### cost_scoreの計算式
`<cost_score>` = `<time>` × `<hourly_rate>`
- `<time>`: ベンチマーク実行時間（秒）を時間に換算（`<time>` / 3600）
- `<hourly_rate>`: 入力JSONの各エントリに含まれる`hourly_rate`フィールドの値（USD/hour）
- `hourly_rate`フィールドが存在しない、または0以下の場合は`<cost_score>`を"unknown"とする

## output metrics
{
    description:"Cost comparison by machine_name",
    workload:{
        <testcategory>:
        {
            <benchmark>:
            {
                <test_name>:
                {
                    <os>:{
                        <machinename>:{
                            "cost_score":"<cost_score>",
                            "benchmark_score":"<benchmark_score>",
                            "unit":"<unit>"
                            },
                        <machinename>:{
                            "cost_score":"<cost_score>",
                            "benchmark_score":"<benchmark_score>",
                            "unit":"<unit>"
                            },
                        .....
                    }
                    .....
                }
                .....
            }
            .....
        }
        .....
    }
}

## exception handling
例外がある場合はここに記す。

# Thread scaling comparison
スレッドスケーリング比較の目的は、同一のWorkloadを同一の`<machinename>`で利用するスレッド数`<N>`を変化させながら実施した際に、その`<machinename>`におけるスレッドスケーリングの特徴を知る事である。

## set reference point
各workload毎の`<values>`値を`<benchmark_score>`とする。スレッド数`<N>`が1通りしか存在しない場合は記載しない。
性能値は同一`<machinename>`、同一`<test_name>`でスレッド数`<N>`が最大値時の実行時間を基準値`100`とする。

## output metrics
{
    description:"Thread scaling comparison",
    header:{
        "machinename":<machinename>,
        "os":<os>
    },
    workload:{
        <testcategory>,
        <benchmark>,
        <test_name>:{
            "unit":"<unit>"{
                "<N>" : "<benchmark_score>"
                "<N>" : "<benchmark_score>"
                ....  : ....
            }
        }
        .....
    }
}

## exception handling
まず最初に基準値を生成し、その後にworkloadの生成を行う。
    - 基準値が生成できない場合は、その原因をErrorとして出力し生成を中断する。

# CSP instance comparison
CSPインスタンス比較の目的は同一のCSPで同一のWorkloadを異なる`<machinename>`（インスタンス）で実施した際に、Workloadを完了させるのに必要な計算機利用料（ベンチマーク時間ｘ時間当たり利用料）を比較することである。各workload毎の値を`<benchmark_score>`とする。計算機利用料の算出は[Cost comparison](#cost-comparison)を参照する。

## set reference point
性能値はそれぞれのCSPが保有しているarm64インスタンスを基準値`100`とする。arm64インスタンスとは`<machinename>`に次の文字列を**部分一致**で含んでいるものとする。
- AWS : "m8g" （例: m8g.xlarge, m8g.2xlarge）
- GCP : "c4a" （例: c4a-standard-8, c4a-highcpu-16）
- OCI : "A1.Flex" （例: VM.Standard.A1.Flex）

## output metrics
{
    description:"CSP instance comparison",
    header:{
        <machinename>,<os>,<csp>
    },
    workload:{
        <testcategory>,<benchmark>,
        <test_name>:"<benchmark_score>"
        .....
    }
}

## exception handling
まず最初に基準値を生成し、その後にworkloadの生成を行う。
    - 基準値が生成できない場合は、その原因をErrorとして出力し生成を中断する。
    - 基準値には存在するが他の<machinename>で存在しない項目が出てきた場合、そのworkloadには`"unknown"`と記載し、ErrorにはせずWarningを出力し次に進む。
    - 他の<machinename>で存在し基準値に存在しない項目が出てきた場合、Warningを出力し次に進む。
    - 基準値または比較対象の性能値が`0`の場合、除算エラーを避けるため`"unknown"`と記載しWarningを出力し次に進む。
    - Warningの際は入力JSONファイルの比較部分の行数を明記する。

# one_big_json_analytics.py specification
ここでは、それぞれのOutput metricsをSTDOUTに生成するPythonスクリプト`one_big_json_analytics.py`を実装する際の仕様について記す。

## input data format
入力データ`one_big_json.json`のフォーマットは[README_results.md](README_results.md)を参照。

## requirement
Python3.10で動作すること。`one_big_json_analytics.py`自分自身と入力ファイルである`one_big_json.json`に対してSyntax Errorを検出する機能を有すること。すべてのオプションが検証されていること。

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
- `--input`(省略可能) :　参照すべき`one_big_json.json`の位置もしくはファイル名を指定する。省略された場合は`${PWD}one_big_json.json`を参照する。
- `--perf`（省略可能）：Performance comparisonのみを出力する。省略された場合は`--all`が自動選択される。
- `--cost`（省略可能）：Cost comparisonのみを出力する。省略された場合は`--all`が自動選択される。
- `--th`（省略可能）：Thread scaling comparisonのみを出力する。省略された場合は`--all`が自動選択される。
- `--csp`（省略可能）：CSP instance comparisonのみを出力する。これ以外のオプションと組み合わせることが可能である。省略された場合は`--all`が自動選択される。
- `--all`（省略可能）：省略された場合はすべてのOutput Metrics出力を選択する。

