#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
one_big_json_analytics.py

Version: v1.3.0
Generated: 2026-02-16

This script analyzes one_big_json.json and generates refined comparisons:
1. Performance comparison - OS-separated leaderboard (Processor-gen aware)
2. Cost comparison - OS-separated economic ranking (Processor-gen aware)
3. Thread scaling comparison - Workload-centric scaling curves across machines
4. CSP instance comparison - Trend analysis (Arch crossover/scaling efficiency)

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
VERSION = "v1.5.0"


class AnalyticsError(RuntimeError):
    """Base class for analytics errors."""


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
        print(
            "Error: Input JSON file was not found.\n"
            f"  path: {file_path}\n"
            "  hint: specify an existing file with --input <path/to/one_big_json.json>\n"
            "  hint: if you only have raw result files, create JSON first with:\n"
            "        ./make_one_big_json.py --dir <results_dir> --output <path/to/one_big_json.json>",
            file=sys.stderr,
        )
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
                                "cpu_name": machine_data.get("cpu_name", "N/A"),
                                "cpu_isa": machine_data.get("cpu_isa", "N/A"),
                                "os": os_name,
                                "testcategory": testcategory,
                                "benchmark": benchmark,
                                "thread": thread,
                                "test_name": test_name,
                                "test_data": test_data,
                                "machine_data": machine_data
                            })

    return workloads


def infer_arch_from_text(value: str) -> str:
    """Infer architecture from a free-form string (machinename/label/cpu_isa)."""
    text = (value or "").lower()
    if any(token in text for token in ["arm64", "aarch64", "armv8", "armv9", "neoverse", "graviton", "axion"]):
        return "arm64"
    if any(token in text for token in ["x86_64", "amd64", "intel", "epyc", "xeon"]):
        return "x86_64"
    return "x86_64"


def should_exclude_arch(arch: str, no_arm64: bool, no_amd64: bool) -> bool:
    if arch == "arm64" and no_arm64:
        return True
    if arch == "x86_64" and no_amd64:
        return True
    return False


def is_higher_better_from_unit(unit: str) -> bool:
    """Infer higher-is-better from unit text using same rule as performance scoring."""
    unit_lower = str(unit or "").lower()
    is_rate = "per second" in unit_lower
    is_time_unit = ("microsecond" in unit_lower or "second" in unit_lower) and not is_rate
    return not is_time_unit


