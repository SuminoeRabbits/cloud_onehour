#!/usr/bin/env python3
"""pgbench-1.11.1 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で Database/pgbench-1.11.1 のみを抽出する。
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

BENCHMARK_NAME = "pgbench-1.11.1"
TESTCATEGORY_HINT = "Database"

# Regex for pgbench PTS output
# Example patterns:
# pts/pgbench-1.11.1 [Scaling Factor: 1 - Clients: 1 - Mode: Read Only]
# tps = 1234.567890 (without initial connection time)
# tps = 1234.567890 (excluding connections establishing)
TEST_VARIANT_RE = re.compile(
    r"pts/pgbench-1.11.1\s+\[Scaling Factor:\s*(?P<sf>\d+)\s+-\s+Clients:\s*(?P<clients>\d+)\s+-\s+Mode:\s*(?P<mode>[^\]]+)\]",
    re.IGNORECASE
)
TPS_RE = re.compile(
    r"tps\s*=\s*(?P<tps>[\d.]+)\s+\((?:without initial connection time|excluding connections establishing)\)",
    re.IGNORECASE
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
    
    # Split content by test variant headers to match pairs of Header + TPS
    # We use finditer to iterate through the log line by line or section by section
    lines = content.splitlines()
    current_variant = None
    
    for line in lines:
        var_match = TEST_VARIANT_RE.search(line)
        if var_match:
            sf = var_match.group("sf")
            clients = var_match.group("clients")
            mode = var_match.group("mode").strip().lower().replace(" ", "")
            current_variant = f"sf{sf}_c{clients}_{mode}"
            continue
        
        if current_variant:
            tps_match = TPS_RE.search(line)
            if tps_match:
                tps_value = float(tps_match.group("tps"))
                
                test_payload[current_variant] = {
                    "description": f"pgbench {current_variant.replace('_', ' ')}",
                    "values": tps_value,
                    "raw_values": [tps_value],
                    "unit": "TPS",
                    "time": 0.0,
                    "test_run_times": [],
                    "cost": 0.0,
                }
                current_variant = None # Reset for next variant

    if not test_payload:
        # Check if there's any indication of failure
        if "The pts/pgbench-1.11.1 test is already running" in content:
            # We could return a node with error info, but usually we just return None if no data
            return None
        return None

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
            "pgbench parser: cloud_onehour/results/<machinename> を入力に "
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
