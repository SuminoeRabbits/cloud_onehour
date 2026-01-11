#!/usr/bin/env python3

"""

PTS Runner Compliance Checker


Verify that pts_runner_*.py scripts comply with CODE_TEMPLATE.md requirements.

Static analysis only - checks code patterns without execution.


Usage:

    ./check_compliance.py pts_runner_nginx-3.0.1.py

    ./check_compliance.py pts_runner_*.py

"""


import argparse

import ast

import re

import sys

from pathlib import Path

from typing import List, Tuple



class ComplianceChecker:

    def __init__(self, filepath: Path):

        self.filepath = filepath

        self.content = filepath.read_text()

        self.errors = []

        self.warnings = []

        self.passed = []

       

    def check_all(self) -> Tuple[bool, int, int]:

        """

        Run all compliance checks.



        Returns:

            Tuple[bool, int, int]: (passed, num_errors, num_warnings)

        """

        print(f"\n{'='*80}")

        print(f"Checking: {self.filepath.name}")

        print(f"{'='*80}")



        # Python syntax check (must pass first)

        self.check_python_syntax()



        # Critical checks (must pass)

        self.check_benchmark_definition()

        self.check_test_results_name()

        self.check_export_results_method()

        self.check_generate_summary_method()

        self.check_dot_removal()

        self.check_test_category_dir_safety()



        # Warning checks (should pass but not critical)

        self.check_required_methods()

        self.check_hardcoded_benchmark_names()

        self.check_docstring_header()

        self.check_perf_events_implementation()

        self.check_install_verification()
        self.check_argparse_setup()
        self.check_batch_env_vars()
        self.check_pts_result_cleanup()



        # Print results

        self.print_results()



        # Return (passed, num_errors, num_warnings)

        return (len(self.errors) == 0, len(self.errors), len(self.warnings))



    def check_python_syntax(self):

        """Check if the Python file has valid syntax"""

        try:

            ast.parse(self.content)

            self.passed.append("✅ Python syntax is valid")

        except SyntaxError as e:

            self.errors.append(

                f"❌ CRITICAL: Python syntax error at line {e.lineno}:\n"

                f"   {e.msg}\n"

                f"   {e.text.strip() if e.text else ''}"

            )

        except Exception as e:

            self.errors.append(f"❌ CRITICAL: Failed to parse Python file: {str(e)}")



    def check_benchmark_definition(self):

        """Check if self.benchmark is defined in __init__"""

        pattern = r'self\.benchmark\s*=\s*["\'][^"\']+["\']'

        if re.search(pattern, self.content):

            self.passed.append("✅ self.benchmark is defined in __init__")

        else:

            self.errors.append("❌ CRITICAL: self.benchmark not defined in __init__")

   

    def check_test_results_name(self):

        """Check if TEST_RESULTS_NAME uses {self.benchmark} (not hardcoded)"""

        # Look for TEST_RESULTS_NAME pattern

        pattern = r'TEST_RESULTS_NAME=([^"\s]+)'

        matches = re.findall(pattern, self.content)

       

        if not matches:

            self.errors.append("❌ CRITICAL: TEST_RESULTS_NAME not found in code")

            return

       

        for match in matches:

            # Check if it uses {self.benchmark}

            if '{self.benchmark}' in match or '{benchmark}' in match:

                self.passed.append(f"✅ TEST_RESULTS_NAME uses {{self.benchmark}}: {match}")

            else:

                # Extract hardcoded name

                hardcoded = match.split('-')[0]

                self.errors.append(

                    f"❌ CRITICAL: TEST_RESULTS_NAME is hardcoded: '{match}'\n"

                    f"   Should use: {{self.benchmark}}-{{num_threads}}threads\n"

                    f"   Found hardcoded: {hardcoded}"

                )

   

    def check_export_results_method(self):

        """Check if export_results() method exists"""

        if re.search(r'def\s+export_results\s*\(', self.content):

            self.passed.append("✅ export_results() method exists")

        else:

            self.errors.append("❌ CRITICAL: export_results() method not found")

   

    def check_generate_summary_method(self):

        """Check if generate_summary() method exists"""

        if re.search(r'def\s+generate_summary\s*\(', self.content):

            self.passed.append("✅ generate_summary() method exists")

        else:

            self.errors.append("❌ CRITICAL: generate_summary() method not found")

   

    def check_dot_removal(self):

        """Check if dot removal is implemented in export_results"""

        if re.search(r"\.replace\(['\"]\.['\"]\s*,\s*['\"]['\"]", self.content):

            self.passed.append("✅ Dot removal implemented: .replace('.', '')")

        else:

            self.warnings.append("⚠️  WARNING: Dot removal not found (may fail for benchmarks with dots)")



    def check_test_category_dir_safety(self):

        """
        Check if test_category_dir is safely converted for directory usage.

        Best practice: Replace spaces and special characters with underscores or hyphens.
        Recommended: self.test_category_dir = self.test_category.replace(" ", "_")
        """

        # Check if test_category_dir assignment exists

        pattern = r'self\.test_category_dir\s*=\s*(.+)'

        match = re.search(pattern, self.content)



        if not match:

            self.errors.append("❌ CRITICAL: test_category_dir assignment not found")

            return



        assignment = match.group(1).strip()



        # Check for common safe conversion patterns

        safe_patterns = [

            r'self\.test_category\.replace\(\s*["\'][ ]["\']\s*,\s*["\'][_-]["\']\s*\)',  # .replace(" ", "_") or .replace(" ", "-")

            r're\.sub\(',  # Using regex substitution

        ]



        is_safe = any(re.search(p, assignment) for p in safe_patterns)



        if is_safe:

            # Verify it's converting spaces at minimum

            if 'replace' in assignment and '" "' in assignment or "' '" in assignment:

                self.passed.append("✅ test_category_dir safely converts spaces to directory-safe format")

            elif 're.sub' in assignment:

                self.passed.append("✅ test_category_dir uses regex for safe directory conversion")

            else:

                self.warnings.append(

                    "⚠️  WARNING: test_category_dir conversion found but pattern unclear\n"

                    f"   Found: {assignment}"

                )

        else:

            self.errors.append(

                f"❌ CRITICAL: test_category_dir may not be safely converting special characters\n"

                f"   Found: {assignment}\n"

                f"   Recommended: self.test_category_dir = self.test_category.replace(' ', '_')\n"

                f"   This ensures safe directory names for categories like 'Memory Access'"

            )



    def check_required_methods(self):

        """Check if all required methods exist"""

        required = [

            'get_os_name',

            'get_cpu_affinity_list',

            'is_wsl',

            'get_perf_events',

            'run_benchmark',

            'install_benchmark'

        ]

       

        missing = []

        for method in required:

            pattern = rf'def\s+{method}\s*\('

            if not re.search(pattern, self.content):

                missing.append(method)

       

        if missing:

            self.warnings.append(f"⚠️  WARNING: Missing recommended methods: {', '.join(missing)}")

        else:

            self.passed.append("✅ All recommended methods present")

   

    def check_hardcoded_benchmark_names(self):

        """Detect potential hardcoded benchmark names in various places"""

        # Common benchmark names to check

        common_names = [

            'compress-7zip', 'nginx', 'openssl', 'stream', 'redis',

            'apache', 'coremark', 'sysbench', 'pgbench'

        ]



        # Check in string literals (excluding comments and docstrings)

        issues = []

        for name in common_names:

            # Skip if this is the actual benchmark file

            if name in self.filepath.name:

                continue



            # Look for hardcoded names in TEST_RESULTS or similar contexts

            pattern = rf'TEST_RESULTS.*["\'].*{name}.*["\']'

            if re.search(pattern, self.content):

                issues.append(name)



        if issues:

            self.warnings.append(

                f"⚠️  WARNING: Potential hardcoded benchmark names found: {', '.join(issues)}\n"

                f"   Verify these are intentional and not copy-paste errors"

            )



    def check_docstring_header(self):

        """Check if docstring includes test characteristics from phoronix-test-suite info"""

        # Look for docstring pattern

        docstring_pattern = r'"""[\s\S]*?"""'

        matches = re.findall(docstring_pattern, self.content)



        if not matches:

            self.warnings.append("⚠️  WARNING: No module docstring found")

            return



        first_docstring = matches[0]



        # Check for test characteristics

        has_dependencies = 'Dependencies' in first_docstring or 'Software Dependencies' in first_docstring

        has_test_type = 'Test Type' in first_docstring or 'Multi-threaded' in first_docstring

        has_characteristics = 'Test Characteristics' in first_docstring or 'THFix_in_compile' in first_docstring



        if has_dependencies and (has_test_type or has_characteristics):

            self.passed.append("✅ Docstring includes test characteristics")

        else:

            self.warnings.append(

                "⚠️  WARNING: Docstring should include test characteristics from 'phoronix-test-suite info'\n"

                "   Expected sections: Software Dependencies, Test Type, Test Characteristics"

            )



    def check_perf_events_implementation(self):

        """Check if get_perf_events() implements 3-tier fallback (HW+SW -> SW -> None)"""

        has_get_perf_events = re.search(r'def\s+get_perf_events\s*\(', self.content)



        if not has_get_perf_events:

            self.warnings.append("⚠️  WARNING: get_perf_events() method not found")

            return



        # Check for hardware events

        has_hw_events = 'cycles,instructions' in self.content or 'hw_events' in self.content



        # Check for software-only events fallback

        has_sw_fallback = 'cpu-clock,task-clock' in self.content or 'sw_events' in self.content



        # Check for conditional perf usage in run_benchmark

        has_conditional_perf = 'if self.perf_events' in self.content



        if has_hw_events and has_sw_fallback and has_conditional_perf:

            self.passed.append("✅ Perf events with proper fallback implemented")

        elif has_conditional_perf:

            self.warnings.append(

                "⚠️  WARNING: Perf implementation incomplete\n"

                "   Should implement 3-tier fallback: HW+SW events -> SW-only events -> None"

            )

        else:

            self.warnings.append(

                "⚠️  WARNING: Conditional perf usage not detected\n"

                "   run_benchmark() should check 'if self.perf_events' before using perf"

            )



    def check_install_verification(self):

        """Check if install_benchmark() uses robust verification pattern"""

        has_install_method = re.search(r'def\s+install_benchmark\s*\(', self.content)



        if not has_install_method:

            self.warnings.append("⚠️  WARNING: install_benchmark() method not found")

            return



        # Check for subprocess.Popen with real-time output streaming

        has_popen_streaming = 'subprocess.Popen' in self.content and 'for line in process.stdout' in self.content



        # Check for filesystem verification (installed-tests directory check)

        has_fs_verification = 'installed-tests' in self.content and '.exists()' in self.content



        # Check for PTS recognition verification (test-installed command)

        has_pts_verification = 'test-installed' in self.content



        verification_score = sum([has_popen_streaming, has_fs_verification, has_pts_verification])



        if verification_score == 3:

            self.passed.append("✅ Install verification uses best practices (streaming + dual verification)")

        elif verification_score >= 1:

            self.warnings.append(

                f"⚠️  WARNING: Install verification could be improved ({verification_score}/3 patterns found)\n"

                "   Best practice: Use subprocess.Popen for streaming + filesystem check + PTS recognition check"

            )

        else:

            self.warnings.append(

                "⚠️  WARNING: No install verification pattern detected\n"

                "   Recommended: Check installed-tests directory and use 'phoronix-test-suite test-installed'"

            )

   

    def check_argparse_setup(self):
        """Check if argparse supports both positional and named thread arguments"""
        # Check for named argument --threads
        has_named_threads = re.search(r"add_argument\(\s*['\"]--threads['\"]", self.content)
        
        # Check for positional argument (threads or threads_pos)
        # Matches: add_argument('threads' OR add_argument('threads_pos'
        has_pos_threads = re.search(r"add_argument\(\s*['\"]threads(?:_pos)?['\"]", self.content)

        if has_named_threads and has_pos_threads:
            self.passed.append("✅ Argparse supports both positional and named thread arguments")
        elif has_named_threads:
             self.warnings.append(
                "⚠️  WARNING: Argparse missing positional thread argument support\n"
                "   Should add parser.add_argument('threads_pos', nargs='?', ...)"
            )
        elif has_pos_threads:
            self.warnings.append(
                "⚠️  WARNING: Argparse missing named --threads argument support\n"
                "   Should add parser.add_argument('--threads', ...)"
            )
        else:
             self.warnings.append("⚠️  WARNING: Neither positional nor named 'threads' argument found in argparse setup")

    def check_batch_env_vars(self):
        """Check if batch environment variables include TEST_RESULTS_DESCRIPTION to suppress prompts"""
        # Look for the batch_env definition line
        # We want to ensure TEST_RESULTS_DESCRIPTION is present
        batch_env_match = re.search(r"batch_env\s*=\s*f?['\"].*TEST_RESULTS_DESCRIPTION=", self.content)
        
        if batch_env_match:
             self.passed.append("✅ Batch env includes TEST_RESULTS_DESCRIPTION (suppresses prompts)")
        else:
             self.warnings.append(
                "⚠️  WARNING: batch_env missing TEST_RESULTS_DESCRIPTION\n"
                "   This may cause interactive prompts. Add TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads"
            )

    def check_pts_result_cleanup(self):
        """Check if PTS result cleanup logic is present to prevent interactive prompts"""
        # Search for logic that removes existing results before running
        has_cleanup_logic = re.search(r"phoronix-test-suite\s+remove-result", self.content)
        
        if has_cleanup_logic:
            self.passed.append("✅ Automated PTS result cleanup logic detected")
        else:
            self.warnings.append(
                "⚠️  WARNING: Automated PTS result cleanup logic missing\n"
                "   Should execute 'phoronix-test-suite remove-result' before benchmark execution to prevent interactive prompts."
            )

    def print_results(self):

        """Print all check results"""

        print()

       

        # Print passed checks

        if self.passed:

            for msg in self.passed:

                print(msg)

       

        # Print warnings

        if self.warnings:

            print()

            for msg in self.warnings:

                print(msg)

       

        # Print errors

        if self.errors:

            print()

            for msg in self.errors:

                print(msg)

       

        # Summary

        print()

        print(f"{'='*80}")

        if self.errors:

            print(f"❌ FAILED: {len(self.errors)} critical issue(s) found")

            if self.warnings:

                print(f"⚠️  {len(self.warnings)} warning(s)")

        elif self.warnings:

            print(f"⚠️  PASSED with warnings: {len(self.warnings)} warning(s)")

        else:

            print(f"✅ PASSED: All checks successful")

        print(f"{'='*80}")



