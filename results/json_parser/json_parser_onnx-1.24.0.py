#!/usr/bin/env python3
"""onnx-1.24.0 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で AI/onnx-1.24.0 のみを抽出する。
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
    from make_one_big_json import get_machine_info  # type: ignore  # pylint: disable=import-error
except ImportError:
    def get_machine_info(machinename: str) -> Dict[str, Any]:
        return {}


BENCHMARK_NAME = "onnx-1.24.0"
TESTCATEGORY_HINT = "AI"

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")
RUN_LOG_RE = re.compile(r"^(?P<thread>\d+)-thread_(?P<model>.+)_run(?P<run>\d+)\.log$")
RUN_TIME_RE = re.compile(r"Total inference run time:\s*([\d.]+)\s*s", re.IGNORECASE)


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
    """Return iterable of thread identifiers from <N>-thread.json files."""
    discovered: set[str] = set()

    for file_path in sorted(benchmark_dir.glob("*-thread.json")):
        thread_prefix = file_path.stem.split("-", 1)[0]
        if thread_prefix:
            discovered.add(thread_prefix)

    for file_path in sorted(benchmark_dir.glob("*-thread_*.log")):
        match = RUN_LOG_RE.match(file_path.name)
        if match:
            discovered.add(match.group("thread"))

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


def _load_thread_json(benchmark_dir: Path, thread_num: str) -> Optional[Dict[str, Any]]:
    """Load <N>-thread.json."""
    json_file = benchmark_dir / f"{thread_num}-thread.json"
    if not json_file.exists():
        return None

    try:
        return json.loads(json_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _collect_model_run_times(benchmark_dir: Path, thread_num: str) -> Dict[str, list[float]]:
    """Parse model-specific run logs and return model -> [run_time_sec, ...]."""
    run_times: Dict[str, list[tuple[int, float]]] = {}

    for log_file in sorted(benchmark_dir.glob(f"{thread_num}-thread_*_run*.log")):
        match = RUN_LOG_RE.match(log_file.name)
        if not match:
            continue

        model_name = match.group("model")
        run_index = int(match.group("run"))
        try:
            text = _strip_ansi(log_file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue

        time_match = RUN_TIME_RE.search(text)
        if not time_match:
            continue

        run_times.setdefault(model_name, []).append((run_index, float(time_match.group(1))))

    ordered: Dict[str, list[float]] = {}
    for model_name, values in run_times.items():
        ordered[model_name] = [val for _, val in sorted(values, key=lambda item: item[0])]
    return ordered


def _collect_thread_payload(
    benchmark_dir: Path,
    thread_num: str,
    cost_hour: float,
) -> Optional[Dict[str, Any]]:
    """Build the `<thread>` node from <N>-thread.json and model run logs."""
    data = _load_thread_json(benchmark_dir, thread_num)
    if not data:
        return None

    results = data.get("results", {})
    if not isinstance(results, dict) or not results:
        return None

    unit = str(data.get("unit", "Inferences Per Second"))
    run_times_by_model = _collect_model_run_times(benchmark_dir, thread_num)
    test_payload: Dict[str, Any] = {}

    for model_name, model_info in results.items():
        if not isinstance(model_info, dict):
            continue

        raw_values = model_info.get("values", [])
        if not isinstance(raw_values, list):
            raw_values = []

        value = model_info.get("mean")
        if value is None and raw_values:
            value = raw_values[0]
        if value is None:
            continue

        test_run_times = run_times_by_model.get(model_name, [])
        if test_run_times:
            time_val = statistics.median(test_run_times)
            cost = round(cost_hour * time_val / 3600, 6) if time_val > 0 else 0.0
            test_run_times_payload: list[float] | str = test_run_times
        else:
            time_val = 0.0
            cost = 0.0
            test_run_times_payload = []

        key = f"ONNX Runtime - Model: {model_name} [{unit}]"
        test_payload[key] = {
            "description": f"Model: {model_name}",
            "values": value,
            "raw_values": raw_values if raw_values else [value],
            "unit": unit,
            "time": time_val,
            "test_run_times": test_run_times_payload,
            "cost": cost,
        }

    if not test_payload:
        return None

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
    """Search for benchmarks recursively and build the full JSON payload."""
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
            "onnx-1.24.0 parser: cloud_onehour/results/<machinename> を入力に "
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
