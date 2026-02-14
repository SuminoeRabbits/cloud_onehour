#!/usr/bin/env python3
"""nginx-3.0.1 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で System/nginx-3.0.1 のみを抽出する。
"""

from __future__ import annotations

import argparse
import json
import py_compile
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

try:
    from make_one_big_json import get_machine_info
except ImportError:
    def get_machine_info(machinename: str) -> Dict[str, Any]:
        return {}


# ---------------------------------------------------------------------------
# [2] ベンチマーク定数
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "nginx-3.0.1"
TESTCATEGORY_HINT = "System"


# ---------------------------------------------------------------------------
# [3] 共通ヘルパー関数
# ---------------------------------------------------------------------------

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")

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
    """Return iterable of thread identifiers from <N>-thread.log files."""
    log_threads = sorted(benchmark_dir.glob("*-thread.log"))
    for file_path in log_threads:
        thread_prefix = file_path.stem.split("-", 1)[0]
        if thread_prefix:
            yield thread_prefix

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


# ---------------------------------------------------------------------------
# [4] ベンチマーク固有抽出 - Pattern C (JSON-based)
# ---------------------------------------------------------------------------

def _load_thread_json(benchmark_dir: Path, thread_num: str) -> list:
    """Load <N>-thread.json and return list of test entries."""
    json_file = benchmark_dir / f"{thread_num}-thread.json"
    if not json_file.exists():
        return []
    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    entries = []
    for _hash, entry in data.get("results", {}).items():
        title = entry.get("title", "")
        description = entry.get("description", "")
        scale = entry.get("scale", "")
        for _sys_id, sys_data in entry.get("results", {}).items():
            value = sys_data.get("value")
            if value is None:
                continue
            raw_values = sys_data.get("raw_values", [value])
            test_run_times = sys_data.get("test_run_times", "N/A")
            entries.append({
                "title": title,
                "description": description,
                "scale": scale,
                "value": value,
                "raw_values": raw_values,
                "test_run_times": test_run_times,
            })
    return entries

def _collect_thread_payload(
    benchmark_dir: Path,
    thread_num: str,
    cost_hour: float,
) -> Optional[Dict[str, Any]]:
    """Build the <thread> node for README_results.md structure."""
    # Pattern C: すべてのデータを <N>-thread.json から取得
    entries = _load_thread_json(benchmark_dir, thread_num)
    if not entries:
        return None

    test_payload = {}

    for entry in entries:
        title = entry["title"]
        description = entry["description"]
        value = entry["value"]
        raw_values = entry["raw_values"]
        test_run_times = entry["test_run_times"]
        unit = entry["scale"]

        if isinstance(test_run_times, list) and len(test_run_times) > 0:
            time_val = statistics.median(test_run_times)
        else:
            time_val = 0.0

        cost = round(cost_hour * time_val / 3600, 6) if time_val > 0 else 0.0

        # キー: "<title> - <description>"
        key = f"{title} - {description}" if description else title

        test_payload[key] = {
            "description": description,
            "values": value,
            "raw_values": raw_values,
            "unit": unit,
            "time": time_val,
            "test_run_times": test_run_times,
            "cost": cost,
        }

    # perf_stat 構築
    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")
    perf_stat = {}
    if start_freq: 
        perf_stat["start_freq"] = start_freq
    if end_freq:   
        perf_stat["end_freq"] = end_freq

    return {"perf_stat": perf_stat, "test_name": test_payload}


# ---------------------------------------------------------------------------
# [5] 共通ディレクトリ走査
# ---------------------------------------------------------------------------

def _build_full_payload(search_root: Path) -> Dict[str, Any]:
    if not search_root.exists():
        raise FileNotFoundError(f"Directory not found: {search_root}")

    all_payload: Dict[str, Any] = {}

    for benchmark_dir in sorted(search_root.glob(f"**/{BENCHMARK_NAME}")):
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
            thread_payload = _collect_thread_payload(
                benchmark_dir, thread_num, cost_hour
            )
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


# ---------------------------------------------------------------------------
# [6] 共通CLIエントリポイント
# ---------------------------------------------------------------------------

def main() -> None:
    # Self syntax check
    try:
        py_compile.compile(str(Path(__file__).resolve()), doraise=True)
    except py_compile.PyCompileError as e:
        print(f"Syntax error in {Path(__file__).name}: {e}", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(
        description=(
            f"{BENCHMARK_NAME} parser: cloud_onehour/results/<machinename> を入力に "
            f"{BENCHMARK_NAME} を README 構造で出力する"
        )
    )
    parser.add_argument(
        "--dir", "-d", type=Path, required=True, dest="search_root",
        help="探索対象を含む親ディレクトリを指定",
    )
    parser.add_argument(
        "--out", "-o", type=Path,
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
