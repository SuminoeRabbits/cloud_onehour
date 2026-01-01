#!/usr/bin/env python3
"""
PTS Runner for build-gcc-1.5.0

Based on test_suite.json configuration:
- test_category: "Build Process"
- THFix_in_compile: false - Thread count can be changed at runtime
- THChange_at_runtime: true - Runtime thread configuration via NUM_CPU_CORES
- TH_scaling: env:NUM_CPU_CORES - Uses make -j $NUM_CPU_CORES
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class BuildGccRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize GCC build benchmark runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "build-gcc-1.5.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Build Process"
        # Replace spaces with underscores in test_category for directory name
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)

        # Determine thread execution mode
        if threads_arg is None:
            # Scaling mode: 1 to vCPU
            self.thread_list = list(range(1, self.vcpu_count + 1))
        else:
            # Fixed mode: single thread count
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

        # Check and setup perf permissions
        self.perf_paranoid = self.check_and_setup_perf_permissions()

    def check_and_setup_perf_permissions(self):
        """
        Check perf_event_paranoid setting and adjust if needed.

        Returns:
            int: Current perf_event_paranoid value after adjustment
        """
        print(f"\n{'='*80}")
        print(">>> Checking perf_event_paranoid setting")
        print(f"{'='*80}")

        try:
            # Read current setting
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True,
                text=True,
                check=True
            )
            current_value = int(result.stdout.strip())

            print(f"  [INFO] Current perf_event_paranoid: {current_value}")

            # If too restrictive, try to adjust
            # Note: -a (system-wide) requires perf_event_paranoid <= 0
            if current_value >= 1:
                print(f"  [WARN] perf_event_paranoid={current_value} is too restrictive for system-wide monitoring")
                print(f"  [INFO] Attempting to adjust perf_event_paranoid to 0...")

                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True,
                    text=True
                )

                if result.returncode == 0:
                    print(f"  [OK] perf_event_paranoid adjusted to 0 (temporary, until reboot)")
                    print(f"       Per-CPU metrics and hardware counters enabled")
                    print(f"       Full monitoring mode: perf stat -A -a")
                    return 0
                else:
                    print(f"  [ERROR] Failed to adjust perf_event_paranoid (sudo required)")
                    print(f"  [WARN] Running in LIMITED mode:")
                    print(f"         - No per-CPU metrics (no -A -a flags)")
                    print(f"         - No hardware counters (cycles, instructions)")
                    print(f"         - Software events only (aggregated)")
                    print(f"         - IPC calculation not available")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                print(f"       Full monitoring mode: perf stat -A -a")
                return current_value

        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            print(f"  [WARN] Assuming restrictive mode (perf_event_paranoid=2)")
            print(f"         Running in LIMITED mode without per-CPU metrics")
            return 2

    def clean_pts_cache(self):
        """Clean all PTS cache for fresh installation."""
        print(">>> Cleaning PTS cache...")

        pts_home = Path.home() / '.phoronix-test-suite'

        # Clean test profiles
        test_profile_dir = pts_home / 'test-profiles' / 'pts' / self.benchmark
        if test_profile_dir.exists():
            print(f"  [CLEAN] Removing test profile: {test_profile_dir}")
            shutil.rmtree(test_profile_dir)

        # Clean installed tests
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark
        if installed_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_dir}")
            shutil.rmtree(installed_dir)

        print("  [OK] PTS cache cleaned")

    def get_cpu_affinity_list(self, n):
        """
        Generate CPU affinity list for HyperThreading optimization.

        Prioritizes physical cores (even IDs) first, then logical cores (odd IDs).
        Pattern: {0,2,4,...,1,3,5,...}

        Args:
            n: Number of threads

        Returns:
            Comma-separated CPU list string (e.g., "0,2,4,1,3")
        """
        half = self.vcpu_count // 2
        cpu_list = []

        if n <= half:
            # Physical cores only: 0,2,4,...
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            # Physical cores + logical cores
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])

        return ','.join(cpu_list)

    def install_benchmark(self):
        """
        Install build-gcc-1.5.0 with GCC-14 native compilation.

        Note: Unlike coremark, GCC build does NOT need reinstallation for each thread count
        because it supports runtime thread configuration via make -j argument.

        Since THFix_in_compile=false, NUM_CPU_CORES is NOT set during build.
        Thread count is controlled at runtime via NUM_CPU_CORES environment variable.
        """
        print(f"\n>>> Installing {self.benchmark_full}...")

        # Remove existing installation first
        print(f"  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        print(f"  [INSTALL CMD] {remove_cmd}")
        subprocess.run(
            ['bash', '-c', remove_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Build install command with environment variables
        # Note: NUM_CPU_CORES is NOT set here because THFix_in_compile=false
        # Thread control is done at runtime, not compile time
        # Use batch-install to suppress prompts
        # MAKEFLAGS: parallelize compilation itself with -j$(nproc)
        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 phoronix-test-suite batch-install {self.benchmark_full}'

        # Print install command for debugging (as per README requirement)
        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Execute install command
        result = subprocess.run(
            ['bash', '-c', install_cmd],
            text=True
        )

        if result.returncode != 0:
            print(f"  [ERROR] Installation failed")
            sys.exit(1)

        print(f"  [OK] Installation completed")

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """
        Parse perf stat output and CPU frequency files to generate performance summary.

        Args:
            perf_stats_file: Path to perf stat output file
            freq_start_file: Path to start frequency file
            freq_end_file: Path to end frequency file
            cpu_list: String of CPU IDs used (e.g., "0,2,4")

        Returns:
            dict: Performance summary containing per-CPU metrics
        """
        print(f"\n>>> Parsing perf stats and frequency data")
        print(f"  [INFO] perf stats file: {perf_stats_file}")
        print(f"  [INFO] freq start file: {freq_start_file}")
        print(f"  [INFO] freq end file: {freq_end_file}")
        print(f"  [INFO] cpu list: {cpu_list}")

        # Parse CPU list to get individual CPU IDs
        cpu_ids = [int(c.strip()) for c in cpu_list.split(',')]
        print(f"  [DEBUG] Parsed CPU IDs: {cpu_ids}")

        # Initialize data structures for per-CPU metrics
        per_cpu_metrics = {}
        for cpu_id in cpu_ids:
            per_cpu_metrics[cpu_id] = {
                'cycles': 0,
                'instructions': 0,
                'cpu_clock': 0,
                'task_clock': 0,
                'context_switches': 0,
                'cpu_migrations': 0
            }

        # Parse perf stat output file
        print(f"  [INFO] Parsing perf stat output...")
        try:
            with open(perf_stats_file, 'r') as f:
                perf_content = f.read()
                print(f"  [DEBUG] perf stat file size: {len(perf_content)} bytes")

                # Parse per-CPU metrics (format: "CPU<n>   <value>   <event>")
                # Example: "CPU0                123456789      cycles"
                for line in perf_content.split('\n'):
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    # Match CPU-specific lines
                    # Format: "CPU<n>   <value>   <event>"
                    match = re.match(r'CPU(\d+)\s+([0-9,]+)\s+(\S+)', line)
                    if match:
                        cpu_num = int(match.group(1))
                        value_str = match.group(2).replace(',', '')
                        event = match.group(3)

                        # Only process CPUs in our cpu_list
                        if cpu_num not in cpu_ids:
                            continue

                        try:
                            value = float(value_str)
                        except ValueError:
                            print(f"  [WARN] Failed to parse value '{value_str}' for CPU{cpu_num} {event}")
                            continue

                        # Map event names to our data structure
                        if event == 'cycles':
                            per_cpu_metrics[cpu_num]['cycles'] = value
                        elif event == 'instructions':
                            per_cpu_metrics[cpu_num]['instructions'] = value
                        elif event == 'cpu-clock':
                            per_cpu_metrics[cpu_num]['cpu_clock'] = value
                        elif event == 'task-clock':
                            per_cpu_metrics[cpu_num]['task_clock'] = value
                        elif event == 'context-switches':
                            per_cpu_metrics[cpu_num]['context_switches'] = value
                        elif event == 'cpu-migrations':
                            per_cpu_metrics[cpu_num]['cpu_migrations'] = value

            print(f"  [OK] Parsed perf stat data for {len(per_cpu_metrics)} CPUs")

        except Exception as e:
            print(f"  [ERROR] Failed to parse perf stat file: {e}")
            raise

        # Parse frequency files
        print(f"  [INFO] Parsing frequency files...")
        freq_start = {}
        freq_end = {}

        try:
            # Read start frequencies (format: one frequency per line in kHz)
            with open(freq_start_file, 'r') as f:
                lines = f.read().strip().split('\n')
                for i, line in enumerate(lines):
                    if line.strip():
                        freq_start[i] = float(line.strip())
            print(f"  [DEBUG] Read {len(freq_start)} start frequencies")

            # Read end frequencies
            with open(freq_end_file, 'r') as f:
                lines = f.read().strip().split('\n')
                for i, line in enumerate(lines):
                    if line.strip():
                        freq_end[i] = float(line.strip())
            print(f"  [DEBUG] Read {len(freq_end)} end frequencies")

        except Exception as e:
            print(f"  [ERROR] Failed to parse frequency files: {e}")
            raise

        # Calculate metrics
        print(f"  [INFO] Calculating performance metrics...")
        perf_summary = {
            'avg_frequency_ghz': {},
            'start_frequency_ghz': {},
            'end_frequency_ghz': {},
            'ipc': {},
            'total_cycles': {},
            'total_instructions': {},
            'cpu_utilization_percent': 0.0,
            'elapsed_time_sec': 0.0
        }

        total_task_clock = 0.0
        max_task_clock = 0.0

        for cpu_id in cpu_ids:
            metrics = per_cpu_metrics[cpu_id]

            # avg_frequency_ghz = cycles / (cpu-clock / 1000) / 1e9
            if metrics['cpu_clock'] > 0:
                avg_freq = metrics['cycles'] / (metrics['cpu_clock'] / 1000.0) / 1e9
                perf_summary['avg_frequency_ghz'][str(cpu_id)] = round(avg_freq, 3)
            else:
                perf_summary['avg_frequency_ghz'][str(cpu_id)] = 0.0

            # start_frequency_ghz = freq_start[cpu] / 1,000,000 (kHz to GHz)
            if cpu_id in freq_start:
                start_freq = freq_start[cpu_id] / 1_000_000.0
                perf_summary['start_frequency_ghz'][str(cpu_id)] = round(start_freq, 3)
            else:
                perf_summary['start_frequency_ghz'][str(cpu_id)] = 0.0

            # end_frequency_ghz = freq_end[cpu] / 1,000,000 (kHz to GHz)
            if cpu_id in freq_end:
                end_freq = freq_end[cpu_id] / 1_000_000.0
                perf_summary['end_frequency_ghz'][str(cpu_id)] = round(end_freq, 3)
            else:
                perf_summary['end_frequency_ghz'][str(cpu_id)] = 0.0

            # ipc = instructions / cycles
            if metrics['cycles'] > 0:
                ipc = metrics['instructions'] / metrics['cycles']
                perf_summary['ipc'][str(cpu_id)] = round(ipc, 2)
            else:
                perf_summary['ipc'][str(cpu_id)] = 0.0

            # Store raw values
            perf_summary['total_cycles'][str(cpu_id)] = int(metrics['cycles'])
            perf_summary['total_instructions'][str(cpu_id)] = int(metrics['instructions'])

            # Track task-clock for utilization calculation
            total_task_clock += metrics['task_clock']
            max_task_clock = max(max_task_clock, metrics['task_clock'])

        # Calculate elapsed time (use max task-clock as elapsed time in ms)
        if max_task_clock > 0:
            perf_summary['elapsed_time_sec'] = round(max_task_clock / 1000.0, 2)

        # Calculate CPU utilization (total task-clock / elapsed_time / num_cpus * 100)
        # This represents the average CPU utilization across all CPUs
        if max_task_clock > 0:
            utilization = (total_task_clock / max_task_clock / len(cpu_ids)) * 100.0
            perf_summary['cpu_utilization_percent'] = round(utilization, 1)

        print(f"  [OK] Performance metrics calculated")
        print(f"  [DEBUG] Elapsed time: {perf_summary['elapsed_time_sec']} sec")
        print(f"  [DEBUG] CPU utilization: {perf_summary['cpu_utilization_percent']}%")

        return perf_summary

    def run_benchmark(self, num_threads):
        """
        Run benchmark with specified thread count.

        Args:
            num_threads: Number of threads to use
        """
        print(f"\n{'='*80}")
        print(f">>> Running benchmark with {num_threads} thread(s)")
        print(f"{'='*80}")

        # Create output directory
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

        # Define file paths for perf stats and frequency monitoring
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # Build PTS command based on thread count
        # If N >= vCPU: don't use taskset (all vCPUs assigned)
        # If N < vCPU: use taskset with CPU affinity

        # Environment variables to suppress all prompts
        # BATCH_MODE, SKIP_ALL_PROMPTS: additional safeguards
        # TEST_RESULTS_NAME, TEST_RESULTS_IDENTIFIER: auto-generate result names
        # DISPLAY_COMPACT_RESULTS: suppress "view text results" prompt
        # Note: PTS_USER_PATH_OVERRIDE removed - use default ~/.phoronix-test-suite/ with batch-setup config
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME=build-gcc-{num_threads}threads TEST_RESULTS_IDENTIFIER=build-gcc-{num_threads}threads'

        if num_threads >= self.vcpu_count:
            # All vCPUs mode - no taskset needed
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            # Partial vCPU mode - use taskset with affinity
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        # Wrap PTS command with perf stat
        # CRITICAL: Environment variables MUST come BEFORE perf stat (README)
        # Otherwise perf stat won't propagate them to the actual command
        pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} perf stat -e cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations -A -a -o {perf_stats_file} {pts_base_cmd}'

        print(f"[INFO] {cpu_info}")

        # Print PTS command to stdout for debugging (as per README requirement)
        print(f"\n{'>'*80}")
        print(f"[PTS BENCHMARK COMMAND]")
        print(f"  {pts_cmd}")
        print(f"  {cpu_info}")
        print(f"  Output:")
        print(f"    Thread log: {log_file}")
        print(f"    Stdout log: {stdout_log}")
        print(f"    Perf stats: {perf_stats_file}")
        print(f"    Freq start: {freq_start_file}")
        print(f"    Freq end: {freq_end_file}")
        print(f"    Perf summary: {perf_summary_file}")
        print(f"{'<'*80}\n")

        # Record CPU frequency before benchmark
        # Use /proc/cpuinfo method to avoid hardware dependencies (as per README)
        print(f"[INFO] Recording CPU frequency before benchmark...")
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_start_file)
        result = subprocess.run(
            ['bash', '-c', command],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"  [WARN] Failed to record start frequency: {result.stderr}")
        else:
            print(f"  [OK] Start frequency recorded")

        # Execute with tee-like behavior: output to both terminal and log files
        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
            # Write command header to stdout.log
            stdout_f.write(f"\n{'='*80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{cpu_info}\n")
            stdout_f.write(f"{'='*80}\n\n")
            stdout_f.flush()

            # Run PTS command with real-time output streaming
            process = subprocess.Popen(
                ['bash', '-c', pts_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Stream output to terminal, thread-specific log, and cumulative stdout.log
            for line in process.stdout:
                print(line, end='')  # Terminal output
                log_f.write(line)    # Thread-specific log file
                stdout_f.write(line) # Cumulative stdout.log
                log_f.flush()
                stdout_f.flush()

            process.wait()
            returncode = process.returncode

        # Record CPU frequency after benchmark
        # Use /proc/cpuinfo method to avoid hardware dependencies (as per README)
        print(f"\n[INFO] Recording CPU frequency after benchmark...")
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_end_file)
        result = subprocess.run(
            ['bash', '-c', command],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            print(f"  [WARN] Failed to record end frequency: {result.stderr}")
        else:
            print(f"  [OK] End frequency recorded")

        if returncode == 0:
            print(f"\n[OK] Benchmark completed successfully")
            print(f"     Thread log: {log_file}")
            print(f"     Stdout log: {stdout_log}")

            # Parse perf stats and save summary
            try:
                perf_summary = self.parse_perf_stats_and_freq(
                    perf_stats_file,
                    freq_start_file,
                    freq_end_file,
                    cpu_list
                )

                # Save perf summary to JSON
                with open(perf_summary_file, 'w') as f:
                    json.dump(perf_summary, f, indent=2)
                print(f"     Perf summary: {perf_summary_file}")

            except Exception as e:
                print(f"  [ERROR] Failed to parse perf stats: {e}")
                print(f"  [INFO] Benchmark results are still valid, continuing...")

        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            return False

        return True

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(f">>> Exporting benchmark results")
        print(f"{'='*80}")

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"build-gcc-{num_threads}threads"

            # Check if result exists
            result_dir = pts_results_dir / result_name
            if not result_dir.exists():
                print(f"[WARN] Result not found for {num_threads} threads: {result_dir}")
                continue

            print(f"\n[INFO] Exporting results for {num_threads} thread(s)...")

            # Export to CSV
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # PTS saves to ~/result_name.csv, move it to our results directory
                home_csv = Path.home() / f"{result_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                # PTS saves to ~/result_name.json, move it to our results directory
                home_json = Path.home() / f"{result_name}.json"
                if home_json.exists():
                    shutil.move(str(home_json), str(json_output))
                    print(f"  [OK] Saved: {json_output}")
            else:
                print(f"  [WARN] JSON export failed: {result.stderr}")

        print(f"\n[OK] Export completed")

    def generate_summary(self):
        """Generate summary.log and summary.json from all thread results."""
        print(f"\n{'='*80}")
        print(f">>> Generating summary")
        print(f"{'='*80}")

        summary_log = self.results_dir / "summary.log"
        summary_json_file = self.results_dir / "summary.json"

        # Collect results from all JSON files
        all_results = []
        for num_threads in self.thread_list:
            json_file = self.results_dir / f"{num_threads}-thread.json"
            if json_file.exists():
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    # Extract benchmark result
                    for result_id, result in data.get('results', {}).items():
                        for system_id, system_result in result.get('results', {}).items():
                            all_results.append({
                                'threads': num_threads,
                                'value': system_result.get('value'),
                                'raw_values': system_result.get('raw_values', []),
                                'test_name': result.get('title'),
                                'description': result.get('description'),
                                'unit': result.get('scale')
                            })

        if not all_results:
            print("[WARN] No results found for summary generation")
            return

        # Generate summary.log (human-readable)
        with open(summary_log, 'w') as f:
            f.write("="*80 + "\n")
            f.write(f"GCC Build Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")
                f.write(f"  Average: {result['value']:.2f} {result['unit']}\n")
                f.write(f"  Raw values: {', '.join([f'{v:.2f}' for v in result['raw_values']])}\n")
                f.write("\n")

            f.write("="*80 + "\n")
            f.write("Summary Table\n")
            f.write("="*80 + "\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20}\n")
            f.write("-"*80 + "\n")
            for result in all_results:
                f.write(f"{result['threads']:<10} {result['value']:<15.2f} {result['unit']:<20}\n")

        print(f"[OK] Summary log saved: {summary_log}")

        # Generate summary.json (AI-friendly format)
        summary_data = {
            "benchmark": self.benchmark,
            "test_category": self.test_category,
            "machine": self.machine_name,
            "vcpu_count": self.vcpu_count,
            "results": all_results
        }

        with open(summary_json_file, 'w') as f:
            json.dump(summary_data, f, indent=2)

        print(f"[OK] Summary JSON saved: {summary_json_file}")

    def run(self):
        """Main execution flow."""
        print(f"{'='*80}")
        print(f"GCC Build Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print(f"[INFO] Test category: {self.test_category}")
        print(f"[INFO] Thread mode: Runtime configurable (THChange_at_runtime=true)")
        print(f"[INFO] Threads to test: {self.thread_list}")
        print(f"[INFO] Results directory: {self.results_dir}")
        print()

        # Clean existing results directory before starting
        if self.results_dir.exists():
            print(f">>> Cleaning existing results directory...")
            print(f"  [INFO] Removing: {self.results_dir}")
            shutil.rmtree(self.results_dir)
            print(f"  [OK] Results directory cleaned")
            print()

        # Clean cache once at the beginning
        self.clean_pts_cache()

        # Install benchmark once (not per thread count, since THFix_in_compile=false)
        self.install_benchmark()

        # Run for each thread count
        failed = []
        for num_threads in self.thread_list:
            # Run benchmark
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        # Export results to CSV and JSON
        self.export_results()

        # Generate summary
        self.generate_summary()

        # Summary
        print(f"\n{'='*80}")
        print(f"Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests: {len(self.thread_list)}")
        print(f"Successful: {len(self.thread_list) - len(failed)}")
        print(f"Failed: {len(failed)}")
        if failed:
            print(f"Failed thread counts: {failed}")
        print(f"{'='*80}")

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description='GCC Build Benchmark Runner',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s           # Run with 1 to vCPU threads (scaling mode)
  %(prog)s 4         # Run with 4 threads only
  %(prog)s 16        # Run with 16 threads (capped at vCPU if exceeded)
        """
    )

    parser.add_argument(
        'threads',
        nargs='?',
        type=int,
        help='Number of threads (optional, omit for scaling mode)'
    )
    
    parser.add_argument(
        '--quick',
        action='store_true',
        help='Quick mode: run tests once (FORCE_TIMES_TO_RUN=1) for development'
    )

    args = parser.parse_args()
    
    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70%% time reduction)")

    # Validate threads argument
    if args.threads is not None and args.threads < 1:
        print(f"[ERROR] Thread count must be >= 1 (got: {args.threads})")
        sys.exit(1)

    # Run benchmark
    runner = BuildGccRunner(args.threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
