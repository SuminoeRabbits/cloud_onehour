#!/usr/bin/env python3
"""memcached-1.2.0 専用 JSON パーサー。

`cloud_onehour/results/<machinename>` を入力に README_results.md と同じ
データ構造（抜粋）で Memory_Access/memcached-1.2.0 のみを抽出する。

データソースパターン: B（ログのみ・複数テスト）
  - <N>-thread.json は存在しない
  - <N>-thread.log 内の各テストセクションから値を抽出
  - テストバリアント: Set To Get Ratio 1:1 / 1:5 / 5:1 / 1:10 / 1:100
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


# ---------------------------------------------------------------------------
# [2] ベンチマーク定数・正規表現
# ---------------------------------------------------------------------------

BENCHMARK_NAME = "memcached-1.2.0"
TESTCATEGORY_HINT = "Memory_Access"

ANSI_ESCAPE_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")

# ログ内の各テストセクションを抽出:
#   [Set To Get Ratio: 1:1] ... Average: 863162.76 Ops/sec
SECTION_RE = re.compile(
    r"\[Set To Get Ratio:\s*([\d:]+)\].*?Average:\s+([\d.]+)\s+Ops/sec",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# [3] 共通ヘルパー関数
# ---------------------------------------------------------------------------

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


def _find_machine_info_in_hierarchy(
    benchmark_dir: Path, search_root: Path
) -> "tuple[str, str, str, Dict[str, Any]]":
    """Find valid machinename by traversing up from benchmark_dir.

    Returns: (machinename, os_name, category_name, machine_info)
    """
    category_dir = benchmark_dir.parent
    category_name = category_dir.name

    current = category_dir.parent

    while current != search_root.parent and current != current.parent:
        machine_info = get_machine_info(current.name)

        # CRITICAL: Must check CSP != "unknown" to avoid false positives
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

    # No valid machinename found in hierarchy, use fallback
    os_dir = category_dir.parent
    machine_dir = os_dir.parent
    return machine_dir.name, os_dir.name, category_name, {}


# ---------------------------------------------------------------------------
# [4] ベンチマーク固有抽出（パターンB: ログのみ・複数テスト）
# ---------------------------------------------------------------------------

def _collect_thread_payload(
    benchmark_dir: Path,
    thread_num: str,
    cost_hour: float,
) -> Optional[Dict[str, Any]]:
    """Extract benchmark results for a specific thread count from log."""
    log_file = benchmark_dir / f"{thread_num}-thread.log"
    if not log_file.exists():
        return None

    content = _strip_ansi(log_file.read_text(encoding="utf-8"))
    test_payload: Dict[str, Any] = {}

    for match in SECTION_RE.finditer(content):
        ratio = match.group(1)        # "1:1", "1:5", "5:1", "1:10", "1:100"
        value = float(match.group(2))
        description = f"Set To Get Ratio: {ratio}"
        key = f"Memcached - {description} [Ops/sec]"

        test_payload[key] = {
            "description": description,
            "values": value,
            "raw_values": [value],
            "unit": "Ops/sec",
            "time": "N/A",
            "test_run_times": "N/A",
            "cost": 0.0,
        }

    if not test_payload:
        return None

    # perf_stat 構築
    start_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_start.txt")
    end_freq = _read_freq_file(benchmark_dir / f"{thread_num}-thread_freq_end.txt")
    perf_stat: Dict[str, Any] = {}
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
