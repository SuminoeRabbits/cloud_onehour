#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
one_big_json_analytics.py

Version: v1.2.0
Generated: 2026-02-05

This script analyzes one_big_json.json and generates four types of comparisons:
1. Performance comparison - Absolute performance across different machines
2. Cost comparison - Cost efficiency across different machines
3. Thread scaling comparison - Thread scaling characteristics within same machine
4. CSP instance comparison - Cost efficiency within same CSP

See README_analytics.md for detailed specification.
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
import subprocess

# Script version
VERSION = "v1.2.0"


class ThreadScalingError(RuntimeError):
    """Raised when thread scaling baselines cannot be established."""


class CSPComparisonError(RuntimeError):
    """Raised when CSP comparison cannot establish reference baselines."""


def get_version_info() -> str:
    """Get version info in format v<major>.<minor>.<patch>-g<git-hash>"""
    try:
        git_hash = subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode('utf-8').strip()
        return f"{VERSION}-g{git_hash}"
    except Exception:
        return f"{VERSION}-gunknown"


def get_generation_log() -> Dict[str, Any]:
    """Generate the generation log with version and timestamp."""
    return {
        "generation log": {
            "version info": get_version_info(),
            "date": datetime.now().strftime("%Y%m%d-%H%M%S")
        }
    }


def parse_time_value(test_data: Dict[str, Any]) -> Optional[float]:
    """Extract execution time as a non-negative float if available."""
    time_val = test_data.get("time")
    if time_val in (None, "N/A"):
        return None

    try:
        time_float = float(time_val) if not isinstance(time_val, (int, float)) else float(time_val)
    except (ValueError, TypeError):
        return None

    if time_float < 0:
        return None

    return time_float


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


def validate_script_syntax() -> bool:
    """Validate this script's Python syntax."""
    try:
        with open(__file__, 'r', encoding='utf-8') as f:
            source = f.read()
        compile(source, __file__, 'exec')
        return True
    except SyntaxError as e:
        print(f"Error: Script syntax error: {e}", file=sys.stderr)
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


