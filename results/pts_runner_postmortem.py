#!/usr/bin/env python3
#
# pts_runner_postmortem.py
#
# このPython3スクリプトはpts_runnerスクリプトが終了後、
# Log Directoryを確認し、テストが正常終了したかどうかを判断し
# その結果と理由を出力する。
# なおスクリプト自身がSyntax Errorを検出する機能も有す。
#
# 1.テスト終了判断基準
#   cloud_onehour/results/README_results.md 内の
#   ### How to distingush `<N>` in `<files>`
#   ### summary file in `<files>`
#   に従う。
#   1.a 例外事項
#   テストが間慮しても、例えば<N>-thread.logに
#   "The following tests failed to properly run"や
#   "The test run did not produce a result"といった
#   テスト失敗を示唆する単語が出現する場合もある。
#   これらのケースは例外事項としてFail判定する。
#
#
# 2.出力
#   cloud_onehour/results/postmortem.json
#
# 3.出力フォーマット
#   {
#     "postmortem_date": "<yyyymmdd-hhmmss>",
#     "project_root": "<path>",
#     "benchmarks": [
#       {
#         "path": "<machinename>/<os>/<testcategory>/<benchmark>",
#         "status": "complete" | "incomplete",
#         "threads": {
#           "<N>": {
#             "status": "complete" | "incomplete",
#             "reason": "<reason>",
#             "missing_files": ["<file1>", "<file2>", ...],
#             "completion_case": 1 | 2 | 3 | 5 | null
#           }
#         }
#       }
#     ],
#     "summary": {
#       "total_benchmarks": 
#       {
#           <machinename>:<int>,
#       }
#       "complete_benchmarks":
#       {
#           <machinename>:<int>,
#       }
#       "incomplete_benchmarks":
#       {
#           <machinename>:<int>,
#           "incomplete_benchmarks_list":
#           {
#               "benchmarks"
#           }
#       }
#     }
#   }
#

import sys
import json
import argparse
import re
import ast
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any

# テスト失敗を示唆する文字列パターン（例外事項1.a）
FAILURE_PATTERNS = [
    "The following tests failed to properly run:",
    "The test run did not produce a result",
    "Test Failed",
    "FAILED",
    "Error:",
    "Exception:",
    "Segmentation fault",
    "core dumped",
    "killed",
    "timeout",
]

# 必須ファイルの定義（README_results.md準拠）
# <N>-thread.csv と <N>-thread.json は必須ファイルから除外
# （<N>-thread.json の有無はケース1/2/3判定で処理する）
REQUIRED_FILES_PER_THREAD = [
    "{N}-thread_freq_end.txt",
    "{N}-thread_freq_start.txt",
    "{N}-thread_perf_stats.txt",
]

# オプションファイルの定義
OPTIONAL_FILES_PER_THREAD = [
    "{N}-thread_perf_summary.json",
]

# stdout.logは<benchmark>ディレクトリ直下に必須
REQUIRED_FILE_BENCHMARK_DIR = "stdout.log"

# ケース5の特殊ベンチマーク
# （<N>-thread_perf_summary.jsonも<N>-thread.jsonも存在しないがテスト完了）
SPECIAL_BENCHMARKS_CASE5 = [
    "build-gcc-1.5.0",
    "build-linux-kernel-1.17.1",
    "build-llvm-1.6.0",
    "coremark-1.0.1",
    "sysbench-1.1.0",
    "java-jmh-1.0.1",
    "ffmpeg-7.0.1",
    "apache-3.0.0",
]


def check_syntax_error(file_path: Path) -> Tuple[bool, Optional[str]]:
    """
    Pythonスクリプトのsyntax errorをチェックする。

    Returns:
        (has_error, error_message)
        - has_error: syntax errorがあればTrue
        - error_message: エラーメッセージ（エラーがなければNone）
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            source = f.read()
        ast.parse(source)
        return False, None
    except SyntaxError as e:
        return True, f"SyntaxError at line {e.lineno}: {e.msg}"
    except Exception as e:
        return True, f"Error parsing file: {str(e)}"


def check_json_syntax(file_path: Path) -> Tuple[bool, Optional[str]]:
    """
    JSONファイルのsyntax errorをチェックする。

    Returns:
        (has_error, error_message)
        - has_error: syntax errorがあればTrue
        - error_message: エラーメッセージ（エラーがなければNone）
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json.load(f)
        return False, None
    except json.JSONDecodeError as e:
        return True, f"JSONDecodeError at line {e.lineno}: {e.msg}"
    except Exception as e:
        return True, f"Error parsing JSON: {str(e)}"


