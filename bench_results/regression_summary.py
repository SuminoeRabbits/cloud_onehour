#!/usr/bin/env python3
#
# regression_summary.py
#
# このスクリプトは${PWD}の*tar.gzをマシン毎に解凍し、${PWD}/<machinename>/results内のファイルを
# まとめて回帰分析を行い、結果を表示します。
#
# 環境と使用方法:
#  Python 3.10以上が必要です。スクリプト自身にSyntax Errorを分析する機能を有します。
#   $> python3 ./regression_summary.py
#
# このスクリプト動作仕様:
# 1. 解凍とone_big_json.jsonの確認
# オプション: --extract(省略可能)　指定時はこのステップのみ
# 1.a 解凍
# <machinename>毎にファイルを解凍します。どの<machinename>のファイルかは、*tar.gzのファイル名から判別します。
# <machinename>_<os>*.tar.gzなどになっています。
# <machinename>毎にディレクトリを作成し、その中に解凍します。解凍が終わると、${PWD}/<machinename>/results内に
# ベンチマーク結果ファイルが配置されます。
# 解凍の順番は以下の通り:
# - 圧縮ファイルのタイムスタンプが古い順
# - そのうえでアルファベット順にソートして順番に解凍
# ${PWD}/<machinename>/results内にone_big_json_<machinename>.jsonが無い場合はその場で生成します。
# one_big_json_<machinename>.jsonが存在する場合でも上書きします。
# 1.b one_big_json_<machinename>.jsonの生成
# <machinename>/results内にone_big_json_<machinename>.jsonが無い場合は生成します。
# JSONが壊れている場合は一旦削除してから生成します。
# $> ../results/make_one_big_json.py \
#    --dir <machinename>/results \
#    --output <machinename>/results/one_big_json_<machinename>.json
# $> ../results/pts_runner_postmortem.py \
#    --dir <machinename>/results \
#    --output <machinename>/results/postmortem_<machinename>.json
#
# 2. <machinename>内でのデータ収集
# オプション: --merge-machine(省略可能)　指定時はこのステップのみ
# まず${PWD}/<machinename>/results内のベンチマーク結果ファイルを収集します。
# one_big_json_<machinename>.jsonが1つでも複数ある場合でも、それを --mergeオプションで一つにまとめます。
# <machinename>/results/all_results_<machinename>.jsonが存在する場合でも上書きします。
# (--merge時は--dirは無視されます)
# $> ../results/make_one_big_json.py \
#     --merge <machinename>/results/one_big_json_<machinename>.json ... \
#     --output <machinename>/results/all_results_<machinename>.json
#
# 3. Globalでのデータ収集
# オプション: --merge-global(省略可能)　指定時はこのステップのみ
# ${PWD}/globalディレクトリで各<machinename>の結果を収集します。
# ${PWD}/globalディレクトリが無い場合は作成します。
# ${PWD}/<machinename>/results内のall_results_<machinename>.jsonを収集し、
# --merge で全体を${PWD}/global以下に一つにまとめます。
# ./global/global_all_results.jsonが存在する場合でも上書きします。
# $> ../results/make_one_big_json.py \
#     --merge ../<machinename>/results/all_results_<machinename>.json ... \
#     --output ./global/global_all_results.json
# 
# 4. Globalでの回帰分析
# オプション: --analyze(省略可能)　指定時はこのステップのみ
# ${PWD}/globalディレクトリで、one_big_json_analytics.pyを使って回帰分析を行います。
# $> ../results/one_big_json_analytics.py \
#     --input ./global/global_all_results.json \
#     --perf > ./global/global_performance_analysis.json
# $> ../results/one_big_json_analytics.py \
#     --input ./global/global_all_results.json \
#     --cost > ./global/global_cost_analysis.json
#
# 5. Globalでの回帰分析を"testcategory"毎に実行
# オプション: --analyze --testcategory（省略可能）指定時はこのステップのみ
# 4.の回帰分析を、"testcategory"リスト毎に実行し,さらに`--no_arm64`,`--no_amd64`を適応します。
# "testcategory"は --inputで指定されたJSONの"testcategory": {...}のキーに対応します。
# この際には${PWD}/global/<testcategory>ディレクトリの存在を確認し、ない場合は作成、その中で分析を行います。
# なお、--testcategoryオプションは--analyzeが指定されている場合にのみ有効です。
# # $> ../results/one_big_json_analytics.py \
#     --input ./global/global_all_results.json \
#     --testcategory <testcategory> \
#     --perf --no_arm64 \
#     --output  ./global/<testcategory>/global_performance_analysis_x86_64.json
# # $> ../results/one_big_json_analytics.py \
#     --input ./global/global_all_results.json \
#     --testcategory <testcategory> \
#     --perf --no_amd64 \
#     --output  ./global/<testcategory>/global_performance_analysis_arm64.json
# $> ../results/one_big_json_analytics.py \
#     --input ./global/global_all_results.json \
#     --testcategory <testcategory> \
#     --cost --no_arm64\
#     --output ./global/<testcategory>/global_cost_analysis_x86_64.json
# $> ../results/one_big_json_analytics.py \
#     --input ./global/global_all_results.json \
#     --testcategory <testcategory> \
#     --cost --no_amd64\
#     --output ./global/<testcategory>/global_cost_analysis_arm64.json
#
#
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import shutil
import re
from pathlib import Path
from typing import Iterable, List, Optional


