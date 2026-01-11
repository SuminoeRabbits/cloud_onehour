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

class SimdJsonRunner:
    def __init__(self, num_threads=None, quick_mode=False):
        self.benchmark = "simdjson-2.1.0"
        self.benchmark_full = "pts/simdjson-2.1.0"
        self.test_category = "Processor"
        self.test_category_dir = self.test_category.replace(' ', '_')
        
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.uname().nodename
        self.os_name = os.uname().sysname

        self.quick_mode = quick_mode
        self.manual_thread_count = num_threads
        self.thread_list = [1] 

        self.results_dir = Path(f"results/{self.test_category_dir}/{self.benchmark}")
        
        self.perf_paranoid = self.check_and_setup_perf_permissions()
        self.perf_events = self.check_perf_event_support()

    def check_perf_event_support(self):
        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations,page-faults"
        hw_events = "cycles,instructions,branches,branch-misses,cache-references,cache-misses"
        
        test_cmd = f"perf stat -e {hw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)
        if result.returncode == 0 and 'not supported' not in (result.stderr + result.stdout).lower():
            return f"{hw_events},{sw_events}"
        return sw_events

    def check_and_setup_perf_permissions(self):
        try:
            result = subprocess.run(['cat', '/proc/sys/kernel/perf_event_paranoid'], capture_output=True, text=True)
            if int(result.stdout.strip()) >= 1:
                subprocess.run(['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'], capture_output=True)
                return 0
            return int(result.stdout.strip())
        except:
            return 2

    def clean_pts_cache(self):
        print(">>> Cleaning PTS cache...")
        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark.split('-')[0]
        if installed_dir.exists():
             shutil.rmtree(installed_dir)
        print("  [OK] PTS cache cleaned")

    def install_benchmark(self):
        print(f"\n>>> Installing {self.benchmark_full}...")
        subprocess.run(['bash', '-c', f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Use NUM_CPU_CORES for compilation speedup
        nproc = os.cpu_count() or 1
        install_cmd = f'NUM_CPU_CORES={nproc} phoronix-test-suite batch-install {self.benchmark_full}'
        process = subprocess.Popen(['bash', '-c', install_cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out = []
        for line in process.stdout:
            print(line, end='')
            out.append(line)
        process.wait()
        
        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        if subprocess.run(['bash', '-c', verify_cmd], capture_output=True).returncode == 0:
             print(f"  [OK] Installation verified")
        else:
             print(f"  [WARN] Installation verification skipped/failed")

    def parse_perf_stats_and_freq(self, perf_file, freq_start, freq_end, cpu_list):
        return {}

    def run_benchmark(self, num_threads):
        print(f"\n>>> Running {self.benchmark} with {num_threads} threads")
        
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        
        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        batch_env = f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        
        pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
        
        if self.perf_events:
             pts_cmd = f'{batch_env} perf stat -e {self.perf_events} -o {perf_stats_file} {pts_base_cmd}'
        else:
             pts_cmd = f'{batch_env} {pts_base_cmd}'

        subprocess.run(['bash', '-c', f'grep "cpu MHz" /proc/cpuinfo | head -1 > {freq_start_file}'])

        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
            process = subprocess.Popen(['bash', '-c', pts_cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in process.stdout:
                print(line, end='')
                log_f.write(line)
                stdout_f.write(line)
            process.wait()
            
        subprocess.run(['bash', '-c', f'grep "cpu MHz" /proc/cpuinfo | head -1 > {freq_end_file}'])
        
        return process.returncode == 0

    def export_results(self):
        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"
        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            result_dir_name = result_name.replace('.', '')
            
            subprocess.run(['phoronix-test-suite', 'result-file-to-csv', result_dir_name], capture_output=True)
            home_csv = Path.home() / f"{result_dir_name}.csv"
            if home_csv.exists():
                shutil.move(str(home_csv), str(self.results_dir / f"{num_threads}-thread.csv"))
                
            subprocess.run(['phoronix-test-suite', 'result-file-to-json', result_dir_name], capture_output=True)
            home_json = Path.home() / f"{result_dir_name}.json"
            if home_json.exists():
                shutil.move(str(home_json), str(self.results_dir / f"{num_threads}-thread.json"))

    def generate_summary(self):
        with open(self.results_dir / "summary.log", 'w') as f:
            f.write(f"Summary for {self.benchmark}\n")

    def run(self):
        if self.results_dir.exists():
            shutil.rmtree(self.results_dir)
        self.results_dir.mkdir(parents=True)
        self.clean_pts_cache()
        self.install_benchmark()
        for t in self.thread_list:
            self.run_benchmark(t)
        self.export_results()
        self.generate_summary()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('threads_pos', nargs='?', type=int)
    parser.add_argument('--threads', type=int)
    parser.add_argument('--quick', action='store_true')
    args = parser.parse_args()
    
    threads = args.threads if args.threads else args.threads_pos
    runner = SimdJsonRunner(num_threads=threads, quick_mode=args.quick)
    runner.run()

if __name__ == "__main__":
    main()