def check_failure_patterns_in_log(log_path: Path) -> Tuple[bool, Optional[str]]:
    """
    ログファイル内にテスト失敗を示唆するパターンがあるかチェックする。

    Returns:
        (has_failure, failure_reason)
        - has_failure: 失敗パターンが見つかればTrue
        - failure_reason: 見つかった失敗パターン（見つからなければNone）
    """
    if not log_path.exists():
        return False, None

    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()

        for pattern in FAILURE_PATTERNS:
            if pattern.lower() in content.lower():
                # コンテキストを取得（パターンの前後の行）
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    if pattern.lower() in line.lower():
                        return True, f"Found failure pattern '{pattern}' in log at line {i+1}"

        return False, None
    except Exception:
        return False, None  # ログが読めない場合は失敗とはみなさない


def find_thread_numbers(benchmark_path: Path) -> List[int]:
    """
    ベンチマークディレクトリ内で見つかったスレッド数のリストを返す。
    <N>-thread...で始まるファイルから<N>を抽出する。
    """
    thread_numbers = set()
    pattern = re.compile(r'^(\d+)-thread')

    for item in benchmark_path.iterdir():
        if item.is_file():
            match = pattern.match(item.name)
            if match:
                thread_numbers.add(int(match.group(1)))
        elif item.is_dir():
            # <N>-thread/ ディレクトリもチェック
            match = pattern.match(item.name)
            if match:
                thread_numbers.add(int(match.group(1)))

    return sorted(list(thread_numbers))


def check_required_files_for_thread(benchmark_path: Path, thread_num: int, benchmark_name: str) -> Tuple[bool, List[str], Optional[int]]:
    """
    指定されたスレッド数のテストに必要なファイルがすべて揃っているかチェック。

    Returns:
        (is_complete, missing_files, completion_case)
        - is_complete: 必須ファイルがすべて揃っているかどうか
        - missing_files: 不足しているファイルのリスト
        - completion_case: 完了ケース番号 (1, 2, 3, 5, または None)
    """
    n = str(thread_num)
    missing_files = []
    completion_case = None

    # まずケース5の特殊ベンチマークかチェック
    is_special_benchmark = benchmark_name in SPECIAL_BENCHMARKS_CASE5

    # ケース5の特殊ベンチマークの場合
    # ケース5は summary.json, <N>-thread_perf_summary.json, <N>-thread.json が
    # すべて存在しないがテスト完了している特殊例
    # 必須: <N>-thread.log（結果抽出に必要）, freq_start, freq_end
    # perf_stats.txt は存在しない場合がある
    if is_special_benchmark:
        # ケース5の必須ファイル（logファイルが必須）
        thread_log = benchmark_path / f"{n}-thread.log"
        thread_log_subdir = benchmark_path / f"{n}-thread" / f"{n}-thread.log"

        if not thread_log.exists() and not thread_log_subdir.exists():
            missing_files.append(f"{n}-thread.log")

        # freq_start, freq_end はオプション扱い（存在すれば読み込む）
        # ただし存在チェックは行わない（ケース5の特殊性）

        if not missing_files:
            completion_case = 5
            return True, [], completion_case
        else:
            return False, missing_files, None

    # 通常のベンチマーク（ケース1, 2, 3）

    # 必須ファイルのチェック（README_results.md準拠）
    # freq_end, freq_start, perf_stats が必須
    # <N>-thread.json の有無はケース判定で処理する
    required_files_to_check = [
        f"{n}-thread_freq_end.txt",
        f"{n}-thread_freq_start.txt",
        f"{n}-thread_perf_stats.txt",
    ]

    for req_file in required_files_to_check:
        file_in_dir = benchmark_path / req_file
        file_in_subdir = benchmark_path / f"{n}-thread" / req_file

        if not file_in_dir.exists() and not file_in_subdir.exists():
            missing_files.append(req_file)

    if missing_files:
        return False, missing_files, None

    # 完了ケースを判定（README_results.md準拠）
    summary_json = benchmark_path / "summary.json"
    n_thread_json = benchmark_path / f"{n}-thread.json"
    n_thread_json_subdir = benchmark_path / f"{n}-thread" / f"{n}-thread.json"
    n_thread_perf_summary = benchmark_path / f"{n}-thread_perf_summary.json"
    n_thread_perf_summary_subdir = benchmark_path / f"{n}-thread" / f"{n}-thread_perf_summary.json"

    has_summary = summary_json.exists()
    has_n_thread_json = n_thread_json.exists() or n_thread_json_subdir.exists()
    has_perf_summary = n_thread_perf_summary.exists() or n_thread_perf_summary_subdir.exists()

    # ケース1: summary.jsonと<N>-thread.jsonの両方が存在
    if has_summary and has_n_thread_json:
        completion_case = 1
    # ケース2: summary.jsonはないが<N>-thread.jsonが存在
    elif not has_summary and has_n_thread_json:
        completion_case = 2
    # ケース3: <N>-thread.jsonはないが<N>-thread_perf_summary.jsonが存在
    elif not has_n_thread_json and has_perf_summary:
        completion_case = 3
    else:
        # <N>-thread.jsonも<N>-thread_perf_summary.jsonも存在しない場合はincomplete
        return False, [f"{n}-thread.json or {n}-thread_perf_summary.json"], None

    return True, [], completion_case


