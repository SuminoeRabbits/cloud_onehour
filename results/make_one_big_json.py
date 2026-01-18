#!/usr/bin/env python3
"""
make_one_big_json.py

Generates one_big_json.json from results directory structure.
Based on README_results.md specification.

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
        Benchmark completion conditions (3 cases):
        Case 1: Both summary.json and <N>-thread.json exist
        Case 2: <N>-thread.json exists (without summary.json)
        Case 3: <N>-thread_perf_summary.json exists

        cost = cost_hour[730h-mo] * time / 3600
        where time is in seconds, so divide by 3600 to convert to hours
    """
    summary_json = benchmark_dir / "summary.json"

    # Find all thread counts from <N>-thread.json files (Case 1 & 2)
    thread_json_files = list(benchmark_dir.glob("*-thread.json"))
    thread_nums = sorted(set(f.stem.split("-")[0] for f in thread_json_files if f.stem.split("-")[0].isdigit()))

    # Find all thread counts from <N>-thread_perf_summary.json files (Case 3)
    perf_summary_files = list(benchmark_dir.glob("*-thread_perf_summary.json"))
    perf_summary_thread_nums = sorted(set(f.stem.split("-")[0] for f in perf_summary_files if f.stem.split("-")[0].isdigit()))

    # Merge thread numbers from both sources
    all_thread_nums = sorted(set(thread_nums + perf_summary_thread_nums))

    # Check if benchmark is complete per README_results.md
    # At least one of: <N>-thread.json or <N>-thread_perf_summary.json must exist
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
        test_results = {}

        # Determine which case to apply
        # Priority: Case 1 > Case 2 > Case 3
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
            # - values: N/A
            # - raw_values: N/A
            # - unit: N/A
            # - test_run_times: [elapsed_time_sec] from <N>-thread_perf_summary.json
            # - description: from <N>-thread.json if available, else "perf stat only"
            perf_summary_file = benchmark_dir / f"{thread_num}-thread_perf_summary.json"
            pts_json = benchmark_dir / f"{thread_num}-thread.json"

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

                    # Calculate cost using elapsed_time_sec
                    cost = cost_hour * elapsed_time_sec / 3600.0

                    test_results[test_name] = {
                        "description": description,
                        "values": "N/A",
                        "raw_values": "N/A",
                        "unit": "N/A",
                        "time": elapsed_time_sec,
                        "test_run_times": [elapsed_time_sec],
                        "cost": cost
                    }
                except (json.JSONDecodeError, IOError) as e:
                    print(f"Warning: Failed to read {perf_summary_file}: {e}", file=sys.stderr)

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
    result = {}

    # Iterate through machinenames
    for machine_dir in sorted(project_root.iterdir()):
        if not machine_dir.is_dir() or machine_dir.name.startswith('.'):
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
        merged_data = {}

        for json_file_path in args.merge:
            json_file = Path(json_file_path)
            if not json_file.exists():
                print(f"Error: JSON file '{json_file}' does not exist", file=sys.stderr)
                sys.exit(1)

            print(f"  Loading {json_file}...")
            try:
                with open(json_file, 'r') as f:
                    json_data = json.load(f)

                # Merge hierarchically
                merged_data = merge_json_data(merged_data, json_data)
            except json.JSONDecodeError as e:
                print(f"Error: Failed to parse {json_file}: {e}", file=sys.stderr)
                sys.exit(1)

        # Write merged output
        print(f"Writing merged output to: {output_file}")
        with open(output_file, 'w') as f:
            json.dump(merged_data, f, indent=2)

        print(f"Successfully merged {len(args.merge)} JSON files into {output_file}")
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

    # Write output
    with open(output_file, 'w') as f:
        json.dump(merged_data, f, indent=2)

    print(f"Successfully generated {output_file}")
    print(f"Total machines processed: {len(merged_data)}")

    # Check output JSON syntax
    print("\nChecking output JSON syntax...")
    if not check_json_syntax(output_file):
        print("Output JSON syntax check failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
