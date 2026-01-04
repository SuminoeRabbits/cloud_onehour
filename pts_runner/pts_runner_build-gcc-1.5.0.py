#!/usr/bin/env python3
"""
PTS Runner for build-gcc-1.5.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain (gcc-14/g++-14 recommended)
  * Flex, Bison
  * GMP, MPFR, MPC libraries
- Estimated Install Time: 60 Minutes
- Environment Size: 10 GB
- Test Type: Processor (Compilation)
- Supported Platforms: Linux

Test Characteristics:
- Multi-threaded: Yes (compilation is highly parallel)
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: N/A (Compiler workload)
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Runtime thread configuration via make -j $NUM_CPU_CORES
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

    def install_benchmark(self):
        """
        Install build-gcc-1.5.0 with GCC-14 native compilation.

        Note: Unlike coremark, GCC build does NOT need reinstallation for each thread count
        because it supports runtime thread configuration via make -j argument.

        Since THFix_in_compile=false, NUM_CPU_CORES is NOT set during build.
        Thread count is controlled at runtime via NUM_CPU_CORES environment variable.
        """
        # [Pattern 5] Pre-download large files from downloads.xml (Size > 256MB)
        print(f"\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=256)

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
        # LINUX_PERF=1: Enable PTS's built-in perf stat module (System Monitor)
        # Note: PTS_USER_PATH_OVERRIDE removed - use default ~/.phoronix-test-suite/ with batch-setup config
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        perf_env = 'LINUX_PERF=1 '
        batch_env = f'{quick_env}{perf_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME=build-gcc-{num_threads}threads TEST_RESULTS_IDENTIFIER=build-gcc-{num_threads}threads'

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
            err_file = self.results_dir / f"{num_threads}-thread.err"
            with open(err_file, 'w') as f:
                f.write(f"Benchmark failed with return code {returncode}\n")
                f.write(f"See {log_file} for details.\n")
            print(f"     Error log: {err_file}")
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
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n")
                f.write(f"  Raw values: {', '.join([f'{v:.2f}' for v in result['raw_values']])}\n")
                f.write("\n")

            f.write("="*80 + "\n")
            f.write("Summary Table\n")
            f.write("="*80 + "\n")
            f.write(f"{'Threads':<10} {'Average':<15} {'Unit':<20}\n")
            f.write("-"*80 + "\n")
            for result in all_results:
                val_str = f"{result['value']:<15.2f}" if result['value'] is not None else "FAILED         "
                f.write(f"{result['threads']:<10} {val_str} {result['unit']:<20}\n")

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
