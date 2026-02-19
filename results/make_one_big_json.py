#!/usr/bin/env python3
"""
make_one_big_json.py

Generates one_big_json.json from results directory structure.
Based on README_results.md specification.

Version info: v2.0.1 (Updated: 2026-02-19)

Benchmark parsing is delegated to json_parser/json_parser_<benchmark>.py modules.
Each module exports _collect_thread_payload(benchmark_dir, thread_num, cost_hour)
that returns the per-thread payload dict.

Usage:
    # Build from directories:
    python3 make_one_big_json.py [--dir PATH] [--output PATH]

    # Merge multiple JSON files:
    python3 make_one_big_json.py --merge FILE1.json FILE2.json ... --output OUTPUT.json
"""

import json
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional
import argparse
import ast
import subprocess
import re
from datetime import datetime
import socket
import glob
from decimal import Decimal, ROUND_HALF_UP


# Script version - Format: v<major>.<minor>.<patch>
SCRIPT_VERSION = "v2.0.1"

CLOUD_INSTANCES_FILE = Path(__file__).resolve().parent.parent / "cloud_instances.json"

LOOKUP_TARGETS = [
    {
        "match": ["rpi5"],
        "static_info": {
            "CSP": "local",
            "total_vcpu": 4,
            "cpu_name": "Cortex-A76",
            "cpu_isa": "Armv8.2-A",
            "cost_hour[730h-mo]": 0.0,
        },
    },
    {
        "match": ["t3", "medium"],
        "provider": "AWS",
        "type": "t3.medium",
        "csp": "AWS",
        "cpu_name": "Intel Xeon Platinum (8000 series)",
        "cpu_isa": "x86-64 (AVX-512)",
        "default_total_vcpu": 2,
        "default_cost": 0.0183,
    },
    {
        "match": ["m7g", "2xlarge"],
        "provider": "AWS",
        "type": "m7g.2xlarge",
        "csp": "AWS",
        "cpu_name": "Neoverse-V1 (Graviton3)",
        "cpu_isa": "Armv8.4-A (SVE-256)",
        "default_total_vcpu": 8,
        "default_cost": 0.4413,
    },
    {
        "match": ["m7g", "4xlarge"],
        "provider": "AWS",
        "type": "m7g.4xlarge",
        "csp": "AWS",
        "cpu_name": "Neoverse-V1 (Graviton3)",
        "cpu_isa": "Armv8.4-A (SVE-256)",
        "default_total_vcpu": 16,
        "default_cost": 0.8629,
    },
    {
        "match": ["m7i", "2xlarge"],
        "provider": "AWS",
        "type": "m7i.2xlarge",
        "csp": "AWS",
        "cpu_name": "Intel Xeon 4 (4th Sapphire Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 8,
        "default_cost": 0.5405,
    },
    {
        "match": ["m7i", "4xlarge"],
        "provider": "AWS",
        "type": "m7i.4xlarge",
        "csp": "AWS",
        "cpu_name": "Intel Xeon 4 (4th Sapphire Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 16,
        "default_cost": 1.0613,
    },
    {
        "match": ["m8a", "2xlarge"],
        "provider": "AWS",
        "type": "m8a.2xlarge",
        "csp": "AWS",
        "cpu_name": "AMD EPYC 9R45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 8,
        "default_cost": 0.64858,
    },
    {
        "match": ["m8a", "4xlarge"],
        "provider": "AWS",
        "type": "m8a.4xlarge",
        "csp": "AWS",
        "cpu_name": "AMD EPYC 9R45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 16,
        "default_cost": 1.27746,
    },
    {
        "match": ["m8i", "2xlarge"],
        "provider": "AWS",
        "type": "m8i.2xlarge",
        "csp": "AWS",
        "cpu_name": "Intel Xeon 6 (6th Granite Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 8,
        "default_cost": 0.56654,
    },
    {
        "match": ["m8i", "4xlarge"],
        "provider": "AWS",
        "type": "m8i.4xlarge",
        "csp": "AWS",
        "cpu_name": "Intel Xeon 6 (6th Granite Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 16,
        "default_cost": 1.11338,
    },
    {
        "match": ["m8g", "2xlarge"],
        "provider": "AWS",
        "type": "m8g.2xlarge",
        "csp": "AWS",
        "cpu_name": "Neoverse-V2 (Graviton4)",
        "cpu_isa": "Armv9.0-A (SVE2-128)",
        "default_total_vcpu": 8,
        "default_cost": 0.48346,
    },
    {
        "match": ["m8g", "4xlarge"],
        "provider": "AWS",
        "type": "m8g.4xlarge",
        "csp": "AWS",
        "cpu_name": "Neoverse-V2 (Graviton4)",
        "cpu_isa": "Armv9.0-A (SVE2-128)",
        "default_total_vcpu": 16,
        "default_cost": 0.94722,
    },
    {
        "match": ["i7ie", "2xlarge"],
        "provider": "AWS",
        "type": "i7ie.2xlarge",
        "csp": "AWS",
        "cpu_name": "Intel Xeon 5 Metal(5th Emerald Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 8,
        "default_cost": 1.2433,
    },
    {
        "match": ["e2-standard-2"],
        "provider": "GCP",
        "type": "e2-standard-2",
        "csp": "GCP",
        "cpu_name": "Intel Xeon / AMD EPYC(Variable)",
        "cpu_isa": "x86-64",
        "default_total_vcpu": 2,
        "default_cost": 0.0683,
    },
    {
        "match": ["c4d-standard-8"],
        "provider": "GCP",
        "type": "c4d-standard-8",
        "csp": "GCP",
        "cpu_name": "AMD EPYC 9B45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 8,
        "default_cost": 0.4057,
    },
    {
        "match": ["c4d-standard-16"],
        "provider": "GCP",
        "type": "c4d-standard-16",
        "csp": "GCP",
        "cpu_name": "AMD EPYC 9B45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 16,
        "default_cost": 0.758,
    },
    {
        "match": ["c4-standard-8"],
        "provider": "GCP",
        "type": "c4-standard-8",
        "csp": "GCP",
        "cpu_name": "Intel Xeon Platinum 8581C (5th Emerald Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 8,
        "default_cost": 0.4231,
    },
    {
        "match": ["c4-standard-16"],
        "provider": "GCP",
        "type": "c4-standard-16",
        "csp": "GCP",
        "cpu_name": "Intel Xeon Platinum 8581C (5th Emerald Rapids)",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 16,
        "default_cost": 0.7928,
    },
    {
        "match": ["c4a-standard-8"],
        "provider": "GCP",
        "type": "c4a-standard-8",
        "csp": "GCP",
        "cpu_name": "Neoverse-V2 (Google Axion)",
        "cpu_isa": "Armv9.0-A (SVE2-128)",
        "default_total_vcpu": 8,
        "default_cost": 0.3869,
    },
    {
        "match": ["c4a-standard-16"],
        "provider": "GCP",
        "type": "c4a-standard-16",
        "csp": "GCP",
        "cpu_name": "Neoverse-V2 (Google Axion)",
        "cpu_isa": "Armv9.0-A (SVE2-128)",
        "default_total_vcpu": 16,
        "default_cost": 0.7712,
    },
    {
        "match": ["t2a-standard-8"],
        "provider": "GCP",
        "type": "t2a-standard-8",
        "csp": "GCP",
        "cpu_name": "Ampere Altra",
        "cpu_isa": "Armv8.2-A (NEON-128)",
        "default_total_vcpu": 8,
        "default_cost": 0.40654,
    },
    {
        "match": ["t2a-standard-16"],
        "provider": "GCP",
        "type": "t2a-standard-16",
        "csp": "GCP",
        "cpu_name": "Ampere Altra",
        "cpu_isa": "Armv8.2-A (NEON-128)",
        "default_total_vcpu": 16,
        "default_cost": 0.75968,
    },
    {
        "match": ["E5", "Flex"],
        "provider": "OCI",
        "type": "VM.Standard.E5.Flex",
        "csp": "OCI",
        "cpu_name": "AMD EPYC 9J14 (Zen 4 \"Genoa\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 8,
        "default_cost": 0.1727,
    },
    {
        "match": ["E6", "Flex"],
        "provider": "OCI",
        "type": "VM.Standard.E6.Flex",
        "name_contains": "vcpu-8",
        "csp": "OCI",
        "cpu_name": "AMD EPYC 9J45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 8,
        "default_cost": 0.1927,
    },
    {
        "match": ["E6", "Flex", "vcpu-16"],
        "provider": "OCI",
        "type": "VM.Standard.E6.Flex",
        "name_contains": "vcpu-16",
        "csp": "OCI",
        "cpu_name": "AMD EPYC 9J45 (Zen 5 \"Turin\")",
        "cpu_isa": "x86-64 (AMX + AVX-512)",
        "default_total_vcpu": 16,
        "default_cost": 0.368,
    },
    {
        "match": ["A1", "Flex"],
        "provider": "OCI",
        "type": "VM.Standard.A1.Flex",
        "name_contains": "vcpu-8",
        "csp": "OCI",
        "cpu_name": "Neoverse-N1(Ampere Altra)",
        "cpu_isa": "Armv8.2 (NEON-128)",
        "default_total_vcpu": 8,
        "default_cost": 0.1367,
    },
    {
        "match": ["A1", "Flex", "vcpu-16"],
        "provider": "OCI",
        "type": "VM.Standard.A1.Flex",
        "name_contains": "vcpu-16",
        "csp": "OCI",
        "cpu_name": "Neoverse-N1(Ampere Altra)",
        "cpu_isa": "Armv8.2 (NEON-128)",
        "default_total_vcpu": 16,
        "default_cost": 0.2647,
    },
    {
        "match": ["A2", "Flex"],
        "provider": "OCI",
        "type": "VM.Standard.A2.Flex",
        "name_contains": "vcpu-8",
        "csp": "OCI",
        "cpu_name": "Ampere one (v8.6A)",
        "cpu_isa": "Armv8.6 (NEON-128)",
        "default_total_vcpu": 8,
        "default_cost": 0.1287,
    },
    {
        "match": ["A2", "Flex", "vcpu-16"],
        "provider": "OCI",
        "type": "VM.Standard.A2.Flex",
        "name_contains": "vcpu-16",
        "csp": "OCI",
        "cpu_name": "Ampere one (v8.6A)",
        "cpu_isa": "Armv8.6 (NEON-128)",
        "default_total_vcpu": 16,
        "default_cost": 0.3607,
    },
    {
        "match": ["A4", "Flex"],
        "provider": "OCI",
        "type": "VM.Standard.A4.Flex",
        "csp": "OCI",
        "cpu_name": "Ampere one (v8.6A)",
        "cpu_isa": "Armv8.6 (NEON-128)",
        "default_total_vcpu": 8,
        "default_cost": 0.1503,
    },
]


