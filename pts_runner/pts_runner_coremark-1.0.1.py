#!/usr/bin/env python3
"""
PTS Runner for coremark-1.0.1

Based on test_suite.json configuration:
- test_category: "Pipeline Efficiency"
- THFix_in_compile: true - Thread count fixed at compile time via -DMULTITHREAD=$NUM_CPU_CORES
- THChange_at_runtime: false - Cannot change threads at runtime
- TH_scaling: N/A
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


class CoreMarkRunner:
    def __init__(self, threads_arg=None):
        """
        Initialize CoreMark runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
        """
        self.benchmark = "coremark-1.0.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Pipeline Efficiency"
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

    def install_benchmark(self, num_threads):
        """
        Clean install coremark-1.0.1 with GCC-14 native compilation.

        Args:
            num_threads: Thread count for -DMULTITHREAD compilation flag
        """
        print(f"\n>>> Installing {self.benchmark_full} with NUM_CPU_CORES={num_threads}...")

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
        # Environment must be set before the command (as per README)
        install_cmd = f'NUM_CPU_CORES={num_threads} CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite install {self.benchmark_full}'

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

        # Build PTS command based on thread count
        # If N >= vCPU: don't use taskset (all vCPUs assigned)
        # If N < vCPU: use taskset with CPU affinity

        # Get user_config directory path
        user_config_dir = self.project_root / "user_config"

        # Environment variables to suppress all prompts
        # PTS_USER_PATH_OVERRIDE: use our batch-mode configured user-config.xml
        # BATCH_MODE, SKIP_ALL_PROMPTS: additional safeguards
        # TEST_RESULTS_NAME, TEST_RESULTS_IDENTIFIER: auto-generate result names
        batch_env = f'PTS_USER_PATH_OVERRIDE={user_config_dir} BATCH_MODE=1 SKIP_ALL_PROMPTS=1 TEST_RESULTS_NAME=coremark-{num_threads}threads TEST_RESULTS_IDENTIFIER=coremark-{num_threads}threads'

        if num_threads >= self.vcpu_count:
            # All vCPUs mode - no taskset needed
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} phoronix-test-suite benchmark {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            # Partial vCPU mode - use taskset with affinity
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} taskset -c {cpu_list} phoronix-test-suite benchmark {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        print(f"[INFO] {cpu_info}")

        # Print PTS command to stdout for debugging (as per README requirement)
        print(f"\n{'>'*80}")
        print(f"[PTS BENCHMARK COMMAND]")
        print(f"  {pts_cmd}")
        print(f"  {cpu_info}")
        print(f"  Output:")
        print(f"    Thread log: {log_file}")
        print(f"    Stdout log: {stdout_log}")
        print(f"{'<'*80}\n")

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

        if returncode == 0:
            print(f"\n[OK] Benchmark completed successfully")
            print(f"     Thread log: {log_file}")
            print(f"     Stdout log: {stdout_log}")
        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            return False

        return True

    def run(self):
        """Main execution flow."""
        print(f"{'='*80}")
        print(f"CoreMark 1.0.1 Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print(f"[INFO] Test category: {self.test_category}")
        print(f"[INFO] Thread mode: Compile-time fixed (THFix_in_compile=true)")
        print(f"[INFO] Threads to test: {self.thread_list}")
        print(f"[INFO] Results directory: {self.results_dir}")
        print()

        # Clean cache once at the beginning
        self.clean_pts_cache()

        # Run for each thread count
        failed = []
        for num_threads in self.thread_list:
            # For compile-time mode, we need to reinstall for each thread count
            self.install_benchmark(num_threads)

            # Run benchmark
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

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
        description='CoreMark 1.0.1 Benchmark Runner',
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

    args = parser.parse_args()

    # Validate threads argument
    if args.threads is not None and args.threads < 1:
        print(f"[ERROR] Thread count must be >= 1 (got: {args.threads})")
        sys.exit(1)

    # Run benchmark
    runner = CoreMarkRunner(args.threads)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
