#!/usr/bin/env python3
"""
one_big_json_analytics.py
Version: v1.2.1
Generated: 2026-01-21

This script analyzes one_big_json.json and generates four types of comparisons:
1. Performance comparison - Absolute performance across different machines
2. Cost comparison - Cost efficiency across different machines
3. Thread scaling comparison - Thread scaling characteristics within same machine
4. CSP instance comparison - Cost efficiency within same CSP
5. Benchmark configuration analysis - Classification of benchmarks based on thread usage
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
        return f"v1.1.0-g{git_hash}"
    except:
        return "v1.1.0-gunknown"


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


def performance_comparison(data: Dict[str, Any], ref_machine: str, ref_os: str) -> Dict[str, Any]:
    """
    Generate performance comparison with specified machine/OS as reference (100)
    """
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
        "description": "Performance comparison by machine_name",
        "workload": {}
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

                        # Build nested structure per specification:
                        # workload > category > benchmark > test_name > os > machinename
                        if testcat not in result["workload"]: result["workload"][testcat] = {}
                        if benchmark not in result["workload"][testcat]: result["workload"][testcat][benchmark] = {}
                        if test_name not in result["workload"][testcat][benchmark]: result["workload"][testcat][benchmark][test_name] = {}
                        if os_name not in result["workload"][testcat][benchmark][test_name]: result["workload"][testcat][benchmark][test_name][os_name] = {}
                        
                        result["workload"][testcat][benchmark][test_name][os_name][machine_name] = score

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


def csp_instance_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate CSP instance comparison.
    Within each CSP, compare instances using the arm64 instance as reference (100).
    Reference instances:
    - AWS: m8g-xlarge
    - GCP: c4a-standard-8
    - OCI: VM.Standard.A1.Flex
    """
    # Define reference instances for each CSP
    csp_reference_instances = {
        "AWS": "aws-m8g-xlarge",
        "GCP": "gcp-c4a-standard-8",
        "OCI": "oci-VM.Standard.A1.Flex"
    }

    # Group machines by CSP
    csp_groups = {}
    for machine_name, machine_data in data.items():
        if machine_name == "generation_log":
            continue

        csp = machine_data.get("CSP", "unknown")
        if csp == "unknown":
            continue

        if csp not in csp_groups:
            csp_groups[csp] = []
        csp_groups[csp].append((machine_name, machine_data))

    result = {
        "generation_log": {
            "version_info": get_version_info(),
            "date": datetime.now().strftime("%Y%m%d-%H%M%S")
        },
        "description": "CSP instance comparison",
        "csps": {}
    }

    # Process each CSP separately
    for csp, machines in csp_groups.items():
        if csp not in csp_reference_instances:
            print(f"Warning: No reference instance defined for CSP '{csp}' - skipping", file=sys.stderr)
            continue

        ref_machine_name = csp_reference_instances[csp]

        # Find reference machine in this CSP group
        ref_machine_data = None
        ref_os = None

        for machine_name, machine_data in machines:
            if machine_name == ref_machine_name:
                ref_machine_data = machine_data
                # Use first available OS as reference
                if "os" in machine_data and len(machine_data["os"]) > 0:
                    ref_os = list(machine_data["os"].keys())[0]
                break

        if ref_machine_data is None or ref_os is None:
            print(f"Error: Reference machine '{ref_machine_name}' not found for CSP '{csp}'", file=sys.stderr)
            continue

        # Collect reference costs
        ref_hourly_cost = ref_machine_data.get("cost_hour[730h-mo]", 0.0)
        reference_costs = {}
        ref_os_data = ref_machine_data["os"][ref_os]

        if "testcategory" not in ref_os_data:
            print(f"Error: No testcategory found in reference {ref_machine_name}/{ref_os}", file=sys.stderr)
            continue

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
            print(f"Error: No reference costs could be calculated for CSP '{csp}'", file=sys.stderr)
            continue

        # Generate comparison for all machines in this CSP
        result["csps"][csp] = {
            "machines": {}
        }

        for machine_name, machine_data in machines:
            if "os" not in machine_data:
                continue

            hourly_cost = machine_data.get("cost_hour[730h-mo]", 0.0)

            for os_name, os_data in machine_data["os"].items():
                machine_key = f"{machine_name}/{os_name}"
                result["csps"][csp]["machines"][machine_key] = {
                    "header": {
                        "machinename": machine_name,
                        "os": os_name,
                        "CSP": csp,
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
                                print(f"Warning: {test_name} exists in {machine_name} but not in reference for CSP {csp}",
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
                            result["csps"][csp]["machines"][machine_key]["workload"][workload_key] = score

                # Check for missing tests
                for key in reference_costs.keys():
                    testcat, benchmark, test_name = key
                    workload_key = f"{testcat}/{benchmark}/{test_name}"
                    if workload_key not in result["csps"][csp]["machines"][machine_key]["workload"]:
                        result["csps"][csp]["machines"][machine_key]["workload"][workload_key] = "unknown"

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


def benchmark_configuration_analysis(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Analyze benchmark configurations based on thread counts and vCPU.
    Classifies benchmarks into:
    - Scaling (N={1..vCPU}): Supports both scale-up and scale-out
    - Multi-thread (N=vCPU): Fixed thread count, scale-out only
    - Single-thread (N=1): Fixed thread count, scale-up only
    - Fixed-thread (Other): Fixed thread count != 1 and != vCPU
    """
    result = {
        "generation_log": {
            "version_info": get_version_info(),
            "date": datetime.now().strftime("%Y%m%d-%H%M%S")
        },
        "description": "Benchmark configuration analysis",
        "machines": {}
    }

    for machine_name, machine_data in data.items():
        if machine_name == "generation_log":
            continue

        if "os" not in machine_data:
            continue

        total_vcpu = machine_data.get("total_vcpu", 0)

        for os_name, os_data in machine_data["os"].items():
            machine_key = f"{machine_name}/{os_name}"
            result["machines"][machine_key] = {
                "header": {
                    "machinename": machine_name,
                    "os": os_name,
                    "total_vcpu": total_vcpu
                },
                "configurations": {}
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
                    
                    if not thread_counts:
                        config_type = "Unknown (No threads)"
                    elif len(thread_counts) > 1:
                        # Case 3: <N>={1,2,3...,vCPU}
                        config_type = f"Scaling (Threads: {min(thread_counts)}..{max(thread_counts)})"
                        if max(thread_counts) != total_vcpu and total_vcpu > 0:
                            config_type += f" [Warning: Max thread {max(thread_counts)} != vCPU {total_vcpu}]"
                    else:
                        # Fixed thread count
                        thread_count = thread_counts[0]
                        if thread_count == total_vcpu:
                            # Case 1: <N>=vCPU
                            config_type = "Multi-thread (Scale-out only)"
                        elif thread_count == 1:
                            # Case 2: <N>=1
                            config_type = "Single-thread (Scale-up only)"
                        else:
                            config_type = f"Fixed-thread (N={thread_count})"

                    workload_key = f"{testcat}/{benchmark}"
                    result["machines"][machine_key]["configurations"][workload_key] = config_type

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
    parser.add_argument('--csp', action='store_true',
                        help='Generate CSP instance comparison only')
    parser.add_argument('--config', action='store_true',
                        help='Generate benchmark configuration analysis only')
    parser.add_argument('--all', action='store_true',
                        help='Generate all comparisons (default if no option specified)')
    parser.add_argument('--ref', type=str, default='aws-m8a-2xlarge-amd64',
                        help='Reference machine name for normalization (default: aws-m8a-2xlarge-amd64)')
    parser.add_argument('--ref-os', type=str, default='Ubuntu_25_04',
                        help='Reference OS name for normalization (default: Ubuntu_25_04)')

    args = parser.parse_args()

    # If no specific option, default to --all
    if not (args.perf or args.cost or args.th or args.csp or args.config or args.all):
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

    # Determine reference machine/OS if not in data
    ref_machine = args.ref
    ref_os = args.ref_os
    
    if ref_machine not in data:
        # Fallback to the first available machine in the data (excluding generation_log)
        available_machines = [m for m in data.keys() if m != "generation_log"]
        if available_machines:
            ref_machine = available_machines[0]
            if "os" in data[ref_machine] and data[ref_machine]["os"]:
                ref_os = list(data[ref_machine]["os"].keys())[0]
            print(f"Warning: Specified reference '{args.ref}' not found. Using '{ref_machine}/{ref_os}' as fallback.", file=sys.stderr)

    # Generate requested comparisons
    results = {}

    if args.perf or args.all:
        print("Generating performance comparison...", file=sys.stderr)
        results["performance_comparison"] = performance_comparison(data, ref_machine, ref_os)

    if args.cost or args.all:
        print("Generating cost comparison...", file=sys.stderr)
        results["cost_comparison"] = cost_comparison(data, ref_machine, ref_os)

    if args.th or args.all:
        print("Generating thread scaling comparison...", file=sys.stderr)
        results["thread_scaling_comparison"] = thread_scaling_comparison(data)

    if args.csp or args.all:
        print("Generating CSP instance comparison...", file=sys.stderr)
        results["csp_instance_comparison"] = csp_instance_comparison(data)

    if args.config or args.all:
        print("Generating benchmark configuration analysis...", file=sys.stderr)
        results["benchmark_configuration_analysis"] = benchmark_configuration_analysis(data)

    # Output to stdout
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
