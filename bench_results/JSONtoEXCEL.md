# JSON to Excel

ベンチマーク結果を保存したJSONファイルを加工しやすい様にExcel（.xlsx）形式に変換します。形式変換の仕様と、その変換を実装したJSONtoEXCEL.pyについて記載します。

## Python version
Python3.12で動作すること。

## Excel index

1行目はIndexとする。

|     | A列       | B列 | C列    | D列  | E列         | F列      | G列   | H列                  |
| --- | --------- | --- | ------ | ---- | ----------- | -------- | ----- | -------------------- |
| 1行 | benchmark | os  | thread | unit | machinename | cpu_name | score | relative_performance |

## Excel contents

２行目以降にJSONのデータをExcelに入れる。例として下記のJSONを考える。

```
{
  "generation log": {
    "version info": "v1.6.0-gb4d3e25",
    "date": "20260302-070133"
  },
  "performance_comparison": {
    "description": "Performance comparison leaderboard by OS",
    "workload": {
      "tensorflow-lite-1.1.0": {
        "TensorFlow Lite 2022-05-18 - Model: SqueezeNet": {
          "os": {
            "RHEL_10": {
              "thread": {
                "4": {
                  "unit": "Microseconds",
                  "leaderboard": [
                    {
                      "rank": 1,
                      "machinename": "aws-m8a-4xlarge-amd64",
                      "cpu_name": "AMD EPYC 9R45 (Zen 5 \"Turin\")",
                      "cpu_isa": "x86-64 (AMX + AVX-512)",
                      "score": 2271.33,
                      "relative_performance": 1.0
                    }]
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

この場合、Excelに抽出されるデータ例とそのデータタイプは以下の通り。

- A列　benchmark : "TensorFlow Lite 2022-05-18 - Model: SqueezeNet",文字列
- B列　os : "RHEL_10" 、文字列
- C列　thread : 4, 整数
- D列　unit :"Microseconds",文字列
- E列　machinename:"aws-m8a-4xlarge-amd64"、文字列
- F列　cpu_name:"AMD EPYC 9R45 (Zen 5 \"Turin\")"、文字列
- G列　score: 2271.33、小数点以下２点
- H列　relative_performance: 1.0、小数点以下２点

## Adding original colum in Excel file

追加でI列を独自に付け加える。I列のデータは以下の条件を満たす。

- 同一のBenchmark、OS、Unitにおいて考える。
- performance_analysisの場合にのみ
  - 最小のthread数でrank=１であるscoreをI列の１００と定義。
- rank, threadを推移させた時のscoreの推移を１００に対して算出し、I列とするが
  - もしunit={Microseconds}の場合は、scoreの推移の逆数x100をI列とする。

I列の趣旨は同一のBenchmark、OS、Unitにおいてperformance_analysisの場合にのみthreadの変化によるscoreの推移を比率として算出する事である。

I列のタイトルは"performance"とする。

## sort, reorder in Excel file
次の順序で列に対してソートを行う。

1. A列 `benchmark` : Excelの自然順ルール
2. E列 `machinename` : Excelの自然順ルール
3. C列 `thread` : Excelの昇順ルール

補足（自然順の意味）:

- 数字を文字列ではなく数値として比較する。
- 例: `1 < 20 < 100 < 200 < 1000 < 4000`


## generate graph 
グラフ作成は*performance_analysis*ファイルにのみ実施されます。
次の手順でそれぞれの`benchmark`毎にグラフを作り、１つのグラフを１ページの構成でまとめたPDFを作る。

### グラフの色

グラフの色はF列`cpu_name`からインスタンスがarm64系なのか、amd系なのか、Intel系なのかを判断する。

- arm64系インスタンスは青っぽい色のバリエーション
- amd系インスタンスは黒っぽい色のバリエーション
- Intel系インスタンスは赤っぽい色のバリエーション

複数のグラフで色が見分けにくい場合に備えて、それぞれの系列の中でSolid line, Dot line(目の粗さによる区別)などを順番に利用して区別をつけやすくする。
グラフに用いた色を凡例と系列にも利用しグラフ全体を見やすくする。

### `thread`が複数ある場合
x軸：`thread`(整数)
y軸：`performance`　y軸は１０刻みで横線
系列:`machinename`
タイトル:`benchmark`(`unit`)

グラフの範囲は`benchmark`名が同じもの。
グラフの系列に番号(1,2...)を付けて、それを凡例とグラフ側にも付けてください。系列の番号がグラフで重なってみえない場合を防ぐために番号はグラフの左端、右端と交互に番号を振る。


### `thread`が１つしかないの場合
`performance`を縦棒グラフで表す。
y軸：`performance`　y軸0-100の間を１０刻みで横線
系列:`machinename`
タイトル:`benchmark`(`unit`),Thread=`thread`
縦棒グラフ時のx軸ラベル：`machinename`（短縮）

グラフの範囲は`benchmark`名が同じもの。
グラフの系列に番号を付けない。
系列は`performance`が大きいものを左において順番に右に並べてください。


## Excel file output
今まで生成されたExcel fileをファイルにダンプする。
`<Testcategory>`として、["AI","Compression","Cryptography_and_TLS","Database","Java_Applications","Memory_Access","Multimedia","Network","Processor","System"]を定義。それぞれの`<Testcategory>`に対して、`<Testcategory>`/`<xxxx>`.jsonを`<xxxx>`.xlsxとしてファイルを作成。Excel保存場所は`<xxxx>`.jsonと同じ。

### Overwrite

すでに同名のExcelファイルがある場合は、上書きする。

## 変換ルール補足

- JSON内の比較ブロックは `*_comparison` を探索し、配下の `workload` を処理対象とする。
- `leaderboard` が存在する場合は `leaderboard` を優先し、無い場合は `ranking` を使用する。
- `score` は `score` を優先し、無い場合は `efficiency_score` を使用する。
- `relative_performance` は `relative_performance` を優先し、無い場合は `relative_cost_efficiency` を使用する。
- `thread` が数字文字列（例: `"4"`）の場合は整数に変換する。

## JSONtoEXCEL.py implementation

- Requirement : Python 3.12 or newer
- Requirement : `openpyxl`
- Requirement : `matplotlib`

### 実行コマンド

```bash
python JSONtoEXCEL.py
```

### オプション

- `--root` : ワークスペースルート（省略時は `JSONtoEXCEL.py` 配置ディレクトリ）
- `--ext` : 出力拡張子（現状 `.xlsx` 固定）
- `--log` : 実行の際に出力されるLog fileが保存されるディレクトリ。省略時は `$PWD/log`。ディレクトリがない場合は自動生成。
- `--graph` :既存のExcelを読みこみ、そこから## generate graph に従いPDFを作成する。 


### 補足

- 変換処理は `JSONtoEXCEL.py` の1ファイルで実施する。
- これまでの中間生成用PowerShellファイル（`*.ps1`）は廃止する。