def infer_workdir_from_argv(default: Path) -> Path:
    """Infer --workdir from argv for dynamic help text generation."""
    argv = sys.argv[1:]
    for idx, arg in enumerate(argv):
        if arg == "--workdir" and idx + 1 < len(argv):
            return Path(argv[idx + 1])
        if arg.startswith("--workdir="):
            return Path(arg.split("=", 1)[1])
    return default


def build_testcategory_help_text(workdir: Path) -> str:
    """Build dynamic help text for available --testcategory values."""
    global_json = workdir / "global" / "global_all_results.json"
    if not global_json.exists():
        return (
            "\nAvailable --testcategory values:\n"
            "  (global/global_all_results.json not found; run --merge-global first)"
        )

    categories = extract_available_testcategories(global_json)
    if not categories:
        return (
            "\nAvailable --testcategory values:\n"
            f"  (no testcategory keys found in {global_json})"
        )

    lines = ["", "Available --testcategory values:"]
    for category in sorted(categories):
        lines.append(f"  - {category}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    default_workdir = Path.cwd()
    inferred_workdir = infer_workdir_from_argv(default_workdir).resolve()
    dynamic_epilog = build_testcategory_help_text(inferred_workdir)

    parser = argparse.ArgumentParser(
        description="Extract CSP tarballs, merge results, and run global regression analysis.",
        epilog=dynamic_epilog,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Run step 1 only: extract CSP tarballs into per-CSP directories.",
    )
    parser.add_argument(
        "--merge-machine",
        action="store_true",
        help="Run step 2 only: merge per-machine one_big_json_*.json files.",
    )
    parser.add_argument(
        "--merge-global",
        action="store_true",
        help="Run step 3 only: merge per-machine *_all_results.json into global.",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run step 4 only: run global performance/cost analysis.",
    )
    parser.add_argument(
        "--testcategory",
        action="append",
        nargs="?",
        const="__ALL__",
        help=(
            "Run step 5 with --analyze: per-testcategory analysis. "
            "If specified without a value, all available testcategories are targeted. "
            "Accepts comma-list or bracket-list (e.g. --testcategory Multimedia,Processor "
            "or --testcategory [Multimedia,Processor])."
        ),
    )
    parser.add_argument(
        "--workdir",
        type=Path,
        default=default_workdir,
        help="Working directory that contains *tar.gz files (default: ${PWD}).",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="Continue even if a CSP step fails.",
    )
    parser.add_argument(
        "--h",
        action="help",
        help="Show this help message and exit.",
    )
    return parser.parse_args()


def parse_testcategory_filters(raw_values: Optional[List[str]]) -> tuple[List[str], bool]:
    """Parse --testcategory values. Returns (requested_categories, all_categories_requested)."""
    if not raw_values:
        return [], False

    parsed: List[str] = []
    seen = set()
    all_categories_requested = False

    for raw in raw_values:
        if raw == "__ALL__":
            all_categories_requested = True
            continue

        text = (raw or "").strip()
        if not text:
            continue

        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1].strip()

        for part in text.split(","):
            category = part.strip().strip("\"'")
            if category and category not in seen:
                parsed.append(category)
                seen.add(category)

    return parsed, all_categories_requested


