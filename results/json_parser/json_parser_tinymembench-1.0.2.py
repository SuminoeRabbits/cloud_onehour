#!/usr/bin/env python3
"""tinymembench-1.0.2 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で Memory_Access/tinymembench-1.0.2 のみを抽出する。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional
import py_compile

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from make_one_big_json import get_machine_info  # type: ignore  # pylint: disable=import-error

BENCHMARK_NAME = "tinymembench-1.0.2"
TESTCATEGORY_HINT = "Memory_Access"


def _extract_test_entries(thread_json: Path) -> List[Dict[str, Any]]:
    data = json.loads(thread_json.read_text(encoding="utf-8"))
    entries: List[Dict[str, Any]] = []

    for test_block in data.get("results", {}).values():
        title = test_block.get("title", "N/A")
        description = test_block.get("description", "")
        unit = test_block.get("scale", "")

        for system_data in test_block.get("results", {}).values():
            value = system_data.get("value")
            raw_values = system_data.get("raw_values")
            if not raw_values and value is not None:
                raw_values = [value]
            test_run_times = system_data.get("test_run_times", [])
            time_value = median(test_run_times) if test_run_times else None

            entries.append(
                {
                    "test_name": title,
                    "description": description,
                    "unit": unit,
                    "value": value,
                    "raw_values": raw_values,
                    "test_run_times": test_run_times,
                    "time": time_value,
                }
            )

    return entries


def _build_test_node(entry: Dict[str, Any], cost_hour: float) -> Dict[str, Any]:
    value = entry.get("value")
    raw_values = entry.get("raw_values")
    test_run_times = entry.get("test_run_times") or []
    time_value: Optional[float] = entry.get("time")
    time_seconds = float(time_value) if time_value is not None else 0.0
    cost = round(cost_hour * time_seconds / 3600, 6) if time_seconds else 0.0

    return {
        "description": entry.get("description", ""),
        "values": value if value is not None else "N/A",
        "raw_values": raw_values if raw_values else ("N/A" if value is None else [value]),
        "unit": entry.get("unit", ""),
        "time": time_seconds if time_seconds else 0.0,
        "test_run_times": test_run_times if test_run_times else [],
        "cost": cost,
    }


def _build_thread_node(entries: List[Dict[str, Any]], cost_hour: float) -> Dict[str, Any]:
    test_name_map: Dict[str, Dict[str, Any]] = {}
    for entry in entries:
        base_name = entry.get("test_name", "unknown")
        description = entry.get("description", "")
        key = f"{base_name} - {description}" if description else base_name
        test_name_map[key] = _build_test_node(entry, cost_hour)

    return {
        "perf_stat": {},
        "test_name": test_name_map,
    }


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


def _parse_tinymembench(benchmark_dir: Path, cost_hour: float) -> Dict[str, Any]:
    thread_nodes: Dict[str, Dict[str, Any]] = {}
    for thread_json in sorted(benchmark_dir.glob("*-thread.json")):
        thread_prefix = thread_json.stem.split("-", 1)[0]
        if not thread_prefix.isdigit():
            continue
        entries = _extract_test_entries(thread_json)
        if entries:
            thread_nodes[thread_prefix] = _build_thread_node(entries, cost_hour)

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

        # Robust machinename detection supporting nested and various structures
        machinename, os_name, category_name, machine_info = _find_machine_info_in_hierarchy(
            benchmark_dir, search_root
        )
        
        # Fallback if machine_info is empty
        if not machine_info:
            machine_info = get_machine_info(machinename)
        
        cost_hour = machine_info.get("cost_hour[730h-mo]", 0.0)

        thread_nodes: Dict[str, Any] = _parse_tinymembench(benchmark_dir, cost_hour)

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
        description="cloud_onehour/results/<machinename> を入力に tinymembench-1.0.2 構造を JSON で出力する"
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
