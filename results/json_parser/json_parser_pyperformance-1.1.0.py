#!/usr/bin/env python3
"""pyperformance-1.1.0 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で Processor/pyperformance-1.1.0 のみを抽出する。
"""

from __future__ import annotations

import argparse
import json
import py_compile
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

try:
    from make_one_big_json import get_machine_info  # type: ignore  # pylint: disable=import-error
except ImportError:
    def get_machine_info(machinename: str) -> Dict[str, Any]:
        return {}


BENCHMARK_NAME = "pyperformance-1.1.0"
TESTCATEGORY_HINT = "Processor"

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")
MEAN_RE = re.compile(r"Mean \+- std dev:\s+([\d.]+)\s+(ms|sec|us)")
START_RE = re.compile(r"Start date:\s+([0-9:\-.\s]+)")
END_RE = re.compile(r"End date:\s+([0-9:\-.\s]+)")
THREAD_FREQ_RE = re.compile(r"-(\d+)threads-freq_start\.txt$")

UNIT_TO_MS = {
    "ms": 1.0,
    "sec": 1000.0,
    "us": 0.001,
}


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from log text."""
    return ANSI_ESCAPE_RE.sub("", text)


def _read_freq_file(freq_file: Path) -> Dict[str, int]:
    """Load freq file into {freq_N: Hz} dict."""
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
    """Return iterable of thread identifiers."""
    discovered: set[str] = set()

    for file_path in sorted(benchmark_dir.glob("*-thread.log")):
        thread_prefix = file_path.stem.split("-", 1)[0]
        if thread_prefix:
            discovered.add(thread_prefix)

    for file_path in sorted(benchmark_dir.glob("*threads-freq_start.txt")):
        match = THREAD_FREQ_RE.search(file_path.name)
        if match:
            discovered.add(match.group(1))

    return sorted(discovered, key=lambda item: int(item))


def _find_machine_info_in_hierarchy(
    benchmark_dir: Path, search_root: Path
) -> tuple[str, str, str, Dict[str, Any]]:
    """Find valid machinename by traversing up from benchmark_dir."""
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
                if len(parts) >= 1:
                    os_name = parts[-1]
                else:
                    os_name = category_dir.parent.name
            except (ValueError, IndexError):
                os_name = category_dir.parent.name
            return machinename, os_name, category_name, machine_info
        current = current.parent

    fallback_os_dir = category_dir.parent
    fallback_machine_dir = fallback_os_dir.parent
    return fallback_machine_dir.name, fallback_os_dir.name, category_name, {}


def _load_thread_json(benchmark_dir: Path, thread_num: str) -> list[tuple[str, dict]]:
    """Load optional <N>-thread.json and return list of test entries."""
    json_file = benchmark_dir / f"{thread_num}-thread.json"
    if not json_file.exists():
        return []

    try:
        data = json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    entries: list[tuple[str, dict]] = []
    for bench_name, bench_info in data.get("results", {}).items():
        if not isinstance(bench_info, dict):
            continue
        unit = str(bench_info.get("unit", "ms"))
        test_key = f"{bench_name} [{unit}]"
        entries.append(
            (
                test_key,
                {
                    "bench_name": bench_name,
                    "description": bench_name,
                    "value": bench_info.get("value"),
                    "unit": unit,
                    "raw_values": bench_info.get("raw_values", []),
                    "test_run_times": bench_info.get("test_run_times", []),
                },
            )
        )
    return entries


def _load_summary_entries(benchmark_dir: Path) -> list[tuple[str, dict]]:
    """Load summary.json and return normalized entries."""
    summary_file = benchmark_dir / "summary.json"
    if not summary_file.exists():
        return []

    try:
        data = json.loads(summary_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []

    entries: list[tuple[str, dict]] = []
    for bench_name, bench_info in data.get("results", {}).items():
        if not isinstance(bench_info, dict):
            continue
        unit = str(bench_info.get("unit", "ms"))
        test_key = f"{bench_name} [{unit}]"
        value = bench_info.get("value")
        entries.append(
            (
                test_key,
                {
                    "bench_name": bench_name,
                    "description": bench_name,
                    "value": value,
                    "unit": unit,
                    "raw_values": [value] if value is not None else [],
                    "test_run_times": [],
                },
            )
        )
    return entries


def _parse_bench_log(bench_log: Path) -> Dict[str, Any]:
    """Parse bench_<name>.log for measured value, unit, and wall-clock duration."""
    if not bench_log.exists():
        return {}

    text = _strip_ansi(bench_log.read_text(encoding="utf-8", errors="replace"))
    parsed: Dict[str, Any] = {}

    mean_match = None
    for match in MEAN_RE.finditer(text):
        mean_match = match
    if mean_match:
        raw_value = float(mean_match.group(1))
        unit = mean_match.group(2)
        parsed["value_ms"] = raw_value * UNIT_TO_MS[unit]
        parsed["unit"] = "ms"
        parsed["raw_log_value"] = raw_value
        parsed["raw_log_unit"] = unit

    start_match = START_RE.search(text)
    end_match = END_RE.search(text)
    if start_match and end_match:
        try:
            start_dt = datetime.strptime(start_match.group(1).strip(), "%Y-%m-%d %H:%M:%S.%f")
            end_dt = datetime.strptime(end_match.group(1).strip(), "%Y-%m-%d %H:%M:%S.%f")
            parsed["time"] = max(0.0, (end_dt - start_dt).total_seconds())
        except ValueError:
            pass

    return parsed


def _collect_thread_payload(
    benchmark_dir: Path,
    thread_num: str,
    cost_hour: float,
) -> Optional[Dict[str, Any]]:
    """Extract benchmark results for a specific thread count."""
    entries = _load_thread_json(benchmark_dir, thread_num)
    if not entries:
        entries = _load_summary_entries(benchmark_dir)
    if not entries:
        return None

    test_payload: Dict[str, Any] = {}
    perf_stat: Dict[str, Any] = {}

    for test_key, test_info in entries:
        bench_name = str(test_info.get("bench_name", "unknown"))
        bench_log = benchmark_dir / f"bench_{bench_name}.log"
        log_info = _parse_bench_log(bench_log)

        value = test_info.get("value")
        if value is None and "value_ms" in log_info:
            value = log_info["value_ms"]

        raw_values = test_info.get("raw_values", [])
        if not raw_values and value is not None:
            raw_values = [value]

        test_run_times = test_info.get("test_run_times", [])
        if not test_run_times and "time" in log_info:
            test_run_times = [log_info["time"]]

        if test_run_times:
            time_val = statistics.median(test_run_times)
        else:
            time_val = 0.0

        cost = round(cost_hour * time_val / 3600, 6) if time_val > 0 else 0.0
        unit = str(test_info.get("unit") or log_info.get("unit") or "ms")

        test_payload[test_key] = {
            "description": test_info.get("description", bench_name),
            "values": value if value is not None else 0.0,
            "raw_values": raw_values,
            "unit": unit,
            "time": time_val,
            "test_run_times": test_run_times if test_run_times else "N/A",
            "cost": cost,
        }

        start_freq = _read_freq_file(benchmark_dir / f"{bench_name}-{thread_num}threads-freq_start.txt")
        end_freq = _read_freq_file(benchmark_dir / f"{bench_name}-{thread_num}threads-freq_end.txt")
        perf_stat[bench_name] = {}
        if start_freq:
            perf_stat[bench_name]["start_freq"] = start_freq
        if end_freq:
            perf_stat[bench_name]["end_freq"] = end_freq
        if not perf_stat[bench_name]:
            perf_stat.pop(bench_name)

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
