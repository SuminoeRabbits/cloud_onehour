#!/usr/bin/env python3
"""sysbench-1.1.0 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で System/sysbench-1.1.0 のみを抽出する。
"""

from __future__ import annotations

import argparse
import json
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


# ---------------------------------------------------------------------------
# Benchmark-specific placeholders
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "sysbench-1.1.0"
TESTCATEGORY_HINT = "System"

# Regex for Sysbench PTS output
# Example:
#     Test: RAM / Memory:
#         23349.43
#     Average: 23349.43 MiB/sec
TEST_SECTION_RE = re.compile(
    r"Test:\s*([^:\n]+):\s*\n\s*[\d.]+\s*\n\s*Average:\s*([\d.]+)\s+([^\n]+)",
    re.MULTILINE | re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Optional helpers reused across many parsers
# ---------------------------------------------------------------------------

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from log text."""
    return ANSI_ESCAPE_RE.sub("", text)


def _read_freq_file(freq_file: Path) -> Dict[str, int]:
    """Load `<thread>-thread_freq_{start,end}.txt` into `{freq_N: Hz}` dict."""
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
    log_threads = sorted(benchmark_dir.glob("*-thread.log"))
    for file_path in log_threads:
        thread_prefix = file_path.stem.split("-", 1)[0]
        if thread_prefix:
            yield thread_prefix


# ---------------------------------------------------------------------------
# Benchmark-specific extraction hooks
# ---------------------------------------------------------------------------

def _collect_thread_payload(
    benchmark_dir: Path,
    thread_num: str,
    cost_hour: float,
) -> Optional[Dict[str, Any]]:
    """Build the `<thread>` node for README_results.md structure."""
    log_file = benchmark_dir / f"{thread_num}-thread.log"
    if not log_file.exists():
        return None

    content = _strip_ansi(log_file.read_text(encoding="utf-8"))
    
    test_payload: Dict[str, Any] = {}
    matches = TEST_SECTION_RE.findall(content)
    
    if not matches:
        return None

    for test_title, value_str, unit in matches:
        test_title = test_title.strip()
        value = float(value_str)
        unit = unit.strip()
        
        # Create a key based on test title
        key = test_title.lower().replace(" / ", "_").replace(" ", "_")
        
        test_payload[key] = {
            "description": f"Sysbench Test: {test_title}",
            "values": value,
            "raw_values": [value],
            "unit": unit,
            "time": 0.0,
            "test_run_times": [],
            "cost": 0.0,
        }

    # Frequency files
    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")

    perf_stat: Dict[str, Any] = {}
    if start_freq:
        perf_stat["start_freq"] = start_freq
    if end_freq:
        perf_stat["end_freq"] = end_freq

    return {
        "perf_stat": perf_stat,
        "test_name": test_payload
    }


# ---------------------------------------------------------------------------
# Machine-level aggregation (共通ロジック)
# ---------------------------------------------------------------------------

def _build_machine_payload(machine_dir: Path) -> Dict[str, Any]:
    if not machine_dir.is_dir():
        raise FileNotFoundError(f"Machine directory not found: {machine_dir}")

    machinename = machine_dir.name
    machine_info = get_machine_info(machinename)
    cost_hour = machine_info.get("cost_hour[730h-mo]", 0.0)

    machine_node: Dict[str, Any] = {
        "CSP": machine_info.get("CSP", "N/A"),
        "total_vcpu": machine_info.get("total_vcpu", 0),
        "cpu_name": machine_info.get("cpu_name", "N/A"),
        "cpu_isa": machine_info.get("cpu_isa", "N/A"),
        "cost_hour[730h-mo]": cost_hour,
        "os": {},
    }

    for os_dir in sorted([p for p in machine_dir.iterdir() if p.is_dir()]):
        os_node: Dict[str, Any] = {"testcategory": {}}
        for testcategory_dir in sorted([p for p in os_dir.iterdir() if p.is_dir()]):
            benchmark_dir = testcategory_dir / BENCHMARK_NAME
            if not benchmark_dir.is_dir():
                continue

            thread_nodes: Dict[str, Any] = {}
            for thread_num in _discover_threads(benchmark_dir):
                thread_payload = _collect_thread_payload(benchmark_dir, thread_num, cost_hour)
                if thread_payload:
                    thread_nodes[thread_num] = thread_payload

            if not thread_nodes:
                continue

            os_node["testcategory"].setdefault(testcategory_dir.name, {"benchmark": {}})
            os_node["testcategory"][testcategory_dir.name]["benchmark"][BENCHMARK_NAME] = {
                "thread": thread_nodes
            }

        if os_node["testcategory"]:
            machine_node["os"][os_dir.name] = os_node

    return {machinename: machine_node}


# ---------------------------------------------------------------------------
# CLI entry point (共通ロジック)
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Sysbench parser: cloud_onehour/results/<machinename> を入力に "
            f"{BENCHMARK_NAME} を README 構造で出力する"
        )
    )
    parser.add_argument(
        "--dir",
        "-d",
        type=Path,
        required=True,
        dest="machine_dir",
        help="cloud_onehour/results/<machinename> ディレクトリへのパスを指定 (必須)",
    )
    parser.add_argument(
        "--out",
        "-o",
        type=Path,
        help="出力先 JSON ファイルへのパス。省略時は stdout に出力",
    )

    args = parser.parse_args()
    payload = _build_machine_payload(args.machine_dir)

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    main()
