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
        self.syntax_ok = True
        self.hardcoded_thread_lists = []

       

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
        if not self.syntax_ok:
            self.print_results()
            return (False, len(self.errors), len(self.warnings))



        # Critical checks (must pass)

        self.check_benchmark_definition()

        self.check_test_results_name()

        self.check_export_results_method()
        self.check_export_results_uses_benchmark()

        self.check_generate_summary_method()

        self.check_run_method_return()

        self.check_dot_removal()

        self.check_test_category_dir_safety()



        # Warning checks (should pass but not critical)

        self.check_required_methods()

        self.check_hardcoded_benchmark_names()

        self.check_docstring_header()

        self.check_perf_events_implementation()

        self.check_install_verification()
        self.check_install_fail_handling()
        self.check_install_log_toggle()
        self.check_argparse_setup()
        self.check_upload_safety()
        self.check_batch_env_vars()
        self.check_pts_cache_clean()
        self.check_results_dir_structure()
        self.check_results_dir_cleanup_safety()
        self.check_thread_capping()

        # Cloud/ARM64 compatibility checks (new 2026-01)
        self.check_perf_init_order()
        self.check_cpu_frequency_methods()
        self.check_downloads_xml_prefetch()

        # Informational checks
        self.find_hardcoded_thread_lists()


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
            self.syntax_ok = False
            self.errors.append(

                f"❌ CRITICAL: Python syntax error at line {e.lineno}:\n"

                f"   {e.msg}\n"

                f"   {e.text.strip() if e.text else ''}"

            )

        except Exception as e:
            self.syntax_ok = False
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

    def check_export_results_uses_benchmark(self):

        """Check if export_results() uses self.benchmark in result naming"""

        match = re.search(
            r'def\s+export_results\s*\(.*?\):([\s\S]*?)(?:\n\s*def\s+|\Z)',
            self.content
        )
        if not match:
            return

        body = match.group(1)
        if 'self.benchmark' in body:
            self.passed.append("✅ export_results() uses self.benchmark for result naming")
        else:
            self.errors.append(
                "❌ CRITICAL: export_results() does not reference self.benchmark\n"
                "   Fix: Use result_name = f\"{self.benchmark}-{num_threads}threads\""
            )

   

    def check_generate_summary_method(self):

        """Check if generate_summary() method exists"""

        if re.search(r'def\s+generate_summary\s*\(', self.content):

            self.passed.append("✅ generate_summary() method exists")

        else:

            self.errors.append("❌ CRITICAL: generate_summary() method not found")



    def check_run_method_return(self):

        """Check if run() method returns True (CRITICAL for cloud_exec.py integration)"""

        # Parse the file as AST to check for return statements in run() method
        try:
            tree = ast.parse(self.content)

            # Find the run() method
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == 'run':
                    # Check if the method has a return True statement
                    has_return_true = False
                    for item in ast.walk(node):
                        if isinstance(item, ast.Return):
                            # Check if return value is True or a truthy expression
                            if item.value is not None:
                                if isinstance(item.value, ast.Constant) and item.value.value is True:
                                    has_return_true = True
                                    break
                                # Also accept "return len(failed) == 0" pattern
                                elif isinstance(item.value, ast.Compare):
                                    has_return_true = True
                                    break

                    if has_return_true:
                        self.passed.append("✅ run() method returns True (cloud_exec.py compatible)")
                    else:
                        self.errors.append("❌ CRITICAL: run() method must return True (for cloud_exec.py integration)")
                        self.errors.append("   Fix: Add 'return True' at the end of run() method")
                    return

            # run() method not found
            self.errors.append("❌ CRITICAL: run() method not found")

        except SyntaxError:
            # Syntax error already reported by check_python_syntax
            pass



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

            'check_and_setup_perf_permissions',

            'run_benchmark',

            'install_benchmark',

            'get_cpu_frequencies',

            'record_cpu_frequency'

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
            # Check for old method name
            if re.search(r'def\s+check_perf_event_support\s*\(', self.content):
                 self.warnings.append("⚠️  WARNING: Using deprecated method 'check_perf_event_support'. Please rename to 'get_perf_events' matching CODE_TEMPLATE.md")
            else:
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

    def check_install_fail_handling(self):
        """Check if install_benchmark() handles installation failures properly.

        Required pattern (from CODE_TEMPLATE.md):
        1. returncode check after process.wait()
        2. Error string detection (Checksum Failed / ERROR / FAILED)
        3. sys.exit(1) on failure

        Without these checks, installation failures are silently ignored,
        leading to phantom benchmark results (e.g., simdjson 31s on GCP).
        """
        has_install_method = re.search(r'def\s+install_benchmark\s*\(', self.content)
        if not has_install_method:
            return  # Already warned by check_install_verification

        # Extract install_benchmark method body
        install_match = re.search(
            r'def\s+install_benchmark\s*\(self[^)]*\)\s*:(.+?)(?=\n    def |\nclass |\Z)',
            self.content,
            re.DOTALL
        )
        if not install_match:
            return

        install_body = install_match.group(1)

        # Check 1: returncode check
        has_returncode_check = bool(
            re.search(r'returncode\s*!=\s*0|process\.returncode\s*!=\s*0', install_body)
        )

        # Check 2: Error string detection
        has_error_string_check = bool(
            re.search(r"Checksum Failed|Downloading of needed test files failed", install_body)
            or re.search(r"'(ERROR|FAILED)'\s+in\s+", install_body)
        )

        # Check 3: sys.exit(1) on failure
        has_sys_exit = bool(
            re.search(r'sys\.exit\s*\(\s*1\s*\)', install_body)
        )

        fail_score = sum([has_returncode_check, has_error_string_check, has_sys_exit])

        if fail_score == 3:
            self.passed.append("✅ Install fail handling: returncode + error strings + sys.exit(1)")
        elif fail_score >= 2:
            missing = []
            if not has_returncode_check:
                missing.append("returncode check")
            if not has_error_string_check:
                missing.append("error string detection (Checksum Failed/ERROR/FAILED)")
            if not has_sys_exit:
                missing.append("sys.exit(1)")
            self.warnings.append(
                f"⚠️  WARNING: Install fail handling incomplete ({fail_score}/3)\n"
                f"   Missing: {', '.join(missing)}"
            )
        else:
            missing = []
            if not has_returncode_check:
                missing.append("returncode check")
            if not has_error_string_check:
                missing.append("error string detection")
            if not has_sys_exit:
                missing.append("sys.exit(1)")
            self.errors.append(
                f"❌ ERROR: Install fail handling missing ({fail_score}/3)\n"
                f"   Missing: {', '.join(missing)}\n"
                f"   Install failures will be silently ignored, causing phantom results.\n"
                f"   Required: returncode check + 'Checksum Failed'/'ERROR'/'FAILED' detection + sys.exit(1)"
            )

    def check_install_log_toggle(self):
        """Check if install_benchmark supports optional install log via env toggle."""
        has_install_method = re.search(r'def\s+install_benchmark\s*\(', self.content)
        if not has_install_method:
            return

        if "PTS_INSTALL_LOG" in self.content or "PTS_INSTALL_LOG_PATH" in self.content:
            self.passed.append("✅ install_benchmark supports optional install log via env toggle")
        else:
            self.warnings.append(
                "⚠️  WARNING: install_benchmark missing optional install log toggle "
                "(PTS_INSTALL_LOG / PTS_INSTALL_LOG_PATH)"
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
        # Look for the batch_env definition (single-line or multi-line)
        batch_env_match = re.search(
            r"batch_env\s*=\s*[\s\S]{0,400}TEST_RESULTS_DESCRIPTION=",
            self.content
        )
        
        if batch_env_match:
             self.passed.append("✅ Batch env includes TEST_RESULTS_DESCRIPTION (suppresses prompts)")
        else:
             self.warnings.append(
                "⚠️  WARNING: batch_env missing TEST_RESULTS_DESCRIPTION\n"
                "   This may cause interactive prompts. Add TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads"
            )

    def check_upload_safety(self):
        """
        Check if ensure_upload_disabled method exists and is called.
        """
        if not re.search(r"def\s+ensure_upload_disabled\s*\(", self.content):
            self.errors.append("❌ Missing ensure_upload_disabled() method to prevent accidental result uploads")
        elif not re.search(r"self\.ensure_upload_disabled\(\)", self.content):
             self.errors.append("❌ ensure_upload_disabled() method exists but is NOT called")
        else:
            self.passed.append("✅ Method ensure_upload_disabled() implemented and called")

    def check_pts_cache_clean(self):
        """Check if PTS result cleanup logic is present to prevent interactive prompts"""
        # Search for logic that removes existing results before running
        has_cleanup_logic = re.search(r"remove-result", self.content)
        
        if has_cleanup_logic:
            self.passed.append("✅ Automated PTS result cleanup logic detected")
        else:
            self.warnings.append(
                "⚠️  WARNING: Automated PTS result cleanup logic missing\n"
                "   Should execute 'phoronix-test-suite remove-result' before benchmark execution to prevent interactive prompts."
            )

    def check_results_dir_structure(self):
        """
        Check if self.results_dir is constructed using the standard project structure.
        Expected: self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark
        """
        # Look for the results_dir assignment
        # We handle variations in whitespace and line breaks
        results_dir_pattern = r'self\.results_dir\s*=\s*self\.project_root\s*/\s*"results"\s*/\s*self\.machine_name\s*/\s*self\.os_name\s*/\s*self\.test_category_dir\s*/\s*self\.benchmark'

        # Also check for variations with single quotes
        results_dir_pattern_sq = r"self\.results_dir\s*=\s*self\.project_root\s*/\s*'results'\s*/\s*self\.machine_name\s*/\s*self\.os_name\s*/\s*self\.test_category_dir\s*/\s*self\.benchmark"

        if re.search(results_dir_pattern, self.content) or re.search(results_dir_pattern_sq, self.content):
            self.passed.append("✅ standard results directory structure used")
        else:
             self.errors.append(
                "❌ CRITICAL: Invalid results directory structure\n"
                "   Expected: self.results_dir = self.project_root / \"results\" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark\n"
                "   This ensures consistent log organization across machines and OS versions."
            )

    def check_results_dir_cleanup_safety(self):
        """
        Check that run() does NOT use shutil.rmtree(self.results_dir) which destroys
        other threads' results when the runner is invoked per-thread.

        BUG: When cloud executor calls the runner separately for each thread count:
          W5: pts_runner 8   -> creates 8-thread files
          W6: pts_runner 12  -> rmtree deletes 8-thread files!
          W7: pts_runner 16  -> rmtree deletes 12-thread files!

        FIX: Only clean files for the current thread count:
          for num_threads in self.thread_list:
              prefix = f"{num_threads}-thread"
              # remove prefix.* and prefix/ directory only
        """
        # Check for the dangerous pattern
        dangerous_pattern = re.search(
            r'shutil\.rmtree\(self\.results_dir\)',
            self.content
        )

        if dangerous_pattern:
            self.errors.append(
                "❌ CRITICAL: shutil.rmtree(self.results_dir) destroys other threads' results\n"
                "   When the runner is invoked per-thread (e.g., pts_runner 8, then pts_runner 12),\n"
                "   rmtree deletes ALL previous thread results.\n"
                "   FIX: Replace with thread-specific cleanup:\n"
                "     self.results_dir.mkdir(parents=True, exist_ok=True)\n"
                "     for num_threads in self.thread_list:\n"
                "         prefix = f\"{num_threads}-thread\"\n"
                "         thread_dir = self.results_dir / prefix\n"
                "         if thread_dir.exists(): shutil.rmtree(thread_dir)\n"
                "         for f in self.results_dir.glob(f\"{prefix}.*\"): f.unlink()"
            )
            return

        # Check for the correct pattern
        safe_pattern = re.search(
            r'for\s+num_threads\s+in\s+self\.thread_list.*?prefix.*?thread',
            self.content,
            re.DOTALL
        )

        if safe_pattern:
            self.passed.append("✅ thread-specific results cleanup (preserves other threads)")
        else:
            self.warnings.append(
                "⚠️  WARNING: Could not verify thread-specific results cleanup pattern\n"
                "   Expected: cleanup loop over self.thread_list with prefix-based file removal"
            )

    def check_thread_capping(self):
        """
        Check if thread count is capped at vcpu_count (min(threads_arg, self.vcpu_count))
        and scaling mode uses the correct pattern (list(range(1, self.vcpu_count + 1))).

        Exception: Single-threaded benchmarks (e.g., redis, phpbench, simdjson, tinymembench, apache)
        are allowed to use self.thread_list = [1] and ignore thread arguments.
        """
        # Single-threaded benchmarks that are intentionally fixed at 1 thread
        single_threaded_benchmarks = [
            'phpbench', 'simdjson', 'tinymembench', 'apache'
        ]

        # Check if this is a single-threaded benchmark
        is_single_threaded = any(name in self.filepath.name for name in single_threaded_benchmarks)

        if is_single_threaded:
            # Check that it uses thread_list = [1]
            if re.search(r'self\.thread_list\s*=\s*\[1\]', self.content):
                self.passed.append("✅ Single-threaded benchmark correctly uses thread_list = [1]")
            else:
                self.warnings.append(
                    f"⚠️  WARNING: Single-threaded benchmark should use self.thread_list = [1]\n"
                    f"   Benchmark: {self.filepath.name}"
                )
            return

        # For multi-threaded benchmarks, check for proper patterns

        # Check 1: Scaling mode pattern (threads_arg is None)
        # Expected: self.thread_list = list(range(1, self.vcpu_count + 1))
        has_correct_scaling = re.search(
            r'self\.thread_list\s*=\s*list\(\s*range\(\s*1\s*,\s*self\.vcpu_count\s*\+\s*1\s*\)\s*\)',
            self.content
        )

        # Anti-pattern: Using custom scaling methods like get_scaling_thread_list()
        has_custom_scaling = re.search(
            r'self\.thread_list\s*=\s*self\.get_scaling_thread_list\(\)',
            self.content
        )

        # Check 2: Fixed mode pattern (threads_arg is not None)
        # Expected: min(threads_arg, self.vcpu_count)
        has_min_capping = re.search(
            r'min\(\s*(?:threads_arg|num_threads)\s*,\s*self\.vcpu_count\s*\)',
            self.content
        )

        # Anti-pattern: Direct assignment without capping
        # Matches: self.thread_list = [threads_arg] or self.thread_list = [num_threads]
        has_uncapped_assignment = re.search(
            r'self\.thread_list\s*=\s*\[\s*(?:threads_arg|num_threads)\s*\]',
            self.content
        )

        # Evaluate results
        issues = []

        if not has_correct_scaling and not has_custom_scaling:
            issues.append(
                "Scaling mode pattern not found\n"
                "   Expected: self.thread_list = list(range(1, self.vcpu_count + 1))"
            )
        elif has_custom_scaling:
            self.errors.append(
                "❌ CRITICAL: Custom scaling method detected\n"
                "   Found: self.thread_list = self.get_scaling_thread_list()\n"
                "   Expected: self.thread_list = list(range(1, self.vcpu_count + 1))\n"
                "   Reference: CODE_TEMPLATE.md lines 232-237\n"
                "   Issue: Should use standard continuous scaling pattern [1, 2, 3, ..., nproc]"
            )

        if has_min_capping and has_correct_scaling:
            self.passed.append("✅ Thread handling correct: scaling mode uses range(1, vcpu+1), fixed mode uses min(threads_arg, vcpu)")
        elif has_min_capping and not has_custom_scaling:
            self.passed.append("✅ Thread count properly capped at vcpu_count: min(threads_arg, self.vcpu_count)")
        elif has_uncapped_assignment:
            self.errors.append(
                "❌ CRITICAL: Thread count not capped at vcpu_count\n"
                "   Found: self.thread_list = [threads_arg] or self.thread_list = [num_threads]\n"
                "   Expected: n = min(threads_arg, self.vcpu_count); self.thread_list = [n]\n"
                "   Reference: CODE_TEMPLATE.md lines 232-237\n"
                "   Issue: User may specify 288 threads on a 4-core system, causing issues"
            )
        else:
            # Check if thread_list is set at all in __init__
            has_thread_list_setup = re.search(r'self\.thread_list\s*=', self.content)
            if has_thread_list_setup and not has_custom_scaling:
                self.warnings.append(
                    "⚠️  WARNING: Thread capping pattern unclear\n"
                    "   Should use: n = min(threads_arg, self.vcpu_count); self.thread_list = [n]\n"
                    "   Reference: CODE_TEMPLATE.md lines 232-237"
                )
            elif not has_thread_list_setup:
                self.warnings.append(
                    "⚠️  WARNING: self.thread_list not initialized in __init__"
                )

    def find_hardcoded_thread_lists(self):
        """
        Detect hardcoded numeric thread lists assigned to self.thread_list.
        """
        try:
            tree = ast.parse(self.content)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
                value = node.value
            else:
                continue

            for target in targets:
                if not (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == 'self'
                    and target.attr == 'thread_list'
                ):
                    continue

                thread_values = self._extract_literal_int_list(value)
                if thread_values is not None:
                    lineno = getattr(node, 'lineno', 0)
                    self.hardcoded_thread_lists.append((lineno, thread_values))

    def _extract_literal_int_list(self, value):
        """
        Return list of ints if value is a literal list/tuple of ints, else None.
        """
        if isinstance(value, (ast.List, ast.Tuple)):
            items = value.elts
        elif isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == 'list':
            if len(value.args) != 1:
                return None
            arg = value.args[0]
            if isinstance(arg, (ast.List, ast.Tuple)):
                items = arg.elts
            else:
                return None
        else:
            return None

        numbers = []
        for item in items:
            if isinstance(item, ast.Constant) and isinstance(item.value, int):
                numbers.append(item.value)
            else:
                return None
        return numbers

    def check_log_file_handling(self):
        """
        Check if benchmark's PTS install.sh or execution scripts use $LOG_FILE variable
        and verify that the Runner script implements proper patching to handle it.

        Background:
            Some PTS install.sh or execution scripts redirect output to $LOG_FILE variable:
                echo "test" > $LOG_FILE
                echo "data" >> $LOG_FILE 2>&1

            When $LOG_FILE is not set in environment, bash produces error:
                "ambiguous redirect"

            This causes "test run did not produce a result" failures.

        Solution Pattern:
            Runner's install_benchmark() should patch the execution script:
                script_path = Path(f"~/.phoronix-test-suite/installed-tests/pts/{self.benchmark}")
                script_file = script_path / "script-name.sh"
                content = script_file.read_text()
                content = re.sub(r'>\\s*\\$LOG_FILE', '', content)  # Remove > $LOG_FILE
                content = re.sub(r'>>\\s*\\$LOG_FILE\\s*2>&1', '', content)  # Remove >> $LOG_FILE 2>&1
                script_file.write_text(content)

        See: CODE_TEMPLATE.md Q5 for detailed solution
        """
        # Extract benchmark name from filename (pts_runner_<benchmark>.py)
        benchmark_match = re.match(r'pts_runner_(.+)\.py', self.filepath.name)
        if not benchmark_match:
            return

        benchmark_name = benchmark_match.group(1)

        # Construct expected benchmark directory path
        from pathlib import Path
        import os

        pts_dir = Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / benchmark_name
        
        install_found = False
        has_log_file_usage = False
        script_files_checked = []

        if pts_dir.exists():
            # Check all shell scripts in the benchmark directory (not recursive, just top level)
            for script_file in pts_dir.glob("*.sh"):
                script_files_checked.append(script_file.name)
                install_found = True
                script_content = script_file.read_text()
                
                # Check if $LOG_FILE is used
                if re.search(r'\$LOG_FILE', script_content):
                    has_log_file_usage = True
                    break
            
            # Also check scripts without .sh extension (like 'numpy', 'pgbench', etc.)
            for script_file in pts_dir.iterdir():
                if script_file.is_file() and os.access(script_file, os.X_OK):
                    # Executable file - check if it's a shell script
                    try:
                        first_line = script_file.read_text().split('\n')[0]
                        if first_line.startswith('#!') and ('sh' in first_line or 'bash' in first_line):
                            script_files_checked.append(script_file.name)
                            install_found = True
                            script_content = script_file.read_text()
                            
                            # Check if $LOG_FILE is used
                            if re.search(r'\$LOG_FILE', script_content):
                                has_log_file_usage = True
                                break
                    except:
                        pass

        # Check if Runner script implements patching
        # Look for patterns that indicate LOG_FILE handling
        has_patch_implementation = False
        
        # Simple check: Does the file contain both "LOG_FILE" and patching keywords?
        if 'LOG_FILE' in self.content:
            # Check for patching keywords
            patch_keywords = [
                'patch',  # "Patch pgbench execution script"
                're.sub',  # regex substitution
                '.replace',  # string replacement
            ]
            
            for keyword in patch_keywords:
                if keyword in self.content:
                    has_patch_implementation = True
                    break

        # Evaluate results
        if not install_found:
            # Cannot verify - benchmark not installed
            self.warnings.append(
                f"⚠️  INFO: Cannot verify $LOG_FILE handling - benchmark not installed\n"
                f"   Install {benchmark_name} first: phoronix-test-suite install {benchmark_name}\n"
                f"   Then re-run compliance check"
            )
        elif has_log_file_usage and has_patch_implementation:
            self.passed.append(
                f"✅ $LOG_FILE handling: Scripts use $LOG_FILE, Runner patches it correctly"
            )
        elif has_log_file_usage and not has_patch_implementation:
            self.errors.append(
                f"❌ CRITICAL: $LOG_FILE problem detected but not handled\n"
                f"   Found: Script(s) use $LOG_FILE variable: {', '.join(script_files_checked)}\n"
                f"   Missing: Runner script does not patch execution scripts\n"
                f"   Impact: Will fail with 'ambiguous redirect' error\n"
                f"   Solution: Add script patching in install_benchmark() method\n"
                f"   Reference: CODE_TEMPLATE.md Q5\n"
                f"   Example pattern:\n"
                f"       script_file = Path(...) / '{benchmark_name}'\n"
                f"       content = script_file.read_text()\n"
                f"       content = re.sub(r'>\\\\s*\\\\$LOG_FILE', '', content)\n"
                f"       content = re.sub(r'>>\\\\s*\\\\$LOG_FILE\\\\s*2>&1', '', content)\n"
                f"       script_file.write_text(content)"
            )
        elif not has_log_file_usage and has_patch_implementation:
            self.warnings.append(
                f"⚠️  WARNING: Runner implements $LOG_FILE patching but scripts don't use it\n"
                f"   Checked: {', '.join(script_files_checked)}\n"
                f"   This is harmless but may be unnecessary code"
            )
        else:
            # No $LOG_FILE usage, no patching - this is fine
            self.passed.append(
                f"✅ $LOG_FILE handling: Not needed (checked {len(script_files_checked)} script(s))"
            )

    def check_perf_init_order(self):
        """
        Check if perf initialization order is correct.

        CRITICAL: check_and_setup_perf_permissions() MUST be called BEFORE get_perf_events()

        Wrong order causes perf to fail on cloud VMs (OCI, etc.) because perf_event_paranoid
        is not adjusted before testing perf availability.

        Correct order:
            self.perf_paranoid = self.check_and_setup_perf_permissions()  # FIRST
            self.perf_events = self.get_perf_events()                      # SECOND

        Wrong order:
            self.perf_events = self.get_perf_events()                      # FIRST (fails on cloud)
            self.perf_paranoid = self.check_and_setup_perf_permissions()  # SECOND (too late)
        """
        # Find positions of both calls
        perf_paranoid_match = re.search(r'self\.perf_paranoid\s*=\s*self\.check_and_setup_perf_permissions', self.content)
        perf_events_match = re.search(r'self\.perf_events\s*=\s*self\.get_perf_events', self.content)

        if not perf_paranoid_match:
            self.warnings.append(
                "⚠️  WARNING: check_and_setup_perf_permissions() call not found\n"
                "   Should call: self.perf_paranoid = self.check_and_setup_perf_permissions()"
            )
            return

        if not perf_events_match:
            self.warnings.append(
                "⚠️  WARNING: get_perf_events() call not found\n"
                "   Should call: self.perf_events = self.get_perf_events()"
            )
            return

        # Check order: perf_paranoid should come BEFORE perf_events
        paranoid_pos = perf_paranoid_match.start()
        events_pos = perf_events_match.start()

        if paranoid_pos < events_pos:
            self.passed.append("✅ Perf initialization order correct (permissions checked before testing)")
        else:
            self.errors.append(
                "❌ CRITICAL: Perf initialization order is wrong\n"
                "   Found: get_perf_events() called BEFORE check_and_setup_perf_permissions()\n"
                "   Expected: check_and_setup_perf_permissions() should be called FIRST\n"
                "   Impact: Perf will fail on cloud VMs (OCI, etc.) with restrictive defaults\n"
                "   Fix: Swap the order in __init__:\n"
                "        self.perf_paranoid = self.check_and_setup_perf_permissions()  # FIRST\n"
                "        self.perf_events = self.get_perf_events()                      # SECOND"
            )

    def check_cpu_frequency_methods(self):
        """
        Check if cross-platform CPU frequency methods are implemented.

        Required methods:
        - get_cpu_frequencies(): Multi-method approach for x86_64, ARM64, and cloud VMs
        - record_cpu_frequency(): Wrapper to record frequencies to file

        Old pattern to avoid:
        - grep "cpu MHz" /proc/cpuinfo (only works on x86_64)

        New pattern required:
        - Uses get_cpu_frequencies() which tries:
          1. /proc/cpuinfo (x86_64)
          2. /sys/devices/system/cpu/cpufreq (ARM64)
          3. lscpu (fallback)
        """
        # Check for get_cpu_frequencies method
        has_get_cpu_freq = re.search(r'def\s+get_cpu_frequencies\s*\(', self.content)

        # Check for record_cpu_frequency method
        has_record_cpu_freq = re.search(r'def\s+record_cpu_frequency\s*\(', self.content)

        # Check for old pattern usage in run_benchmark (not in get_cpu_frequencies method)
        # Old pattern: cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk...'
        # This pattern is ONLY acceptable inside get_cpu_frequencies() as a fallback
        has_old_pattern = re.search(r'cmd_template\s*=\s*["\']grep\s+["\']?cpu MHz', self.content)

        # Check if record_cpu_frequency is used in run_benchmark
        uses_new_pattern = re.search(r'self\.record_cpu_frequency\(', self.content)

        issues = []

        if not has_get_cpu_freq:
            issues.append("Missing get_cpu_frequencies() method")

        if not has_record_cpu_freq:
            issues.append("Missing record_cpu_frequency() method")

        if has_old_pattern:
            issues.append("Uses old 'grep cpu MHz' pattern (only works on x86_64)")

        if has_get_cpu_freq and has_record_cpu_freq and not has_old_pattern:
            if uses_new_pattern:
                self.passed.append("✅ Cross-platform CPU frequency methods implemented and used")
            else:
                self.warnings.append(
                    "⚠️  WARNING: CPU frequency methods exist but may not be used in run_benchmark\n"
                    "   Should use: self.record_cpu_frequency(freq_start_file)"
                )
        elif issues:
            self.errors.append(
                "❌ CRITICAL: Cross-platform CPU frequency handling incomplete\n"
                f"   Issues: {', '.join(issues)}\n"
                "   Impact: CPU frequency recording will fail on ARM64 and some cloud VMs\n"
                "   Fix: Add get_cpu_frequencies() and record_cpu_frequency() methods\n"
                "   Reference: CODE_TEMPLATE.md 'クロスプラットフォームCPU周波数取得' section"
            )

    def check_downloads_xml_prefetch(self):
        """
        Check if PreSeedDownloader attempts to fetch downloads.xml when missing.

        Expected pattern:
          - logs when downloads.xml is missing
          - runs 'phoronix-test-suite info <benchmark>' to fetch test profile
          - rechecks downloads.xml presence
        """
        has_downloads_xml_check = re.search(r'downloads\.xml', self.content)
        has_pts_info_call = re.search(r'phoronix-test-suite["\']?,\s*[\'"]info', self.content)
        has_missing_log = re.search(r'downloads\.xml not found', self.content)

        if has_downloads_xml_check and has_pts_info_call and has_missing_log:
            self.passed.append("✅ downloads.xml prefetch via phoronix-test-suite info implemented")
        elif has_downloads_xml_check:
            self.warnings.append(
                "⚠️  WARNING: downloads.xml prefetch missing\n"
                "   Recommended: if downloads.xml is missing, run 'phoronix-test-suite info <benchmark>'\n"
                "   This enables aria2c pre-seeding before install"
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

   

    parser.add_argument(

        '--install-and-check',

        action='store_true',

        help='Install benchmarks before checking (use with --all to verify $LOG_FILE handling)'

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
    hardcoded_scripts = []

   

    for filepath in files:

        if not filepath.exists():

            print(f"ERROR: File not found: {filepath}")

            continue

       

        # Install benchmark if --install-and-check is specified

        if args.install_and_check:

            # Extract benchmark name from filename (pts_runner_<benchmark>.py)

            import re

            benchmark_match = re.match(r'pts_runner_(.+)\.py', filepath.name)

            if benchmark_match:

                benchmark_name = benchmark_match.group(1)

                

                # Check if already installed

                pts_dir = Path.home() / ".phoronix-test-suite" / "installed-tests" / "pts" / benchmark_name

                if not pts_dir.exists():

                    print(f"\n{'='*80}")

                    print(f"Installing: {benchmark_name}")

                    print(f"{'='*80}")

                    

                    import subprocess

                    result = subprocess.run(

                        ['phoronix-test-suite', 'install', benchmark_name],

                        capture_output=False,

                        text=True

                    )

                    

                    if result.returncode != 0:

                        print(f"⚠️  WARNING: Failed to install {benchmark_name}")

                        print(f"   Continuing with compliance check anyway...")

                else:

                    print(f"\n[INFO] {benchmark_name} already installed, skipping installation")

       

        checker = ComplianceChecker(filepath)

        passed, num_errors, num_warnings = checker.check_all()
        if checker.hardcoded_thread_lists:
            hardcoded_scripts.append((filepath.name, checker.hardcoded_thread_lists))

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

        print(f"\n{'='*80}")
        print("HARDCODED THREAD LISTS")
        print(f"{'='*80}")
        if hardcoded_scripts:
            for name, entries in hardcoded_scripts:
                for lineno, threads in entries:
                    line_label = f"line {lineno}" if lineno else "line ?"
                    print(f"{name}: {line_label} -> {threads}")
        else:
            print("None")

        return 0 if failed_count == 0 else 1

    else:

        # Single file - return based on its result

        return 0 if results[0][1] else 1



if __name__ == "__main__":

    sys.exit(main()) 
