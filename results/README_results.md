# about README_results.md
ディレクトリ構造、ファイル構造、ファイル名の命名規則、データ内容を解説。
その後のデータベース作成向けJSONファイル(one_big_json.json)実装仕様を説明する。

# Directory and structure
`results/<machinename>/<os>/<testcategory>/<benchmark>/files`
のディレクトリ構造。今後の命名規則として下記の通りにする。
"machinename"=<machinename>
"os"=<os>
"testcategory"=<testcategory>
"benchmark"=<benchmark>

# File details

## Common rule
データを読むうえで共通の概念を説明する。

### Number of threads in hardware and Thread count in benchmark
ベンチマークで利用するスレッド数は`<N>`で表される。
ベンチマークが走るハードウェアの持っているスレッド数`nproc`は`vCPU`として表される。

### CPU affinity 
ベンチマークで利用されるＣＰＵアフィニティの順序はamd64系のHyperThread機能を考慮して、`{0,2,4,6....,1,3,5,7..[vCPU-1]}`である。
例えば`vCPU=4`, `<N>=3のベンチマーク設定で利用されるCPUアフィニティは`{0,1,2}`となる。`<N>=vCPU`の場合はすべてのCPUアフィニティを利用していることになる。このようなCPUアフィニティの分散は`vCPU=physical CPU`であるプロセッサでは意味がないが、両方で対応できるようにこのような仕様にしている。

## Performance summary file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/<N>-thread_perf_summary.json`　
    <N>スレッド毎にファイルが存在する。
    もしこのファイルが特定の<N>だけ存在しない場合は、そのスレッド数のテストは行っていない、もしくは失敗していることを意味する。


## Frequency file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/<N>-thread_freq_*.txt`　
    <N>スレッド毎にファイルが存在する。
    ファイルが存在していてもテストは失敗していることがあるので注意。
    ベンチマーク開始地点は<N>-threa_freq_start.txt。
    ベンチマーク終了地点は<N>-threa_freq_end.txt。
    CPUアフィニティ順に[Hz]単位で記録されている。

## Benchmark summary file
- `results/<machinename>/<os>/<testcategory>/<benchmark>/summary.json`
    すべての<N>-thread_perf_summary.jsonをまとめたファイルであり、1つだけ存在。
    存在しない場合はテストが失敗している。

# Extracting one big JSON

## Definition of one big JSON format
{
    machinename:"<machinename>"{
        total_vcpu:"<vcpu>"
        os:"<os>"{
            testcategory:"<testcategory>"{
                benchmark:"<benchmark>"{
                    testname:"<testname>"{
                        description:"<description>"
                        test_thread:"<N>"{
                            start_freq:{"freq_0":<freq_0>, "freq_1":<freq_1>, "freq_2":<freq_2>, ...}
                            end_freq:{"freq_0":<freq_0>, "freq_1":<freq_1>, "freq_2":<freq_2>, ...}
                            ipc:{"ipc_0":<ipc_0>, "ipc_1":<ipc_1>, "ipc_2":<ipc_2>, ...}
                            total_cycles:{"total_cycles_0":<total_cycles_0>, "total_cycles_1":<total_cycles_1>, "total_cycles_2":<total_cycles_2>, ...}
                            total_instructions:{"total_instructions_0":<total_instructions_0>, "total_instructions_1":<total_instructions_1>, "total_instructions_2":<total_instructions_2>, ...}
                            cpu_utilization_percent:<cpu_utilization_percent>
                            elapsed_time_sec:<elapsed_time_sec>
                        }
                    }
                }
            }
        }
    }
}


# Search, analysis and report by AI