def check_benchmark_completion(benchmark_path: Path) -> Dict[str, Any]:
    """
    ベンチマークディレクトリのテスト完了状態をチェック。

    Returns:
        {
            "status": "complete" | "incomplete",
            "threads": {
                "<N>": {
                    "status": "complete" | "incomplete",
                    "reason": "<reason>",
                    "missing_files": [...],
                    "completion_case": 1 | 2 | 3 | 4 | null
                }
            }
        }
    """
    result = {
        "status": "incomplete",
        "threads": {}
    }

    benchmark_name = benchmark_path.name
    thread_numbers = find_thread_numbers(benchmark_path)

    if not thread_numbers:
        result["threads"]["N/A"] = {
            "status": "incomplete",
            "reason": "No thread-specific files found",
            "missing_files": [],
            "completion_case": None
        }
        return result

    all_complete = True

    for n in thread_numbers:
        is_complete, missing_files, completion_case = check_required_files_for_thread(
            benchmark_path, n, benchmark_name
        )

        if is_complete:
            # 例外事項1.a: ログファイルに失敗パターンがないかチェック
            log_file = benchmark_path / f"{n}-thread.log"
            has_failure, failure_reason = check_failure_patterns_in_log(log_file)

            if has_failure:
                all_complete = False
                result["threads"][str(n)] = {
                    "status": "incomplete",
                    "reason": f"Test failure detected: {failure_reason}",
                    "missing_files": [],
                    "completion_case": completion_case,
                    "failure_detected": True
                }
            else:
                result["threads"][str(n)] = {
                    "status": "complete",
                    "reason": f"All required files present (Case {completion_case})",
                    "missing_files": [],
                    "completion_case": completion_case
                }
        else:
            all_complete = False
            result["threads"][str(n)] = {
                "status": "incomplete",
                "reason": f"Missing required files: {', '.join(missing_files)}",
                "missing_files": missing_files,
                "completion_case": None
            }

    if all_complete:
        result["status"] = "complete"

    return result


def find_benchmarks(project_root: Path) -> List[Path]:
    """
    プロジェクトルートから<machinename>/<os>/<testcategory>/<benchmark>構造を持つ
    すべてのベンチマークディレクトリを見つける。
    """
    benchmarks = []

    # <machinename>レベル
    for machine_dir in sorted(project_root.iterdir()):
        if not machine_dir.is_dir():
            continue
        # 隠しディレクトリやファイルをスキップ
        if machine_dir.name.startswith('.'):
            continue
        # 特定のファイルをスキップ
        if machine_dir.suffix in ['.py', '.json', '.md', '.diff']:
            continue

        # <os>レベル
        for os_dir in sorted(machine_dir.iterdir()):
            if not os_dir.is_dir():
                continue
            if os_dir.name.startswith('.'):
                continue

            # <testcategory>レベル
            for category_dir in sorted(os_dir.iterdir()):
                if not category_dir.is_dir():
                    continue
                if category_dir.name.startswith('.'):
                    continue

                # <benchmark>レベル
                for benchmark_dir in sorted(category_dir.iterdir()):
                    if not benchmark_dir.is_dir():
                        continue
                    if benchmark_dir.name.startswith('.'):
                        continue

                    benchmarks.append(benchmark_dir)

    return benchmarks


