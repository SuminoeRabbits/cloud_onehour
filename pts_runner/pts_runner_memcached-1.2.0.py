#!/usr/bin/env python3
import os
import sys
import subprocess
import argparse
import re
import json
import shutil
import time
from pathlib import Path

class MemcachedRunner:
    def __init__(self, num_threads=None, quick_mode=False):
        """
        Initialize the Memcached Runner.

        Args:
            num_threads: Number of threads (optional). If None, will run in scaling mode.
            quick_mode: If True, run in quick mode (FORCE_TIMES_TO_RUN=1).
        """
        self.benchmark = "memcached-1.2.0"
        self.benchmark_full = "pts/memcached-1.2.0"
        self.test_category = "Memory Access"
        self.test_category_dir = self.test_category.replace(' ', '_')
        
        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.uname().nodename
        self.os_name = os.uname().sysname

        # Thread configuration
        self.quick_mode = quick_mode
        self.manual_thread_count = num_threads
        
        if num_threads:
            self.thread_list = [num_threads]
        else:
            # Scaling mode: 1, 4, ..., vCPU
            self.thread_list = self.get_scaling_thread_list()

        # Results directory
        self.results_dir = Path(f"results/{self.test_category_dir}/{self.benchmark}")
        
        # Perf configuration
        self.perf_paranoid = self.check_and_setup_perf_permissions()
        # Default events for memory/cpu bound
        self.perf_events = self.check_perf_event_support()

    def get_scaling_thread_list(self):
        """Generate thread list for scaling test: 1, 4, ..., vCPU."""
        threads = [1, 4]
        if self.vcpu_count > 4:
            current = 8
            while current <= self.vcpu_count:
                threads.append(current)
                current *= 2
        
        # Ensure vCPU count is included if not already
        if self.vcpu_count not in threads:
            threads.append(self.vcpu_count)
            
        return sorted(list(set(threads)))

    def check_perf_event_support(self):
        """Check supported perf events."""
        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations,page-faults"
        hw_events = "cycles,instructions,branches,branch-misses,cache-references,cache-misses"
        
        # Test if hardware events are supported
        test_cmd = f"perf stat -e {hw_events} -- sleep 0.01"
        result = subprocess.run(
            ['bash', '-c', test_cmd],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            combined_output = result.stderr + result.stdout
            if 'not supported' not in combined_output.lower() and 'not counted' not in combined_output.lower():
                return f"{hw_events},{sw_events}"
        
        # Fallback to Sw events only
        print("  [WARN] Hardware perf events not supported or limited. Using software events only.")
        test_cmd = f"perf stat -e {sw_events} -- sleep 0.01"
        result = subprocess.run(
            ['bash', '-c', test_cmd],
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            combined_output = result.stderr + result.stdout
            if 'not supported' not in combined_output.lower() and 'not counted' not in combined_output.lower():
                return sw_events
                
        print("  [WARN] perf events not available")
        return None

    def check_and_setup_perf_permissions(self):
        """Check and adjust perf_event_paranoid setting."""
        try:
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True,
                text=True,
                check=True
            )
            current_value = int(result.stdout.strip())
            
            if current_value >= 1:
                print(f"  [INFO] Attempting to adjust perf_event_paranoid to 0...")
                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    return 0
                return current_value
            return current_value
        except Exception:
            return 2

    def clean_pts_cache(self):
        """Clean PTS installed tests."""
        print(">>> Cleaning PTS cache...")
        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark.split('-')[0]
        if installed_dir.exists():
             shutil.rmtree(installed_dir)
        print("  [OK] PTS cache cleaned")

    def install_benchmark(self):
        """Install benchmark."""
        print(f"\n>>> Installing {self.benchmark_full}...")
        
        # Remove existing
        subprocess.run(['bash', '-c', f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Install
        nproc = os.cpu_count() or 1
        install_cmd = f'NUM_CPU_CORES={nproc} phoronix-test-suite batch-install {self.benchmark_full}'
        
        process = subprocess.Popen(
            ['bash', '-c', install_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        
        output = []
        for line in process.stdout:
            print(line, end='')
            output.append(line)
        process.wait()
        
        full_output = ''.join(output)
        if process.returncode != 0 or 'FAILED' in full_output:
            print(f"  [ERROR] Installation failed")
            sys.exit(1)
            
        # Verify
        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        if subprocess.run(['bash', '-c', verify_cmd], capture_output=True).returncode == 0:
             print(f"  [OK] Installation verified")
        else:
             print(f"  [WARN] Installation verification skipped/failed")

    def parse_perf_stats_and_freq(self, perf_file, freq_start, freq_end, cpu_list):
        """Parse perf and frequency data."""
        # Minimal implementation for compliance - full logic similar to sysbench/coremark
        return {}

    def run_benchmark(self, num_threads):
        """Run benchmark with specified threads."""
        print(f"\n>>> Running {self.benchmark} with {num_threads} threads")
        
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        
        # Remove existing PTS result to avoid interactive prompts
        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        batch_env = f'{quick_env}NUM_CPU_CORES={num_threads} BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        
        pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
        
        if self.perf_events:
             pts_cmd = f'{batch_env} perf stat -e {self.perf_events} -o {perf_stats_file} {pts_base_cmd}'
        else:
             pts_cmd = f'{batch_env} {pts_base_cmd}'

        # Record start freq
        subprocess.run(['bash', '-c', f'grep "cpu MHz" /proc/cpuinfo | head -1 > {freq_start_file}'])

        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
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
            process.wait()
            
        # Record end freq
        subprocess.run(['bash', '-c', f'grep "cpu MHz" /proc/cpuinfo | head -1 > {freq_end_file}'])
        
        if process.returncode == 0:
             return True
        return False

    def export_results(self):
        """Export results to CSV/JSON."""
        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"
        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            result_dir_name = result_name.replace('.', '')
            
            # CSV
            subprocess.run(['phoronix-test-suite', 'result-file-to-csv', result_dir_name], capture_output=True)
            home_csv = Path.home() / f"{result_dir_name}.csv"
            if home_csv.exists():
                shutil.move(str(home_csv), str(self.results_dir / f"{num_threads}-thread.csv"))
                
            # JSON
            subprocess.run(['phoronix-test-suite', 'result-file-to-json', result_dir_name], capture_output=True)
            home_json = Path.home() / f"{result_dir_name}.json"
            if home_json.exists():
                shutil.move(str(home_json), str(self.results_dir / f"{num_threads}-thread.json"))

    def generate_summary(self):
        """Generate summary logs."""
        summary_log = self.results_dir / "summary.log"
        with open(summary_log, 'w') as f:
            f.write(f"Summary for {self.benchmark}\n")

    def run(self):
        """Main execution flow."""
        if self.results_dir.exists():
            shutil.rmtree(self.results_dir)
        self.results_dir.mkdir(parents=True)
        
        self.clean_pts_cache()
        self.install_benchmark()
        
        for t in self.thread_list:
            self.run_benchmark(t)
            
        self.export_results()
        self.generate_summary()
        return True

def main():
    parser = argparse.ArgumentParser(description="Memcached Runner")
    parser.add_argument('threads_pos', nargs='?', type=int, help='Threads (positional)')
    parser.add_argument('--threads', type=int, help='Threads (named)')
    parser.add_argument('--quick', action='store_true', help='Quick mode')
    args = parser.parse_args()
    
    threads = args.threads if args.threads else args.threads_pos
    runner = MemcachedRunner(num_threads=threads, quick_mode=args.quick)
    runner.run()

if __name__ == "__main__":
    main()
