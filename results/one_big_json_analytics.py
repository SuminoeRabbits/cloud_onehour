#!/usr/bin/env python3
"""
one_big_json_analytics.py
Version: v1.0.0
Generated: 2026-01-18

This script analyzes one_big_json.json and generates three types of comparisons:
1. Performance comparison - Absolute performance across different machines
2. Cost comparison - Cost efficiency across different machines
3. Thread scaling comparison - Thread scaling characteristics within same machine
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import subprocess


def get_version_info() -> str:
    """Get version info in format v<major>.<minor>.<patch>-g<git-hash>"""
    try:
        git_hash = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
        return f"v1.0.0-g{git_hash}"
    except:
        return "v1.0.0-gunknown"


def validate_json_syntax(file_path: Path) -> bool:
    """Validate JSON syntax of input file"""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json.load(f)
        return True
    except json.JSONDecodeError as e:
        print(f"Error: JSON syntax error in {file_path}: {e}", file=sys.stderr)
        return False
    except FileNotFoundError:
        print(f"Error: File not found: {file_path}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Error: Failed to read {file_path}: {e}", file=sys.stderr)
        return False


def load_data(file_path: Path) -> Optional[Dict[str, Any]]:
    """Load one_big_json.json data"""
    if not validate_json_syntax(file_path):
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error: Failed to load data: {e}", file=sys.stderr)
        return None


def get_performance_value(test_data: Dict[str, Any]) -> Optional[float]:
    """
    Get performance value from test data.
    - If "values" exists and is not "N/A", use it (higher is better)
    - Otherwise use "time" (lower is better, so we invert it)
    """
    if "values" in test_data and test_data["values"] != "N/A":
        try:
            return float(test_data["values"])
        except (ValueError, TypeError):
            return None
    elif "time" in test_data and test_data["time"] > 0:
        return test_data["time"]
    return None


def is_values_based(test_data: Dict[str, Any]) -> bool:
    """Check if test uses values (higher is better) or time (lower is better)"""
    return "values" in test_data and test_data["values"] != "N/A"


def performance_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate performance comparison with aws-m8a-2xlarge-amd64/Ubuntu_25_04 as reference (100)
    """
    ref_machine = "aws-m8a-2xlarge-amd64"
    ref_os = "Ubuntu_25_04"

    # Find reference values
    if ref_machine not in data:
        return {
            "error": f"Reference machine '{ref_machine}' not found in data"
        }

    machine_data = data[ref_machine]
    if "os" not in machine_data or ref_os not in machine_data["os"]:
        return {
            "error": f"Reference OS '{ref_os}' not found in machine '{ref_machine}'"
        }

    # Collect reference values
    reference_values = {}
    ref_os_data = machine_data["os"][ref_os]

    if "testcategory" not in ref_os_data:
        return {"error": f"No testcategory found in reference {ref_machine}/{ref_os}"}

    for testcat, testcat_data in ref_os_data["testcategory"].items():
        if "benchmark" not in testcat_data:
            continue
        for benchmark, bench_data in testcat_data["benchmark"].items():
            if "thread" not in bench_data:
                continue
            # Use nproc thread count for reference
            thread_counts = list(bench_data["thread"].keys())
            max_thread = max(thread_counts, key=lambda x: int(x))

            if "test_name" not in bench_data["thread"][max_thread]:
                continue

            for test_name, test_data in bench_data["thread"][max_thread]["test_name"].items():
                perf_val = get_performance_value(test_data)
                if perf_val is not None:
                    key = (testcat, benchmark, test_name)
                    reference_values[key] = {
                        "value": perf_val,
                        "is_values": is_values_based(test_data)
                    }

    if not reference_values:
        return {"error": "No reference values could be extracted"}

    # Generate comparison for all machines
    result = {
        "generation_log": {
            "version_info": get_version_info(),
            "date": datetime.now().strftime("%Y%m%d-%H%M%S")
        },
        "description": "Performance comparison",
        "reference": {
            "machine": ref_machine,
            "os": ref_os,
            "value": 100
        },
        "machines": {}
    }

    for machine_name, machine_data in data.items():
        if machine_name == "generation_log":
            continue

        if "os" not in machine_data:
            continue

        for os_name, os_data in machine_data["os"].items():
            machine_key = f"{machine_name}/{os_name}"
            result["machines"][machine_key] = {
                "header": {
                    "machinename": machine_name,
                    "os": os_name,
                    "CSP": machine_data.get("CSP", "unknown"),
                    "cpu_name": machine_data.get("cpu_name", "unknown")
                },
                "workload": {}
            }

            if "testcategory" not in os_data:
                continue

            for testcat, testcat_data in os_data["testcategory"].items():
                if "benchmark" not in testcat_data:
                    continue
                for benchmark, bench_data in testcat_data["benchmark"].items():
                    if "thread" not in bench_data:
                        continue

                    thread_counts = list(bench_data["thread"].keys())
                    max_thread = max(thread_counts, key=lambda x: int(x))

                    if "test_name" not in bench_data["thread"][max_thread]:
                        continue

                    for test_name, test_data in bench_data["thread"][max_thread]["test_name"].items():
                        key = (testcat, benchmark, test_name)

                        # Check if this test exists in reference
                        if key not in reference_values:
                            print(f"Warning: {test_name} exists in {machine_name} but not in reference",
                                  file=sys.stderr)
                            continue

                        perf_val = get_performance_value(test_data)
                        ref_info = reference_values[key]

                        if perf_val is None:
                            score = "unknown"
                        elif ref_info["value"] == 0:
                            print(f"Warning: Reference value is 0 for {testcat}/{benchmark}/{test_name} - setting to 'unknown'",
                                  file=sys.stderr)
                            score = "unknown"
                        elif perf_val == 0:
                            print(f"Warning: Performance value is 0 for {machine_name} in {testcat}/{benchmark}/{test_name} - setting to 'unknown'",
                                  file=sys.stderr)
                            score = "unknown"
                        else:
                            if ref_info["is_values"]:
                                # Higher is better, so normalize directly
                                score = round((perf_val / ref_info["value"]) * 100, 2)
                            else:
                                # Time-based, lower is better, so invert
                                score = round((ref_info["value"] / perf_val) * 100, 2)

                        workload_key = f"{testcat}/{benchmark}/{test_name}"
                        result["machines"][machine_key]["workload"][workload_key] = score

            # Check for missing tests in this machine
            for key in reference_values.keys():
                testcat, benchmark, test_name = key
                workload_key = f"{testcat}/{benchmark}/{test_name}"
                if workload_key not in result["machines"][machine_key]["workload"]:
                    result["machines"][machine_key]["workload"][workload_key] = "unknown"

    return result


