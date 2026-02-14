#!/usr/bin/env python3
"""json_parser_* の CODE_TEMPLATE.md 準拠チェック (Python 3.10)。

このスクリプトは json_parser_<benchmark>.py が CODE_TEMPLATE.md の
仕様に準拠しているかを包括的にチェックします。

主なチェック項目:
1. Python 3.10 互換性（構文、future imports）
2. ファイル命名規則とBENCHMARK_NAME定数
3. 自己構文チェックブロック（py_compile.compile）
4. CLI引数（--dir必須「探索対象を含む親ディレクトリを指定」, --out任意）
5. _find_machine_info_in_hierarchy()実装（堅牢なmachinename検出）
6. 共通ヘルパー関数（_strip_ansi, _read_freq_file, _discover_threads）
7. _build_full_payload()および_collect_thread_payload()存在確認
8. perf_stat周波数処理
9. cost計算式（round(cost_hour * time / 3600, 6)）
10. パターン別データソース（log vs JSON）
11. 旧式固定階層ロジックの検出
12. wrapper-style実装の検出と警告
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import List, Tuple


CASE5_BENCHMARKS = {
    "build-gcc-1.5.0",
    "build-linux-kernel-1.17.1",
    "build-llvm-1.6.0",
    "coremark-1.0.1",
    "sysbench-1.1.0",
    "java-jmh-1.0.1",
    "ffmpeg-7.0.1",
}


class JsonParserComplianceChecker:
    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.content = filepath.read_text(encoding="utf-8")
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.passed: List[str] = []
        self.syntax_ok = True

    def check_all(self) -> Tuple[bool, int, int]:
        print(f"\n{'=' * 80}")
        print(f"Checking: {self.filepath.name}")
        print(f"{'=' * 80}")

        self.check_python_syntax()
        if not self.syntax_ok:
            self.print_results()
            return False, len(self.errors), len(self.warnings)

        self.check_python310_compatibility()
        self.check_filename_rule()
        self.check_benchmark_name_constant()
        self.check_self_syntax_check_block()
        self.check_main_cli_args()
        self.check_build_full_payload_exists()
        self.check_machine_info_hierarchy_function()
        self.check_common_helper_functions()
        self.check_perf_stat_handling()
        self.check_cost_formula()
        self.check_collect_thread_payload()
        self.check_pattern_alignment()
        self.check_common_wrapper_warning()
        self.check_deprecated_hierarchy_logic()

        self.print_results()
        return len(self.errors) == 0, len(self.errors), len(self.warnings)

    def _benchmark_from_filename(self) -> str:
        name = self.filepath.name
        if not name.startswith("json_parser_") or not name.endswith(".py"):
            return ""
        return name[len("json_parser_") : -3]

    def check_python_syntax(self) -> None:
        try:
            ast.parse(self.content)
            self.passed.append("✅ Python syntax is valid")
        except SyntaxError as exc:
            self.syntax_ok = False
            self.errors.append(
                f"❌ CRITICAL: Syntax error line {exc.lineno}: {exc.msg}"
            )

    def check_python310_compatibility(self) -> None:
        """Check for Python 3.10 compatibility requirements."""
        has_future_import = "from __future__ import annotations" in self.content
        
        if has_future_import:
            self.passed.append("✅ Python 3.10 compatibility: 'from __future__ import annotations' present")
        else:
            self.warnings.append(
                "⚠️  WARNING: Missing 'from __future__ import annotations'. "
                "Required for Python 3.10 compatibility with type hints."
            )
        
        # Check for Python 3.11+ specific syntax that should be avoided
        if "match " in self.content and "case " in self.content:
            # Simple heuristic - could be improved
            if re.search(r"\bmatch\s+\w+\s*:\s*case\b", self.content):
                self.errors.append(
                    "❌ CRITICAL: Match statement detected. "
                    "Not available in Python 3.10, use if/elif instead."
                )
        
        if "ExceptionGroup" in self.content:
            self.errors.append(
                "❌ CRITICAL: ExceptionGroup detected. "
                "Not available in Python 3.10."
            )

    def check_filename_rule(self) -> None:
        if re.match(r"^json_parser_[\w.-]+\.py$", self.filepath.name):
            self.passed.append("✅ Filename follows json_parser_<benchmark>.py")
        else:
            self.errors.append("❌ CRITICAL: Invalid filename rule")

    def check_benchmark_name_constant(self) -> None:
        expected = self._benchmark_from_filename()
        m = re.search(r'BENCHMARK_NAME\s*=\s*["\']([^"\']+)["\']', self.content)
        if not m:
            self.errors.append("❌ CRITICAL: BENCHMARK_NAME constant not found")
            return
        actual = m.group(1)
        if actual != expected:
            self.errors.append(
                f"❌ CRITICAL: BENCHMARK_NAME mismatch: expected '{expected}', found '{actual}'"
            )
        else:
            self.passed.append("✅ BENCHMARK_NAME matches filename")

    def check_self_syntax_check_block(self) -> None:
        has_import = "import py_compile" in self.content
        has_compile = "py_compile.compile(str(Path(__file__).resolve()), doraise=True)" in self.content
        has_except = "except py_compile.PyCompileError" in self.content
        if has_import and has_compile and has_except:
            self.passed.append("✅ Self syntax check block is implemented")
        else:
            self.errors.append(
                "❌ CRITICAL: Missing self syntax check in main() (py_compile.compile + except)"
            )

    def check_main_cli_args(self) -> None:
        has_main = re.search(r"def\s+main\s*\(", self.content) is not None
        has_dir = "--dir" in self.content and "required=True" in self.content
        has_out = "--out" in self.content
        if has_main and has_dir and has_out:
            self.passed.append("✅ CLI args (--dir required, --out optional) are present")
        else:
            self.errors.append("❌ CRITICAL: CLI args are not compliant with template")

    def check_build_full_payload_exists(self) -> None:
        if "def _build_full_payload" in self.content:
            self.passed.append("✅ _build_full_payload() exists")
        elif "run_main(" in self.content:
            self.warnings.append(
                "⚠️  WARNING: Wrapper-style parser detected (run_main). Template expects in-file _build_full_payload()."
            )
        else:
            self.errors.append("❌ CRITICAL: _build_full_payload() not found")

    def check_machine_info_hierarchy_function(self) -> None:
        """Check for robust _find_machine_info_in_hierarchy() implementation."""
        has_function = "def _find_machine_info_in_hierarchy" in self.content
        has_correct_sig = re.search(
            r"def _find_machine_info_in_hierarchy\s*\(\s*benchmark_dir\s*:\s*Path\s*,\s*search_root\s*:\s*Path\s*\)",
            self.content
        )
        has_return_type = "-> tuple[str, str, str, Dict[str, Any]]" in self.content
        uses_in_build = "_find_machine_info_in_hierarchy(" in self.content and "_build_full_payload" in self.content
        
        # Check for correct CSP validation (rejecting "unknown")
        has_csp_check = re.search(
            r'machine_info\.get\("CSP"\)\s+and\s+machine_info\.get\("CSP"\)\s*!=\s*"unknown"',
            self.content
        )
        
        if has_function and has_correct_sig:
            self.passed.append("✅ _find_machine_info_in_hierarchy() with correct signature")
            if has_return_type:
                self.passed.append("✅ Correct return type annotation (tuple[str, str, str, Dict[str, Any]])")
            else:
                self.warnings.append("⚠️  WARNING: _find_machine_info_in_hierarchy() missing return type annotation")
            
            if uses_in_build:
                self.passed.append("✅ _find_machine_info_in_hierarchy() is called in _build_full_payload()")
            else:
                self.errors.append(
                    "❌ CRITICAL: _find_machine_info_in_hierarchy() exists but not used in _build_full_payload()"
                )
            
            if has_csp_check:
                self.passed.append("✅ CSP validation correctly rejects 'unknown' values")
            else:
                self.warnings.append(
                    "⚠️  WARNING: CSP validation should reject 'unknown' to avoid false positives "
                    "(check: machine_info.get('CSP') != 'unknown')"
                )
        elif "run_main(" in self.content:
            self.warnings.append(
                "⚠️  WARNING: Wrapper-style parser. If migrating to template, add _find_machine_info_in_hierarchy()"
            )
        else:
            self.errors.append(
                "❌ CRITICAL: _find_machine_info_in_hierarchy() not found. "
                "New template requires robust machinename detection."
            )

    def check_deprecated_hierarchy_logic(self) -> None:
        """Check for old fixed 3-level hierarchy logic."""
        # Patterns that indicate old-style fixed hierarchy
        old_patterns = [
            r"machine_dir\s*=\s*os_dir\.parent",
            r"machinename\s*=\s*machine_dir\.name",
            r"category_dir\s*=\s*benchmark_dir\.parent[\s\n]+os_dir\s*=\s*category_dir\.parent[\s\n]+machine_dir\s*=\s*os_dir\.parent",
        ]
        
        found_old_style = False
        for pattern in old_patterns:
            if re.search(pattern, self.content):
                found_old_style = True
                break
        
        if found_old_style and "_find_machine_info_in_hierarchy" not in self.content:
            self.errors.append(
                "❌ CRITICAL: Old fixed 3-level hierarchy detection found. "
                "Update to use _find_machine_info_in_hierarchy() for robustness."
            )
        elif found_old_style and "_find_machine_info_in_hierarchy" in self.content:
            self.warnings.append(
                "⚠️  WARNING: Both old and new hierarchy logic detected. "
                "Ensure old fixed logic is removed from _build_full_payload()."
            )
        else:
            # No old style found, which is good
            pass

    def check_common_helper_functions(self) -> None:
        """Check for common helper functions from template."""
        has_strip_ansi = "def _strip_ansi" in self.content
        has_ansi_re = "ANSI_ESCAPE_RE" in self.content
        has_read_freq = "def _read_freq_file" in self.content
        has_discover_threads = "def _discover_threads" in self.content
        
        helper_count = sum([has_strip_ansi, has_read_freq, has_discover_threads])
        
        if helper_count >= 2:
            self.passed.append(f"✅ Common helper functions present ({helper_count}/3)")
            if not has_strip_ansi:
                self.warnings.append("⚠️  WARNING: _strip_ansi() not found (needed for ANSI escape removal)")
            if not has_read_freq:
                self.warnings.append("⚠️  WARNING: _read_freq_file() not found (needed for perf_stat)")
            if not has_discover_threads:
                self.warnings.append("⚠️  WARNING: _discover_threads() not found (needed for thread detection)")
        elif "run_main(" in self.content:
            # Wrapper style, these functions might be in common module
            self.warnings.append("⚠️  WARNING: Helper functions check skipped for wrapper-style parser")
        else:
            self.errors.append(
                f"❌ CRITICAL: Missing common helper functions ({helper_count}/3 found). "
                "Template requires: _strip_ansi(), _read_freq_file(), _discover_threads()"
            )

    def check_perf_stat_handling(self) -> None:
        has_start = "start_freq" in self.content
        has_end = "end_freq" in self.content
        if has_start and has_end:
            self.passed.append("✅ perf_stat start/end frequency handling detected")
        else:
            self.errors.append("❌ CRITICAL: perf_stat frequency handling is missing")

    def check_cost_formula(self) -> None:
        if re.search(r"round\(\s*cost_hour\s*\*\s*.*?/\s*3600\s*,\s*6\s*\)", self.content):
            self.passed.append("✅ cost formula round(cost_hour * time / 3600, 6) detected")
        else:
            self.warnings.append("⚠️  WARNING: cost formula pattern not detected")

    def check_collect_thread_payload(self) -> None:
        """Check for _collect_thread_payload() implementation."""
        has_function = "def _collect_thread_payload" in self.content
        has_correct_sig = re.search(
            r"def _collect_thread_payload\s*\(\s*benchmark_dir\s*[,:]\s*Path\s*,\s*thread_num\s*[,:]\s*str\s*,\s*cost_hour\s*[,:]\s*float",
            self.content
        )
        returns_dict = "return {" in self.content or 'return {"perf_stat"' in self.content
        has_perf_stat = '"perf_stat"' in self.content or "'perf_stat'" in self.content
        has_test_name = '"test_name"' in self.content or "'test_name'" in self.content
        
        if has_function and has_correct_sig:
            self.passed.append("✅ _collect_thread_payload() with correct signature")
            if has_perf_stat and has_test_name:
                self.passed.append('✅ Returns dict with "perf_stat" and "test_name" keys')
            else:
                self.warnings.append(
                    '⚠️  WARNING: _collect_thread_payload() should return dict with "perf_stat" and "test_name"'
                )
        elif "run_main(" in self.content:
            self.warnings.append("⚠️  WARNING: Wrapper-style parser. _collect_thread_payload() check skipped")
        else:
            self.errors.append(
                "❌ CRITICAL: _collect_thread_payload() not found or has incorrect signature. "
                "Required: (benchmark_dir: Path, thread_num: str, cost_hour: float)"
            )

    def check_pattern_alignment(self) -> None:
        benchmark = self._benchmark_from_filename()
        uses_log = "-thread.log" in self.content
        uses_json = "-thread.json" in self.content

        if benchmark in CASE5_BENCHMARKS:
            if uses_log:
                self.passed.append("✅ Case5 benchmark uses log-based extraction")
            else:
                self.errors.append("❌ CRITICAL: Case5 benchmark must parse <N>-thread.log")
            return

        if uses_json:
            self.passed.append("✅ Non-Case5 benchmark uses json-based extraction")
        else:
            self.warnings.append(
                "⚠️  WARNING: Non-Case5 parser does not appear to use <N>-thread.json"
            )

    def check_common_wrapper_warning(self) -> None:
        if "from _json_parser_common import run_main" in self.content:
            self.warnings.append(
                "⚠️  WARNING: _json_parser_common wrapper usage found. If strict template parity is required, inline implementation is recommended."
            )

    def print_results(self) -> None:
        print()
        for line in self.passed:
            print(line)
        if self.warnings:
            print()
            for line in self.warnings:
                print(line)
        if self.errors:
            print()
            for line in self.errors:
                print(line)

        print()
        print(f"{'=' * 80}")
        if self.errors:
            print(f"❌ FAILED: {len(self.errors)} critical issue(s), {len(self.warnings)} warning(s)")
        elif self.warnings:
            print(f"⚠️  PASSED with warnings: {len(self.warnings)} warning(s)")
        else:
            print("✅ PASSED: All checks successful")
        print(f"{'=' * 80}")


def collect_targets(args: argparse.Namespace) -> List[Path]:
    if args.all:
        base = Path(__file__).parent
        return sorted(base.glob("json_parser_*.py"))

    if args.files:
        return [Path(p) for p in args.files]

    return []


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check json_parser scripts for CODE_TEMPLATE.md compliance.\n\n"
            "Verifies Python 3.10 compatibility, required functions, "
            "robust machinename detection (_find_machine_info_in_hierarchy), "
            "and adherence to template structure."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  # Check all json_parser files in current directory\n"
            "  ./check_compliance_json_parser.py --all\n\n"
            "  # Check specific file\n"
            "  ./check_compliance_json_parser.py json_parser_apache-3.0.0.py\n\n"
            "  # Check multiple files with glob\n"
            "  ./check_compliance_json_parser.py json_parser_*.py\n\n"
            "Exit codes:\n"
            "  0: All checks passed (or passed with warnings only)\n"
            "  1: One or more critical issues found\n"
        ),
    )
    parser.add_argument("files", nargs="*", help="Target json_parser files")
    parser.add_argument("--all", action="store_true", help="Check all json_parser_*.py")
    args = parser.parse_args()

    targets = collect_targets(args)
    if not targets:
        parser.print_help()
        return 1

    results = []
    total_errors = 0
    total_warnings = 0

    for path in targets:
        if not path.exists():
            print(f"ERROR: File not found: {path}")
            total_errors += 1
            continue

        checker = JsonParserComplianceChecker(path)
        passed, errs, warns = checker.check_all()
        results.append((path.name, passed, errs, warns))
        total_errors += errs
        total_warnings += warns

    if len(results) > 1:
        print(f"\n{'=' * 80}")
        print("OVERALL SUMMARY")
        print(f"{'=' * 80}")
        for name, passed, errs, warns in results:
            status = "✅ PASS" if passed else "❌ FAIL"
            if passed and warns == 0:
                print(f"{status:10} {name}")
            else:
                print(f"{status:10} {name} ({errs} critical, {warns} warnings)")
        print(f"\nTotal:    {len(results)} files")
        print(f"Critical: {total_errors}")
        print(f"Warnings: {total_warnings}")

    return 0 if total_errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