def generate_postmortem(project_root: Path) -> Dict[str, Any]:
    """
    プロジェクトルートを解析してpostmortem結果を生成。
    summaryは<machinename>ごとに集計する。
    """
    postmortem = {
        "postmortem_date": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "project_root": str(project_root.absolute()),
        "benchmarks": [],
        "summary": {
            "total_benchmarks": {},
            "complete_benchmarks": {},
            "incomplete_benchmarks": {},
            "incomplete_benchmarks_list": {}
        }
    }

    benchmarks = find_benchmarks(project_root)

    for benchmark_path in benchmarks:
        # 相対パスを計算
        rel_path = benchmark_path.relative_to(project_root)
        path_parts = rel_path.parts

        # <machinename>は相対パスの最初の部分
        machinename = path_parts[0] if path_parts else "unknown"

        completion_status = check_benchmark_completion(benchmark_path)

        benchmark_result = {
            "path": str(rel_path),
            "status": completion_status["status"],
            "threads": completion_status["threads"]
        }

        postmortem["benchmarks"].append(benchmark_result)

        # <machinename>ごとに集計
        if machinename not in postmortem["summary"]["total_benchmarks"]:
            postmortem["summary"]["total_benchmarks"][machinename] = 0
            postmortem["summary"]["complete_benchmarks"][machinename] = 0
            postmortem["summary"]["incomplete_benchmarks"][machinename] = 0
            postmortem["summary"]["incomplete_benchmarks_list"][machinename] = []

        postmortem["summary"]["total_benchmarks"][machinename] += 1
        if completion_status["status"] == "complete":
            postmortem["summary"]["complete_benchmarks"][machinename] += 1
        else:
            postmortem["summary"]["incomplete_benchmarks"][machinename] += 1
            # 不完全なベンチマークのパスをリストに追加
            postmortem["summary"]["incomplete_benchmarks_list"][machinename].append(str(rel_path))

    return postmortem


def main():
    parser = argparse.ArgumentParser(
        description="PTS Runner Postmortem - Analyze test completion status"
    )
    parser.add_argument(
        "--dir", "-D",
        type=str,
        default=".",
        help="Project root directory (default: current directory)"
    )
    parser.add_argument(
        "--output", "-O",
        type=str,
        default=None,
        help="Output file path (default: <project_root>/postmortem.json)"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--check-syntax", "-c",
        action="store_true",
        help="Check syntax of this script and exit"
    )

    args = parser.parse_args()

    # スクリプト自身のSyntax Errorチェック
    script_path = Path(__file__).resolve()
    has_syntax_error, syntax_error_msg = check_syntax_error(script_path)
    if has_syntax_error:
        print(f"Syntax Error in script: {syntax_error_msg}", file=sys.stderr)
        sys.exit(2)

    if args.check_syntax:
        print(f"Syntax check passed for: {script_path}")
        sys.exit(0)

    project_root = Path(args.dir).resolve()

    if not project_root.exists():
        print(f"Error: Project root directory does not exist: {project_root}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = project_root / "postmortem.json"

    if args.verbose:
        print(f"Analyzing project root: {project_root}")
        print(f"Output file: {output_path}")

    # Postmortem生成
    postmortem = generate_postmortem(project_root)

    # 結果を出力
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(postmortem, f, indent=2, ensure_ascii=False)

    # 出力JSONのSyntax Errorチェック
    has_json_error, json_error_msg = check_json_syntax(output_path)
    if has_json_error:
        print(f"JSON Syntax Error in output: {json_error_msg}", file=sys.stderr)
        sys.exit(2)

    # サマリーを表示
    print(f"\n=== PTS Runner Postmortem ===")
    print(f"Date: {postmortem['postmortem_date']}")
    print(f"Project Root: {postmortem['project_root']}")
    print(f"\nSummary (per machinename):")
    for machinename in postmortem["summary"]["total_benchmarks"]:
        total = postmortem["summary"]["total_benchmarks"][machinename]
        complete = postmortem["summary"]["complete_benchmarks"][machinename]
        incomplete = postmortem["summary"]["incomplete_benchmarks"][machinename]
        print(f"  {machinename}:")
        print(f"    Total: {total}, Complete: {complete}, Incomplete: {incomplete}")

    if args.verbose:
        print("\n=== Benchmark Details ===")
        for benchmark in postmortem["benchmarks"]:
            status_symbol = "✓" if benchmark["status"] == "complete" else "✗"
            print(f"\n{status_symbol} {benchmark['path']}")
            for thread, thread_info in benchmark["threads"].items():
                thread_status = "✓" if thread_info["status"] == "complete" else "✗"
                print(f"    {thread_status} Thread {thread}: {thread_info['reason']}")
                if thread_info["missing_files"]:
                    print(f"        Missing: {', '.join(thread_info['missing_files'])}")

    print(f"\nOutput written to: {output_path}")

    # 終了コードを設定（不完全なテストがあれば1）
    total_incomplete = sum(postmortem["summary"]["incomplete_benchmarks"].values())
    if total_incomplete > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
