#!/usr/bin/env python3
"""
PTS Runner for Rustls 0.23.17

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * Rust
  * Curl
- Estimated Install Time: 69 Seconds
- Environment Size: 896 MB
- Test Type: Processor
- Supported Platforms: Linux, BSD, MacOSX

Test Characteristics:
- Multi-threaded: Yes (built-in multi-threaded benchmark)
- THFix_in_compile: false (uses built-in Rust benchmark with thread control)
- THChange_at_runtime: true (Rust benchmark handles thread count internally)
- Description: Modern TLS library written in Rust with built-in multi-threaded benchmarks
- Benchmark Suites: TLS handshake operations (various cipher suites)
"""

import os
import sys
import subprocess
import json
import shutil
import re
from pathlib import Path


class BenchmarkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize the Rustls benchmark runner.

        Rustls Characteristics:
        - Pure Rust TLS library with built-in benchmarks
        - Multi-threaded by default (Rust's built-in benchmark framework)
        - No compile-time thread fixing needed (Rust handles internally)
        - Multiple cipher suite benchmarks available
        - Modern, memory-safe TLS implementation

        Args:
            threads_arg: Number of threads to test (None = test all)
            quick_mode: If True, run with FORCE_TIMES_TO_RUN=1
        """
        # Benchmark configuration
        self.benchmark = "rustls-1.0.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Cryptography and TLS"
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Thread list setup
        if threads_arg is None:
            self.thread_list = list(range(1, self.vcpu_count + 1))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Results directory
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

        # Check perf permissions (standard Linux check)
        self.perf_paranoid = self.check_and_setup_perf_permissions()

        # Detect environment for logging
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # Feature Detection: Check if perf is actually functional
        self.perf_events = self.get_perf_events()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

    def get_os_name(self):
        """
        Get OS name and version formatted as <Distro>_<Version>.
        Example: Ubuntu_22_04
        """
        try:
            # Try lsb_release first
            cmd = "lsb_release -d -s"
            result = subprocess.run(cmd.split(), capture_output=True, text=True)
            if result.returncode == 0:
                description = result.stdout.strip()
                parts = description.split()
                if len(parts) >= 2:
                    distro = parts[0]
                    version = parts[1].replace('.', '_')
                    return f"{distro}_{version}"
        except Exception:
            pass

        # Fallback to /etc/os-release
        try:
            with open('/etc/os-release', 'r') as f:
                lines = f.readlines()
            info = {}
            for line in lines:
                if '=' in line:
                    k, v = line.strip().split('=', 1)
                    info[k] = v.strip('"')

            if 'NAME' in info and 'VERSION_ID' in info:
                distro = info['NAME'].split()[0]
                version = info['VERSION_ID'].replace('.', '_')
                return f"{distro}_{version}"
        except Exception:
            pass

        return "Unknown_OS"

    def get_cpu_affinity_list(self, n):
        """Generate CPU affinity list for HyperThreading optimization."""
        half = self.vcpu_count // 2
        cpu_list = []

        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])

        return ','.join(cpu_list)

    def is_wsl(self):
        """
        Detect if running in WSL environment (for logging purposes only).
        """
        try:
            if not os.path.exists('/proc/version'):
                return False
            with open('/proc/version', 'r') as f:
                content = f.read().lower()
                return 'microsoft' in content or 'wsl' in content
        except Exception:
            return False

    def check_and_setup_perf_permissions(self):
        """
        Check perf_event_paranoid level (for conditional perf usage).
        Does NOT modify system settings.
        """
        try:
            with open('/proc/sys/kernel/perf_event_paranoid', 'r') as f:
                paranoid_level = int(f.read().strip())
                return paranoid_level
        except Exception:
            return 2

    def get_perf_events(self):
        """
        Determine available perf events by testing actual command execution.

        Tests in this order:
        1. Hardware + Software events (cycles, instructions, etc.)
        2. Software-only events (cpu-clock, task-clock, etc.)
        3. None (perf not available)

        Returns:
            str: Comma-separated perf event list, or None if unavailable
        """
        import shutil

        # 1. Check if perf command exists in PATH
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None

        # 2. Test Hardware + Software events (Preferred for Native Linux)
        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        test_cmd = f"{perf_path} stat -e {hw_events} sleep 0.01 2>&1"

        try:
            result = subprocess.run(
                ['bash', '-c', test_cmd],
                capture_output=True,
                text=True,
                timeout=3
            )

            output = result.stdout + result.stderr

            # Check if all events are supported
            if result.returncode == 0 and '<not supported>' not in output:
                print(f"  [OK] Hardware PMU available: {hw_events}")
                return hw_events

            # 3. Test Software-only events (Fallback for Cloud/VM/Standard WSL)
            sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
            test_sw_cmd = f"{perf_path} stat -e {sw_events} sleep 0.01 2>&1"
            result_sw = subprocess.run(
                ['bash', '-c', test_sw_cmd],
                capture_output=True,
                text=True,
                timeout=3
            )

            if result_sw.returncode == 0:
                print(f"  [INFO] Hardware PMU not available. Using software events: {sw_events}")
                return sw_events

        except subprocess.TimeoutExpired:
            print("  [WARN] perf test timed out")
        except Exception as e:
            print(f"  [DEBUG] perf test execution failed: {e}")

        print("  [INFO] perf command exists but is not functional (permission or kernel issue)")
        return None

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """
        Parse perf stat output and CPU frequency files.
        Handles both hardware and software-only perf events gracefully.
        """
        # If perf monitoring was disabled, return minimal info
        if not self.perf_events or not perf_stats_file.exists():
            return {
                'note': 'perf monitoring not available',
                'cpu_list': cpu_list
            }

        cpu_ids = [int(c.strip()) for c in cpu_list.split(',')]
        per_cpu_metrics = {cpu_id: {} for cpu_id in cpu_ids}

        # Parse perf stat output with flexible regex
        with open(perf_stats_file, 'r') as f:
            for line in f:
                # Match: "CPU0  123,456  cycles" or "CPU0  <not supported>  cycles"
                match = re.match(r'CPU(\d+)\s+([\d,.<>a-zA-Z\s]+)\s+([a-zA-Z0-9\-_]+)', line)
                if match:
                    cpu_num = int(match.group(1))
                    value_str = match.group(2).strip()
                    event = match.group(3)

                    if cpu_num in per_cpu_metrics and '<not supported>' not in value_str:
                        try:
                            # Remove units like "msec" if present (e.g. "123.45 msec" -> "123.45")
                            value_clean = value_str.split()[0]
                            value = float(value_clean.replace(',', ''))
                            per_cpu_metrics[cpu_num][event] = value
                        except ValueError:
                            continue

        # Parse frequency files
        freq_start = []
        freq_end = []

        if freq_start_file.exists():
            with open(freq_start_file, 'r') as f:
                freq_start = [int(line.strip()) for line in f if line.strip().isdigit()]

        if freq_end_file.exists():
            with open(freq_end_file, 'r') as f:
                freq_end = [int(line.strip()) for line in f if line.strip().isdigit()]

        # Calculate average frequencies
        avg_freq_start = sum(freq_start) / len(freq_start) if freq_start else 0
        avg_freq_end = sum(freq_end) / len(freq_end) if freq_end else 0

        # Calculate per-CPU metrics
        for cpu_id in cpu_ids:
            metrics = per_cpu_metrics[cpu_id]

            # IPC calculation (if hardware counters available)
            if 'cycles' in metrics and 'instructions' in metrics and metrics['cycles'] > 0:
                metrics['ipc'] = metrics['instructions'] / metrics['cycles']

            # CPU utilization (if task-clock available)
            if 'task-clock' in metrics and 'cpu-clock' in metrics:
                # task-clock is in milliseconds
                total_time_ms = metrics.get('cpu-clock', metrics.get('task-clock', 0))
                if total_time_ms > 0:
                    metrics['utilization'] = min(100.0, (metrics['task-clock'] / total_time_ms) * 100)

        return {
            'per_cpu_metrics': per_cpu_metrics,
            'cpu_list': cpu_list,
            'frequency': {
                'start_khz': freq_start,
                'end_khz': freq_end,
                'avg_start_mhz': avg_freq_start / 1000 if avg_freq_start > 0 else 0,
                'avg_end_mhz': avg_freq_end / 1000 if avg_freq_end > 0 else 0
            }
        }

    def install_benchmark(self):
        """
        Install Rustls benchmark.

        Note: Rustls uses Rust's built-in benchmark framework.
        No special patches needed - Rust toolchain handles everything.
        """
        print(f"\n{'='*80}")
        print(f">>> Installing {self.benchmark}")
        print(f"{'='*80}")

        # Remove existing installation
        print(f"[INFO] Removing any existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Build install command with optimizations
        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" phoronix-test-suite batch-install {self.benchmark_full}'

        print(f"[INFO] Installing with command: {install_cmd}")
        result = subprocess.run(['bash', '-c', install_cmd], capture_output=True, text=True)

        if result.returncode != 0:
            print(f"  [ERROR] Installation failed")
            print(result.stderr)
            sys.exit(1)

        print(f"  [OK] Installation completed and verified")

    def run_benchmark(self, num_threads):
        """
        Run Rustls benchmark with conditional perf monitoring.

        Rustls uses Rust's built-in benchmark framework which handles
        multi-threading internally. We set NUM_CPU_CORES to control parallelism.
        """
        print(f"\n{'='*80}")
        print(f">>> Running {self.benchmark} with {num_threads} thread(s)")
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

        # Build PTS base command (taskset if needed)
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using CPU affinity: {cpu_list}"

        print(f"  [INFO] {cpu_info}")

        # Environment variables for batch mode execution
        # MUST USE {self.benchmark} - DO NOT HARDCODE BENCHMARK NAME
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads'

        # Construct Final Command with conditional perf
        if self.perf_events:
            # Perf available - check if we can use per-CPU breakdown
            if self.perf_paranoid <= 0:
                # Full monitoring mode with per-CPU metrics
                perf_cmd = f"perf stat -e {self.perf_events} -A -a -o {perf_stats_file}"
                print(f"  [INFO] Running with perf monitoring (per-CPU mode)")
            else:
                # Limited mode without per-CPU breakdown
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
                print(f"  [INFO] Running with perf monitoring (aggregated mode)")

            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {perf_cmd} {pts_base_cmd}'
        else:
            # Perf unavailable
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
            print(f"  [INFO] Running without perf")

        # Record CPU frequency before benchmark
        print(f"[INFO] Recording CPU frequency before benchmark...")
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_start_file)
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)

        # Execute benchmark with real-time output streaming
        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
            stdout_f.write(f"\n{'='*80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND - {num_threads} thread(s)]\n")
            stdout_f.write(f"{pts_cmd}\n")
            stdout_f.write(f"{'='*80}\n\n")
            stdout_f.flush()

            process = subprocess.Popen(
                ['bash', '-c', pts_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            for line in process.stdout:
                print(line, end='')
                log_f.write(line)
                stdout_f.write(line)
                log_f.flush()
                stdout_f.flush()

            process.wait()
            returncode = process.returncode

        # Record CPU frequency after benchmark
        command = cmd_template.format(file=freq_end_file)
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)

        if returncode == 0:
            print(f"\n[OK] Benchmark completed successfully")
            # Parse perf stats if available
            if self.perf_events and perf_stats_file.exists():
                try:
                    perf_summary = self.parse_perf_stats_and_freq(
                        perf_stats_file, freq_start_file, freq_end_file, cpu_list
                    )
                    with open(perf_summary_file, 'w') as f:
                        json.dump(perf_summary, f, indent=2)
                    print(f"  [OK] Perf summary saved: {perf_summary_file}")
                except Exception as e:
                    print(f"  [ERROR] Failed to parse perf stats: {e}")
            return True
        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            return False

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(f">>> Exporting results")
        print(f"{'='*80}")

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"

            # CRITICAL: PTS removes dots from directory names
            result_dir_name = result_name.replace('.', '')
            result_dir = pts_results_dir / result_dir_name

            if not result_dir.exists():
                print(f"[WARN] Result not found: {result_dir}")
                print(f"[INFO] Expected: {result_name}, actual: {result_dir_name}")
                continue

            print(f"[DEBUG] result_name: {result_name}, result_dir_name: {result_dir_name}")

            # Export to CSV - Use result_dir_name (dots removed)
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_dir_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_dir_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            # Export to JSON - Use result_dir_name (dots removed)
            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_dir_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                home_json = Path.home() / f"{result_dir_name}.json"
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
            f.write(f"Rustls Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n\n")

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
        # Install benchmark
        self.install_benchmark()

        # Run benchmark for each thread count
        for num_threads in self.thread_list:
            success = self.run_benchmark(num_threads)
            if not success:
                print(f"[ERROR] Benchmark failed for {num_threads} thread(s)")
                sys.exit(1)

        # Export results
        self.export_results()

        # Generate summary
        self.generate_summary()

        print(f"\n{'='*80}")
        print(f">>> All tasks completed successfully!")
        print(f"{'='*80}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Run Rustls benchmark')
    parser.add_argument('threads', type=int, nargs='?', default=None,
                        help='Number of threads to use (default: test all from 1 to vCPU count)')
    parser.add_argument('--quick', action='store_true',
                        help='Quick mode: run with FORCE_TIMES_TO_RUN=1 for faster testing')
    args = parser.parse_args()

    runner = BenchmarkRunner(threads_arg=args.threads, quick_mode=args.quick)
    runner.run()


if __name__ == "__main__":
    main()
