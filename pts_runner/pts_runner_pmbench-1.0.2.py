#!/usr/bin/env python3
"""
PTS Runner for pmbench-1.0.2

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * UUID
  * Libxml2
- Estimated Install Time: 2 Seconds
- Environment Size: 3 MB
- Test Type: Memory
- Supported Platforms: Linux

Test Characteristics:
- Multi-threaded: Yes (supports concurrent worker threads)
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: N/A
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Runtime thread configuration via -j option
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

class PreSeedDownloader:
    """
    Utility to pre-download large test files into Phoronix Test Suite cache
    using faster downloaders (aria2c) if available.
    """
    def __init__(self, cache_dir=None):
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            self.cache_dir = Path.home() / ".phoronix-test-suite" / "download-cache"
        
        self.aria2_available = shutil.which("aria2c") is not None

    def is_aria2_available(self):
        return self.aria2_available

    def download_from_xml(self, benchmark_name, threshold_mb=256):
        """
        Parse downloads.xml for the benchmark and download large files.
        
        Args:
            benchmark_name: Full benchmark name (e.g., "pts/x265-1.5.0")
            threshold_mb: Size threshold in MB to trigger aria2c (default: 256MB)
        """
        if not self.aria2_available:
            return False

        # Locate downloads.xml
        # ~/.phoronix-test-suite/test-profiles/<benchmark_name>/downloads.xml
        profile_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / benchmark_name / "downloads.xml"
        
        if not profile_path.exists():
            # downloads.xml might not exist if test isn't installed/info'd yet, but that's fine.
            return False
            
        try:
            import xml.etree.ElementTree as ET
            tree = ET.parse(profile_path)
            root = tree.getroot()
            
            # Find all Package elements
            downloads_node = root.find('Downloads')
            if downloads_node is None:
                return False
                
            for package in downloads_node.findall('Package'):
                url_node = package.find('URL')
                filename_node = package.find('FileName')
                filesize_node = package.find('FileSize')
                
                if url_node is None or filename_node is None:
                    continue
                    
                url = url_node.text.strip()
                filename = filename_node.text.strip()
                
                # Determine size
                size_bytes = -1
                if filesize_node is not None and filesize_node.text:
                    try:
                        size_bytes = int(filesize_node.text.strip())
                    except ValueError:
                        pass
                
                # If size not in XML, try to get it from network (fallback)
                if size_bytes <= 0:
                    size_bytes = self.get_remote_file_size(url)
                    
                # Check threshold
                if size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb >= threshold_mb:
                        print(f"  [INFO] {filename} is large ({size_mb:.1f} MB), accelerating with aria2c...")
                        self.ensure_file(url, filename)

        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False

        return True

    def get_remote_file_size(self, url):
        """
        Get remote file size in bytes using curl.
        Returns -1 if size cannot be determined.
        """
        try:
            # -s: Silent, -I: Header only, -L: Follow redirects
            cmd = ['curl', '-s', '-I', '-L', url]
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            if result.returncode != 0:
                return -1
                
            # Parse Content-Length
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        size_str = line.split(':')[1].strip()
                        return int(size_str)
                    except ValueError:
                        pass
        except Exception:
            pass
            
        return -1

    def ensure_file(self, url, filename):
        """
        Directly download file using aria2c (assumes size check passed).
        """
        target_path = self.cache_dir / filename
        
        # Check if file exists in cache
        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        # Need to download
        print(f"  [ARIA2] Downloading {filename} with 16 connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # aria2c command
        cmd = [
            "aria2c", "-x", "16", "-s", "16", 
            "-d", str(self.cache_dir), 
            "-o", filename,
            url
        ]
        
        try:
            subprocess.run(cmd, check=True)
            print(f"  [aria2c] Download completed: {filename}")
            return True
        except subprocess.CalledProcessError:
            print(f"  [WARN] aria2c download failed, falling back to PTS default")
            # Clean up partial download
            if target_path.exists():
                target_path.unlink()
            return False

class PmbenchRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize pmbench (paging/virtual memory benchmark) runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "pmbench-1.0.2"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Memory"
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

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

        # Check perf permissions
        self.perf_paranoid = self.check_and_setup_perf_permissions()

    def get_os_name(self):
        """Get OS name and version formatted as <Distro>_<Version>."""
        try:
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

    def check_and_setup_perf_permissions(self):
        """Check and adjust perf_event_paranoid setting."""
        print(f"\n{'='*80}")
        print(">>> Checking perf_event_paranoid setting")
        print(f"{'='*80}")

        try:
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True,
                text=True,
                check=True
            )
            current_value = int(result.stdout.strip())
            print(f"  [INFO] Current perf_event_paranoid: {current_value}")

            if current_value >= 1:
                print(f"  [WARN] perf_event_paranoid={current_value} is too restrictive")
                print(f"  [INFO] Attempting to adjust to 0...")

                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True,
                    text=True
                )

                if result.returncode == 0:
                    print(f"  [OK] perf_event_paranoid adjusted to 0")
                    return 0
                else:
                    print(f"  [ERROR] Failed to adjust (sudo required)")
                    print(f"  [WARN] Running in LIMITED mode")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value

        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            return 2

    def clean_pts_cache(self):
        """Clean PTS installed tests for fresh installation."""
        print(">>> Cleaning PTS cache...")

        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark

        if installed_dir.exists():
            print(f"  [CLEAN] Removing installed test: {installed_dir}")
            shutil.rmtree(installed_dir)

        print("  [OK] PTS cache cleaned")

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

    def install_benchmark(self):
        """Install benchmark with error detection and verification."""
        # [Pattern 5] Pre-download large files from downloads.xml (Size > 256MB)
        print(f"\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=256)


        print(f"\n>>> Installing {self.benchmark_full}...")

        print(f"  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        result = subprocess.run(
            ['bash', '-c', install_cmd],
            capture_output=True,
            text=True
        )

        install_failed = False
        if result.returncode != 0:
            install_failed = True
        elif result.stdout and ('Checksum Failed' in result.stdout or 'Downloading of needed test files failed' in result.stdout):
            install_failed = True
        elif result.stderr and ('Checksum Failed' in result.stderr or 'failed' in result.stderr.lower()):
            install_failed = True

        if install_failed:
            print(f"  [ERROR] Installation failed")
            if result.stdout:
                print(f"  [ERROR] stdout: {result.stdout[-500:]}")
            if result.stderr:
                print(f"  [ERROR] stderr: {result.stderr[-500:]}")
            sys.exit(1)

        verify_cmd = f'phoronix-test-suite info {self.benchmark_full}'
        verify_result = subprocess.run(
            ['bash', '-c', verify_cmd],
            capture_output=True,
            text=True
        )

        if verify_result.returncode != 0 or 'not found' in verify_result.stdout.lower():
            print(f"  [ERROR] Installation verification failed")
            print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
            sys.exit(1)

        print(f"  [OK] Installation completed and verified")

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
        print(f"\n>>> Parsing perf stats and frequency data")

        cpu_ids = [int(x.strip()) for x in cpu_list.split(',')]

        perf_summary = {
            "avg_frequency_ghz": {},
            "start_frequency_ghz": {},
            "end_frequency_ghz": {},
            "ipc": {},
            "total_cycles": {},
            "total_instructions": {},
            "cpu_utilization_percent": 0.0,
            "elapsed_time_sec": 0.0
        }

        # Parse start frequencies
        if freq_start_file.exists():
            with open(freq_start_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    freq_khz = float(freq_lines[idx].strip())
                    perf_summary["start_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)

        # Parse end frequencies
        if freq_end_file.exists():
            with open(freq_end_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    freq_khz = float(freq_lines[idx].strip())
                    perf_summary["end_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)

        # Parse perf stats
        if perf_stats_file.exists():
            with open(perf_stats_file, 'r') as f:
                perf_data = f.read()

            per_cpu_cycles = {}
            per_cpu_instructions = {}
            per_cpu_clock = {}

            for cpu_id in cpu_ids:
                cycles_match = re.search(rf'CPU{cpu_id}\s+(\d+(?:,\d+)*)\s+cycles', perf_data)
                if cycles_match:
                    per_cpu_cycles[str(cpu_id)] = int(cycles_match.group(1).replace(',', ''))

                instr_match = re.search(rf'CPU{cpu_id}\s+(\d+(?:,\d+)*)\s+instructions', perf_data)
                if instr_match:
                    per_cpu_instructions[str(cpu_id)] = int(instr_match.group(1).replace(',', ''))

                clock_match = re.search(rf'CPU{cpu_id}\s+([\d,]+(?:\.\d+)?)\s+msec\s+cpu-clock', perf_data)
                if clock_match:
                    per_cpu_clock[str(cpu_id)] = float(clock_match.group(1).replace(',', ''))

            # Calculate IPC and average frequency
            for cpu_id_str in per_cpu_cycles.keys():
                cycles = per_cpu_cycles.get(cpu_id_str, 0)
                instructions = per_cpu_instructions.get(cpu_id_str, 0)
                clock_ms = per_cpu_clock.get(cpu_id_str, 0)

                perf_summary["total_cycles"][cpu_id_str] = cycles
                perf_summary["total_instructions"][cpu_id_str] = instructions

                if cycles > 0:
                    perf_summary["ipc"][cpu_id_str] = round(instructions / cycles, 2)

                if clock_ms > 0 and cycles > 0:
                    avg_freq_ghz = (cycles / (clock_ms / 1000)) / 1e9
                    perf_summary["avg_frequency_ghz"][cpu_id_str] = round(avg_freq_ghz, 3)

            # Parse elapsed time
            elapsed_match = re.search(r'([\d,]+(?:\.\d+)?)\s+seconds time elapsed', perf_data)
            if elapsed_match:
                perf_summary["elapsed_time_sec"] = float(elapsed_match.group(1).replace(',', ''))

        print(f"  [OK] Perf stats parsed successfully")
        return perf_summary

    def run_benchmark(self, num_threads):
        """Run benchmark with specified thread count."""
        print(f"\n{'='*80}")
        print(f">>> Running {self.benchmark_full} with {num_threads} thread(s)")
        print(f"{'='*80}")

        thread_dir = self.results_dir / f"{num_threads}-thread"
        thread_dir.mkdir(parents=True, exist_ok=True)

        perf_stats_file = thread_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = thread_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = thread_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = thread_dir / f"{num_threads}-thread_perf_summary.json"

        # Setup environment variables
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads'

        # Build PTS command
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        # Wrap with perf stat (environment variables BEFORE perf stat)
        pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} perf stat -e cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations -A -a -o {perf_stats_file} {pts_base_cmd}'

        print(f"  [INFO] {cpu_info}")
        print(f"\n{'>'*80}")
        print(f"[PTS RUN COMMAND]")
        print(f"  {pts_cmd}")
        print(f"{'<'*80}\n")

        # Record CPU frequency BEFORE benchmark
        print(f"[INFO] Recording CPU frequency before benchmark...")
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_start_file)
        result = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [WARN] Failed to record start frequency: {result.stderr}")
        else:
            print(f"  [OK] Start frequency recorded")

        # Execute PTS command
        subprocess.run(['bash', '-c', pts_cmd])

        # Record CPU frequency AFTER benchmark
        print(f"\n[INFO] Recording CPU frequency after benchmark...")
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_end_file)
        result = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  [WARN] Failed to record end frequency: {result.stderr}")
        else:
            print(f"  [OK] End frequency recorded")

        # Parse perf stats
        perf_summary = self.parse_perf_stats_and_freq(
            perf_stats_file, freq_start_file, freq_end_file, cpu_list
        )

        # Save perf summary
        with open(perf_summary_file, 'w') as f:
            json.dump(perf_summary, f, indent=2)
        print(f"  [OK] Perf summary saved to {perf_summary_file}")

    def run(self):
        """Main execution flow."""
        print(f"\n{'#'*80}")
        print(f"# PTS Runner: {self.benchmark_full}")
        print(f"# Machine: {self.machine_name}")
        print(f"# OS: {self.os_name}")
        print(f"# vCPU Count: {self.vcpu_count}")
        print(f"# Thread List: {self.thread_list}")
        if self.quick_mode:
            print(f"# Quick Mode: ENABLED (FORCE_TIMES_TO_RUN=1)")
        print(f"{'#'*80}")

        # Clean results directory
        if self.results_dir.exists():
            print(f"\n>>> Cleaning existing results directory: {self.results_dir}")
            shutil.rmtree(self.results_dir)

        self.results_dir.mkdir(parents=True, exist_ok=True)

        # Clean and install
        self.clean_pts_cache()
        self.install_benchmark()

        # Run for each thread count
        for num_threads in self.thread_list:
            self.run_benchmark(num_threads)

        # Export results to CSV and JSON
        self.export_results()

        # Generate summary
        self.generate_summary()

        print(f"\n{'='*80}")
        print(f">>> All benchmarks completed successfully")
        print(f">>> Results directory: {self.results_dir}")
        print(f"{'='*80}")

        return True

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(f">>> Exporting benchmark results")
        print(f"{'='*80}")

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"

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
            f.write("="*80 + "\\n")
            f.write(f"Benchmark Summary: {self.benchmark}\\n")
            f.write(f"Machine: {self.machine_name}\\n")
            f.write(f"Test Category: {self.test_category}\\n")
            f.write("="*80 + "\\n\\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\\n")
                f.write(f"  Test: {result['test_name']}\\n")
                f.write(f"  Description: {result['description']}\\n")
                
                # Check for None to avoid f-string crash
                if result['value'] is not None:
                    f.write(f"  Average: {result['value']:.2f} {result['unit']}\\n")
                else:
                    f.write(f"  Average: None (Test Failed)\\n")
                    
                # Handle raw values safely
                raw_vals = result.get('raw_values')
                if raw_vals:
                    val_str = ', '.join([f'{v:.2f}' for v in raw_vals if v is not None])
                    f.write(f"  Raw values: {val_str}\\n")
                else:
                    f.write(f"  Raw values: N/A\\n")
                    
                f.write("\\n")

            f.write("="*80 + "\\n")
            f.write("Summary Table\\n")
            f.write("="*80 + "\\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20}\\n")
            f.write("-"*80 + "\\n")
            for result in all_results:
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "None"
                f.write(f"{result['threads']:<10} {val_str:<15} {result['unit']:<20}\\n")

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


def main():
    parser = argparse.ArgumentParser(
        description='pmbench Linux Paging/Virtual Memory Benchmark Runner'
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

    runner = PmbenchRunner(args.threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