def postprocess_output_by_arch(output: Dict[str, Any], no_arm64: bool, no_amd64: bool) -> Dict[str, Any]:
    """Apply architecture exclusion as a post-process on generated JSON."""
    if not no_arm64 and not no_amd64:
        return output

    # performance_comparison: leaderboard filtering
    perf = output.get("performance_comparison", {}).get("workload", {})
    for tc in list(perf.keys()):
        for bm in list(perf[tc].keys()):
            for tn in list(perf[tc][bm].keys()):
                os_map = perf[tc][bm][tn].get("os", {})
                for os_name in list(os_map.keys()):
                    th_map = os_map[os_name].get("thread", {})
                    for th in list(th_map.keys()):
                        lb = th_map[th].get("leaderboard", [])
                        kept = []
                        for ent in lb:
                            arch = infer_arch_from_text(ent.get("cpu_isa", "") + " " + ent.get("machinename", ""))
                            if not should_exclude_arch(arch, no_arm64, no_amd64):
                                kept.append(ent)
                        if kept:
                            hib = is_higher_better_from_unit(th_map[th].get("unit", ""))
                            best_score = kept[0].get("score", 0)
                            reranked = []
                            for idx, item in enumerate(kept, start=1):
                                score = item.get("score", 0)
                                rel = 0.0
                                if best_score not in (0, None) and score not in (0, None):
                                    rel = round((score / best_score), 2) if hib else round((best_score / score), 2)
                                new_item = dict(item)
                                new_item["rank"] = idx
                                new_item["relative_performance"] = rel
                                reranked.append(new_item)
                            th_map[th]["leaderboard"] = reranked
                        else:
                            del th_map[th]
                    if not th_map:
                        del os_map[os_name]
                if not os_map:
                    del perf[tc][bm][tn]
            if not perf[tc][bm]:
                del perf[tc][bm]
        if not perf[tc]:
            del perf[tc]

    # cost_comparison: ranking filtering
    cost = output.get("cost_comparison", {}).get("workload", {})
    for tc in list(cost.keys()):
        for bm in list(cost[tc].keys()):
            for tn in list(cost[tc][bm].keys()):
                os_map = cost[tc][bm][tn].get("os", {})
                for os_name in list(os_map.keys()):
                    th_map = os_map[os_name].get("thread", {})
                    for th in list(th_map.keys()):
                        rk = th_map[th].get("ranking", [])
                        kept = []
                        for ent in rk:
                            arch = infer_arch_from_text(ent.get("cpu_isa", "") + " " + ent.get("machinename", ""))
                            if not should_exclude_arch(arch, no_arm64, no_amd64):
                                kept.append(ent)
                        if kept:
                            best_eff = kept[0].get("efficiency_score", 0)
                            reranked = []
                            for idx, item in enumerate(kept, start=1):
                                eff = item.get("efficiency_score", 0)
                                rel = 0.0
                                if best_eff not in (0, None) and eff not in (0, None):
                                    rel = round(eff / best_eff, 2)
                                new_item = dict(item)
                                new_item["rank"] = idx
                                new_item["relative_cost_efficiency"] = rel
                                reranked.append(new_item)
                            th_map[th]["ranking"] = reranked
                        else:
                            del th_map[th]
                    if not th_map:
                        del os_map[os_name]
                if not os_map:
                    del cost[tc][bm][tn]
            if not cost[tc][bm]:
                del cost[tc][bm]
        if not cost[tc]:
            del cost[tc]

    # thread_scaling_comparison: curves filtering by machine label
    th_cmp = output.get("thread_scaling_comparison", {}).get("workload", {})
    for tc in list(th_cmp.keys()):
        for bm in list(th_cmp[tc].keys()):
            for tn in list(th_cmp[tc][bm].keys()):
                curves = th_cmp[tc][bm][tn].get("curves", {})
                kept_curves = {}
                for machine_label, points in curves.items():
                    arch = infer_arch_from_text(machine_label)
                    if not should_exclude_arch(arch, no_arm64, no_amd64):
                        kept_curves[machine_label] = points
                if kept_curves:
                    th_cmp[tc][bm][tn]["curves"] = kept_curves
                else:
                    del th_cmp[tc][bm][tn]
            if not th_cmp[tc][bm]:
                del th_cmp[tc][bm]
        if not th_cmp[tc]:
            del th_cmp[tc]

    # csp_instance_comparison: trends filtering; remove if baseline excluded/empty
    csp = output.get("csp_instance_comparison", {}).get("workload", {})
    for tc in list(csp.keys()):
        for bm in list(csp[tc].keys()):
            for tn in list(csp[tc][bm].keys()):
                entry = csp[tc][bm][tn]
                baseline = entry.get("baseline", {})
                baseline_arch = infer_arch_from_text(baseline.get("arch", "") + " " + baseline.get("machinename", ""))
                if should_exclude_arch(baseline_arch, no_arm64, no_amd64):
                    del csp[tc][bm][tn]
                    continue

                trends = entry.get("trends", {})
                kept_trends = {}
                for label, tval in trends.items():
                    arch = infer_arch_from_text(label)
                    if not should_exclude_arch(arch, no_arm64, no_amd64):
                        kept_trends[label] = tval
                if kept_trends:
                    entry["trends"] = kept_trends
                else:
                    del csp[tc][bm][tn]
            if not csp[tc][bm]:
                del csp[tc][bm]
        if not csp[tc]:
            del csp[tc]

    return output


def get_performance_score(test_data: Dict[str, Any]) -> Tuple[Any, bool]:
    """
    Get performance score. Preference: values > time.
    Returns (score, higher_is_better).
    Per README_analytics.md, if unit is time-based (Seconds, Microseconds), lower is better.
    """
    values = test_data.get("values")
    unit = str(test_data.get("unit", "")).lower()
    
    
    # If unit is Microseconds or Seconds, lower is better.
    # EXCEPTION: "per second" (rate) is higher is better.
    unit_lower = unit.lower()
    is_rate = "per second" in unit_lower
    is_time_unit = ("microsecond" in unit_lower or "second" in unit_lower) and not is_rate
    
    if values not in (None, "N/A"):
        try:
            score = float(values)
            # Default for values is higher-is-better, UNLESS it's a time unit
            hib = not is_time_unit
            return score, hib
        except (ValueError, TypeError):
            pass

    time_val = parse_time_value(test_data)
    if time_val is not None:
        return time_val, False

    return None, False


def get_hourly_rate(machine_data: Dict[str, Any]) -> Optional[float]:
    """Return positive hourly_rate defined in input data."""
    # Look for diverse keys if necessary, but spec says "cost_hour[730h-mo]" or similar
    # The current one_big_json.json mapping uses cost_hour[730h-mo]
    hourly_rate = machine_data.get("cost_hour[730h-mo]")
    if hourly_rate in (None, "N/A"):
        return None

    try:
        rate = float(hourly_rate)
        return rate if rate > 0 else None
    except (ValueError, TypeError):
        return None