# ---------------------------------------------------------------------------
# cloud_instances.json lookup helpers
# ---------------------------------------------------------------------------

def _load_cloud_instance_entries() -> List[Dict[str, Any]]:
    """Load and flatten cloud_instances.json contents."""

    if not CLOUD_INSTANCES_FILE.exists():
        print(f"Warning: {CLOUD_INSTANCES_FILE} not found. Falling back to default machine table.", file=sys.stderr)
        return []

    try:
        with open(CLOUD_INSTANCES_FILE, 'r') as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError) as exc:
        print(f"Warning: Failed to parse {CLOUD_INSTANCES_FILE}: {exc}. Falling back to default machine table.", file=sys.stderr)
        return []

    entries: List[Dict[str, Any]] = []

    for provider_key, provider_payload in data.items():
        regions = provider_payload.get("regions", {})
        for region_info in regions.values():
            for instance in region_info.get("instances", []):
                entries.append({
                    "provider": provider_key.upper(),
                    "type": instance.get("type", ""),
                    "name": instance.get("name", ""),
                    "hostname": instance.get("hostname", ""),
                    "vcpus": instance.get("vcpus", 0),
                    "cpu_cost": instance.get("cpu_cost_hour[730h-mo]", 0.0),
                    "extra_cost": instance.get("extra_150g_storage_cost_hour", 0.0) if instance.get("extra_150g_storage") else 0.0,
                })

    return entries


