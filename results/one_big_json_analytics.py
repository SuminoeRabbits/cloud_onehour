#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
one_big_json_analytics.py

Version: v1.1.0
Generated: 2026-01-22

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
VERSION = "v1.1.0"


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
    - If "values" exists and is not "N/A", use it as benchmark_score (higher is better)
    - Otherwise use "time" as benchmark_score (lower is better)
    - unit is extracted from test_data["unit"] field
    """
    values = test_data.get("values")
    time_val = test_data.get("time")
    unit = test_data.get("unit", "N/A")
    if unit is None or unit == "":
        unit = "N/A"

    # Check if values exists and is valid
    if values is not None and values != "N/A":
        try:
            benchmark_score = float(values) if not isinstance(values, (int, float)) else values
            time_score = time_val if time_val is not None and time_val != "N/A" else "unknown"
            if isinstance(time_score, (int, float)) and time_score == 0:
                time_score = "unknown"
            return (benchmark_score, time_score, True, unit)
        except (ValueError, TypeError):
            pass

    # Fall back to time
    if time_val is not None and time_val != "N/A":
        try:
            time_score = float(time_val) if not isinstance(time_val, (int, float)) else time_val
            if time_score == 0:
                return ("unknown", "unknown", False, unit)
            return (time_score, time_score, False, unit)
        except (ValueError, TypeError):
            pass

    return ("unknown", "unknown", False, unit)


def get_hourly_rate(machine_data: Dict[str, Any]) -> Any:
    """Return hourly rate from input data (preferred: hourly_rate)."""
    hourly_rate = machine_data.get("hourly_rate")
    if hourly_rate is not None:
        return hourly_rate
    return machine_data.get("cost_hour[730h-mo]", 0)


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
                        <os>: {
                            "thread": {
                                "<N>": {
                                    <machinename>: {
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
        if hourly_rate is not None and hourly_rate > 0:
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
    Generate Thread scaling comparison metrics.
    For each machine, shows how benchmark score scales with thread count.
    Only includes workloads with more than one thread count.
    Reference: max thread count = 100

    Output structure (list of):
    {
        description: "Thread scaling comparison",
        header: {
            "machinename": <machinename>,
            "os": <os>
        },
        workload: {
            <testcategory>: {
                <benchmark>: {
                    <test_name>: {
                        "unit": <unit>,
                        "<N>": <benchmark_score>
                    }
                }
            }
        }
    }
    """
    results = []

    workloads = extract_workloads(data)

    # Group by machinename -> os -> testcategory -> benchmark -> test_name -> threads
    # Also store unit for each test_name
    machine_workloads: Dict[Tuple, Dict[str, Any]] = {}
    machine_workloads_unit: Dict[Tuple, str] = {}
    for w in workloads:
        key = (w["machinename"], w["os"], w["testcategory"], w["benchmark"], w["test_name"])
        if key not in machine_workloads:
            machine_workloads[key] = {}

        thread = w["thread"]
        benchmark_score, _, _, unit = get_benchmark_score(w["test_data"])
        machine_workloads[key][thread] = benchmark_score
        machine_workloads_unit[key] = unit

    # Generate results for each machine/os combination
    machine_os_pairs = set()
    for w in workloads:
        machine_os_pairs.add((w["machinename"], w["os"]))

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

        for key, thread_scores in machine_workloads.items():
            if key[0] != machinename or key[1] != os_name:
                continue

            # Skip if only one thread count
            if len(thread_scores) <= 1:
                continue

            testcategory, benchmark, test_name = key[2], key[3], key[4]
            unit = machine_workloads_unit.get(key, "N/A")

            # Find max thread count for reference
            valid_threads = []
            for t, score in thread_scores.items():
                if score != "unknown":
                    try:
                        valid_threads.append((int(t), score))
                    except ValueError:
                        continue

            if not valid_threads:
                continue

            max_thread = max(valid_threads, key=lambda x: x[0])
            ref_score = max_thread[1]

            if ref_score == 0:
                print(f"Warning: Reference score is 0 for {machinename}/{os_name}/{testcategory}/{benchmark}/{test_name}",
                      file=sys.stderr)
                continue

            # Calculate relative scores (reference = 100)
            scaling_data: Dict[str, Any] = {"unit": unit}
            for thread, score in thread_scores.items():
                if score == "unknown":
                    scaling_data[thread] = "unknown"
                else:
                    try:
                        relative_score = round((float(score) / float(ref_score)) * 100, 2)
                        scaling_data[thread] = relative_score
                    except (ValueError, TypeError, ZeroDivisionError):
                        scaling_data[thread] = "unknown"

            # Build nested structure
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
            print(f"Warning: No arm64 reference instance found for CSP {csp}", file=sys.stderr)
            continue

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
            if hourly_rate is not None and hourly_rate > 0:
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
        thread_results = thread_scaling_comparison(data)
        if thread_results:
            output["thread_scaling_comparison"] = thread_results

    if args.csp or args.all:
        print("Generating CSP instance comparison...", file=sys.stderr)
        csp_results = csp_instance_comparison(data)
        if csp_results:
            output["csp_instance_comparison"] = csp_results

    # Output to stdout
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