def cost_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate cost comparison with aws-m8a-2xlarge-amd64/Ubuntu_25_04 as reference (100)
    Cost = execution_time * hourly_cost
    """
    ref_machine = "aws-m8a-2xlarge-amd64"
    ref_os = "Ubuntu_25_04"

    # Find reference machine
    if ref_machine not in data:
        return {
            "error": f"Reference machine '{ref_machine}' not found in data"
        }

    machine_data = data[ref_machine]
    if "os" not in machine_data or ref_os not in machine_data["os"]:
        return {
            "error": f"Reference OS '{ref_os}' not found in machine '{ref_machine}'"
        }

    ref_hourly_cost = machine_data.get("cost_hour[730h-mo]", 0.0)

    # Collect reference costs
    reference_costs = {}
    ref_os_data = machine_data["os"][ref_os]

    if "testcategory" not in ref_os_data:
        return {"error": f"No testcategory found in reference {ref_machine}/{ref_os}"}

    for testcat, testcat_data in ref_os_data["testcategory"].items():
        if "benchmark" not in testcat_data:
            continue
        for benchmark, bench_data in testcat_data["benchmark"].items():
            if "thread" not in bench_data:
                continue
            thread_counts = list(bench_data["thread"].keys())
            max_thread = max(thread_counts, key=lambda x: int(x))

            if "test_name" not in bench_data["thread"][max_thread]:
                continue

            for test_name, test_data in bench_data["thread"][max_thread]["test_name"].items():
                time_val = test_data.get("time", 0)
                if time_val > 0:
                    cost = (time_val / 3600.0) * ref_hourly_cost
                    key = (testcat, benchmark, test_name)
                    reference_costs[key] = cost

    if not reference_costs:
        return {"error": "No reference costs could be calculated"}

    # Generate comparison for all machines
    result = {
        "generation_log": {
            "version_info": get_version_info(),
            "date": datetime.now().strftime("%Y%m%d-%H%M%S")
        },
        "description": "Cost comparison",
        "reference": {
            "machine": ref_machine,
            "os": ref_os,
            "value": 100
        },
        "machines": {}
    }

    for machine_name, machine_data in data.items():
        if machine_name == "generation_log":
            continue

        if "os" not in machine_data:
            continue

        hourly_cost = machine_data.get("cost_hour[730h-mo]", 0.0)

        for os_name, os_data in machine_data["os"].items():
            machine_key = f"{machine_name}/{os_name}"
            result["machines"][machine_key] = {
                "header": {
                    "machinename": machine_name,
                    "os": os_name,
                    "CSP": machine_data.get("CSP", "unknown"),
                    "cpu_name": machine_data.get("cpu_name", "unknown"),
                    "cost_hour": hourly_cost
                },
                "workload": {}
            }

            if "testcategory" not in os_data:
                continue

            for testcat, testcat_data in os_data["testcategory"].items():
                if "benchmark" not in testcat_data:
                    continue
                for benchmark, bench_data in testcat_data["benchmark"].items():
                    if "thread" not in bench_data:
                        continue

                    thread_counts = list(bench_data["thread"].keys())
                    max_thread = max(thread_counts, key=lambda x: int(x))

                    if "test_name" not in bench_data["thread"][max_thread]:
                        continue

                    for test_name, test_data in bench_data["thread"][max_thread]["test_name"].items():
                        key = (testcat, benchmark, test_name)

                        if key not in reference_costs:
                            print(f"Warning: {test_name} exists in {machine_name} but not in reference",
                                  file=sys.stderr)
                            continue

                        time_val = test_data.get("time", 0)

                        if time_val <= 0:
                            print(f"Warning: Time value is 0 or negative for {machine_name} in {testcat}/{benchmark}/{test_name} - setting to 'unknown'",
                                  file=sys.stderr)
                            score = "unknown"
                        elif hourly_cost <= 0:
                            print(f"Warning: Hourly cost is 0 or negative for {machine_name} - setting to 'unknown'",
                                  file=sys.stderr)
                            score = "unknown"
                        else:
                            cost = (time_val / 3600.0) * hourly_cost
                            if cost == 0:
                                print(f"Warning: Calculated cost is 0 for {machine_name} in {testcat}/{benchmark}/{test_name} - setting to 'unknown'",
                                      file=sys.stderr)
                                score = "unknown"
                            elif reference_costs[key] == 0:
                                print(f"Warning: Reference cost is 0 for {testcat}/{benchmark}/{test_name} - setting to 'unknown'",
                                      file=sys.stderr)
                                score = "unknown"
                            else:
                                # Lower cost is better, so invert
                                score = round((reference_costs[key] / cost) * 100, 2)

                        workload_key = f"{testcat}/{benchmark}/{test_name}"
                        result["machines"][machine_key]["workload"][workload_key] = score

            # Check for missing tests
            for key in reference_costs.keys():
                testcat, benchmark, test_name = key
                workload_key = f"{testcat}/{benchmark}/{test_name}"
                if workload_key not in result["machines"][machine_key]["workload"]:
                    result["machines"][machine_key]["workload"][workload_key] = "unknown"

    return result


def thread_scaling_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate thread scaling comparison.
    For each machine, use max thread count (nproc) as reference (100).
    """
    result = {
        "generation_log": {
            "version_info": get_version_info(),
            "date": datetime.now().strftime("%Y%m%d-%H%M%S")
        },
        "description": "Thread scaling comparison",
        "machines": {}
    }

    for machine_name, machine_data in data.items():
        if machine_name == "generation_log":
            continue

        if "os" not in machine_data:
            continue

        for os_name, os_data in machine_data["os"].items():
            machine_key = f"{machine_name}/{os_name}"
            result["machines"][machine_key] = {
                "header": {
                    "machinename": machine_name,
                    "os": os_name
                },
                "workload": {}
            }

            if "testcategory" not in os_data:
                continue

            for testcat, testcat_data in os_data["testcategory"].items():
                if "benchmark" not in testcat_data:
                    continue
                for benchmark, bench_data in testcat_data["benchmark"].items():
                    if "thread" not in bench_data:
                        continue

                    thread_counts = sorted([int(t) for t in bench_data["thread"].keys()])

                    if len(thread_counts) < 2:
                        # Need at least 2 thread counts for scaling analysis
                        continue

                    max_thread = str(max(thread_counts))

                    # Get all test names from max thread
                    if "test_name" not in bench_data["thread"][max_thread]:
                        continue

                    for test_name in bench_data["thread"][max_thread]["test_name"].keys():
                        # Get reference value (max thread count)
                        ref_data = bench_data["thread"][max_thread]["test_name"][test_name]
                        ref_time = ref_data.get("time", 0)

                        if ref_time <= 0:
                            continue

                        workload_key = f"{testcat}/{benchmark}/{test_name}"
                        result["machines"][machine_key]["workload"][workload_key] = {}

                        # Calculate scaling for each thread count
                        for thread_str in bench_data["thread"].keys():
                            thread_num = int(thread_str)

                            if "test_name" not in bench_data["thread"][thread_str]:
                                continue
                            if test_name not in bench_data["thread"][thread_str]["test_name"]:
                                continue

                            test_data = bench_data["thread"][thread_str]["test_name"][test_name]
                            time_val = test_data.get("time", 0)

                            if time_val <= 0:
                                score = "unknown"
                            else:
                                # Lower time is better, so invert for scaling score
                                score = round((ref_time / time_val) * 100, 2)

                            result["machines"][machine_key]["workload"][workload_key][thread_str] = score

    return result