def _find_instance_entry(instances: List[Dict[str, Any]], target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Locate the first instance matching the target criteria."""

    type_value = target.get("type", "").lower()
    name_contains = target.get("name_contains")
    provider = target.get("provider", "").upper()

    for entry in instances:
        if type_value and entry.get("type", "").lower() != type_value:
            continue
        if provider and entry.get("provider", "").upper() != provider:
            continue
        if name_contains and name_contains.lower() not in entry.get("name", "").lower():
            continue
        return entry

    return None


def _quantize_cost(value: float) -> float:
    """Round cost to 5 decimal places using half-up rules."""

    return float(Decimal(str(value)).quantize(Decimal("0.00001"), rounding=ROUND_HALF_UP))


def _build_machine_lookup() -> List[Dict[str, Any]]:
    """Construct MACHINE_LOOKUP dynamically from cloud_instances definitions."""

    instances = _load_cloud_instance_entries()
    lookup: List[Dict[str, Any]] = []

    for target in LOOKUP_TARGETS:
        if "static_info" in target:
            lookup.append({"match": target["match"], "info": target["static_info"]})
            continue

        entry = _find_instance_entry(instances, target) if instances else None

        if entry is None:
            print(
                f"Warning: Instance definition for type '{target.get('type')}' not found in cloud_instances.json. "
                f"Using default values for {target['match']}.",
                file=sys.stderr,
            )
            cost = target.get("default_cost", 0.0)
            total_vcpu = target.get("default_total_vcpu", 0)
        else:
            cost = _quantize_cost(entry.get("cpu_cost", 0.0) + entry.get("extra_cost", 0.0))
            total_vcpu = entry.get("vcpus", target.get("default_total_vcpu", 0))

        info = {
            "CSP": target.get("csp", "unknown"),
            "total_vcpu": total_vcpu,
            "cpu_name": target.get("cpu_name", "unknown"),
            "cpu_isa": target.get("cpu_isa", "unknown"),
            "cost_hour[730h-mo]": cost,
        }

        lookup.append({"match": target["match"], "info": info})

    lookup.sort(key=lambda item: len(item["match"]), reverse=True)
    return lookup


MACHINE_LOOKUP = _build_machine_lookup()


# ---------------------------------------------------------------------------
# Machine info lookup
# ---------------------------------------------------------------------------

def get_machine_info(machinename: str) -> Dict[str, Any]:
    """
    Get machine info from Look-Up-Table based on machinename.
    Searches for partial matches (case-insensitive substrings).

    Per README_results.md specification:
    - All machine info is obtained from Look-Up-Table only
    - If not in Look-Up-Table, cost defaults to 0.0 and other fields to "unknown"
    """
    machinename_lower = machinename.lower()
    for entry in MACHINE_LOOKUP:
        if all(part.lower() in machinename_lower for part in entry["match"]):
            return entry["info"].copy()

    # Default fallback - not in lookup table at all
    print(f"Warning: Machine '{machinename}' not found in lookup table. Using defaults.", file=sys.stderr)
    return {
        "CSP": "unknown",
        "total_vcpu": 0,
        "cpu_name": "unknown",
        "cpu_isa": "unknown",
        "cost_hour[730h-mo]": 0.0
    }


# ---------------------------------------------------------------------------
# Version and generation log
# ---------------------------------------------------------------------------

def get_version_info() -> str:
    """
    Get version info in format: v<major>.<minor>.<patch>-g<git-hash>

    Returns:
        Version string like "v2.0.0-g1277d46" if in git repo,
        or "v2.0.0-gunknown" if not in git repo or git not available
    """
    try:
        git_hash = subprocess.check_output(
            ['git', 'rev-parse', '--short=7', 'HEAD'],
            stderr=subprocess.DEVNULL,
            cwd=Path(__file__).parent
        ).decode().strip()

        return f"{SCRIPT_VERSION}-g{git_hash}"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return f"{SCRIPT_VERSION}-gunknown"


def get_generation_timestamp() -> str:
    """Get current timestamp in yyyymmdd-hhmmss format."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def create_generation_log() -> Dict[str, Any]:
    """Create generation log dict for output JSON."""
    return {
        "generation log": {
            "version info": get_version_info(),
            "date": get_generation_timestamp()
        }
    }


def parse_version(version_str: str) -> Optional[tuple]:
    """Parse version string to extract major.minor.patch."""
    match = re.match(r'v(\d+)\.(\d+)\.(\d+)', version_str)
    if match:
        return tuple(map(int, match.groups()))
    return None


def check_version_compatibility(version1: str, version2: str) -> bool:
    """
    Check if two versions are compatible for merging.
    Versions are compatible when their base semantic version (vX.Y.Z)
    matches, regardless of git hash suffix.
    """
    match1 = re.match(r'^(v\d+\.\d+\.\d+)', version1)
    match2 = re.match(r'^(v\d+\.\d+\.\d+)', version2)
    if not match1 or not match2:
        return False
    return match1.group(1) == match2.group(1)


def extract_version_info(json_data: Dict[str, Any]) -> Optional[str]:
    """
    Extract version info from JSON data.
    Supports both current keys ("generation log"/"version info")
    and legacy keys ("generation_log"/"version_info").
    """
    gen_log_new = json_data.get("generation log")
    if isinstance(gen_log_new, dict):
        version_new = gen_log_new.get("version info")
        if isinstance(version_new, str) and version_new:
            return version_new

    gen_log_old = json_data.get("generation_log")
    if isinstance(gen_log_old, dict):
        version_old = gen_log_old.get("version_info")
        if isinstance(version_old, str) and version_old:
            return version_old

    return None


# ---------------------------------------------------------------------------
# Utility functions (kept for external import compatibility)
# ---------------------------------------------------------------------------

def strip_ansi_codes(text: str) -> str:
    """Remove ANSI color codes from text."""
    ansi_escape = re.compile(r'\x1b\[[0-9;]*m')
    return ansi_escape.sub('', text)


def read_log_file_safe(file_path: Path) -> str:
    """Safely read a log file with automatic ANSI code removal."""
    with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()
    return strip_ansi_codes(content)


def read_freq_file(freq_file: Path) -> Dict[str, int]:
    """
    Read frequency file and return dict with freq_0, freq_1, etc.
    File format: one frequency per line in Hz.
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
                freq_hz = int(line)
            except ValueError:
                if ':' in line:
                    try:
                        freq_mhz_str = line.split(':')[1].strip()
                        freq_mhz = float(freq_mhz_str)
                        freq_hz = int(freq_mhz * 1000)
                    except (ValueError, IndexError):
                        continue
                else:
                    continue

            freq_dict[f"freq_{idx}"] = freq_hz
            idx += 1

    return freq_dict


# ---------------------------------------------------------------------------
# Dynamic benchmark parser loading
# ---------------------------------------------------------------------------

# Cache for loaded parser modules
_parser_cache: Dict[str, Any] = {}


def _load_benchmark_parser(benchmark_name: str):
    """
    Dynamically import json_parser/json_parser_<benchmark_name>.py and return the module.
    Returns None if the parser file does not exist.
    Results are cached to avoid repeated imports.
    """
    if benchmark_name in _parser_cache:
        return _parser_cache[benchmark_name]

    parser_dir = Path(__file__).resolve().parent / "json_parser"
    parser_file = parser_dir / f"json_parser_{benchmark_name}.py"

    if not parser_file.exists():
        _parser_cache[benchmark_name] = None
        return None

    try:
        module_name = f"json_parser_{benchmark_name.replace('-', '_').replace('.', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, parser_file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _parser_cache[benchmark_name] = module
        return module
    except Exception as e:
        print(f"Warning: Failed to load parser for '{benchmark_name}': {e}", file=sys.stderr)
        _parser_cache[benchmark_name] = None
        return None


def _discover_threads(benchmark_dir: Path) -> List[str]:
    """
    Discover all thread numbers from benchmark directory.
    Searches *-thread.log, *-thread.json, and *-thread_perf_summary.json files.
    """
    threads = set()
    for pattern in ("*-thread.log", "*-thread.json", "*-thread_perf_summary.json"):
        for f in benchmark_dir.glob(pattern):
            pfx = f.stem.split("-", 1)[0]
            if pfx and pfx.isdigit():
                threads.add(pfx)
    return sorted(threads)


def _fallback_collect_thread_payload(
    benchmark_dir: Path, thread_num: str, cost_hour: float
) -> Optional[Dict[str, Any]]:
    """
    Fallback parser for benchmarks without a dedicated json_parser module.
    Uses _json_parser_common._collect_thread_payload if available,
    otherwise implements generic JSON-based extraction.
    """
    try:
        common_module_path = Path(__file__).resolve().parent / "json_parser" / "_json_parser_common.py"
        if common_module_path.exists():
            spec = importlib.util.spec_from_file_location("_json_parser_common", common_module_path)
            common_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(common_module)
            return common_module._collect_thread_payload(benchmark_dir, thread_num, cost_hour)
    except Exception as e:
        print(f"Warning: Fallback parser failed: {e}", file=sys.stderr)

    return None


# ---------------------------------------------------------------------------
# Benchmark processing (delegates to json_parser modules)
# ---------------------------------------------------------------------------

def process_benchmark(benchmark_dir: Path, cost_hour: float = 0.0) -> Optional[Dict[str, Any]]:
    """
    Process a benchmark directory by delegating to the appropriate json_parser module.

    1. Dynamically loads json_parser/json_parser_<benchmark_name>.py
    2. Discovers all thread numbers in the directory
    3. Calls _collect_thread_payload() for each thread
    4. Falls back to generic JSON extraction if no specific parser exists
    """
    benchmark_name = benchmark_dir.name

    # Load the benchmark-specific parser module
    parser_module = _load_benchmark_parser(benchmark_name)

    # Discover all thread numbers
    thread_nums = _discover_threads(benchmark_dir)
    if not thread_nums:
        print(f"Warning: Skipping benchmark at {benchmark_dir} (no thread files found)", file=sys.stderr)
        return None

    # Warn once if no dedicated parser exists
    has_dedicated_parser = parser_module and hasattr(parser_module, '_collect_thread_payload')
    if not has_dedicated_parser:
        print(f"Warning: No dedicated parser for '{benchmark_name}'. Falling back to generic parser.", file=sys.stderr)

    # Process each thread
    benchmark_result = {}
    for thread_num in thread_nums:
        payload = None
        if has_dedicated_parser:
            try:
                payload = parser_module._collect_thread_payload(
                    benchmark_dir, thread_num, cost_hour)
            except Exception as e:
                print(f"Warning: Parser for '{benchmark_name}' failed on thread {thread_num}: {e}", file=sys.stderr)
                payload = _fallback_collect_thread_payload(
                    benchmark_dir, thread_num, cost_hour)
        else:
            payload = _fallback_collect_thread_payload(
                benchmark_dir, thread_num, cost_hour)

        if payload:
            benchmark_result[thread_num] = payload

    return benchmark_result if benchmark_result else None


# ---------------------------------------------------------------------------
# JSON structure builder
# ---------------------------------------------------------------------------

def build_json_structure(project_root: Path) -> Dict[str, Any]:
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
    SKIP_DIRS = {'__pycache__', '.pytest_cache', 'node_modules', '.git', '.venv', 'venv', 'json_parser'}

    result = {}

    for machine_dir in sorted(project_root.iterdir()):
        if not machine_dir.is_dir() or machine_dir.name.startswith('.') or machine_dir.name in SKIP_DIRS:
            continue

        machinename = machine_dir.name
        machine_info = get_machine_info(machinename)

        machine_data = {
            "CSP": machine_info["CSP"],
            "total_vcpu": machine_info["total_vcpu"],
            "cpu_name": machine_info["cpu_name"],
            "cpu_isa": machine_info["cpu_isa"],
            "cost_hour[730h-mo]": machine_info["cost_hour[730h-mo]"],
            "os": {}
        }

        for os_dir in sorted(machine_dir.iterdir()):
            if not os_dir.is_dir():
                continue

            os_name = os_dir.name
            os_data = {"testcategory": {}}

            for testcategory_dir in sorted(os_dir.iterdir()):
                if not testcategory_dir.is_dir():
                    continue

                testcategory = testcategory_dir.name
                testcategory_data = {"benchmark": {}}

                for benchmark_dir in sorted(testcategory_dir.iterdir()):
                    if not benchmark_dir.is_dir():
                        continue

                    benchmark = benchmark_dir.name
                    benchmark_data = process_benchmark(benchmark_dir, machine_info["cost_hour[730h-mo]"])

                    if benchmark_data:
                        testcategory_data["benchmark"][benchmark] = {"thread": benchmark_data}

                if testcategory_data["benchmark"]:
                    os_data["testcategory"][testcategory] = testcategory_data

            if os_data["testcategory"]:
                machine_data["os"][os_name] = os_data

        result[machinename] = machine_data

    return result


# ---------------------------------------------------------------------------
# JSON merge helpers
# ---------------------------------------------------------------------------

def _merge_missing(dst: Dict[str, Any], src: Dict[str, Any]) -> None:
    """Merge src into dst without overwriting existing keys."""
    for key, value in src.items():
        if key not in dst:
            dst[key] = value
            continue
        if isinstance(dst[key], dict) and isinstance(value, dict):
            _merge_missing(dst[key], value)


def merge_json_data(data1: Dict[str, Any], data2: Dict[str, Any]) -> Dict[str, Any]:
    """Merge two JSON structures without overwriting existing data."""
    result = data1.copy()
    for machine_name, machine_data in data2.items():
        if machine_name in result:
            if isinstance(result[machine_name], dict) and isinstance(machine_data, dict):
                _merge_missing(result[machine_name], machine_data)
            else:
                continue
        else:
            result[machine_name] = machine_data
    return result


# ---------------------------------------------------------------------------
# Syntax checks
# ---------------------------------------------------------------------------

def check_syntax() -> bool:
    """Check syntax of this script. Returns True if no errors."""
    errors = []
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
    """Check syntax of the output JSON file. Returns True if valid."""
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


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    hostname = socket.gethostname()
    default_output = f'one_big_json_{hostname}.json'

    parser = argparse.ArgumentParser(description='Generate one_big_json.json from results directory')
    parser.add_argument('--dir', '-D', type=str, action='append',
                        help='Path to project root (can be specified multiple times). Default: current directory')
    parser.add_argument('--output', '-O', type=str, default=default_output,
                        help=f'Output JSON file path (default: {default_output})')
    parser.add_argument('--force', '-F', action='store_true',
                        help='Force overwrite without confirmation when --output is specified')
    parser.add_argument('--merge', '-M', nargs='*', metavar='JSON_FILE',
                        help='Merge multiple JSON files instead of building from directories. If no files specified, merges all one_big_json_*.json in current directory. Requires --output to be specified.')

    args = parser.parse_args()

    # Check this script's syntax
    print("Checking script syntax...")
    if not check_syntax():
        print("Script syntax check failed. Aborting.", file=sys.stderr)
        sys.exit(1)

    output_file = Path(args.output)

    # Handle --merge mode
    if args.merge is not None:
        hostname = socket.gethostname()
        default_output = f'one_big_json_{hostname}.json'
        if args.output == default_output:
            print("Error: When using --merge, you must specify --output with a non-default filename.", file=sys.stderr)
            print("Example: make_one_big_json.py --merge ./1.json ./2.json --output ./New.json", file=sys.stderr)
            sys.exit(1)

        merge_files = args.merge
        if not merge_files:
            pattern = 'one_big_json_*.json'
            merge_files = glob.glob(pattern)
            if not merge_files:
                print(f"Error: No files matching pattern '{pattern}' found in current directory.", file=sys.stderr)
                print("Please specify JSON files explicitly or ensure one_big_json_*.json files exist.", file=sys.stderr)
                sys.exit(1)
            print(f"Auto-detected {len(merge_files)} JSON files matching '{pattern}':")
            for f in merge_files:
                print(f"  - {f}")
        else:
            merge_files = args.merge

        if output_file.exists() and not args.force:
            response = input(f"Output file '{output_file}' already exists. Overwrite? [y/N]: ")
            if response.lower() not in ['y', 'yes']:
                print("Aborted.")
                sys.exit(0)

        print(f"Merging {len(merge_files)} JSON files...")
        current_version = get_version_info()
        merged_data = {}
        first_version = None

        for idx, json_file_path in enumerate(merge_files):
            json_file = Path(json_file_path)
            if not json_file.exists():
                print(f"Error: JSON file '{json_file}' does not exist", file=sys.stderr)
                sys.exit(1)

            print(f"  Loading {json_file}...")
            try:
                with open(json_file, 'r') as f:
                    json_data = json.load(f)

                file_version = extract_version_info(json_data)
                if file_version:
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

                    json_data.pop("generation log", None)
                    json_data.pop("generation_log", None)
                else:
                    print(f"Error: No version info found in {json_file}", file=sys.stderr)
                    print("  Each JSON must include generation log version info for merge validation.", file=sys.stderr)
                    sys.exit(1)

                merged_data = merge_json_data(merged_data, json_data)
            except json.JSONDecodeError as e:
                print(f"Error: Failed to parse {json_file}: {e}", file=sys.stderr)
                sys.exit(1)

        final_output = create_generation_log()
        final_output.update(merged_data)

        print(f"Writing merged output to: {output_file}")
        with open(output_file, 'w') as f:
            json.dump(final_output, f, indent=2)

        print(f"Successfully merged {len(merge_files)} JSON files into {output_file}")
        print(f"Output version: {current_version}")
        print(f"Total machines in merged output: {len(merged_data)}")

        print("\nChecking output JSON syntax...")
        if not check_json_syntax(output_file):
            print("Output JSON syntax check failed.", file=sys.stderr)
            sys.exit(1)

        return

    # Normal mode: build from directories
    project_roots = args.dir if args.dir else ['.']

    if output_file.exists() and not args.force:
        response = input(f"Output file '{output_file}' already exists. Overwrite? [y/N]: ")
        if response.lower() not in ['y', 'yes']:
            print("Aborted.")
            sys.exit(0)

    merged_data = {}
    for project_root_str in project_roots:
        project_root = Path(project_root_str).resolve()

        if not project_root.exists():
            print(f"Error: Project root '{project_root}' does not exist", file=sys.stderr)
            sys.exit(1)

        print(f"Processing results from: {project_root}")

        json_data = build_json_structure(project_root)

        merged_data = merge_json_data(merged_data, json_data)

    print(f"Output file: {output_file}")

    current_version = get_version_info()
    final_output = create_generation_log()
    final_output.update(merged_data)

    with open(output_file, 'w') as f:
        json.dump(final_output, f, indent=2)

    print(f"Successfully generated {output_file}")
    print(f"Output version: {current_version}")
    print(f"Total machines processed: {len(merged_data)}")

    print("\nChecking output JSON syntax...")
    if not check_json_syntax(output_file):
        print("Output JSON syntax check failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
