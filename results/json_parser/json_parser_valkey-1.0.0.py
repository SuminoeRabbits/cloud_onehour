#!/usr/bin/env python3
"""valkey-1.0.0 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で Database/valkey-1.0.0 のみを抽出する。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
import py_compile

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

try:
    from make_one_big_json import get_machine_info  # type: ignore  # pylint: disable=import-error
except ImportError:
    def get_machine_info(machinename: str) -> Dict[str, Any]:
        return {}


BENCHMARK_NAME = "valkey-1.0.0"
TESTCATEGORY_HINT = "Database"

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
    current = category_dir.parent

    while current != search_root.parent and current != current.parent:
        machine_info = get_machine_info(current.name)

        if machine_info and machine_info.get("CSP") and machine_info.get("CSP") != "unknown":
            machinename = current.name
            try:
                rel_path = category_dir.parent.relative_to(current)
                parts = rel_path.parts
                os_name = parts[-1] if len(parts) >= 1 else category_dir.parent.name
            except (ValueError, IndexError):
                os_name = category_dir.parent.name
            return machinename, os_name, category_name, machine_info

        current = current.parent

    os_dir = category_dir.parent
    machine_dir = os_dir.parent
    return machine_dir.name, os_dir.name, category_name, {}


def _load_thread_json(benchmark_dir: Path, thread_num: str) -> list[tuple[str, dict]]:
    """Load <N>-thread.json and return list of (test_key, test_data) tuples."""
    json_file = benchmark_dir / f"{thread_num}-thread.json"
    if not json_file.exists():
        return []

    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    entries: list[tuple[str, dict]] = []
    for _hash, entry in data.get("results", {}).items():
        title = entry.get("title", "")
        description = entry.get("description", "")
        scale = entry.get("scale", "")

        for _sys_id, sys_data in entry.get("results", {}).items():
            value = sys_data.get("value")
            if value is None:
                continue
            raw_values = sys_data.get("raw_values")
            if not isinstance(raw_values, list) or len(raw_values) == 0:
                raw_values = [value]

            test_key = title or description or "Unknown"
            test_info = {
                "description": description or title,
                "value": value,
                "raw_values": raw_values,
                "test_run_times": sys_data.get("test_run_times", "N/A"),
                "scale": scale,
            }
            entries.append((test_key, test_info))

    return entries


def _collect_thread_payload(
    benchmark_dir: Path,
    thread_num: str,
    cost_hour: float,
) -> Optional[Dict[str, Any]]:
    entries = _load_thread_json(benchmark_dir, thread_num)
    if not entries:
        return None

    test_payload: Dict[str, Any] = {}

    for test_key, test_info in entries:
        value = test_info.get("value")
        raw_values = test_info.get("raw_values", [])
        test_run_times = test_info.get("test_run_times", "N/A")
        scale = test_info.get("scale", "")
        description = test_info.get("description", "")

        if isinstance(test_run_times, list) and len(test_run_times) > 0:
            time_val = statistics.median(test_run_times)
        else:
            time_val = 0.0

        cost = round(cost_hour * time_val / 3600, 6) if time_val > 0 else 0.0

        test_payload[test_key] = {
            "description": description,
            "values": value if value is not None else 0.0,
            "raw_values": raw_values,
            "unit": scale,
            "time": time_val,
            "test_run_times": test_run_times,
            "cost": cost,
        }

    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")
    perf_stat: Dict[str, Any] = {}
    if start_freq:
        perf_stat["start_freq"] = start_freq
    if end_freq:
        perf_stat["end_freq"] = end_freq

    return {"perf_stat": perf_stat, "test_name": test_payload}


def _build_full_payload(search_root: Path) -> Dict[str, Any]:
    if not search_root.exists():
        raise FileNotFoundError(f"Directory not found: {search_root}")

    all_payload: Dict[str, Any] = {}

    for benchmark_dir in sorted(search_root.glob(f"**/{BENCHMARK_NAME}")):
        if not benchmark_dir.is_dir():
            continue

        machinename, os_name, category_name, machine_info = _find_machine_info_in_hierarchy(
            benchmark_dir, search_root
        )

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
