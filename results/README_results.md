# about README_results.md
ディレクトリ構造、ファイル構造、ファイル名の命名規則とその後のデータベース作成向けJSONファイル(one_big_json.json)

# Directory and structure
`results/<machinename>/<os>/<testcategory>/<testname>/files`
のディレクトリ構造。今後の命名規則として下記の通りにする。
"machinename"=<machinename>
"os"=<os>
"testcategory"=<testcategory>
"testname"=<testname>

# File details
## performace summary file
<N>スレッド毎に存在。
`results/<machinename>/<os>/<testcategory>/<testname>/<N>-thread_perf_sumary.json`　
もしこのファイルがない場合はそのテストは失敗している。

## Frequency file
<N>スレッド毎に存在。
`results/<machinename>/<os>/<testcategory>/<testname>/<N>-thread_freq_*.txt`　
ファイルが存在していてもテストは失敗していることがあるので注意。

`results/<machinename>/<os>/<testcategory>/<testname>/summary.json`

# extracting one big JSON


# Search, analysis and report by AI