def performance_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Section 1: Performance comparison (OS-separated leaderboard)
    """
    result = {
        "description": "Performance comparison leaderboard by OS",
        "workload": {}
    }

    workloads = extract_workloads(data)

    # Grouping
    grouped: Dict[tuple, List[Dict]] = {}
    for w in workloads:
        key = (w["testcategory"], w["benchmark"], w["test_name"], w["os"], w["thread"])
        score, hib = get_performance_score(w["test_data"])
        if score is None:
            continue
        
        w["score_internal"] = score
        w["higher_is_better"] = hib
        grouped.setdefault(key, []).append(w)

    for (tc, bm, tn, os_name, thread), entries in grouped.items():
        hib = entries[0]["higher_is_better"]
        # Sort by score
        sorted_entries = sorted(entries, key=lambda x: x["score_internal"], reverse=hib)
        
        if not sorted_entries:
            continue

        best_score = sorted_entries[0]["score_internal"]
        leaderboard = []
        for i, ent in enumerate(sorted_entries):
            score = ent["score_internal"]
            rel_perf = round(best_score / score, 2) if not hib else round(score / best_score, 2)
            
            leaderboard.append({
                "rank": i + 1,
                "machinename": ent["machinename"],
                "cpu_name": ent["cpu_name"],
                "cpu_isa": ent["cpu_isa"],
                "score": score,
                "relative_performance": rel_perf
            })

        # Nesting
        result["workload"].setdefault(tc, {}).setdefault(bm, {}).setdefault(tn, {}).setdefault("os", {}).setdefault(os_name, {}).setdefault("thread", {})[thread] = {
            "unit": ent["test_data"].get("unit", "N/A"),
            "leaderboard": leaderboard
        }

    return result


def cost_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Section 2: Cost comparison (OS-separated economic ranking)
    """
    result = {
        "description": "Cost efficiency ranking by OS",
        "workload": {}
    }

    workloads = extract_workloads(data)
    
    grouped: Dict[tuple, List[Dict]] = {}
    for w in workloads:
        efficiency = get_economic_efficiency(w)
        if efficiency is None:
            continue
            
        w["efficiency"] = efficiency
        
        key = (w["testcategory"], w["benchmark"], w["test_name"], w["os"], w["thread"])
        grouped.setdefault(key, []).append(w)

    for (tc, bm, tn, os_name, thread), entries in grouped.items():
        # Sort by efficiency (higher is better rank)
        sorted_entries = sorted(entries, key=lambda x: x["efficiency"], reverse=True)
        if not sorted_entries:
            continue
            
        best_efficiency = sorted_entries[0]["efficiency"]
        ranking = []
        for i, ent in enumerate(sorted_entries):
            # relative_cost_efficiency: current_eff / best_eff
            # Result is 1.0 for the best, and < 1.0 for others
            rel_efficiency = round(ent["efficiency"] / best_efficiency, 2) if best_efficiency > 0 else 0.0
            
            ranking.append({
                "rank": i + 1,
                "machinename": ent["machinename"],
                "cpu_name": ent["cpu_name"],
                "cpu_isa": ent["cpu_isa"],
                "efficiency_score": round(ent["efficiency"], 6),
                "relative_cost_efficiency": rel_efficiency
            })

        result["workload"].setdefault(tc, {}).setdefault(bm, {}).setdefault(tn, {}).setdefault("os", {}).setdefault(os_name, {}).setdefault("thread", {})[thread] = {
            "unit": "Efficiency (Throughput/USD)",
            "ranking": ranking
        }

    return result