def extract_workloads(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract all workload entries from the JSON data.
    Returns a list of dicts with workload information.
    """
    workloads = []

    for machinename, machine_data in data.items():
        if machinename in ("generation_log", "generation log"):
            continue

        os_data = machine_data.get("os", {})
        for os_name, os_content in os_data.items():
            testcategory_data = os_content.get("testcategory", {})
            for testcategory, tc_content in testcategory_data.items():
                benchmark_data = tc_content.get("benchmark", {})
                for benchmark, bm_content in benchmark_data.items():
                    thread_data = bm_content.get("thread", {})
                    for thread, th_content in thread_data.items():
                        test_name_data = th_content.get("test_name", {})
                        for test_name, test_data in test_name_data.items():
                            workloads.append({
                                "machinename": machinename,
                                "os": os_name,
                                "testcategory": testcategory,
                                "benchmark": benchmark,
                                "thread": thread,
                                "test_name": test_name,
                                "test_data": test_data,
                                "machine_data": machine_data
                            })

    return workloads


def get_benchmark_score(test_data: Dict[str, Any]) -> Tuple[Any, Any, bool, str]:
    """
    Get benchmark score, time, and unit from test data.
    Returns (benchmark_score, time_score, has_values, unit)
    - Prefer "values" only when raw samples exist
    - Otherwise use valid execution time
    - unit is extracted from test_data["unit"] field
    """
    values = test_data.get("values")
    unit = test_data.get("unit", "N/A")
    if unit in (None, ""):
        unit = "N/A"

    time_value = parse_time_value(test_data)
    time_score: Any = time_value if time_value is not None else "unknown"

    if values not in (None, "N/A"):
        try:
            benchmark_score = float(values) if not isinstance(values, (int, float)) else values
            return (benchmark_score, time_score, True, unit)
        except (ValueError, TypeError):
            pass

    if time_value is not None:
        return (time_value, time_value, False, unit)

    return ("unknown", "unknown", False, unit)


def get_hourly_rate(machine_data: Dict[str, Any]) -> Optional[float]:
    """Return positive hourly_rate defined in input data."""
    hourly_rate = machine_data.get("hourly_rate")
    if hourly_rate in (None, "N/A"):
        return None

    try:
        rate = float(hourly_rate) if not isinstance(hourly_rate, (int, float)) else float(hourly_rate)
    except (ValueError, TypeError):
        return None

    return rate if rate > 0 else None


def performance_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate Performance comparison metrics.
    Compares benchmark scores across different machines for the same workload.

    Output structure:
    {
        description: "Performance comparison by machine_name",
        workload: {
            <testcategory>: {
                <benchmark>: {
                    <test_name>: {
                        "thread": {
                            "<N>": {
                                <machinename>: {
                                    <os>: {
                                        "time_score": <time_score>,
                                        "benchmark_score": <benchmark_score>,
                                        "unit": <unit>
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
    result = {
        "description": "Performance comparison by machine_name",
        "workload": {}
    }

    workloads = extract_workloads(data)

    # Group by testcategory -> benchmark -> test_name -> os -> machinename -> thread
    for w in workloads:
        testcategory = w["testcategory"]
        benchmark = w["benchmark"]
        test_name = w["test_name"]
        os_name = w["os"]
        machinename = w["machinename"]
        thread = w["thread"]
        test_data = w["test_data"]

        benchmark_score, time_score, _, unit = get_benchmark_score(test_data)

        # Build nested structure
        if testcategory not in result["workload"]:
            result["workload"][testcategory] = {}
        if benchmark not in result["workload"][testcategory]:
            result["workload"][testcategory][benchmark] = {}
        workload_entry = result["workload"][testcategory][benchmark]
        if test_name not in workload_entry:
            workload_entry[test_name] = {"thread": {}}

        thread_map = workload_entry[test_name]["thread"]
        if thread not in thread_map:
            thread_map[thread] = {}

        if machinename not in thread_map[thread]:
            thread_map[thread][machinename] = {}

        thread_map[thread][machinename][os_name] = {
            "time_score": time_score,
            "benchmark_score": benchmark_score,
            "unit": unit
        }

    return result


def cost_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate Cost comparison metrics.
    cost_score = (time / 3600) * hourly_rate

    Output structure:
    {
        description: "Cost comparison by machine_name",
        workload: {
            <testcategory>: {
                <benchmark>: {
                    <test_name>: {
                        <os>: {
                            "thread": {
                                "<N>": {
                                    <machinename>: {
                                        "time_score": <time_score>,
                                        "cost_score": <cost_score>,
                                        "unit": <unit>
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
    result = {
        "description": "Cost comparison by machine_name",
        "workload": {}
    }

    workloads = extract_workloads(data)

    for w in workloads:
        testcategory = w["testcategory"]
        benchmark = w["benchmark"]
        test_name = w["test_name"]
        os_name = w["os"]
        machinename = w["machinename"]
        thread = w["thread"]
        test_data = w["test_data"]
        machine_data = w["machine_data"]

        benchmark_score, time_score, _, unit = get_benchmark_score(test_data)

        # Calculate cost_score
        hourly_rate = get_hourly_rate(machine_data)
        time_val = test_data.get("time")

        cost_score: Any = "unknown"
        if hourly_rate is not None:
            if time_val is not None and time_val != "N/A" and time_val != 0:
                try:
                    time_float = float(time_val) if not isinstance(time_val, (int, float)) else time_val
                    cost_score = round((time_float / 3600) * hourly_rate, 6)
                except (ValueError, TypeError):
                    cost_score = "unknown"

        # Build nested structure: thread -> machinename
        if testcategory not in result["workload"]:
            result["workload"][testcategory] = {}
        if benchmark not in result["workload"][testcategory]:
            result["workload"][testcategory][benchmark] = {}
        if test_name not in result["workload"][testcategory][benchmark]:
            result["workload"][testcategory][benchmark][test_name] = {}
        if os_name not in result["workload"][testcategory][benchmark][test_name]:
            result["workload"][testcategory][benchmark][test_name][os_name] = {
                "thread": {}
            }
        if thread not in result["workload"][testcategory][benchmark][test_name][os_name]["thread"]:
            result["workload"][testcategory][benchmark][test_name][os_name]["thread"][thread] = {}

        result["workload"][testcategory][benchmark][test_name][os_name]["thread"][thread][machinename] = {
            "time_score": time_score,
            "cost_score": cost_score,
            "unit": unit
        }

    return result


def thread_scaling_comparison(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate Thread scaling comparison metrics based on execution time.
    The execution time at the maximum thread count becomes the reference (score 100).
    Raises ThreadScalingError when the reference execution time cannot be resolved.
    """
    workloads = extract_workloads(data)

    time_by_workload: Dict[Tuple[str, str, str, str, str], Dict[str, Any]] = {}
    thread_number_map: Dict[Tuple[str, str, str, str, str], Dict[str, int]] = {}
    unit_by_workload: Dict[Tuple[str, str, str, str, str], str] = {}

    for w in workloads:
        key = (w["machinename"], w["os"], w["testcategory"], w["benchmark"], w["test_name"])
        thread_label = str(w["thread"])
        try:
            thread_number = int(thread_label)
        except (TypeError, ValueError):
            print(
                f"Warning: Skip non-integer thread '{thread_label}' for "
                f"{w['machinename']}/{w['os']}/{w['testcategory']}/{w['benchmark']}/{w['test_name']}",
                file=sys.stderr
            )
            continue

        time_value = parse_time_value(w["test_data"])
        time_by_workload.setdefault(key, {})[thread_label] = time_value if time_value is not None else "unknown"
        thread_number_map.setdefault(key, {})[thread_label] = thread_number
        unit = w["test_data"].get("unit")
        unit_by_workload[key] = unit if unit not in (None, "") else "N/A"

    machine_os_pairs = {(w["machinename"], w["os"]) for w in workloads}
    results: List[Dict[str, Any]] = []

    for machinename, os_name in sorted(machine_os_pairs):
        result = {
            "description": "Thread scaling comparison",
            "header": {
                "machinename": machinename,
                "os": os_name
            },
            "workload": {}
        }

        has_scaling_data = False

        for key, thread_times in time_by_workload.items():
            if key[0] != machinename or key[1] != os_name:
                continue

            thread_numbers = thread_number_map.get(key, {})
            if len(thread_numbers) <= 1:
                continue

            max_thread_label = max(thread_numbers, key=lambda t: thread_numbers[t])
            ref_time = thread_times.get(max_thread_label)

            if ref_time == "unknown" or ref_time == 0:
                raise ThreadScalingError(
                    "Cannot determine reference execution time for "
                    f"{machinename}/{os_name}/{key[2]}/{key[3]}/{key[4]} (thread={max_thread_label})"
                )

            scaling_data: Dict[str, Any] = {"unit": unit_by_workload.get(key, "N/A")}
            for thread_label, value in sorted(thread_times.items(), key=lambda item: thread_numbers[item[0]]):
                if value == "unknown" or value == 0:
                    scaling_data[thread_label] = "unknown"
                else:
                    try:
                        relative_score = round((ref_time / value) * 100, 2)
                    except ZeroDivisionError:
                        relative_score = "unknown"
                    scaling_data[thread_label] = relative_score

            testcategory, benchmark, test_name = key[2], key[3], key[4]
            if testcategory not in result["workload"]:
                result["workload"][testcategory] = {}
            if benchmark not in result["workload"][testcategory]:
                result["workload"][testcategory][benchmark] = {}

            result["workload"][testcategory][benchmark][test_name] = scaling_data
            has_scaling_data = True

        if has_scaling_data:
            results.append(result)

    return results


def get_csp_from_machinename(machinename: str, machine_data: Dict[str, Any]) -> str:
    """Get CSP from machine data or infer from machinename."""
    csp = machine_data.get("CSP", "unknown")
    if csp != "unknown":
        return csp

    # Infer from machinename patterns
    machinename_lower = machinename.lower()
    if any(x in machinename_lower for x in ["m8g", "m8a", "m8i", "m7i", "i7ie", "t3"]):
        return "AWS"
    if any(x in machinename_lower for x in ["c4a", "c4d", "c4-", "e2-"]):
        return "GCP"
    if "vm.standard" in machinename_lower or "flex" in machinename_lower:
        return "OCI"

    return "unknown"


def is_arm64_reference(machinename: str, csp: str) -> bool:
    """
    Check if machinename is an arm64 reference instance for the given CSP.
    Reference instances (partial match):
    - AWS: "m8g"
    - GCP: "c4a"
    - OCI: "A1.Flex"
    """
    machinename_lower = machinename.lower()

    if csp == "AWS":
        return "m8g" in machinename_lower
    elif csp == "GCP":
        return "c4a" in machinename_lower
    elif csp == "OCI":
        return "a1.flex" in machinename_lower

    return False


def csp_instance_comparison(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Generate CSP instance comparison metrics.
    Compares cost efficiency across instances within the same CSP.
    Reference: arm64 instance cost = 100

    Output structure (list of):
    {
        description: "CSP instance comparison",
        header: {
            "machinename": <machinename>,
            "os": <os>,
            "csp": <csp>
        },
        workload: {
            <testcategory>: {
                <benchmark>: {
                    <test_name>: <benchmark_score>
                }
            }
        }
    }
    """
    results = []
    workloads = extract_workloads(data)

    # Group by CSP
    csp_machines: Dict[str, set] = {}
    for w in workloads:
        machinename = w["machinename"]
        machine_data = w["machine_data"]
        csp = get_csp_from_machinename(machinename, machine_data)

        if csp == "unknown":
            continue

        if csp not in csp_machines:
            csp_machines[csp] = set()
        csp_machines[csp].add((machinename, w["os"]))

    # For each CSP, generate comparison
    for csp, machine_os_set in csp_machines.items():
        # Find arm64 reference machine
        ref_machines = [(m, o) for m, o in machine_os_set if is_arm64_reference(m, csp)]

        if not ref_machines:
            raise CSPComparisonError(f"No arm64 reference instance found for CSP {csp}")

        # Group workloads by test
        test_workloads: Dict[Tuple, Dict[str, Any]] = {}
        for w in workloads:
            if get_csp_from_machinename(w["machinename"], w["machine_data"]) != csp:
                continue

            key = (w["testcategory"], w["benchmark"], w["test_name"], w["os"])
            if key not in test_workloads:
                test_workloads[key] = {}

            machinename = w["machinename"]
            test_data = w["test_data"]
            machine_data = w["machine_data"]

            # Calculate cost_score
            hourly_rate = get_hourly_rate(machine_data)
            time_val = test_data.get("time")

            cost_score = None
            if hourly_rate is not None:
                if time_val is not None and time_val != "N/A" and time_val != 0:
                    try:
                        time_float = float(time_val) if not isinstance(time_val, (int, float)) else time_val
                        cost_score = (time_float / 3600) * hourly_rate
                    except (ValueError, TypeError):
                        pass

            test_workloads[key][machinename] = cost_score

        # Generate results for each machine in this CSP
        for machinename, os_name in sorted(machine_os_set):
            result = {
                "description": "CSP instance comparison",
                "header": {
                    "machinename": machinename,
                    "os": os_name,
                    "csp": csp
                },
                "workload": {}
            }

            has_data = False

            for key, machine_costs in test_workloads.items():
                testcategory, benchmark, test_name, test_os = key

                if test_os != os_name:
                    continue

                if machinename not in machine_costs:
                    continue

                # Find reference cost (arm64 instance)
                ref_cost = None
                for ref_m, ref_o in ref_machines:
                    if ref_o == os_name and ref_m in machine_costs:
                        ref_cost = machine_costs[ref_m]
                        break

                if ref_cost is None or ref_cost == 0:
                    print(f"Warning: Cannot calculate reference cost for {csp}/{testcategory}/{benchmark}/{test_name}",
                          file=sys.stderr)
                    score: Any = "unknown"
                elif machine_costs[machinename] is None:
                    score = "unknown"
                elif machine_costs[machinename] == 0:
                    print(f"Warning: Cost is 0 for {machinename}/{testcategory}/{benchmark}/{test_name}",
                          file=sys.stderr)
                    score = "unknown"
                else:
                    # Calculate relative score (reference = 100)
                    # Lower cost is better, so: ref_cost / current_cost * 100
                    score = round((ref_cost / machine_costs[machinename]) * 100, 2)

                # Build nested structure
                if testcategory not in result["workload"]:
                    result["workload"][testcategory] = {}
                if benchmark not in result["workload"][testcategory]:
                    result["workload"][testcategory][benchmark] = {}

                result["workload"][testcategory][benchmark][test_name] = score
                has_data = True

            if has_data:
                results.append(result)

    return results


def main():
    parser = argparse.ArgumentParser(
        description='Analyze one_big_json.json and generate performance comparisons'
    )
    parser.add_argument('--input', type=str,
                        default=str(Path.cwd() / 'one_big_json.json'),
                        help='Path to one_big_json.json (default: ${PWD}/one_big_json.json)')
    parser.add_argument('--perf', action='store_true',
                        help='Generate performance comparison only')
    parser.add_argument('--cost', action='store_true',
                        help='Generate cost comparison only')
    parser.add_argument('--th', action='store_true',
                        help='Generate thread scaling comparison only')
    parser.add_argument('--csp', action='store_true',
                        help='Generate CSP instance comparison only')
    parser.add_argument('--all', action='store_true',
                        help='Generate all comparisons (default if no option specified)')

    args = parser.parse_args()

    # Validate script syntax
    if not validate_script_syntax():
        sys.exit(1)

    # If no specific option, default to --all
    if not (args.perf or args.cost or args.th or args.csp or args.all):
        args.all = True

    # Load data
    input_path = Path(args.input)
    data = load_data(input_path)

    if data is None:
        sys.exit(1)

    # Generate outputs with generation log
    output = get_generation_log()

    if args.perf or args.all:
        print("Generating performance comparison...", file=sys.stderr)
        output["performance_comparison"] = performance_comparison(data)

    if args.cost or args.all:
        print("Generating cost comparison...", file=sys.stderr)
        output["cost_comparison"] = cost_comparison(data)

    if args.th or args.all:
        print("Generating thread scaling comparison...", file=sys.stderr)
        try:
            thread_results = thread_scaling_comparison(data)
        except ThreadScalingError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if thread_results:
            output["thread_scaling_comparison"] = thread_results

    if args.csp or args.all:
        print("Generating CSP instance comparison...", file=sys.stderr)
        try:
            csp_results = csp_instance_comparison(data)
        except CSPComparisonError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        if csp_results:
            output["csp_instance_comparison"] = csp_results

    # Output to stdout
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
