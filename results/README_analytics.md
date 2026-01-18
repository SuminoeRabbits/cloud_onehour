# about README_analytics
`README_results.md`とそこから生成された`one_big_json.json`のデータ解析法。

# TOC

- [about README\_analytics](#about-readme_analytics)
- [Performance comparison](#performance-comparison)
  - [set reference point](#set-reference-point)
  - [output metrix](#output-metrix)
  - [exception handling](#exception-handling)
- [Cost comparison](#cost-comparison)
  - [set reference point](#set-reference-point-1)
  - [output metrix](#output-metrix-1)
  - [exception handling](#exception-handling-1)
- [Thread scaling comparison](#thread-scaling-comparison)
  - [set reference point](#set-reference-point-2)
  - [output metrix](#output-metrix-2)
  - [exception handling](#exception-handling-2)
- [CSP instance comparison](#csp-instance-comparison)
  - [set reference point](#set-reference-point-3)
  - [output metrix](#output-metrix-3)
  - [exception handling](#exception-handling-3)
- [one\_big\_json\_analytics.py specification](#one_big_json_analyticspy-specification)
  - [requirement](#requirement)
  - [script version info](#script-version-info)
  - [argument parameters](#argument-parameters)


# Performance comparison
絶対性能比較の目的は、同一のWorkloadを異なる<machinename>で実施した際に、どれだけ短い時間で完了させることができるか、もしくは特定の性能仕様でどれだけ高い数値を出せるか、を比較することである。
- "test_name"に"values"値が存在する場合はその数字を性能値とする。この場合は高いほど良い。
- "test_name"に"values"値が存在しない場合は"time"を性能値とする。この場合は低いほど良い。

## set reference point
性能値は`"aws-m8a-2xlarge-amd64":"os":"Ubuntu_25_04": `を基準値`100`とし、各workload毎の値を`<benchmark_score>`とする。

## output metrix
{
    description:"Performance comparison",
    header:{
        <machinename>,<os>,<testcategocy>,<benchmark>
    },
    workload:{
        <test_name>:"<benchmark_score>"
        .....
    }
}

## exception handling
まず最初に基準値を生成し、その後にworkloadの生成を行う。
    - 基準値が生成できない場合は、その原因をErrorとして出力し生成を中断する。
    - 基準値には存在するが他の<machinename>で存在しない項目が出てきた場合、そのworkloadには`"unknown"`と記載し、ErrorにはせずWarningを出力し次に進む。
    - 他の<machinename>で存在し基準値に存在しない項目が出てきた場合、Warningを出力し次に進む。
    - 基準値または比較対象の性能値が`0`の場合、除算エラーを避けるため`"unknown"`と記載し、ErrorにはせずWarningを出力し次に進む。
    - Warningの際は入力JSONファイルの比較部分の行数を明記する。


# Cost comparison
コスト比較の目的は同一のWorkloadを異なる`<machinename>`で実施した際に、Workloadを完了させるのに必要な計算機利用料（ベンチマーク時間ｘ時間当たり利用料）を比較することである。各workload毎の値を`<benchmark_score>`とする。

## set reference point
性能値は`"aws-m8a-2xlarge-amd64":"os":"Ubuntu_25_04": `を基準値`100`とする。

## output metrix
{
    description:"Cost comparison",
    header:{
        <machinename>,<os>,<testcategocy>,<benchmark>
    },
    workload:{
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

# Thread scaling comparison
スレッドスケーリング比較の目的は、同一のWorkloadを同一の`<machinename>`で利用するスレッド数`<N>`を変化させながら実施した際に、その`<machinename>`におけるスレッドスケーリングの特徴を知る事である。各workload毎の値を`<benchmark_score>`とする。

## set reference point
性能値は同一`<machinename>`、同一`<test_name>`でスレッド数が最大値`nproc`時の実行時間を基準値`100`とする。

## output metrix
{
    description:"Thread scaling comparison",
    header:{
        <machinename>,<os>
    },
    workload:{
        <testcategocy>,
        <benchmark>,
        <test_name>:{
            "<N>" : "<benchmark_score>"
            "<N>" : "<benchmark_score>"
            ....  : ....
        }
        .....
    }
}

## exception handling
まず最初に基準値を生成し、その後にworkloadの生成を行う。
    - 基準値が生成できない場合は、その原因をErrorとして出力し生成を中断する。

# CSP instance comparison
CPSインスタンス比較の目的は同一のCSPで同一のWorkloadを異なる`<machinename>`（インスタンス）で実施した際に、Workloadを完了させるのに必要な計算機利用料（ベンチマーク時間ｘ時間当たり利用料）を比較することである。各workload毎の値を`<benchmark_score>`とする。計算機利用料の算出は[Cost comparison](#cost-comparison)を参照する。

## set reference point
性能値はそれぞれのCSPが保有しているarm64インスタンスを基準値`100`とする。arm64インスタンスとはインスタンス名に次の単語を含んでいる。
- AWS : "m8g-xlarge"
- GCP : "c4a-standard-8"
- OCI : "VM.Standard.A1.Flex"

## output metrix
{
    description:"CSP instance comparison",
    header:{
        <machinename>,<os>,<csp>
    },
    workload:{
        <testcategocy>,<benchmark>,
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
ここでは、それぞれのOutput metrixをSTDOUTに生成するPythonスクリプト`one_json_json_analytics.py`を実装する際の仕様について記す。

## requirement
Python3.10で動作すること。`one_big_json_analytics.py`自分自身と入力ファイルである`one_big_json.json`に対してSyntax Errorを検出する機能を有する。

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
- `--csp`（省略可能）：CSP instance comparisonのみを出力する。省略された場合は`--all`が自動選択される。
- `--all`（省略可能）：省略された場合はすべてのOutput Metrix出力を選択する。

