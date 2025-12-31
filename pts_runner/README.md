# introduction
pts_runner/pts_runner_<testname>.pyでやりたいことを書きます。
なおPTSの利用方法は
```
phoronix-test-suite --help
```
を参照。
すべてのpts_runner/pts_runner_<testname>.pyにおいて可能な限り関数名、構造を共通化。個別対応が必要な部分はそれが個別であることがわかるように実装。

## Find <testname> 
<testname>は../test_suite.json内にリストがある。"items": 以下の"pts/<testname>"という形式。../test_suite.jsonに登録されていない<testname>は不正。

## setup
PTSのCacheをすべてクリアする。
なおPTSはInstallされていることが前提(../scripts/setup_pts.sh)。

## clean up
PTSから<testname>をPTSのInstallコマンドを使ってクリーンインストールする、その際にはGcc-14を使って毎回Nativeでコンパイルを行う。環境としてはUbuntu22以上、Hardwareはarm64/amd64の両方を前提とする。

- なおデバッグの為に、このPTSのクリーンインストールコマンドを標準出力に出す。

- クリーンインストール時のBuildは、"THFix_in_compile": trueの場合はNUM_CPU_CORES=vCPU数でBinaryの対応するスレッド数設定、コンパイル時は最大限並列化させたいので -j`nproc` としてビルドを行う。

- クリーンインストール時のBuildは、"THFix_in_compile": falseの場合はNUM_CPU_CORESを指定しない。

- PTSのインストールコマンドはbatch-installを使う事。

## set <N>=number of threads

実行形式として、引数が与えられるのでそれを間違えないように。引数の定義は

- 引数　<N>, Nは1以上の整数の場合
taskset を用いて利用するCPUアフィニティを固定したうえで、NUM_CPU_CORES=<N>としてptsを実行する。
なおアフィニティはamd64系のHyperThread機能を最大限利用するために{0,2,4...1,3,5...}という順番で並べる。

- 引数　<N>, Nは1以上の整数の場合でN>=ｖCPUの場合
Nが環境のvCPU数と同数もしくは超える場合は、N=vCPUとし、NUM_CPU_CORES=<N>
この場合、すべてのvCPUにスレッドを割り当てるとの観点からtasksetは用いる必要はない。

- 引数が与えられない場合
NUM_CPU_CORES=1,2,3,,,,vCPUと<N>を＋１で数を増やしながらベンチマークを行う。この際のtasksetの設定は3-aと同じく、amd64系のHyperThread機能を最大限利用するために{0,2,4...1,3,5...}という順番で並べる。

## set runtime config(common setting)
これらの設定は、PTSでテストを実行時にのみ利用される。
- NUM_CPU_CORES=<N>
- BATCH_MODE=1
- SKIP_ALL_PROMPTS=1

## set runtime config(unique setting)
これらの設定は、<testname>毎に固有でPTSでテストを実行時にのみ利用される。
今の時点では未定。

## set output directory
ベンチマーク結果は results/<machine name>/<test_category>/<testname>/<N>-thread.log
<test_category>にスペースがある場合は"_"に置換する。
ベンチマーク結果はRaw dataと、すべての<N>のデータをわかりやすく読みやすく集計したsummary.log, summary.logを別のAI解析に利用するために統一されたJSON formatで記述したsummary.jsonから構成される。summaryはすべての<N>のデータを集計する必要があるので、テスト終了後に行われることが期待される。

## run PTS
上記の設定を使って、PTSを走行。
その際のPTSコマンドはデバッグの為に標準出力する。

- 標準出力、標準エラー出力は
results/<machine name>/<test_category>/<testname>/stdout.log
にteeコマンドでダンプしながら、ターミナルにも出力。

- PTSのbatch-runコマンドを利用

- 環境設定はPTS実施の先に来ないとだめ。
例えば $> NUM_CPU_CORES=4 phoronix-test-suite benchmark