def main():

    parser = argparse.ArgumentParser(

        description='Check PTS runner scripts for CODE_TEMPLATE.md compliance',

        formatter_class=argparse.RawDescriptionHelpFormatter,

        epilog="""

Examples:

  %(prog)s pts_runner_nginx-3.0.1.py

  %(prog)s pts_runner_*.py

  %(prog)s --all



Critical Checks:

  - Python syntax is valid (no syntax errors)

  - self.benchmark defined in __init__

  - TEST_RESULTS_NAME uses {self.benchmark} (not hardcoded)

  - export_results() method exists

  - generate_summary() method exists

  - Dot removal implemented

  - test_category_dir safely converts to directory-safe format



Warning Checks:

  - Required methods present (get_os_name, get_cpu_affinity_list, etc.)

  - No hardcoded benchmark names

  - Docstring includes test characteristics from phoronix-test-suite info

  - Perf events with 3-tier fallback (HW+SW -> SW -> None)

  - Install verification with streaming and dual checks

        """

    )

   

    parser.add_argument(

        'files',

        nargs='*',

        help='PTS runner scripts to check'

    )

   

    parser.add_argument(

        '--all',

        action='store_true',

        help='Check all pts_runner_*.py files in current directory'

    )

   

    args = parser.parse_args()

   

    # Determine files to check

    if args.all:

        script_dir = Path(__file__).parent

        files = sorted(script_dir.glob('pts_runner_*.py'))

    elif args.files:

        files = [Path(f) for f in args.files]

    else:

        parser.print_help()

        return 1

   

    if not files:

        print("No files found to check")

        return 1

   

    # Check each file

    results = []

    total_errors = 0

    total_warnings = 0

   

    for filepath in files:

        if not filepath.exists():

            print(f"ERROR: File not found: {filepath}")

            continue

       

        checker = ComplianceChecker(filepath)

        passed, num_errors, num_warnings = checker.check_all()

        results.append((filepath.name, passed, num_errors, num_warnings))

        total_errors += num_errors

        total_warnings += num_warnings

   

    # Overall summary

    if len(results) > 1:

        print(f"\n{'='*80}")

        print("OVERALL SUMMARY")

        print(f"{'='*80}")

       

        passed_count = sum(1 for _, passed, _, _ in results if passed)

        failed_count = len(results) - passed_count

       

        for name, passed, num_errors, num_warnings in results:

            status = "✅ PASS" if passed else "❌ FAIL"

            if not passed:

                print(f"{status:10} {name} ({num_errors} critical, {num_warnings} warnings)")

            else:

                print(f"{status:10} {name}")

       

        print(f"\nTotal:    {len(results)} files")

        print(f"Passed:   {passed_count}")

        print(f"Failed:   {failed_count}")

        print(f"Critical: {total_errors} errors")

        print(f"Warnings: {total_warnings}")

       

        return 0 if failed_count == 0 else 1

    else:

        # Single file - return based on its result

        return 0 if results[0][1] else 1



if __name__ == "__main__":

    sys.exit(main()) 