def thread_scaling_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Section 3: Thread scaling comparison (Workload-centric curves across machines)
    """
    result = {
        "description": "Thread scaling comparison by workload",
        "workload": {}
    }

    workloads = extract_workloads(data)
    
    # Target structure: Workload -> Machine -> Thread -> Score/HIB
    curves_data: Dict[tuple, Dict[str, Dict[str, float]]] = {}
    hib_map: Dict[tuple, bool] = {}
    unit_map: Dict[tuple, str] = {}

    for w in workloads:
        key = (w["testcategory"], w["benchmark"], w["test_name"])
        score, hib = get_performance_score(w["test_data"])
        if score is None:
            continue
            
        try:
            th_num = int(w["thread"])
        except (ValueError, TypeError):
            continue
            
        machine_label = f"{w['machinename']} ({w['cpu_isa']})"
        curves_data.setdefault(key, {}).setdefault(machine_label, {})[str(th_num)] = score
        hib_map[key] = hib
        unit_map[key] = w["test_data"].get("unit", "N/A")

    for key, machine_curves in curves_data.items():
        tc, bm, tn = key
        hib = hib_map[key]
        final_curves = {}
        
        for m_label, th_scores in machine_curves.items():
            if len(th_scores) < 2:
                continue
            
            # Find max thread baseline
            max_th = str(max(int(t) for t in th_scores.keys()))
            max_val = th_scores[max_th]
            
            if max_val == 0:
                continue
                
            sorted_th = sorted(th_scores.keys(), key=lambda x: int(x))
            normalized = {}
            for t in sorted_th:
                val = th_scores[t]
                if val == 0:
                    normalized[t] = 0.0
                    continue
                # If higher is better: (current / max) * 100
                # If lower is better (time): (max / current) * 100
                if hib:
                    normalized[t] = round((val / max_val) * 100, 2)
                else:
                    normalized[t] = round((max_val / val) * 100, 2)
            
            final_curves[m_label] = normalized

        if final_curves:
            result["workload"].setdefault(tc, {}).setdefault(bm, {})[tn] = {
                "unit": unit_map[key],
                "curves": final_curves
            }

    return result


def get_arch_from_machinename(machinename: str, machine_data: Dict[str, Any]) -> str:
    """Infer architecture from machine data or name."""
    isa = str(machine_data.get("cpu_isa", "")).lower()
    if "arm" in isa or "aarch64" in isa:
        return "arm64"
    if "x86" in isa or "amd64" in isa:
        return "x86_64"
    
    # Fallback to name patterns
    name = machinename.lower()
    if any(p in name for p in ["m8g", "m7g", "c4a", "t2a", "a1.flex", "a2.flex"]):
        return "arm64"
    return "x86_64"


def is_arm64_baseline(machinename: str) -> bool:
    """Check if machinename is an arm64 reference instance."""
    name = machinename.lower()
    # AWS: m8g, GCP: c4a, OCI: a1.flex (partial matches)
    return any(p in name for p in ["m8g", "c4a", "a1.flex"])


def get_economic_efficiency(w: Dict[str, Any]) -> Optional[float]:
    """
    Calculate Economic Efficiency: Throughput per Hourly Rate.
    Higher is always better.
    """
    score, hib = get_performance_score(w["test_data"])
    rate = get_hourly_rate(w["machine_data"])
    
    if score is None or rate is None or score <= 0 or rate <= 0:
        return None
        
    # Convert to "Throughput" (higher is better)
    throughput = score if hib else (1.0 / score)
    
    return throughput / rate


def csp_instance_comparison(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Section 4: CSP instance comparison (Trend analysis/Arch crossover)
    """
    result = {
        "description": "CSP instance comparison (Trend Analysis)",
        "workload": {}
    }

    workloads = extract_workloads(data)
    
    # Group by Workload -> CSP
    csp_map = {}
    for w in workloads:
        mname = w["machinename"].lower()
        if mname.startswith("aws"): csp_map[w["machinename"]] = "AWS"
        elif mname.startswith("gcp"): csp_map[w["machinename"]] = "GCP"
        elif mname.startswith("oci"): csp_map[w["machinename"]] = "OCI"
        elif "m8" in mname or "m7" in mname: csp_map[w["machinename"]] = "AWS"
        else: csp_map[w["machinename"]] = "Local/Other"

    # Workload -> CSP -> Machine -> Thread -> Efficiency
    comparison_data: Dict[tuple, Dict[str, Dict[str, Dict[str, float]]]] = {}

    for w in workloads:
        key = (w["testcategory"], w["benchmark"], w["test_name"])
        csp = csp_map.get(w["machinename"], "Unknown")
        if csp == "Unknown": continue
        
        efficiency = get_economic_efficiency(w)
        if efficiency is None:
            continue
            
        th_label = str(w["thread"])
        comparison_data.setdefault(key, {}).setdefault(csp, {}).setdefault(w["machinename"], {})[th_label] = efficiency

    for key, csp_machines in comparison_data.items():
        tc, bm, tn = key
        
        for csp, machines in csp_machines.items():
            # Find baseline (arm64)
            baseline_machine = None
            for mname in machines:
                if is_arm64_baseline(mname):
                    baseline_machine = mname
                    break
            
            if not baseline_machine:
                continue
                
            baseline_effs = machines[baseline_machine]
            trends = {}
            
            for mname, effs in machines.items():
                if mname == baseline_machine:
                    continue
                
                # Check arch
                arch = "x86_64" if "arm" not in mname.lower() else "arm64"
                label = f"{mname} ({arch})"
                
                scores = {}
                for th, eff in effs.items():
                    if th in baseline_effs:
                        # Relative Efficiency: (current_eff / baseline_eff) * 100
                        # Result: > 100 means more efficient than Arm
                        scores[th] = round((eff / baseline_effs[th]) * 100, 2)
                
                if not scores:
                    continue
                
                # Insight calculation
                sorted_th = sorted(scores.keys(), key=lambda x: int(x))
                max_adv_th = max(scores, key=lambda k: scores[k])
                
                # Find crossover_point (where it dips below 100)
                # Note: if it starts below 100, crossover is N/A or thread 1 depending on perspective.
                # Here we stick to "when it crosses 100 boundary"
                crossover = "N/A"
                if len(sorted_th) > 1:
                    for i in range(len(sorted_th) - 1):
                        curr_th = sorted_th[i]
                        next_th = sorted_th[i+1]
                        # Check if it crosses 100 in either direction
                        if (scores[curr_th] >= 100 and scores[next_th] < 100) or \
                           (scores[curr_th] < 100 and scores[next_th] >= 100):
                            crossover = f"between {curr_th} and {next_th}"
                            break

                # Scaling efficiency trend
                trend_desc = "consistent"
                if len(scores) > 1:
                    first_score = scores[sorted_th[0]]
                    last_score = scores[sorted_th[-1]]
                    if last_score < first_score * 0.95:
                        trend_desc = "declining_relative_to_arm"
                    elif last_score > first_score * 1.05:
                        trend_desc = "improving_relative_to_arm"

                trends[label] = {
                    "scores": scores,
                    "insight": {
                        "max_advantage": {"thread": max_adv_th, "score": scores[max_adv_th]},
                        "crossover_point": crossover,
                        "scaling_efficiency": trend_desc
                    }
                }

            if trends:
                result["workload"].setdefault(tc, {}).setdefault(bm, {})[tn] = {
                    "baseline": {
                        "machinename": baseline_machine,
                        "arch": "arm64",
                        "csp": csp
                    },
                    "trends": trends
                }

    return result


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
    parser.add_argument('--no_arm64', action='store_true',
                        help='Exclude arm64 instances from output JSON (post-process stage)')
    parser.add_argument('--no_amd64', action='store_true',
                        help='Exclude amd64/x86_64 instances from output JSON (post-process stage)')
    parser.add_argument('--all', action='store_true',
                        help='Generate all comparisons')
    parser.add_argument('--output', type=str,
                        help='Output file path (default: ${PWD}/one_big_json_analytics_<type>.json)')

    args = parser.parse_args()

    # Validate script syntax
    if not validate_script_syntax():
        sys.exit(1)

    # If no specific option, default to --perf
    if not (args.perf or args.cost or args.th or args.csp or args.all):
        args.perf = True

    # Load data
    input_path = Path(args.input)
    data = load_data(input_path)

    if data is None:
        sys.exit(1)

    # Generate outputs with generation log
    output = get_generation_log()

    if args.perf or args.all:
        output["performance_comparison"] = performance_comparison(data)

    if args.cost or args.all:
        output["cost_comparison"] = cost_comparison(data)

    if args.th or args.all:
        output["thread_scaling_comparison"] = thread_scaling_comparison(data)

    if args.csp or args.all:
        output["csp_instance_comparison"] = csp_instance_comparison(data)

    # Post-process architecture filtering on generated JSON only
    output = postprocess_output_by_arch(output, args.no_arm64, args.no_amd64)

    selected_modes = [
        flag_name for enabled, flag_name in [
            (args.perf, "perf"),
            (args.cost, "cost"),
            (args.th, "th"),
            (args.csp, "csp"),
        ] if enabled
    ]

    if args.all:
        output_type = "all"
    elif len(selected_modes) == 1:
        output_type = selected_modes[0]
    else:
        output_type = "mixed"

    arch_suffix = ""
    if args.no_arm64 and args.no_amd64:
        arch_suffix = "_no_arm64_no_amd64"
    elif args.no_arm64:
        arch_suffix = "_no_arm64"
    elif args.no_amd64:
        arch_suffix = "_no_amd64"

    default_output = Path.cwd() / f"one_big_json_analytics_{output_type}{arch_suffix}.json"
    output_path = Path(args.output) if args.output else default_output
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding='utf-8')

    # Keep stdout output for CLI compatibility.
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
