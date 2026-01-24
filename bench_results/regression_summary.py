#!/usr/bin/env python3
#
# regression_summary.py
#
# このスクリプトは${PWD}の*tar.gzをCSP毎に解凍し、${PWD}/<csp>/results内のファイルを
# まとめて回帰分析を行い、結果を表示します。
#
# 環境と使用方法:
#  Python 3.10以上が必要です。スクリプト自身にSyntax Errorを分析する機能を有します。
#   $> python3 ./regression_summary.py
#
# 動作仕様:
# 1. 解凍
# <csp>毎にファイルを解凍します。どの<csp>のファイルかは、*tar.gzのファイル名から判別します。
# oci_*.tar.gz, aws_*.tar.gz,gcp_*.tar.gzなど。
# <csp>毎にディレクトリを作成し、その中に解凍します。解凍が終わると、${PWD}/<csp>/results内に
# ベンチマーク結果ファイルが配置されます。
#
# 2. <csp>内でのデータ収集
#　まず${PWD}/<csp>/results内のベンチマーク結果ファイルを収集します。
#  <machinename>は複数あるので、それを --mergeオプションで一つにまとめます。
#  $> cd ${PWD}/<csp>/results && \
#     ../../../results/make_one_big_json.py --merge one_big_json_<machinename>.json --output <csp>_all_results.json
#
# 3. Globalでのデータ収集
#  ${PWD}/globalディレクトリに移動し、各<csp>の結果を収集します。
#  ${PWD}/<csp>/results内の<csp>_all_results.jsonを収集し、--merge で全体を一つにまとめます。
#  $> cd ${PWD}/global/results && \
#     ../../../results/make_one_big_json.py --merge ../<csp>/<csp>_all_results.json.json --output global_all_results.json
# 
# 4. Globalでの回帰分析
#  ${PWD}/globalディレクトリで、one_big_json_analytics.pyを使って回帰分析を行います。
#  $> cd ${PWD}/global && \
#     ../../../results/one_big_json_analytics.py \
#     --input global_all_results.json --perf > global_performance_analysis.json
#  $> cd ${PWD}/global && \
#     ../../../results/one_big_json_analytics.py \
#     --input global_all_results.json --cost > global_cost_analysis.json
#
#
from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
from pathlib import Path
from typing import Iterable, List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract CSP tarballs, merge results, and run global regression analysis."
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=Path.cwd(),
        help="Working directory that contains *tar.gz files (default: ${PWD}).",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue even if a CSP step fails.",
    )
    return parser.parse_args()


def validate_script_syntax() -> bool:
    try:
        with open(__file__, "r", encoding="utf-8") as handle:
            source = handle.read()
        compile(source, __file__, "exec")
        return True
    except SyntaxError as exc:
        print(f"Error: Script syntax error: {exc}", file=sys.stderr)
        return False


def find_tarballs(workdir: Path) -> List[Path]:
    return sorted(workdir.glob("*.tar.gz"))


def infer_csp_name(tar_path: Path) -> str:
    name = tar_path.name
    if "_" in name:
        return name.split("_", 1)[0].lower()
    # Fallback: strip double suffix .tar.gz
    stem = tar_path.name
    if stem.endswith(".tar.gz"):
        stem = stem[:-7]
    return stem.lower()


def is_within_directory(base: Path, target: Path) -> bool:
    try:
        base_resolved = base.resolve()
    except FileNotFoundError:
        base_resolved = base.resolve(strict=False)
    try:
        target_resolved = target.resolve()
    except FileNotFoundError:
        target_resolved = target.resolve(strict=False)
    try:
        target_resolved.relative_to(base_resolved)
        return True
    except ValueError:
        return False


def safe_extract(tar: tarfile.TarFile, target_dir: Path) -> None:
    for member in tar.getmembers():
        member_path = target_dir / member.name
        if not is_within_directory(target_dir, member_path):
            raise RuntimeError(f"Unsafe path in tar file: {member.name}")
    tar.extractall(path=target_dir)


def run_script(args: List[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=cwd, check=True, text=True, capture_output=True)


def merge_jsons(
    make_one_big_json: Path,
    inputs: Iterable[Path],
    output: Path,
    cwd: Path,
) -> None:
    input_list = [str(p) for p in inputs]
    if not input_list:
        raise RuntimeError(f"No input JSON files found for merge output: {output}")
    cmd = [sys.executable, str(make_one_big_json), "--merge", *input_list, "--output", str(output)]
    run_script(cmd, cwd)


def run_analytics(
    analytics_script: Path,
    input_json: Path,
    output_json: Path,
    mode_flag: str,
    cwd: Path,
) -> None:
    cmd = [
        sys.executable,
        str(analytics_script),
        "--input",
        str(input_json),
        mode_flag,
    ]
    result = run_script(cmd, cwd)
    output_json.write_text(result.stdout, encoding="utf-8")


def main() -> int:
    if not validate_script_syntax():
        return 1
    args = parse_args()
    workdir = args.workdir.resolve()
    script_dir = Path(__file__).resolve().parent
    results_dir = (script_dir / ".." / "results").resolve()
    make_one_big_json = results_dir / "make_one_big_json.py"
    analytics_script = results_dir / "one_big_json_analytics.py"

    tarballs = find_tarballs(workdir)
    if not tarballs:
        print(f"No tar.gz files found in {workdir}", file=sys.stderr)
        return 1

    csp_dirs: List[Path] = []

    for tar_path in tarballs:
        csp_name = infer_csp_name(tar_path)
        csp_dir = workdir / csp_name
        csp_dir.mkdir(parents=True, exist_ok=True)
        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                safe_extract(tar, csp_dir)
            csp_dirs.append(csp_dir)
            print(f"Extracted {tar_path.name} -> {csp_dir}")
        except Exception as exc:
            print(f"Failed to extract {tar_path}: {exc}", file=sys.stderr)
            if not args.keep_going:
                return 1

    for csp_dir in csp_dirs:
        results_path = csp_dir / "results"
        if not results_path.is_dir():
            msg = f"Missing results directory: {results_path}"
            if args.keep_going:
                print(msg, file=sys.stderr)
                continue
            print(msg, file=sys.stderr)
            return 1

        input_jsons = sorted(results_path.glob("one_big_json_*.json"))
        output_json = results_path / f"{csp_dir.name}_all_results.json"
        try:
            merge_jsons(make_one_big_json, input_jsons, output_json, results_path)
            print(f"Merged CSP results -> {output_json}")
        except Exception as exc:
            print(f"Failed to merge CSP results in {results_path}: {exc}", file=sys.stderr)
            if not args.keep_going:
                return 1

    global_dir = workdir / "global"
    global_dir.mkdir(parents=True, exist_ok=True)
    global_results = global_dir / "global_all_results.json"
    global_inputs = [
        (csp_dir / "results" / f"{csp_dir.name}_all_results.json") for csp_dir in csp_dirs
    ]
    try:
        merge_jsons(make_one_big_json, global_inputs, global_results, global_dir)
        print(f"Merged global results -> {global_results}")
    except Exception as exc:
        print(f"Failed to merge global results: {exc}", file=sys.stderr)
        return 1

    try:
        perf_output = global_dir / "global_performance_analysis.json"
        cost_output = global_dir / "global_cost_analysis.json"
        run_analytics(analytics_script, global_results, perf_output, "--perf", global_dir)
        run_analytics(analytics_script, global_results, cost_output, "--cost", global_dir)
        print(f"Generated analysis -> {perf_output}")
        print(f"Generated analysis -> {cost_output}")
    except Exception as exc:
        print(f"Failed to run analytics: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
