#!/usr/bin/env python3
"""
make_one_big_json.py

Generates one_big_json.json from results directory structure.
Based on README_results.md specification.

Version info: v1.0.0 (Updated: 2026-01-18)

Important: All log files are automatically processed to remove ANSI color codes
before parsing. This ensures consistent regex matching across different environments.

Usage:
    # Build from directories:
    python3 make_one_big_json.py [--dir PATH] [--output PATH] [--instance_source PATH]

    # Merge multiple JSON files:
    python3 make_one_big_json.py --merge FILE1.json FILE2.json ... --output OUTPUT.json
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
import argparse
import ast
import py_compile
import statistics
import subprocess
import re
from datetime import datetime


# Script version - Format: v<major>.<minor>.<patch>
SCRIPT_VERSION = "v1.0.0"


def get_version_info() -> str:
    """
    Get version info in format: v<major>.<minor>.<patch>-g<git-hash>

    Returns:
        Version string like "v1.0.0-g1277d46" if in git repo,
        or "v1.0.0-unknown" if not in git repo or git not available
    """
    try:
        # Try to get git hash (short form, 7 characters)
        git_hash = subprocess.check_output(
            ['git', 'rev-parse', '--short=7', 'HEAD'],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).parent
        ).decode().strip()

        return f"{SCRIPT_VERSION}-g{git_hash}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not in git repo or git not available
        return f"{SCRIPT_VERSION}-unknown"


def get_generation_timestamp() -> str:
    """
    Get current timestamp in yyyymmdd-hhmmss format.

    Returns:
        Timestamp string like "20260118-143025"
    """
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_generation_log() -> Dict[str, Any]:
    """
    Create generation log dict for output JSON.

    Returns:
        Dict with version_info and date
    """
    return {
        "generation_log": {
            "version_info": get_version_info(),
            "date": get_generation_timestamp()
        }
    }


def parse_version(version_str: str) -> Optional[tuple]:
    """
    Parse version string to extract major.minor.patch.

    Args:
        version_str: Version string like "v1.0.0-g1277d46"

    Returns:
        Tuple of (major, minor, patch) or None if parsing fails
    """
    match = re.match(r'v(\d+)\.(\d+)\.(\d+)', version_str)
    if match:
        return tuple(map(int, match.groups()))
    return None


def check_version_compatibility(version1: str, version2: str) -> bool:
    """
    Check if two versions are compatible for merging.

    Per README_results.md specification:
    - Versions must match exactly for merging

    Args:
        version1: First version string
        version2: Second version string

    Returns:
        True if versions are compatible, False otherwise
    """
    # Extract version part (without git hash) for comparison
    v1_match = re.match(r'(v\d+\.\d+\.\d+)', version1)
    v2_match = re.match(r'(v\d+\.\d+\.\d+)', version2)

    if not v1_match or not v2_match:
        return False

    # Per specification: versions must match exactly
    return v1_match.group(1) == v2_match.group(1)


# Look-Up-Table from README_results.md
# Note: cost_hour[730h-mo] will be calculated from cloud_instances.json
# as cpu_cost_hour[730h-mo] + extra_150g_storage_cost_hour
MACHINE_LOOKUP = {
    "rpi5": {
        "CSP": "local",
        "total_vcpu": 4,
        "cpu_name": "Cortex-A76",
        "cpu_isa": "Armv8.2-A",
        "cost_hour[730h-mo]": 0.0
    },
    "t3_medium": {
        "CSP": "AWS",
        "total_vcpu": 2,
        "cpu_name": "Intel Xeon Platinum (8000 series)",
        "cpu_isa": "x86-64 (AVX-512)",
        "cost_hour[730h-mo]": 0.0183
    },
    "m8a_2xlarge": {
        "CSP": "AWS",
        "total_vcpu": 8,
        "cpu_name": "AMD EPYC 9R45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "cost_hour[730h-mo]": 0.3164
    },
    "m8i_2xlarge": {
        "CSP": "AWS",
        "total_vcpu": 8,
        "cpu_name": "Intel Xeon 6 (6th Granite Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "cost_hour[730h-mo]": 0.2594
    },
    "i7ie_2xlarge": {
        "CSP": "AWS",
        "total_vcpu": 8,
        "cpu_name": "Intel Xeon 5 Metal(5th Emerald Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "cost_hour[730h-mo]": 1.2433
    },
    "m7i_2xlarge": {
        "CSP": "AWS",
        "total_vcpu": 8,
        "cpu_name": "Intel Xeon 4 (4th Sapphire Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "cost_hour[730h-mo]": 0.5405
    },
    "m8g_2xlarge": {
        "CSP": "AWS",
        "total_vcpu": 8,
        "cpu_name": "Neoverse-V2 (Graviton4)",
        "cpu_isa": "Armv9.0-A (SVE2-128)",
        "cost_hour[730h-mo]": 0.2274
    },
    "e2-standard-2": {
        "CSP": "GCP",
        "total_vcpu": 2,
        "cpu_name": "Intel Xeon / AMD EPYC(Variable)",
        "cpu_isa": "x86-64",
        "cost_hour[730h-mo]": 0.0683
    },
    "c4d-standard-8": {
        "CSP": "GCP",
        "total_vcpu": 8,
        "cpu_name": "AMD EPYC 9B45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "cost_hour[730h-mo]": 0.4057
    },
    "c4-standard-8": {
        "CSP": "GCP",
        "total_vcpu": 8,
        "cpu_name": "Intel Xeon Platinum 8581C (5th Emerald Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "cost_hour[730h-mo]": 0.4231
    },
    "c4a-standard-8": {
        "CSP": "GCP",
        "total_vcpu": 8,
        "cpu_name": "Neoverse-V2 (Google Axion)",
        "cpu_isa": "Armv9.0-A (SVE2-128)",
        "cost_hour[730h-mo]": 0.3869
    },
    "VM.Standard.E5.Flex": {
        "CSP": "OCI",
        "total_vcpu": 8,
        "cpu_name": "AMD EPYC 9J14 (Zen 4 \"Genoa\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "cost_hour[730h-mo]": 0.1727
    },
    "VM.Standard.E6.Flex": {
        "CSP": "OCI",
        "total_vcpu": 8,
        "cpu_name": "AMD EPYC 9J45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "cost_hour[730h-mo]": 0.1927
    },
    "VM.Standard.A1.Flex": {
        "CSP": "OCI",
        "total_vcpu": 8,
        "cpu_name": "Ampere one (v8.6A)",
        "cpu_isa": "Armv8.6 (NEON-128)",
        "cost_hour[730h-mo]": 0.1367
    }
}


def load_cloud_instances(instance_source: Path) -> Dict[str, Any]:
    """
    Load cloud_instances.json from the specified directory.
    Returns the parsed JSON data, or empty dict if file not found.
    """
    cloud_instances_file = instance_source / "cloud_instances.json"

    if not cloud_instances_file.exists():
        print(f"Error: cloud_instances.json not found in {instance_source}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(cloud_instances_file, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse cloud_instances.json: {e}", file=sys.stderr)
        sys.exit(1)


def get_cost_from_instances(machinename: str, cloud_instances: Dict[str, Any]) -> float:
    """
    Get cost_hour[730h-mo] from cloud_instances.json.
    Cost is calculated as: cpu_cost_hour[730h-mo] + extra_150g_storage_cost_hour.

    According to README_results.md specification:
    - Sum of cpu_cost_hour[730h-mo] and extra_150g_storage_cost_hour
    - If not found in cloud_instances.json, returns the default value from MACHINE_LOOKUP
    - If not in MACHINE_LOOKUP either, returns 0.0
    """
    # Search through all CSPs and instances
    for csp, csp_data in cloud_instances.items():
        if not isinstance(csp_data, dict) or "instances" not in csp_data:
            continue

        for instance in csp_data["instances"]:
            # Check if this instance matches the machinename
            # Try matching by hostname or name field
            if (instance.get("hostname") == machinename or
                instance.get("name") == machinename or
                instance.get("type", "").replace(".", "-") in machinename):

                cpu_cost = instance.get("cpu_cost_hour[730h-mo]", 0.0)
                storage_cost = instance.get("extra_150g_storage_cost_hour", 0.0)

                return cpu_cost + storage_cost

    # Not found in cloud_instances.json, use default from MACHINE_LOOKUP
    return None


def get_machine_info(machinename: str, cloud_instances: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get machine info from Look-Up-Table based on machinename.
    Searches for partial matches (e.g., "t3" and "medium" for "t3_medium").

    Cost calculation priority (per README_results.md):
    1. First try to get cost from cloud_instances.json
    2. If not found, use the default value from MACHINE_LOOKUP
    3. If not in MACHINE_LOOKUP either, use 0.0
    """
    # Direct match
    if machinename in MACHINE_LOOKUP:
        info = MACHINE_LOOKUP[machinename].copy()
        cost_from_json = get_cost_from_instances(machinename, cloud_instances)
        if cost_from_json is not None:
            info["cost_hour[730h-mo]"] = cost_from_json
        # else: keep the default value from MACHINE_LOOKUP
        return info

    # Partial match for compound names
    machinename_lower = machinename.lower()
    for key, value in MACHINE_LOOKUP.items():
        parts = key.replace("-", "_").split("_")
        if all(part in machinename_lower for part in parts):
            info = value.copy()
            cost_from_json = get_cost_from_instances(machinename, cloud_instances)
            if cost_from_json is not None:
                info["cost_hour[730h-mo]"] = cost_from_json
            # else: keep the default value from MACHINE_LOOKUP
            return info

    # Default fallback - not in lookup table at all
    print(f"Warning: Machine '{machinename}' not found in lookup table. Using defaults.", file=sys.stderr)
    cost_from_json = get_cost_from_instances(machinename, cloud_instances)
    return {
        "CSP": "unknown",
        "total_vcpu": 0,
        "cpu_name": "unknown",
        "cpu_isa": "unknown",
        "cost_hour[730h-mo]": cost_from_json if cost_from_json is not None else 0.0
    }


