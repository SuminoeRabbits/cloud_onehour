#!/usr/bin/env python3
"""
PTS Runner for compress-lz4-1.10.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
- Estimated Install Time: 26 Seconds
- Environment Size: 2000 MB
- Download Size: 202.5 MB
- Test Type: Processor
- Supported Platforms: Linux, BSD, MacOSX, Solaris, Windows

Test Characteristics:
- Multi-threaded: No (single-threaded compression/decompression)
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: MMX, SSE, SSE2
- Instructions Possible With Extra Compiler Flags: MMX, SSE, SSE2, AVX, AVX2, AVX512
- THFix_in_compile: false
- THChange_at_runtime: false
- Description: LZ4 compression benchmark using silesia archive
- Test modes: Multiple compression levels (1, 2, 3, 9, 12) with compression/decompression speed tests
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

    def download_from_xml(self, benchmark_name, threshold_mb=100):
        """
        Parse downloads.xml for the benchmark and download large files.
        """
        if not self.aria2_available:
            return False

        profile_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / benchmark_name / "downloads.xml"
        if not profile_path.exists():
            return False

        try:
            tree = ET.parse(profile_path)
            root = tree.getroot()
            downloads_node = root.find('Downloads')

            if downloads_node is None:
                return False

            for package in downloads_node.findall('Package'):
                url_node = package.find('URL')
                filename_node = package.find('FileName')
                filesize_node = package.find('FileSize')

                if url_node is None or filename_node is None:
                    continue

                # Handle comma-separated URLs
                urls = [u.strip() for u in url_node.text.split(',')]
                url = urls[0] if urls else None
                filename = filename_node.text.strip()

                if not url:
                    continue

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
                        # Pass all URLs to ensure_file for fallback support
                        self.ensure_file(urls, filename)
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
                print(f"  [WARN] Failed to get headers for {url}")
                return -1

            # Parse Content-Length
            # Look for "content-length: 12345" (case insensitive)
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        size_str = line.split(':')[1].strip()
                        return int(size_str)
                    except ValueError:
                        pass
        except Exception as e:
            print(f"  [WARN] Error checking size: {e}")

        return -1

    def ensure_file(self, urls, filename):
        """
        Directly download file using aria2c (assumes size check passed).
        Args:
            urls: List of URLs or single URL string
            filename: Target filename
        """
        target_path = self.cache_dir / filename

        # Check if file exists in cache
        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        if isinstance(urls, str):
            urls = [urls]

        # Need to download
        print(f"  [ARIA2] Downloading {filename} with 16 connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # aria2c command - pass all URLs as separate arguments
        cmd = [
            "aria2c", "-x", "16", "-s", "16",
            "-d", str(self.cache_dir),
            "-o", filename
        ] + urls

        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] aria2c download failed for {filename}: {e}")
            return False
        return True


class CompressLZ4BenchmarkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        # Benchmark configuration
        self.benchmark = "compress-lz4-1.10.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Compression"
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Thread list setup (LZ4 is single-threaded, but we run once per thread count for consistency)
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
        # Enforce safety
        self.ensure_upload_disabled()
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
        Check perf_event_paranoid level (but do not modify it).
        """
        try:
            with open('/proc/sys/kernel/perf_event_paranoid', 'r') as f:
                level = int(f.read().strip())
            return level
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
        # Check if perf command exists in PATH
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None

        # Test Hardware + Software events (Preferred for Native Linux)
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

            # Test Software-only events (Fallback for Cloud/VM/Standard WSL)
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
                            # Remove units like "msec" if present
                            value_clean = value_str.split()[0]
                            value = float(value_clean.replace(',', ''))
                            per_cpu_metrics[cpu_num][event] = value
                        except ValueError:
                            continue

        return {'per_cpu_metrics': per_cpu_metrics, 'cpu_list': cpu_list}

    def run_benchmark(self, num_threads):
        """Run benchmark with conditional perf monitoring."""
        # Create output directory
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

        # Define file paths for perf stats and frequency monitoring
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # Build PTS base command (LZ4 is single-threaded, but we use taskset for consistency)
        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'

        # Environment variables for batch mode execution
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        # Remove existing PTS result to avoid interactive prompts
        # PTS sanitizes identifiers (e.g. 1.0.2 -> 102), so we try to remove both forms
        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        batch_env = f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'

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
                except Exception as e:
                    print(f"  [ERROR] Failed to parse perf stats: {e}")
            return True
        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            return False

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
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
                    results_data = data.get('results', {})

                    # Handle both dict (normal case) and list (failure case)
                    if isinstance(results_data, list):
                        print(f"[WARN] No valid results in {json_file.name} (benchmark may have failed)")
                        continue

                    for result_id, result in results_data.items():
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
            f.write(f"Benchmark Summary\n")
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

    def install_benchmark(self):
        """Install benchmark with pre-seeding for large files."""
        print(f"\n{'='*80}")
        print(f">>> Installing {self.benchmark_full}")
        print(f"{'='*80}")

        # Pre-seed large files (202.5MB download - silesia archive)
        print(f"\n>>> Pre-seeding large files (if needed)")
        downloader = PreSeedDownloader()
        if downloader.is_aria2_available():
            print(f"  [OK] aria2c is available for accelerated downloads")
            downloader.download_from_xml(self.benchmark_full, threshold_mb=100)
        else:
            print(f"  [INFO] aria2c not available, using PTS default downloader")

        # Remove existing installation
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Build install command with compiler optimizations
        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

        # Execute with real-time output streaming
        print(f"  Running installation...")
        process = subprocess.Popen(['bash', '-c', install_cmd],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   text=True,
                                   bufsize=1)

        for line in process.stdout:
            print(line, end='')

        process.wait()

        # Verify installation with dual check
        pts_home = Path.home() / '.phoronix-test-suite'
        install_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark

        if not install_dir.exists():
            print(f"  [ERROR] Installation failed: {install_dir} does not exist")
            print(f"  [ERROR] Check output above for details")
            sys.exit(1)

        # Secondary check: PTS recognition
        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        result = subprocess.run(['bash', '-c', verify_cmd], capture_output=True, text=True)
        if self.benchmark_full not in result.stdout:
            print(f"  [WARN] {self.benchmark_full} may not be fully recognized by PTS")

        print(f"  [OK] Installation completed and verified")


    def ensure_upload_disabled(self):
        """
        Ensure that PTS results upload is disabled in user-config.xml.
        This is a safety measure to prevent accidental data leaks.
        """
        config_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if not config_path.exists():
            return
            
        try:
            with open(config_path, 'r') as f:
                content = f.read()
                
            if '<UploadResults>TRUE</UploadResults>' in content:
                print("  [WARN] UploadResults is TRUE in user-config.xml. Disabling...")
                content = content.replace('<UploadResults>TRUE</UploadResults>', '<UploadResults>FALSE</UploadResults>')
                with open(config_path, 'w') as f:
                    f.write(content)
                print("  [OK] UploadResults set to FALSE")
        except Exception as e:
            print(f"  [WARN] Failed to check/update user-config.xml: {e}")

    def run(self):
        """Main execution method."""
        print(f"{'='*80}")
        print(f"PTS Benchmark Runner: {self.benchmark}")
        print(f"Machine: {self.machine_name}")
        print(f"OS: {self.os_name}")
        print(f"vCPU Count: {self.vcpu_count}")
        print(f"Thread List: {self.thread_list}")
        print(f"Quick Mode: {self.quick_mode}")
        print(f"Results Directory: {self.results_dir}")
        print(f"{'='*80}\n")

        # Install benchmark
        self.install_benchmark()

        # Run benchmark for each thread count
        for num_threads in self.thread_list:
            print(f"\n{'='*80}")
            print(f">>> Running {self.benchmark} with {num_threads} thread(s)")
            print(f"{'='*80}")

            success = self.run_benchmark(num_threads)
            if not success:
                print(f"[ERROR] Benchmark failed for {num_threads} thread(s)")
                sys.exit(1)

        # Export results
        print(f"\n{'='*80}")
        print(f">>> Exporting results")
        print(f"{'='*80}")
        self.export_results()

        # Generate summary
        self.generate_summary()

        print(f"\n{'='*80}")
        print(f"[SUCCESS] All benchmarks completed successfully")
        print(f"{'='*80}")

        return True


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        'threads_pos',
        nargs='?',
        type=int,
        help='Number of threads (optional, omit for scaling mode)'
    )

    parser.add_argument(
        '--threads',
        type=int,
        help='Run benchmark with specified number of threads only (1 to CPU count)'
    )

    parser.add_argument(
        '--quick',
        action='store_true',
        help='Quick mode: Run each test only once (for development/testing)'
    )

    args = parser.parse_args()

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70%% time reduction)")

    # Resolve threads argument (prioritize --threads if both provided)
    threads = args.threads if args.threads is not None else args.threads_pos

    runner = CompressLZ4BenchmarkRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