def extract_available_testcategories(global_json: Path) -> set[str]:
    """Collect available testcategory keys from merged global JSON."""
    categories: set[str] = set()
    try:
        with open(global_json, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:
        return categories

    for machine_name, machine_data in data.items():
        if machine_name in ("generation log", "generation_log"):
            continue
        if not isinstance(machine_data, dict):
            continue
        os_data = machine_data.get("os", {})
        if not isinstance(os_data, dict):
            continue
        for _, os_content in os_data.items():
            if not isinstance(os_content, dict):
                continue
            tc_map = os_content.get("testcategory", {})
            if isinstance(tc_map, dict):
                categories.update(tc_map.keys())

    return categories


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
    tarballs = list(workdir.glob("*.tar.gz"))
    tarballs.sort(key=lambda path: (path.stat().st_mtime, path.name))
    return tarballs


def find_csp_dirs(workdir: Path) -> List[Path]:
    csp_dirs = []
    reserved_names = {"global", "tmp", "logs"}
    for entry in workdir.iterdir():
        if not entry.is_dir() or entry.name in reserved_names:
            continue
        if (entry / "results").is_dir():
            csp_dirs.append(entry)
    return sorted(csp_dirs)


def generate_one_big_json_if_missing(
    results_path: Path,
    make_one_big_json: Path,
    machinename: str,
) -> None:
    output_json = results_path / f"one_big_json_{machinename}.json"
    if output_json.exists():
        if is_valid_one_big_json(output_json, machinename):
            print(f"Overwriting existing JSON -> {output_json}")
        else:
            print(f"Removing invalid JSON -> {output_json}")
            output_json.unlink(missing_ok=True)
    cmd = [
        sys.executable,
        str(make_one_big_json),
        "--dir",
        str(results_path),
        "--output",
        str(output_json),
        "--force",
    ]
    run_script(cmd, results_path)
    print(f"Generated missing JSON -> {output_json}")


def get_script_major_version(script_path: Path) -> Optional[str]:
    try:
        content = script_path.read_text(encoding="utf-8")
        # Look for SCRIPT_VERSION = "vX.Y.Z"
        match = re.search(r'SCRIPT_VERSION\s*=\s*["\'](v\d+)\.\d+\.\d+["\']', content)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def is_valid_one_big_json(json_path: Path, machinename: str, target_major: Optional[str] = None) -> bool:
    try:
        with open(json_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Invalid JSON file {json_path}: {exc}", file=sys.stderr)
        return False

    # Version check
    if target_major:
        file_version = (
            data.get("generation log", {}).get("version info")
            or data.get("generation_log", {}).get("version_info")
            or "unknown"
        )
        match = re.match(r'(v\d+)\.\d+\.\d+', file_version)
        if not match or match.group(1) != target_major:
            print(f"Version mismatch in {json_path}: expected {target_major}, found {file_version}", file=sys.stderr)
            return False

    if machinename not in data:
        print(
            f"Invalid one_big_json: missing machinename '{machinename}' in {json_path}",
            file=sys.stderr,
        )
        return False
    machine_data = data.get(machinename, {})
    os_data = machine_data.get("os", {})
    if not isinstance(os_data, dict) or not os_data:
        print(
            f"Invalid one_big_json: empty os data for '{machinename}' in {json_path}",
            file=sys.stderr,
        )
        return False
    return True


def infer_machine_name(tar_path: Path) -> str:
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
    cmd = [
        sys.executable,
        str(make_one_big_json),
        "--merge",
        *input_list,
        "--output",
        str(output),
        "--force",
    ]
    run_script(cmd, cwd)


def run_analytics(
    analytics_script: Path,
    input_json: Path,
    output_json: Path,
    mode_flags: List[str],
    cwd: Path,
) -> None:
    cmd = [
        sys.executable,
        str(analytics_script),
        "--input",
        str(input_json),
        *mode_flags,
        "--output",
        str(output_json),
    ]
    run_script(cmd, cwd)


def run_postmortem(
    postmortem_script: Path,
    results_path: Path,
    output_json: Path,
    cwd: Path,
) -> None:
    cmd = [
        sys.executable,
        str(postmortem_script),
        "--dir",
        str(results_path),
        "--output",
        str(output_json),
    ]
    run_script(cmd, cwd)


def main() -> int:
    if not validate_script_syntax():
        return 1
    args = parse_args()
    requested_testcategories, all_testcategories_requested = parse_testcategory_filters(args.testcategory)
    if (requested_testcategories or all_testcategories_requested) and not args.analyze:
        print("Error: --testcategory requires --analyze.", file=sys.stderr)
        return 1

    workdir = args.workdir.resolve()
    script_dir = Path(__file__).resolve().parent
    results_dir = (script_dir / ".." / "results").resolve()
    make_one_big_json = results_dir / "make_one_big_json.py"
    analytics_script = results_dir / "one_big_json_analytics.py"
    postmortem_script = results_dir / "pts_runner_postmortem.py"
    target_major = get_script_major_version(make_one_big_json)

    step_flags = [args.extract, args.merge_machine, args.merge_global, args.analyze]
    run_all = not any(step_flags)

    csp_dirs: List[Path] = []

    if run_all or args.extract:
        tarballs = find_tarballs(workdir)
        if not tarballs:
            print(f"No tar.gz files found in {workdir}", file=sys.stderr)
            return 1

        for tar_path in tarballs:
            if tar_path.stat().st_size == 0:
                print(f"Skipping empty tar.gz: {tar_path}", file=sys.stderr)
                tar_path.unlink(missing_ok=True)
                continue
            machinename = infer_machine_name(tar_path)
            machine_dir = workdir / machinename
            machine_dir.mkdir(parents=True, exist_ok=True)
            try:
                with tarfile.open(tar_path, "r:gz") as tar:
                    safe_extract(tar, machine_dir)
                csp_dirs.append(machine_dir)
                print(f"Extracted {tar_path.name} -> {machine_dir}")
            except Exception as exc:
                print(f"Failed to extract {tar_path}: {exc}", file=sys.stderr)
                if not args.keep_going:
                    return 1
        for csp_dir in csp_dirs:
            results_path = csp_dir / "results"
            if results_path.is_dir():
                try:
                    # check and regenerate if invalid or old version
                    output_json = results_path / f"one_big_json_{csp_dir.name}.json"
                    if not output_json.exists() or not is_valid_one_big_json(output_json, csp_dir.name, target_major):
                        generate_one_big_json_if_missing(results_path, make_one_big_json, csp_dir.name)
                    
                    postmortem_output = results_path / f"postmortem_{csp_dir.name}.json"
                    run_postmortem(postmortem_script, results_path, postmortem_output, results_path)
                    print(f"Generated postmortem -> {postmortem_output}")
                except Exception as exc:
                    print(f"Failed to generate JSON in {results_path}: {exc}", file=sys.stderr)
                    if not args.keep_going:
                        return 1
    else:
        csp_dirs = find_csp_dirs(workdir)

    if run_all or args.merge_machine:
        for csp_dir in csp_dirs:
            results_path = csp_dir / "results"
            if not results_path.is_dir():
                msg = f"Missing results directory: {results_path}"
                if args.keep_going:
                    print(msg, file=sys.stderr)
                    continue
                print(msg, file=sys.stderr)
                return 1

            output_json = results_path / f"all_results_{csp_dir.name}.json"
            try:
                # Always regenerate canonical one_big_json with latest parser/lookup logic
                generate_one_big_json_if_missing(results_path, make_one_big_json, csp_dir.name)

                # Ensure any additional one_big_json_*.json are still valid
                input_jsons = sorted(results_path.glob("one_big_json_*.json"))
                for json_file in input_jsons:
                    if not is_valid_one_big_json(json_file, csp_dir.name, target_major):
                        print(f"Removing invalid JSON -> {json_file}")
                        json_file.unlink(missing_ok=True)

                input_jsons = sorted(results_path.glob("one_big_json_*.json"))
                if not input_jsons:
                    raise RuntimeError(f"No valid one_big_json files found in {results_path}")

                if len(input_jsons) == 1:
                    shutil.copy2(input_jsons[0], output_json)
                    print(f"Copied single CSP result -> {output_json}")
                else:
                    merge_jsons(make_one_big_json, input_jsons, output_json, results_path)
                    print(f"Merged CSP results -> {output_json}")
            except Exception as exc:
                print(f"Failed to merge CSP results in {results_path}: {exc}", file=sys.stderr)
                if not args.keep_going:
                    return 1

    global_dir = workdir / "global"
    global_results = global_dir / "global_all_results.json"
    if run_all or args.merge_global:
        global_dir.mkdir(parents=True, exist_ok=True)
        global_inputs = []
        for csp_dir in csp_dirs:
            p = csp_dir / "results" / f"all_results_{csp_dir.name}.json"
            if p.exists():
                global_inputs.append(p)
            else:
                print(f"Warning: Missing results for {csp_dir.name} -> {p}", file=sys.stderr)

        if not global_inputs:
            print("Error: No individual results found to merge into global.", file=sys.stderr)
            return 1

        try:
            merge_jsons(make_one_big_json, global_inputs, global_results, global_dir)
            print(f"Merged global results -> {global_results}")
        except Exception as exc:
            print(f"Failed to merge global results: {exc}", file=sys.stderr)
            return 1

    if run_all or args.analyze:
        try:
            if requested_testcategories or all_testcategories_requested:
                available_categories = extract_available_testcategories(global_results)
                valid_categories = []
                if all_testcategories_requested:
                    valid_categories = sorted(available_categories)

                for category in requested_testcategories:
                    if category in available_categories:
                        if category not in valid_categories:
                            valid_categories.append(category)
                    else:
                        print(
                            f"Warning: testcategory '{category}' not found in {global_results}. Skipping.",
                            file=sys.stderr,
                        )

                if not valid_categories:
                    print("Error: No valid --testcategory values to analyze.", file=sys.stderr)
                    return 1

                for category in valid_categories:
                    category_dir = global_dir / category
                    category_dir.mkdir(parents=True, exist_ok=True)

                    cost_x86_output = category_dir / "global_cost_analysis_x86_64.json"
                    cost_arm64_output = category_dir / "global_cost_analysis_arm64.json"

                    run_analytics(analytics_script, global_results, cost_x86_output, ["--testcategory", category, "--cost", "--no_arm64"], category_dir)
                    run_analytics(analytics_script, global_results, cost_arm64_output, ["--testcategory", category, "--cost", "--no_amd64"], category_dir)

                    print(f"Generated analysis -> {cost_x86_output}")
                    print(f"Generated analysis -> {cost_arm64_output}")
            else:
                perf_output = global_dir / "global_performance_analysis.json"
                cost_output = global_dir / "global_cost_analysis.json"
                th_output = global_dir / "global_thread_scaling_analysis.json"
                csp_output = global_dir / "global_csp_comparison_analysis.json"
                perf_arm64_output = global_dir / "global_performance_analysis_arm64.json"
                cost_arm64_output = global_dir / "global_cost_analysis_arm64.json"
                th_arm64_output = global_dir / "global_thread_scaling_analysis_arm64.json"
                csp_arm64_output = global_dir / "global_csp_comparison_analysis_arm64.json"

                run_analytics(analytics_script, global_results, perf_output, ["--perf"], global_dir)
                run_analytics(analytics_script, global_results, cost_output, ["--cost"], global_dir)
                run_analytics(analytics_script, global_results, th_output, ["--th"], global_dir)
                run_analytics(analytics_script, global_results, csp_output, ["--csp"], global_dir)

                run_analytics(analytics_script, global_results, perf_arm64_output, ["--perf", "--no_amd64"], global_dir)
                run_analytics(analytics_script, global_results, cost_arm64_output, ["--cost", "--no_amd64"], global_dir)
                run_analytics(analytics_script, global_results, th_arm64_output, ["--th", "--no_amd64"], global_dir)
                run_analytics(analytics_script, global_results, csp_arm64_output, ["--csp", "--no_amd64"], global_dir)

                print(f"Generated analysis -> {perf_output}")
                print(f"Generated analysis -> {cost_output}")
                print(f"Generated analysis -> {th_output}")
                print(f"Generated analysis -> {csp_output}")
                print(f"Generated analysis -> {perf_arm64_output}")
                print(f"Generated analysis -> {cost_arm64_output}")
                print(f"Generated analysis -> {th_arm64_output}")
                print(f"Generated analysis -> {csp_arm64_output}")
        except Exception as exc:
            print(f"Failed to run analytics: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
