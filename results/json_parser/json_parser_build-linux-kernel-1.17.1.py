#!/usr/bin/env python3
"""build-linux-kernel-1.17.1 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を起点に README_results.md の構造（抜粋）で
Build Process / build-linux-kernel-1.17.1 のみを抽出する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from make_one_big_json import get_machine_info  # type: ignore  # pylint: disable=import-error

BENCHMARK_NAME = "build-linux-kernel-1.17.1"
TESTCATEGORY_HINT = "Build_Process"
TEST_NAME = "Timed Linux Kernel Compilation"
DESCRIPTION = "Timed Linux Kernel Compilation 6.15"

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")
AVERAGE_RE = re.compile(r"Average[:\s]+([\d.]+)\s+Seconds", re.IGNORECASE)
BUILD_RE = re.compile(r"Build:\s*([^\[\]:]+)", re.IGNORECASE)


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _read_freq_file(freq_file: Path) -> Dict[str, int]:
    freq: Dict[str, int] = {}
    if not freq_file.exists():
        return freq

    idx = 0
    with freq_file.open(encoding="utf-8") as handle:
        for line in handle:
            value = line.strip()
            if not value:
                continue

            try:
                freq_hz = int(value)
            except ValueError:
                if ":" not in value:
                    continue
                try:
                    freq_mhz = float(value.split(":", 1)[1].strip())
                except ValueError:
                    continue
                freq_hz = int(freq_mhz * 1000)

            freq[f"freq_{idx}"] = freq_hz
            idx += 1

    return freq


def _extract_tests(thread_log: Path) -> List[Tuple[str, float]]:
    if not thread_log.exists():
        return []

    text = _strip_ansi(thread_log.read_text(encoding="utf-8", errors="replace"))
    entries: List[Tuple[str, float]] = []
    pending_config: Optional[str] = None
    unnamed_counter = 1

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        build_match = BUILD_RE.search(stripped)
        if build_match:
            pending_config = build_match.group(1).strip()
            continue

        avg_match = AVERAGE_RE.search(stripped)
        if avg_match:
            try:
                value = float(avg_match.group(1))
            except ValueError:
                continue
            config = pending_config or f"Run {unnamed_counter}"
            entries.append((config, value))
            pending_config = None
            unnamed_counter += 1

    return entries


def _build_test_map(tests: List[Tuple[str, float]], cost_hour: float) -> Dict[str, Any]:
    test_map: Dict[str, Any] = {}
    for config, value in tests:
        if not config.startswith("Run "):
            suffix = f"Build: {config}"
        else:
            suffix = config
        key = f"{TEST_NAME} - {suffix}" if suffix else TEST_NAME
        cost = round(cost_hour * value / 3600, 6) if value else 0.0
        test_map[key] = {
            "description": DESCRIPTION,
            "values": value,
            "raw_values": [value],
            "unit": "Seconds",
            "time": value,
            "test_run_times": [value],
            "cost": cost,
        }
    return test_map


def _build_thread_node(benchmark_dir: Path, thread_num: str, cost_hour: float) -> Optional[Dict[str, Any]]:
    thread_log = benchmark_dir / f"{thread_num}-thread.log"
    tests = _extract_tests(thread_log)
    if not tests:
        return None

    perf_stat: Dict[str, Any] = {}
    freq_start = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    if freq_start:
        perf_stat["start_freq"] = freq_start
    freq_end = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")
    if freq_end:
        perf_stat["end_freq"] = freq_end

    return {
        "perf_stat": perf_stat,
        "test_name": _build_test_map(tests, cost_hour),
    }


def _parse_benchmark(benchmark_dir: Path, cost_hour: float) -> Dict[str, Any]:
    thread_nodes: Dict[str, Any] = {}
    for log_file in sorted(benchmark_dir.glob("*-thread.log")):
        thread_prefix = log_file.stem.split("-thread", 1)[0]
        if not thread_prefix:
            continue
        thread_node = _build_thread_node(benchmark_dir, thread_prefix, cost_hour)
        if thread_node:
            thread_nodes[thread_prefix] = thread_node

    return thread_nodes


def _build_full_payload(search_root: Path) -> Dict[str, Any]:
    """Search for benchmarks recursively and build the full JSON payload.

    想定構造: <search_root>/**/<machinename>/<os>/<testcategory>/<BENCHMARK_NAME>
    """
    if not search_root.exists():
        raise FileNotFoundError(f"Directory not found: {search_root}")

    all_payload: Dict[str, Any] = {}

    for benchmark_dir in sorted(search_root.glob(f"**/{BENCHMARK_NAME}")):
        if not benchmark_dir.is_dir():
            continue

        category_dir = benchmark_dir.parent
        os_dir = category_dir.parent
        machine_dir = os_dir.parent

        machinename = machine_dir.name
        os_name = os_dir.name
        category_name = category_dir.name

        machine_info = get_machine_info(machinename)
        cost_hour = machine_info.get("cost_hour[730h-mo]", 0.0)

        thread_nodes: Dict[str, Any] = _parse_benchmark(benchmark_dir, cost_hour)

        if not thread_nodes:
            continue

        if machinename not in all_payload:
            all_payload[machinename] = {
                "CSP": machine_info.get("CSP", "N/A"),
                "total_vcpu": machine_info.get("total_vcpu", 0),
                "cpu_name": machine_info.get("cpu_name", "N/A"),
                "cpu_isa": machine_info.get("cpu_isa", "N/A"),
                "cost_hour[730h-mo]": cost_hour,
                "os": {},
            }

        machine_node = all_payload[machinename]
        if os_name not in machine_node["os"]:
            machine_node["os"][os_name] = {"testcategory": {}}

        os_node = machine_node["os"][os_name]
        if category_name not in os_node["testcategory"]:
            os_node["testcategory"][category_name] = {"benchmark": {}}

        benchmark_group = os_node["testcategory"][category_name]["benchmark"]
        benchmark_group[BENCHMARK_NAME] = {"thread": thread_nodes}

    return all_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="cloud_onehour/results/<machinename> から build-linux-kernel-1.17.1 を抽出する"
    )
    parser.add_argument(
        "--dir",
        "-d",
        type=Path,
        required=True,
        dest="search_root",
        help="探索を開始するルートディレクトリを指定（例: results フォルダや特定のマシンフォルダ）",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        help="出力先 JSON ファイル（省略時は stdout）",
    )

    args = parser.parse_args()
    payload = _build_full_payload(args.search_root)

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