def strip_ansi_codes(text: str) -> str:
    """
    Remove ANSI color codes from text.

    ANSI codes like \x1b[1;34m (blue) and \x1b[0m (reset) can interfere
    with regex matching. This function removes all ANSI escape sequences.

    Args:
        text: Text that may contain ANSI codes

    Returns:
        Text with ANSI codes removed
    """
    # Pattern to match ANSI escape sequences
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)


def read_log_file_safe(file_path: Path) -> str:
    """
    Safely read a log file with automatic ANSI code removal.

    This function ensures that all log files are read consistently with
    ANSI color codes removed, preventing regex matching issues.

    Args:
        file_path: Path to the log file to read

    Returns:
        File content with ANSI codes removed

    Raises:
        IOError: If file cannot be read
    """
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    # Always strip ANSI codes from log files
    return strip_ansi_codes(content)


def read_freq_file(freq_file: Path) -> Dict[str, int]:
    """
    Read frequency file and return dict with freq_0, freq_1, etc.
    File format: one frequency per line in Hz.

    Supports two formats:
    1. Plain number (Hz): "3192614"
    2. cpufreq format: "cpu MHz\t\t: 3192.614"
    """
    if not freq_file.exists():
        return {}

    freq_dict = {}
    idx = 0
    with open(freq_file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            try:
                # Try plain number format (Hz)
                freq_hz = int(line)
            except ValueError:
                # Try cpufreq format "cpu MHz : 3192.614"
                if ':' in line:
                    try:
                        # Extract the frequency value after ':'
                        freq_mhz_str = line.split(':')[1].strip()
                        freq_mhz = float(freq_mhz_str)
                        # Convert MHz to Hz
                        freq_hz = int(freq_mhz * 1000)
                    except (ValueError, IndexError):
                        # Skip lines that can't be parsed
                        continue
                else:
                    # Skip lines that can't be parsed
                    continue

            freq_dict[f"freq_{idx}"] = freq_hz
            idx += 1

    return freq_dict


def read_perf_summary(perf_file: Path) -> Dict[str, Any]:
    """
    Read perf_summary.json and extract metrics.
    Returns dict with ipc, total_cycles, total_instructions per CPU.

    Note: IPC, total_cycles, and total_instructions calculation
    depends on actual perf_summary.json structure.
    Current implementation is based on observed file format.
    """
    if not perf_file.exists():
        return {}

    with open(perf_file, 'r') as f:
        perf_data = json.load(f)

    result = {
        "ipc": {},
        "total_cycles": {},
        "total_instructions": {},
        "cpu_utilization_percent": 0.0,
        "elapsed_time_sec": 0.0
    }

    # Extract per-CPU metrics
    per_cpu = perf_data.get("per_cpu_metrics", {})
    for cpu_id, metrics in per_cpu.items():
        # Note: IPC and cycles/instructions may not be in current format
        # These would need to be calculated from actual perf stat output
        # For now, we use available metrics as placeholders

        # cpu-clock is in milliseconds, convert to seconds for elapsed time
        cpu_clock = metrics.get("cpu-clock", 0.0)
        result["elapsed_time_sec"] = max(result["elapsed_time_sec"], cpu_clock / 1000.0)

        # Placeholder values - actual implementation would need perf stat raw data
        result["ipc"][f"ipc_{cpu_id}"] = 0.0
        result["total_cycles"][f"total_cycles_{cpu_id}"] = 0
        result["total_instructions"][f"total_instructions_{cpu_id}"] = 0

    # CPU utilization would need to be calculated from actual metrics
    # Placeholder for now
    result["cpu_utilization_percent"] = 0.0

    return result


def process_thread_data(benchmark_dir: Path, thread_num: str) -> Optional[Dict[str, Any]]:
    """
    Process data for a specific thread count.
    Returns dict with perf_stat and test results, or None if incomplete.

    Per README_results.md specification:
    Required files:
    - <N>-thread_freq_start.txt
    - <N>-thread_freq_end.txt
    - <N>-thread_perf_stats.txt
    - <N>-thread.csv
    - <N>-thread.json

    Optional files:
    - <N>-thread_perf_summary.json
    """
    # Check required files per README_results.md
    freq_start = benchmark_dir / f"{thread_num}-thread_freq_start.txt"
    freq_end = benchmark_dir / f"{thread_num}-thread_freq_end.txt"
    perf_stats = benchmark_dir / f"{thread_num}-thread_perf_stats.txt"
    thread_csv = benchmark_dir / f"{thread_num}-thread.csv"
    thread_json = benchmark_dir / f"{thread_num}-thread.json"

    # Optional file
    perf_summary = benchmark_dir / f"{thread_num}-thread_perf_summary.json"

    # Skip if any REQUIRED file is missing
    required_files = [freq_start, freq_end, perf_stats, thread_csv, thread_json]
    if not all(f.exists() for f in required_files):
        return None

    # Build thread data
    thread_data = {
        "perf_stat": {
            "start_freq": read_freq_file(freq_start),
            "end_freq": read_freq_file(freq_end),
        }
    }

    # Add perf summary data if available (optional file)
    if perf_summary.exists():
        perf_metrics = read_perf_summary(perf_summary)
        thread_data["perf_stat"].update(perf_metrics)
    else:
        # Add placeholder values if perf_summary.json is missing
        thread_data["perf_stat"].update({
            "ipc": {},
            "total_cycles": {},
            "total_instructions": {},
            "cpu_utilization_percent": 0.0,
            "elapsed_time_sec": 0.0
        })

    return thread_data


def get_test_raw_data(benchmark_dir: Path, thread_num: str, test_name: str, description: str) -> Dict[str, Any]:
    """
    Get raw test data from <N>-thread.json by matching description.

    Per README_results.md specification:
    1. Open <N>-thread.json
    2. Find entry matching both test_name and description
    3. Return raw_values, test_run_times, value, and unit from the raw data

    Args:
        benchmark_dir: Path to benchmark directory
        thread_num: Thread count as string (e.g., "1", "4")
        test_name: Test name to match
        description: Description to match

    Returns:
        Dict with raw_values, test_run_times, value, unit, or empty dict if not found
    """
    pts_json = benchmark_dir / f"{thread_num}-thread.json"
    if not pts_json.exists():
        return {}

    try:
        with open(pts_json, 'r') as f:
            pts_data = json.load(f)

        # Search through results for matching test_name and description
        for test_id, test_info in pts_data.get("results", {}).items():
            if test_info.get("title") == test_name and test_info.get("description") == description:
                # Get the results for this specific benchmark run
                for run_id, run_data in test_info.get("results", {}).items():
                    return {
                        "raw_values": run_data.get("raw_values", []),
                        "test_run_times": run_data.get("test_run_times", []),
                        "value": run_data.get("value", 0.0),
                        "unit": test_info.get("scale", "")
                    }

        return {}
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        print(f"Warning: Failed to read raw data from {pts_json}: {e}", file=sys.stderr)
        return {}


def process_benchmark(benchmark_dir: Path, cost_hour: float = 0.0) -> Optional[Dict[str, Any]]:
    """
    Process a benchmark directory.
    Returns dict with all thread data and test results.

    Args:
        benchmark_dir: Path to benchmark directory
        cost_hour: Cost per hour (cost_hour[730h-mo]) for this machine

    Per README_results.md specification:
        Benchmark completion conditions (4 cases):
        Case 1: Both summary.json and <N>-thread.json exist
        Case 2: <N>-thread.json exists (without summary.json)
        Case 3: <N>-thread_perf_summary.json exists
        Case 4: Special case for build-* benchmarks without perf_summary.json

        cost = cost_hour[730h-mo] * time / 3600
        where time is in seconds, so divide by 3600 to convert to hours
    """
    summary_json = benchmark_dir / "summary.json"
    benchmark_name = benchmark_dir.name

    # Case 4 special benchmarks (per README_results.md)
    case4_benchmarks = ["build-gcc-1.5.0", "build-linux-kernel-1.17.1", "build-llvm-1.6.0", "coremark-1.0.1"]
    is_case4 = benchmark_name in case4_benchmarks

    # Find all thread counts from <N>-thread.json files (Case 1 & 2)
    thread_json_files = list(benchmark_dir.glob("*-thread.json"))
    thread_nums = sorted(set(f.stem.split("-")[0] for f in thread_json_files if f.stem.split("-")[0].isdigit()))

    # Find all thread counts from <N>-thread_perf_summary.json files (Case 3)
    perf_summary_files = list(benchmark_dir.glob("*-thread_perf_summary.json"))
    perf_summary_thread_nums = sorted(set(f.stem.split("-")[0] for f in perf_summary_files if f.stem.split("-")[0].isdigit()))

    # Find all thread counts from <N>-thread.log files (Case 4)
    thread_log_files = list(benchmark_dir.glob("*-thread.log"))
    log_thread_nums = sorted(set(f.stem.split("-")[0] for f in thread_log_files if f.stem.split("-")[0].isdigit()))

    # Merge thread numbers from all sources
    all_thread_nums = sorted(set(thread_nums + perf_summary_thread_nums + (log_thread_nums if is_case4 else [])))

    # Check if benchmark is complete per README_results.md
    # At least one of: <N>-thread.json, <N>-thread_perf_summary.json, or (Case 4: <N>-thread.log) must exist
    if not all_thread_nums:
        print(f"Warning: Skipping incomplete benchmark at {benchmark_dir} (no <N>-thread.json or <N>-thread_perf_summary.json found)", file=sys.stderr)
        return None

    # Try to read summary.json if it exists (Case 1)
    summary_data = None
    if summary_json.exists():
        try:
            with open(summary_json, 'r') as f:
                summary_data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to read {summary_json}: {e}", file=sys.stderr)

    benchmark_result = {}

    for thread_num in all_thread_nums:
        # Determine which case applies for this thread_num
        has_thread_json = thread_num in thread_nums
        has_perf_summary = thread_num in perf_summary_thread_nums

        thread_data = process_thread_data(benchmark_dir, thread_num)
        if thread_data is None:
            # thread_data can be None if required files are missing
            # Try to process anyway with minimal perf_stat
            thread_data = {"perf_stat": {}}

        # Extract test results
        # Case 1: If summary.json exists with <N>-thread.json, use both
        # Case 2: If only <N>-thread.json exists (no summary.json), extract directly from it
        # Case 3: If <N>-thread_perf_summary.json exists, use it (with or without <N>-thread.json)
        # Case 4: Special case for build-* benchmarks (use <N>-thread.log)
        test_results = {}
        has_thread_log = thread_num in log_thread_nums

        # Determine which case to apply
        # Priority: Case 1 > Case 2 > Case 3 > Case 4
        if summary_data and "results" in summary_data and has_thread_json:
            # Case 1: Both summary.json and <N>-thread.json exist
            # Use summary.json for test metadata and <N>-thread.json for raw data
            for result in summary_data["results"]:
                if result.get("threads") == int(thread_num):
                    test_name = result.get("test_name", "unknown")
                    description = result.get("description", "")

                    # Get raw data from <N>-thread.json by matching description
                    # Per README_results.md: Use raw data from <N>-thread.json, not averaged summary.json
                    raw_data = get_test_raw_data(benchmark_dir, thread_num, test_name, description)

                    # Extract data from raw_data
                    raw_values = raw_data.get("raw_values", [])
                    test_run_times = raw_data.get("test_run_times", [])
                    value = raw_data.get("value", result.get("value", 0.0))
                    unit = raw_data.get("unit", result.get("unit", ""))

                    # Use median test_run_time for cost calculation (handles outliers)
                    time_sec = statistics.median(test_run_times) if test_run_times else 0.0

                    # Calculate cost: cost_hour * time_sec / 3600
                    cost = cost_hour * time_sec / 3600.0

                    # Per README_results.md: Key generation rule
                    # If same test_name but different description, use "test_name - description" format
                    key = f"{test_name} - {description}" if description else test_name

                    test_results[key] = {
                        "description": description,
                        "values": value,
                        "raw_values": raw_values,
                        "unit": unit,
                        "time": time_sec,
                        "test_run_times": test_run_times,
                        "cost": cost
                    }

        elif has_thread_json:
            # Case 2: No summary.json, extract directly from <N>-thread.json
            pts_json = benchmark_dir / f"{thread_num}-thread.json"
            if pts_json.exists():
                try:
                    with open(pts_json, 'r') as f:
                        pts_data = json.load(f)

                    # Extract all test results from <N>-thread.json
                    for test_id, test_info in pts_data.get("results", {}).items():
                        test_name = test_info.get("title", "unknown")
                        description = test_info.get("description", "")

                        # Get the results for this specific benchmark run
                        for run_id, run_data in test_info.get("results", {}).items():
                            raw_values = run_data.get("raw_values", [])
                            test_run_times = run_data.get("test_run_times", [])
                            value = run_data.get("value", 0.0)
                            unit = test_info.get("scale", "")

                            # Use median test_run_time for cost calculation (handles outliers)
                            time_sec = statistics.median(test_run_times) if test_run_times else 0.0

                            # Calculate cost: cost_hour * time_sec / 3600
                            cost = cost_hour * time_sec / 3600.0

                            # Per README_results.md: Key generation rule
                            key = f"{test_name} - {description}" if description else test_name

                            test_results[key] = {
                                "description": description,
                                "values": value,
                                "raw_values": raw_values,
                                "unit": unit,
                                "time": time_sec,
                                "test_run_times": test_run_times,
                                "cost": cost
                            }
                            # Only process first run_id
                            break

                except (json.JSONDecodeError, IOError) as e:
                    print(f"Warning: Failed to read {pts_json}: {e}", file=sys.stderr)

        elif has_perf_summary:
            # Case 3: <N>-thread_perf_summary.json exists (with or without <N>-thread.json)
            # Per README_results.md Case 3:
            # - values: N/A (default, overridden for specific benchmarks)
            # - raw_values: N/A (default, overridden for specific benchmarks)
            # - unit: N/A (default, overridden for specific benchmarks)
            # - test_run_times: [elapsed_time_sec] from <N>-thread_perf_summary.json
            # - description: from <N>-thread.json if available, else "perf stat only"
            #
            # Exception handling for specific benchmarks (per README_results.md):
            # - coremark-1.0.1: Extract from <N>-thread.log "Average: XXXX.XXXX Iterations/Sec"
            # - build-gcc-1.5.0: Extract from <N>-thread.log "Average: XXXX.XXXX Seconds"
            # - build-linux-kernel-1.17.1: Extract from <N>-thread.log "Average: XXXX.XXXX Seconds"
            # - build-llvm-1.6.0: Extract from <N>-thread.log "Average: XXXX.XXXX Seconds"
            perf_summary_file = benchmark_dir / f"{thread_num}-thread_perf_summary.json"
            pts_json = benchmark_dir / f"{thread_num}-thread.json"
            thread_log = benchmark_dir / f"{thread_num}-thread.log"

            if perf_summary_file.exists():
                try:
                    with open(perf_summary_file, 'r') as f:
                        perf_data = json.load(f)

                    # Extract elapsed_time_sec from perf_summary
                    elapsed_time_sec = perf_data.get("elapsed_time_sec", 0.0)

                    # Try to get test_name and description from <N>-thread.json if it exists
                    test_name = benchmark_dir.name  # default to benchmark name
                    description = "perf stat only"  # default description

                    if pts_json.exists():
                        try:
                            with open(pts_json, 'r') as f:
                                pts_data = json.load(f)

                            # Extract test_name and description from <N>-thread.json
                            for test_id, test_info in pts_data.get("results", {}).items():
                                test_name = test_info.get("title", test_name)
                                description = test_info.get("description", description)
                                break  # Use first test found
                        except (json.JSONDecodeError, IOError):
                            pass  # Use defaults

                    # Default values for Case 3
                    values = "N/A"
                    raw_values = "N/A"
                    unit = "N/A"
                    test_run_times = [elapsed_time_sec]

                    # Exception handling for specific benchmarks
                    # Note: coremark-1.0.1 has been moved to Case 4
                    benchmark_name = benchmark_dir.name
                    if thread_log.exists():
                        try:
                            # Read log file with automatic ANSI code removal
                            log_content = read_log_file_safe(thread_log)

                            # Verify file is not empty
                            if not log_content.strip():
                                print(f"Warning: Log file is empty: {thread_log}", file=sys.stderr)
                            elif benchmark_name in ["build-gcc-1.5.0", "build-linux-kernel-1.17.1", "build-llvm-1.6.0"]:
                                # Extract "Average: XXXX.XXXX Seconds" (very flexible regex)
                                # Try multiple patterns to handle various formats, including leading whitespace
                                patterns = [
                                    r'Average[:\s]+([\d.]+)\s+Seconds',  # Most flexible: any whitespace/colon combo
                                    r'Average:\s*([\d.]+)\s*Seconds',  # Standard format
                                    r'^\s*Average:\s*([\d.]+)\s*Seconds',  # With leading whitespace
                                ]

                                match = None
                                for pattern in patterns:
                                    match = re.search(pattern, log_content, re.IGNORECASE | re.MULTILINE)
                                    if match:
                                        break

                                if match:
                                    value = float(match.group(1))
                                    values = value
                                    raw_values = [value]
                                    unit = "Seconds"
                                    test_run_times = [value]

                                    # Set appropriate description
                                    if benchmark_name == "build-gcc-1.5.0":
                                        description = "Timed GCC Compilation 15.2"
                                    elif benchmark_name == "build-linux-kernel-1.17.1":
                                        description = "Timed Linux Kernel Compilation 6.15"
                                    elif benchmark_name == "build-llvm-1.6.0":
                                        description = "Timed LLVM Compilation 21.1"
                                else:
                                    # Enhanced debugging: show file location and excerpt with hex dump
                                    excerpt_lines = [line for line in log_content.split('\n') if 'average' in line.lower() or 'seconds' in line.lower()]
                                    excerpt = '\n    '.join(excerpt_lines[:5]) if excerpt_lines else "(no lines with 'average' or 'seconds' found)"
                                    print(f"Warning: Could not find 'Average: X Seconds' pattern in {thread_log}", file=sys.stderr)
                                    print(f"  File exists: {thread_log.exists()}, Size: {thread_log.stat().st_size if thread_log.exists() else 'N/A'} bytes", file=sys.stderr)
                                    print(f"  Relevant lines:\n    {excerpt}", file=sys.stderr)
                                    # Show hex dump of first relevant line for debugging
                                    if excerpt_lines:
                                        first_line = excerpt_lines[0]
                                        hex_dump = ' '.join(f'{ord(c):02x}' for c in first_line[:50])
                                        print(f"  First line hex (first 50 chars): {hex_dump}", file=sys.stderr)

                        except (IOError, ValueError) as e:
                            print(f"Warning: Failed to parse {thread_log}: {e}", file=sys.stderr)

                    # Calculate cost using elapsed_time_sec (or extracted value for build-* benchmarks)
                    cost_time = test_run_times[0] if test_run_times else elapsed_time_sec
                    cost = cost_hour * cost_time / 3600.0

                    test_results[test_name] = {
                        "description": description,
                        "values": values,
                        "raw_values": raw_values,
                        "unit": unit,
                        "time": elapsed_time_sec,
                        "test_run_times": test_run_times,
                        "cost": cost
                    }
                except (json.JSONDecodeError, IOError) as e:
                    print(f"Warning: Failed to read {perf_summary_file}: {e}", file=sys.stderr)

        elif is_case4 and has_thread_log:
            # Case 4: Special case for benchmarks without perf_summary.json (use <N>-thread.log)
            # Per README_results.md Case 4:
            # - coremark-1.0.1: Extract "Average: XXXX.XXXX Iterations/Sec" from <N>-thread.log
            # - build-*: Extract "Average: XXXX.XXXX Seconds" from <N>-thread.log
            thread_log = benchmark_dir / f"{thread_num}-thread.log"

            if thread_log.exists():
                try:
                    # Read log file with automatic ANSI code removal
                    log_content = read_log_file_safe(thread_log)

                    # Verify file is not empty
                    if not log_content.strip():
                        print(f"Warning: Log file is empty: {thread_log}", file=sys.stderr)
                    elif benchmark_name == "coremark-1.0.1":
                        # Extract "Average: XXXX.XXXX Iterations/Sec" (very flexible regex)
                        patterns = [
                            r'Average[:\s]+([\d.]+)\s+Iterations?/Sec',  # Most flexible
                            r'Average:\s*([\d.]+)\s*Iterations?/Sec',
                            r'^\s*Average:\s*([\d.]+)\s*Iterations?/Sec',  # With leading whitespace
                        ]

                        match = None
                        for pattern in patterns:
                            match = re.search(pattern, log_content, re.IGNORECASE | re.MULTILINE)
                            if match:
                                break

                        if match:
                            value = float(match.group(1))
                            values = value
                            raw_values = [value]
                            unit = "Iterations/Sec"
                            test_run_times = ["N/A"]
                            test_name = "Coremark"
                            description = "Coremark 1.0"

                            # Calculate cost using N/A for test_run_times
                            cost = "N/A"

                            test_results[test_name] = {
                                "description": description,
                                "values": values,
                                "raw_values": raw_values,
                                "unit": unit,
                                "time": "N/A",
                                "test_run_times": test_run_times,
                                "cost": cost
                            }
                        else:
                            # Enhanced debugging
                            excerpt_lines = [line for line in log_content.split('\n') if 'average' in line.lower() or 'iteration' in line.lower()]
                            excerpt = '\n    '.join(excerpt_lines[:5]) if excerpt_lines else "(no relevant lines found)"
                            print(f"Warning: Could not find 'Average: X Iterations/Sec' pattern in {thread_log}", file=sys.stderr)
                            print(f"  File exists: {thread_log.exists()}, Size: {thread_log.stat().st_size if thread_log.exists() else 'N/A'} bytes", file=sys.stderr)
                            print(f"  Relevant lines:\n    {excerpt}", file=sys.stderr)
                            if excerpt_lines:
                                first_line = excerpt_lines[0]
                                hex_dump = ' '.join(f'{ord(c):02x}' for c in first_line[:50])
                                print(f"  First line hex (first 50 chars): {hex_dump}", file=sys.stderr)
                    else:
                        # Extract "Average: XXXX.XXXX Seconds" from log (very flexible regex)
                        # Try multiple patterns to handle various formats, including leading whitespace
                        patterns = [
                            r'Average[:\s]+([\d.]+)\s+Seconds',  # Most flexible: any whitespace/colon combo
                            r'Average:\s*([\d.]+)\s*Seconds',  # Standard format
                            r'^\s*Average:\s*([\d.]+)\s*Seconds',  # With leading whitespace
                        ]

                        match = None
                        matched_pattern = None
                        for pattern in patterns:
                            match = re.search(pattern, log_content, re.IGNORECASE | re.MULTILINE)
                            if match:
                                matched_pattern = pattern
                                break

                        if match:
                            value = float(match.group(1))
                            values = value
                            raw_values = [value]
                            unit = "Seconds"
                            test_run_times = [value]

                            # Set appropriate description and test_name based on benchmark
                            if benchmark_name == "build-gcc-1.5.0":
                                test_name = "Timed GCC Compilation"
                                description = "Timed GCC Compilation 15.2"
                            elif benchmark_name == "build-linux-kernel-1.17.1":
                                test_name = "Timed Linux Kernel Compilation"
                                description = "Timed Linux Kernel Compilation 6.15"
                            elif benchmark_name == "build-llvm-1.6.0":
                                test_name = "Timed LLVM Compilation"
                                description = "Timed LLVM Compilation 21.1"
                            else:
                                test_name = benchmark_name
                                description = "Build benchmark"

                            # Calculate cost using extracted time value
                            cost = cost_hour * value / 3600.0

                            test_results[test_name] = {
                                "description": description,
                                "values": values,
                                "raw_values": raw_values,
                                "unit": unit,
                                "time": value,
                                "test_run_times": test_run_times,
                                "cost": cost
                            }
                        else:
                            # Enhanced debugging: show file location and excerpt with hex dump
                            excerpt_lines = [line for line in log_content.split('\n') if 'average' in line.lower() or 'seconds' in line.lower()]
                            excerpt = '\n    '.join(excerpt_lines[:5]) if excerpt_lines else "(no lines with 'average' or 'seconds' found)"
                            print(f"Warning: Could not find 'Average: X Seconds' pattern in {thread_log}", file=sys.stderr)
                            print(f"  File exists: {thread_log.exists()}, Size: {thread_log.stat().st_size if thread_log.exists() else 'N/A'} bytes", file=sys.stderr)
                            print(f"  Relevant lines:\n    {excerpt}", file=sys.stderr)
                            # Show hex dump of first relevant line for debugging
                            if excerpt_lines:
                                first_line = excerpt_lines[0]
                                hex_dump = ' '.join(f'{ord(c):02x}' for c in first_line[:50])
                                print(f"  First line hex (first 50 chars): {hex_dump}", file=sys.stderr)

                except (IOError, ValueError) as e:
                    print(f"Warning: Failed to parse {thread_log}: {e}", file=sys.stderr)

        # Add test results to thread data under "test_name" key
        if test_results:
            thread_data["test_name"] = test_results
            benchmark_result[thread_num] = thread_data

    return benchmark_result if benchmark_result else None


def build_json_structure(project_root: Path, cloud_instances: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build the complete JSON structure by traversing the directory tree.
    Structure per README_results.md:
    {
        "<machinename>": {
            "CSP": "<csp>",
            "total_vcpu": "<vcpu>",
            "cpu_name": "<cpu_name>",
            "cpu_isa": "<cpu_isa>",
            "cost_hour[730h-mo]": "<cost>",
            "os": {
                "<os>": {
                    "testcategory": {
                        "<testcategory>": {
                            "benchmark": {
                                "<benchmark>": {
                                    "thread": {
                                        "<N>": { ... }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    """
    # Directories to skip during processing
    SKIP_DIRS = {'__pycache__', '.pytest_cache', 'node_modules', '.git', '.venv', 'venv'}

    result = {}

    # Iterate through machinenames
    for machine_dir in sorted(project_root.iterdir()):
        if not machine_dir.is_dir() or machine_dir.name.startswith('.') or machine_dir.name in SKIP_DIRS:
            continue

        machinename = machine_dir.name
        machine_info = get_machine_info(machinename, cloud_instances)

        machine_data = {
            "CSP": machine_info["CSP"],
            "total_vcpu": machine_info["total_vcpu"],
            "cpu_name": machine_info["cpu_name"],
            "cpu_isa": machine_info["cpu_isa"],
            "cost_hour[730h-mo]": machine_info["cost_hour[730h-mo]"],
            "os": {}
        }

        # Iterate through OS directories
        for os_dir in sorted(machine_dir.iterdir()):
            if not os_dir.is_dir():
                continue

            os_name = os_dir.name
            os_data = {"testcategory": {}}

            # Iterate through testcategory directories
            for testcategory_dir in sorted(os_dir.iterdir()):
                if not testcategory_dir.is_dir():
                    continue

                testcategory = testcategory_dir.name
                testcategory_data = {"benchmark": {}}

                # Iterate through benchmark directories
                for benchmark_dir in sorted(testcategory_dir.iterdir()):
                    if not benchmark_dir.is_dir():
                        continue

                    benchmark = benchmark_dir.name
                    # Pass cost_hour to process_benchmark for cost calculation
                    benchmark_data = process_benchmark(benchmark_dir, machine_info["cost_hour[730h-mo]"])

                    if benchmark_data:
                        # Wrap benchmark_data in "thread" key
                        testcategory_data["benchmark"][benchmark] = {"thread": benchmark_data}

                if testcategory_data["benchmark"]:
                    os_data["testcategory"][testcategory] = testcategory_data

            if os_data["testcategory"]:
                machine_data["os"][os_name] = os_data

        result[machinename] = machine_data

    return result


def merge_json_data(data1: Dict[str, Any], data2: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two JSON structures. Machine data is merged at the top level.
    """
    result = data1.copy()
    for machine_name, machine_data in data2.items():
        if machine_name in result:
            # Merge OS data
            for os_name, os_data in machine_data.get("os", {}).items():
                if os_name not in result[machine_name].get("os", {}):
                    if "os" not in result[machine_name]:
                        result[machine_name]["os"] = {}
                    result[machine_name]["os"][os_name] = os_data
        else:
            result[machine_name] = machine_data
    return result


def check_syntax() -> bool:
    """
    Check syntax of this script and the output JSON file.
    Returns True if no errors, False otherwise.
    """
    errors = []

    # Check this script's syntax
    script_path = Path(__file__)
    try:
        with open(script_path, 'r') as f:
            ast.parse(f.read())
        print(f"✓ Syntax check passed: {script_path}")
    except SyntaxError as e:
        errors.append(f"Syntax error in {script_path}: {e}")
        print(f"✗ Syntax error in {script_path}: {e}", file=sys.stderr)

    return len(errors) == 0


def check_json_syntax(json_file: Path) -> bool:
    """
    Check syntax of the output JSON file.
    Returns True if valid, False otherwise.
    """
    try:
        with open(json_file, 'r') as f:
            json.load(f)
        print(f"✓ JSON syntax check passed: {json_file}")
        return True
    except json.JSONDecodeError as e:
        print(f"✗ JSON syntax error in {json_file}: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print(f"✗ File not found: {json_file}", file=sys.stderr)
        return False


def main():
    parser = argparse.ArgumentParser(description='Generate one_big_json.json from results directory')
    parser.add_argument('--dir', type=str, action='append',
                        help='Path to project root (can be specified multiple times). Default: current directory')
    parser.add_argument('--output', type=str, default='one_big_json.json',
                        help='Output JSON file path (default: one_big_json.json)')
    parser.add_argument('--instance_source', type=str, default='../',
                        help='Directory containing cloud_instances.json (default: ../)')
    parser.add_argument('--merge', nargs='+', metavar='JSON_FILE',
                        help='Merge multiple JSON files instead of building from directories. Requires --output to be specified.')

    args = parser.parse_args()

    # Check this script's syntax
    print("Checking script syntax...")
    if not check_syntax():
        print("Script syntax check failed. Aborting.", file=sys.stderr)
        sys.exit(1)

    output_file = Path(args.output)

    # Handle --merge mode
    if args.merge:
        # In merge mode, --output must be specified and different from default
        if args.output == 'one_big_json.json':
            print("Error: When using --merge, you must specify --output with a non-default filename.", file=sys.stderr)
            print("Example: make_one_big_json.py --merge ./1.json ./2.json --output ./New.json", file=sys.stderr)
            sys.exit(1)

        # Check if output file exists and confirm overwrite
        if output_file.exists():
            response = input(f"Output file '{output_file}' already exists. Overwrite? [y/N]: ")
            if response.lower() not in ['y', 'yes']:
                print("Aborted.")
                sys.exit(0)

        # Merge JSON files
        print(f"Merging {len(args.merge)} JSON files...")
        current_version = get_version_info()
        merged_data = {}
        first_version = None

        for idx, json_file_path in enumerate(args.merge):
            json_file = Path(json_file_path)
            if not json_file.exists():
                print(f"Error: JSON file '{json_file}' does not exist", file=sys.stderr)
                sys.exit(1)

            print(f"  Loading {json_file}...")
            try:
                with open(json_file, 'r') as f:
                    json_data = json.load(f)

                # Check version compatibility (per README_results.md specification)
                if "generation_log" in json_data and "version_info" in json_data["generation_log"]:
                    file_version = json_data["generation_log"]["version_info"]

                    if idx == 0:
                        first_version = file_version
                        print(f"    Version: {file_version}")
                    else:
                        print(f"    Version: {file_version}")
                        if not check_version_compatibility(first_version, file_version):
                            print(f"Error: Version mismatch detected!", file=sys.stderr)
                            print(f"  First file version: {first_version}", file=sys.stderr)
                            print(f"  Current file version: {file_version}", file=sys.stderr)
                            print(f"  JSON files with different versions cannot be merged.", file=sys.stderr)
                            sys.exit(1)

                    # Remove generation_log before merging (will be recreated)
                    json_data.pop("generation_log", None)
                else:
                    print(f"    Warning: No version info found in {json_file}", file=sys.stderr)

                # Merge hierarchically
                merged_data = merge_json_data(merged_data, json_data)
            except json.JSONDecodeError as e:
                print(f"Error: Failed to parse {json_file}: {e}", file=sys.stderr)
                sys.exit(1)

        # Add generation_log to merged data
        final_output = create_generation_log()
        final_output.update(merged_data)

        # Write merged output
        print(f"Writing merged output to: {output_file}")
        with open(output_file, 'w') as f:
            json.dump(final_output, f, indent=2)

        print(f"Successfully merged {len(args.merge)} JSON files into {output_file}")
        print(f"Output version: {current_version}")
        print(f"Total machines in merged output: {len(merged_data)}")

        # Check output JSON syntax
        print("\nChecking output JSON syntax...")
        if not check_json_syntax(output_file):
            print("Output JSON syntax check failed.", file=sys.stderr)
            sys.exit(1)

        return

    # Normal mode: build from directories
    # If --dir is not specified, use current directory
    project_roots = args.dir if args.dir else ['.']

    # Load cloud_instances.json
    instance_source = Path(args.instance_source).resolve()
    print(f"Loading cloud_instances.json from: {instance_source}")
    cloud_instances = load_cloud_instances(instance_source)

    # Check if output file exists and confirm overwrite
    if output_file.exists():
        response = input(f"Output file '{output_file}' already exists. Overwrite? [y/N]: ")
        if response.lower() not in ['y', 'yes']:
            print("Aborted.")
            sys.exit(0)

    # Process all project roots and merge results
    merged_data = {}
    for project_root_str in project_roots:
        project_root = Path(project_root_str).resolve()

        if not project_root.exists():
            print(f"Error: Project root '{project_root}' does not exist", file=sys.stderr)
            sys.exit(1)

        print(f"Processing results from: {project_root}")

        # Build JSON structure
        json_data = build_json_structure(project_root, cloud_instances)

        # Merge with existing data
        merged_data = merge_json_data(merged_data, json_data)

    print(f"Output file: {output_file}")

    # Add generation_log to output
    current_version = get_version_info()
    final_output = create_generation_log()
    final_output.update(merged_data)

    # Write output
    with open(output_file, 'w') as f:
        json.dump(final_output, f, indent=2)

    print(f"Successfully generated {output_file}")
    print(f"Output version: {current_version}")
    print(f"Total machines processed: {len(merged_data)}")

    # Check output JSON syntax
    print("\nChecking output JSON syntax...")
    if not check_json_syntax(output_file):
        print("Output JSON syntax check failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
