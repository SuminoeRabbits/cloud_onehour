#!/usr/bin/env python3
"""compress-xz-1.1.0 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で Compression/compress-xz-1.1.0 のみを抽出する。

特記: 本パーサーは `<N>-thread.log` を主データソースとして扱う。
"""

from __future__ import annotations

import argparse
import json
import py_compile
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

try:
    from make_one_big_json import get_machine_info  # type: ignore  # pylint: disable=import-error
except ImportError:
    def get_machine_info(machinename: str) -> Dict[str, Any]:
        return {}


BENCHMARK_NAME = "compress-xz-1.1.0"
TESTCATEGORY_HINT = "Compression"

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")
AVERAGE_RE = re.compile(r"Average:\s*([\d.]+)\s+Seconds", re.IGNORECASE)
DESCRIPTION_RE = re.compile(r"^\s*(Compressing .+?):\s*$", re.MULTILINE)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from log text."""
    return ANSI_ESCAPE_RE.sub("", text)


def _read_freq_file(freq_file: Path) -> Dict[str, int]:
    """Load <N>-thread_freq_{start,end}.txt into {freq_N: Hz} dict."""
    if not freq_file.exists():
        return {}

    freqs: Dict[str, int] = {}
    with freq_file.open(encoding="utf-8") as handle:
        for idx, line in enumerate(handle):
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
            freqs[f"freq_{idx}"] = freq_hz
    return freqs


def _discover_threads(benchmark_dir: Path) -> Iterable[str]:
    """Return iterable of thread identifiers from log/json files."""
    threads = set()

    for file_path in benchmark_dir.glob("*-thread.log"):
        thread_prefix = file_path.stem.split("-", 1)[0]
        if thread_prefix:
            threads.add(thread_prefix)

    for file_path in benchmark_dir.glob("*-thread.json"):
        thread_prefix = file_path.stem.split("-", 1)[0]
        if thread_prefix:
            threads.add(thread_prefix)

    return sorted(threads)


def _find_machine_info_in_hierarchy(benchmark_dir: Path, search_root: Path) -> tuple[str, str, str, Dict[str, Any]]:
    """Find valid machinename by traversing up from benchmark_dir.
    
    Returns: (machinename, os_name, category_name, machine_info)
    """
    category_dir = benchmark_dir.parent
    category_name = category_dir.name
    
    # Start from os_dir and traverse upward toward search_root
    current = category_dir.parent
    
    # Track hierarchy from benchmark up to find valid machine
    path_parts = []
    while current != search_root.parent and current != current.parent:
        path_parts.append((current.name, current))
        
        # Try this directory name as machinename via LUT lookup
        machine_info = get_machine_info(current.name)
        
        # Valid machine found if get_machine_info returns non-empty dict with CSP
        if machine_info and machine_info.get("CSP"):
            machinename = current.name
            
            # Determine os_name: directory immediately above testcategory
            try:
                rel_path = category_dir.parent.relative_to(current)
                parts = rel_path.parts
                
                # Extract the final component (os_name directory)
                if len(parts) >= 1:
                    os_name = parts[-1]
                else:
                    # Edge case: machinename/testcategory/benchmark (no os level)
                    os_name = category_dir.parent.name
            except (ValueError, IndexError):
                # Fallback: use parent of category_dir directly
                os_name = category_dir.parent.name
            
            return machinename, os_name, category_name, machine_info
        
        current = current.parent
    
    # No valid machinename found in hierarchy, use fallback
    os_dir = category_dir.parent
    machine_dir = os_dir.parent
    return machine_dir.name, os_dir.name, category_name, {}


def _collect_thread_payload(
    benchmark_dir: Path,
    thread_num: str,
    cost_hour: float,
) -> Optional[Dict[str, Any]]:
    """Build the `<thread>` node for README_results.md structure."""
    log_file = benchmark_dir / f"{thread_num}-thread.log"
    if not log_file.exists():
        return None

    content = _strip_ansi(log_file.read_text(encoding="utf-8", errors="ignore"))

    avg_match = AVERAGE_RE.search(content)
    if not avg_match:
        return None

    value = float(avg_match.group(1))

    desc_match = DESCRIPTION_RE.search(content)
    description = (
        desc_match.group(1).strip()
        if desc_match
        else "Compressing ubuntu-16.04.3-server-i386.img, Compression Level 9"
    )

    time_val = value
    cost = round(cost_hour * time_val / 3600, 6) if time_val > 0 else 0.0
    key = f"XZ Compression - {description}"

    test_payload: Dict[str, Any] = {
        key: {
            "description": description,
            "values": value,
            "raw_values": [value],
            "unit": "Seconds",
            "time": time_val,
            "test_run_times": [value],
            "cost": cost,
        }
    }

    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")

    perf_stat: Dict[str, Any] = {}
    if start_freq:
        perf_stat["start_freq"] = start_freq
    if end_freq:
        perf_stat["end_freq"] = end_freq

    return {
        "perf_stat": perf_stat,
        "test_name": test_payload,
    }


def _build_full_payload(search_root: Path) -> Dict[str, Any]:
    if not search_root.exists():
        raise FileNotFoundError(f"Directory not found: {search_root}")

    all_payload: Dict[str, Any] = {}

    benchmark_dirs = []
    if search_root.is_dir() and search_root.name == BENCHMARK_NAME:
        benchmark_dirs.append(search_root)
    benchmark_dirs.extend(sorted(search_root.glob(f"**/{BENCHMARK_NAME}")))

    for benchmark_dir in benchmark_dirs:
        if not benchmark_dir.is_dir():
            continue

        # Robust machinename detection supporting nested and various structures
        machinename, os_name, category_name, machine_info = _find_machine_info_in_hierarchy(
            benchmark_dir, search_root
        )
        
        # Fallback if machine_info is empty
        if not machine_info:
            machine_info = get_machine_info(machinename)
        
        cost_hour = machine_info.get("cost_hour[730h-mo]", 0.0)

        thread_nodes: Dict[str, Any] = {}
        for thread_num in _discover_threads(benchmark_dir):
            thread_payload = _collect_thread_payload(benchmark_dir, thread_num, cost_hour)
            if thread_payload:
                thread_nodes[thread_num] = thread_payload

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
    # Self syntax check
    try:
        py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    except py_compile.PyCompileError as e:
        print(f"Syntax error in {Path(__file__).name}: {e}", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description=(
            "compress-xz parser: cloud_onehour/results/<machinename> を入力に "
            f"{BENCHMARK_NAME} を README 構造で出力する"
        )
    )
    parser.add_argument(
        "--dir",
        "-d",
        type=Path,
        required=True,
        dest="search_root",
        help="探索対象を含む親ディレクトリを指定",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        help="出力先 JSON ファイルへのパス。省略時は stdout に出力",
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
