#!/usr/bin/env python3
"""
PTS Runner for coremark-1.0.1

CoreMark is a small, efficient benchmark for evaluating the performance of
embedded microcontrollers and central processing units (CPUs).

System Dependencies:
  - gcc-14 (required for compilation)
  - make
  - Estimated Install Time: < 1 min
  - Environment Size: < 10 MB

Test Type:
  - Processor

Supported Platforms:
  - Linux (AArch64, x86_64)

Test Characteristics:
  - Multi-threaded: True
  - USE_NO_TASKSET: False (Can use taskset)
  - THFix_in_compile: True (Thread count fixed at compile time)
  - THChange_at_runtime: False
  - TH_scaling: 1 thread per core commonly used
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

class PreSeedDownloader:
    """
    Handles pre-seeding of large benchmark files to avoid PTS download timeouts.
    """
    def __init__(self):
        self.pts_home = Path.home() / '.phoronix-test-suite'
        self.download_cache = self.pts_home / 'download-cache'

    def get_remote_file_size(self, url):
        """Get file size in bytes from URL using curl."""
        try:
            result = subprocess.run(
                ['curl', '-sI', url],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                match = re.search(r'content-length:\s*(\d+)', result.stdout, re.IGNORECASE)
                if match:
                    return int(match.group(1))
        except Exception:
            pass
        return 0

    def ensure_file(self, filename, url, min_size_mb=0):
        """
        Ensure file exists in PTS download cache.
        If missing or matching expected size, download with aria2c.
        """
        self.download_cache.mkdir(parents=True, exist_ok=True)
        file_path = self.download_cache / filename

        should_download = True
        if file_path.exists():
            # If size is specified/known, verify it
            if min_size_mb > 0:
                size_mb = file_path.stat().st_size / (1024 * 1024)
                if size_mb >= min_size_mb * 0.9: # 10% tolerance
                    print(f"  [CACHE] File {filename} exists and size is valid ({size_mb:.1f} MB)")
                    should_download = False
                else:
                    print(f"  [CACHE] File {filename} exists but size mismatch ({size_mb:.1f} MB < {min_size_mb} MB)")
            else:
                 print(f"  [CACHE] File {filename} exists (skipping verification)")
                 should_download = False

        if should_download:
            print(f"  [DOWNLOAD] Downloading {filename} with aria2c...")
            # Use aria2c for faster multi-connection download
            cmd = ['aria2c', '-x', '16', '-s', '16', '-k', '1M', '-d', str(self.download_cache), '-o', filename, url]
            try:
                subprocess.run(cmd, check=True)
                print(f"  [OK] Download completed: {filename}")
            except subprocess.CalledProcessError:
                print(f"  [ERROR] Download failed for {filename}")
                # Don't exit, let PTS try its own download method as fallback

    def download_from_xml(self, benchmark_name, threshold_mb=256):
        """
        Parse downloads.xml for the benchmark and download files larger than threshold.
        
        Args:
            benchmark_name: Full benchmark name (e.g., 'pts/test-name')
            threshold_mb: Download files larger than this size (MB)
        """
        print(f"\n>>> Checking for large files (> {threshold_mb} MB) in downloads.xml")
        
        # Locate downloads.xml
        # Typically in ~/.phoronix-test-suite/test-profiles/pts/<benchmark>/downloads.xml
        # Need to handle 'pts/' prefix
        short_name = benchmark_name.replace('pts/', '')
        profile_dir = self.pts_home / 'test-profiles' / 'pts' / short_name
        xml_path = profile_dir / 'downloads.xml'

        if not xml_path.exists():
            print(f"  [INFO] downloads.xml not found at {xml_path}")
            # Try to fetch profile if missing? For now just skip.
            return

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            
            count = 0
            for package in root.findall('Package'):
                url_elem = package.find('URL')
                filename_elem = package.find('FileName')
                size_elem = package.find('FileSize')
                
                if url_elem is None or filename_elem is None:
                    continue
                    
                url = url_elem.text.strip()
                filename = filename_elem.text.strip()
                
                # Check URL links to other variables (not supported yet)
                if ',' in url and 'http' not in url:
                    continue

                # Determine file size
                file_size_mb = 0
                if size_elem is not None and size_elem.text:
                    try:
                        file_size_bytes = int(size_elem.text)
                        file_size_mb = file_size_bytes / (1024 * 1024)
                    except ValueError:
                        pass
                
                # If size not in XML, try to get it from network (expensive, optional)
                # For optimization, we rely on XML size or user knowledge usually. 
                # If XML size is 0/missing, we skip unless we want to force check.
                
                if file_size_mb > threshold_mb:
                    print(f"  [INFO] Found large file: {filename} ({file_size_mb:.1f} MB)")
                    self.ensure_file(filename, url, min_size_mb=file_size_mb)
                    count += 1
            
            if count == 0:
                print(f"  [INFO] No files larger than {threshold_mb} MB found in downloads.xml")
                
        except ET.ParseError:
            print(f"  [WARN] Failed to parse {xml_path}")
        except Exception as e:
            print(f"  [WARN] Error processing downloads.xml: {e}")


class CoreMarkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize CoreMark runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "coremark-1.0.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Pipeline Efficiency"
        # Replace spaces with underscores in test_category for directory name
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

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
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

        # Check and setup perf permissions
        self.perf_paranoid = self.check_and_setup_perf_permissions()

    def get_os_name(self):
        """
        Get OS name and version formatted as <Distro>_<Version>.
        Example: Ubuntu_22_04
        """
        try:
            # Try lsb_release first as it's standard on Ubuntu
            import subprocess
            cmd = "lsb_release -d -s"
            result = subprocess.run(cmd.split(), capture_output=True, text=True)
            if result.returncode == 0:
                description = result.stdout.strip() # e.g. "Ubuntu 22.04.4 LTS"
                # Extract "Ubuntu" and "22.04"
                parts = description.split()
                if len(parts) >= 2:
                    distro = parts[0]
                    version = parts[1]
                    # Handle version with dots
                    version = version.replace('.', '_')
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
                distro = info['NAME'].split()[0] # "Ubuntu"
                version = info['VERSION_ID'].replace('.', '_')
                return f"{distro}_{version}"
        except Exception:
            pass
            
        return "Unknown_OS"

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
        """Clean PTS installed tests for fresh installation."""
        print(">>> Cleaning PTS cache...")

        pts_home = Path.home() / '.phoronix-test-suite'

        # NOTE: Do NOT clean test profiles - they may contain manual fixes for checksum issues
        # Only clean installed tests to force fresh compilation

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

        # Pre-seed large downloads if any (generic check)
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=256)

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
        # Use batch-install to suppress prompts
        # NUM_CPU_CORES: sets thread count in the binary (for THFix_in_compile=true)
        # MAKEFLAGS: parallelize compilation itself with -j$(nproc)
        nproc = os.cpu_count() or 1
        install_cmd = f'NUM_CPU_CORES={num_threads} MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

        # Print install command for debugging (as per README requirement)
        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Execute install command
        result = subprocess.run(
            ['bash', '-c', install_cmd],
            capture_output=True,
            text=True
        )

        # Check for installation failure
        # PTS may return 0 even if download failed, so check stdout/stderr for error messages
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
                print(f"  [ERROR] stdout: {result.stdout[-500:]}")  # Last 500 chars
            if result.stderr:
                print(f"  [ERROR] stderr: {result.stderr[-500:]}")
            sys.exit(1)

        # Verify installation by checking if test is actually installed
        verify_cmd = f'phoronix-test-suite info {self.benchmark_full}'
        verify_result = subprocess.run(
            ['bash', '-c', verify_cmd],
            capture_output=True,
            text=True
        )

        if verify_result.returncode != 0 or 'not found' in verify_result.stdout.lower():
            print(f"  [ERROR] Installation verification failed - test not found")
            print(f"  [INFO] This may be due to download/checksum failures")
            print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
            sys.exit(1)

        print(f"  [OK] Installation completed and verified")



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

        # Define file paths for frequency monitoring
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"

        # Build PTS command based on thread count
        # If N >= vCPU: don't use taskset (all vCPUs assigned)
        # If N < vCPU: use taskset with CPU affinity

        # Environment variables to suppress all prompts
        # BATCH_MODE, SKIP_ALL_PROMPTS: additional safeguards
        # TEST_RESULTS_NAME, TEST_RESULTS_IDENTIFIER: auto-generate result names
        # DISPLAY_COMPACT_RESULTS: suppress "view text results" prompt
        # FORCE_TIMES_TO_RUN: quick mode for development (run once instead of 3+ times)
        # LINUX_PERF=1: Enable PTS's built-in perf stat module (System Monitor)
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        perf_env = 'LINUX_PERF=1 '
        batch_env = f'{quick_env}{perf_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME=coremark-{num_threads}threads TEST_RESULTS_IDENTIFIER=coremark-{num_threads}threads'

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

        # Construct Final Command (Env Vars + PTS Command)
        # Note: We rely on LINUX_PERF=1 instead of manual `perf stat` wrapping
        pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'

        print(f"[INFO] {cpu_info}")

        # Print PTS command to stdout for debugging (as per README requirement)
        print(f"\n{'>'*80}")
        print(f"[PTS BENCHMARK COMMAND]")
        print(f"  {pts_cmd}")
        print(f"  {cpu_info}")
        print(f"  Output:")
        print(f"    Thread log: {log_file}")
        print(f"    Stdout log: {stdout_log}")
        print(f"    Freq start: {freq_start_file}")
        print(f"    Freq end: {freq_end_file}")
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
            result_name = f"coremark-{num_threads}threads"

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
            f.write(f"CoreMark 1.0.1 Benchmark Summary\n")
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
        print(f"CoreMark 1.0.1 Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print(f"[INFO] Test category: {self.test_category}")
        print(f"[INFO] Thread mode: Compile-time fixed (THFix_in_compile=true)")
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

        # Run for each thread count
        failed = []
        for num_threads in self.thread_list:
            # For compile-time mode, we need to reinstall for each thread count
            self.install_benchmark(num_threads)

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
    runner = CoreMarkRunner(args.threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