def main():
    parser = argparse.ArgumentParser(
        description='Analyze one_big_json.json and generate performance comparisons'
    )
    parser.add_argument('--input', type=str, default='one_big_json.json',
                        help='Path to one_big_json.json (default: ./one_big_json.json)')
    parser.add_argument('--perf', action='store_true',
                        help='Generate performance comparison only')
    parser.add_argument('--cost', action='store_true',
                        help='Generate cost comparison only')
    parser.add_argument('--th', action='store_true',
                        help='Generate thread scaling comparison only')
    parser.add_argument('--all', action='store_true',
                        help='Generate all comparisons (default if no option specified)')

    args = parser.parse_args()

    # If no specific option, default to --all
    if not (args.perf or args.cost or args.th or args.all):
        args.all = True

    # Validate script syntax (basic check)
    try:
        compile(open(__file__).read(), __file__, 'exec')
    except SyntaxError as e:
        print(f"Error: Syntax error in {__file__}: {e}", file=sys.stderr)
        sys.exit(1)

    # Load data
    input_path = Path(args.input)
    data = load_data(input_path)

    if data is None:
        sys.exit(1)

    # Generate requested comparisons
    results = {}

    if args.perf or args.all:
        print("Generating performance comparison...", file=sys.stderr)
        results["performance_comparison"] = performance_comparison(data)

    if args.cost or args.all:
        print("Generating cost comparison...", file=sys.stderr)
        results["cost_comparison"] = cost_comparison(data)

    if args.th or args.all:
        print("Generating thread scaling comparison...", file=sys.stderr)
        results["thread_scaling_comparison"] = thread_scaling_comparison(data)

    # Output to stdout
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
