#!/usr/bin/env python3
"""
PTS Runner for ffmpeg-7.0.1

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * Yasm Assembler
  * Nasm Assembler
  * Python
  * CMake
- Estimated Install Time: 453 Seconds
- Environment Size: 1300 MB
- Test Type: Processor
- Supported Platforms: Linux

Test Characteristics:
- Multi-threaded: Yes (video encoding is highly parallel)
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: SVE2 support for ARM architectures
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Runtime thread configuration via -threads $NUM_CPU_CORES option
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


class FFmpegRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize FFmpeg runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "ffmpeg-7.0.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Multimedia"
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
                    return 0
                else:
                    print(f"  [ERROR] Failed to adjust perf_event_paranoid (sudo required)")
                    print(f"  [WARN] Running in LIMITED mode")
                    return current_value
            else:
                print(f"  [OK] perf_event_paranoid={current_value} is acceptable")
                return current_value

        except Exception as e:
            print(f"  [ERROR] Could not check perf_event_paranoid: {e}")
            print(f"  [WARN] Assuming restrictive mode (perf_event_paranoid=2)")
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

        # NOTE: We do NOT remove the test profile here.
        # We want to keep it (or download it) so we can patch downloads.xml.
        # Removing it forces a re-download which might just act weirdly or fail.

        print("  [OK] PTS cache cleaned")

    def get_cpu_affinity_list(self, n):
        """
        Generate CPU affinity list for HyperThreading optimization.

        Args:
            n: Number of threads

        Returns:
            Comma-separated CPU list string (e.g., "0,2,4,1,3")
        """
        half = self.vcpu_count // 2
        cpu_list = []

        if n <= half:
            cpu_list = [str(i * 2) for i in range(n)]
        else:
            cpu_list = [str(i * 2) for i in range(half)]
            logical_count = n - half
            cpu_list.extend([str(i * 2 + 1) for i in range(logical_count)])

        return ','.join(cpu_list)

    def fix_x264_checksum(self):
        """
        Fix x264 checksum in downloads.xml due to upstream changes.

        VideoLAN's x264 git archive ZIP has changed, causing checksum mismatch.
        This method updates the downloads.xml with the correct checksums.
        """
        downloads_xml = Path.home() / '.phoronix-test-suite' / 'test-profiles' / 'pts' / self.benchmark / 'downloads.xml'

        if not downloads_xml.exists():
            print(f"  [WARN] downloads.xml not found, skipping checksum fix")
            return

        try:
            with open(downloads_xml, 'r') as f:
                content = f.read()

            # Target x264 file: x264-7ed753b10a61d0be95f683289dfb925b800b0676.zip
            # New checksums (actual current file)
            new_md5 = '62a5d6c7207be9f0aa6e1a9552345c8c'
            new_sha256 = 'd859f2b4b0b70d6f10a33e9b5e0a6bf41d97e4414644c4f85f02b7665c0d1292'
            new_size = '1222391'

            # Safer approach: Split content into Package blocks to avoid regex crossing boundaries
            packages = content.split('</Package>')
            output_packages = []
            modified = False

            for pkg in packages:
                # Ensure we only target the x264 package, avoiding false positives (e.g. if x264 is mentioned in ffmpeg block)
                if 'x264-7ed753b10a61d0be95f683289dfb925b800b0676.zip' in pkg and 'ffmpeg-7.0.tar.xz' not in pkg:
                    # found the target package
                    
                    # Update MD5
                    pkg = re.sub(r'<MD5>[a-fA-F0-9]+</MD5>', f'<MD5>{new_md5}</MD5>', pkg)
                    # Update SHA256
                    pkg = re.sub(r'<SHA256>[a-fA-F0-9]+</SHA256>', f'<SHA256>{new_sha256}</SHA256>', pkg)
                    # Update FileSize
                    pkg = re.sub(r'<FileSize>\d+</FileSize>', f'<FileSize>{new_size}</FileSize>', pkg)
                    
                    modified = True
                    print(f"  [DEBUG] Fixed checksums in x264 package block")
                
                output_packages.append(pkg)

            if modified:
                print(f"  [FIX] Updating x264 checksums in downloads.xml (Safe Split)")
                modified_content = '</Package>'.join(output_packages)
                
                with open(downloads_xml, 'w') as f:
                    f.write(modified_content)
                print(f"  [OK] x264 checksums updated")
            else:
                print(f"  [INFO] x264 package not found or already correct (no changes needed)") 
            
        except Exception as e:
            print(f"  [WARN] Failed to fix checksums: {e}")

    def install_benchmark(self):
        """
        Install ffmpeg-7.0.1 with GCC-14 native compilation.

        Note: FFmpeg supports runtime thread configuration via -threads option.
        Since THFix_in_compile=false, NUM_CPU_CORES is NOT set during build.
        """
        print(f"\n>>> Installing {self.benchmark_full}...")

        # Ensure test profile exists (download if needed) so we can patch it
        print(f"  [INFO] Ensuring test profile exists...")
        subprocess.run(
            ['phoronix-test-suite', 'info', self.benchmark_full],
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )

        # Pre-apply x264 checksum fix to avoid double installation time
        self.fix_x264_checksum()

        # Remove existing installation first
        print(f"  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        print(f"  [INSTALL CMD] {remove_cmd}")
        subprocess.run(
            ['bash', '-c', remove_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Build install command
        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 CFLAGS="-O3 -march=native -mtune=native" CXXFLAGS="-O3 -march=native -mtune=native" phoronix-test-suite batch-install {self.benchmark_full}'

        # Print install command for debugging
        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        # Execute install command (attempt 1)
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

        # If first attempt failed due to x264 checksum, fix and retry
        if install_failed and result.stdout and 'x264-7ed753b10a61d0be95f683289dfb925b800b0676.zip' in result.stdout:
            print(f"  [WARN] x264 checksum failure detected, applying fix...")
            self.fix_x264_checksum()

            print(f"  [INFO] Retrying installation after checksum fix...")
            result = subprocess.run(
                ['bash', '-c', install_cmd],
                capture_output=True,
                text=True
            )

            # Re-check for installation failure
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
            # Print previous install stdout/stderr for debugging context since we returned 0 but failed verify
            if result.stdout:
                print(f"  [DEBUG] Install stdout: {result.stdout[-1000:]}")
            if result.stderr:
                print(f"  [DEBUG] Install stderr: {result.stderr[-1000:]}")

            print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
            sys.exit(1)

        print(f"  [OK] Installation completed and verified")

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files."""
        print(f"\n>>> Parsing perf stats and frequency data")

        cpu_ids = [int(c.strip()) for c in cpu_list.split(',')]
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

        # Parse perf stat output
        try:
            with open(perf_stats_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    match = re.match(r'CPU(\d+)\s+([0-9,]+)\s+(\S+)', line)
                    if match:
                        cpu_num = int(match.group(1))
                        value_str = match.group(2).replace(',', '')
                        event = match.group(3)

                        if cpu_num not in cpu_ids:
                            continue

                        try:
                            value = float(value_str)
                        except ValueError:
                            continue

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

        except Exception as e:
            print(f"  [ERROR] Failed to parse perf stat file: {e}")
            raise

        # Parse frequency files
        freq_start = {}
        freq_end = {}

        try:
            with open(freq_start_file, 'r') as f:
                lines = f.read().strip().split('\n')
                for i, line in enumerate(lines):
                    if line.strip():
                        freq_start[i] = float(line.strip())

            with open(freq_end_file, 'r') as f:
                lines = f.read().strip().split('\n')
                for i, line in enumerate(lines):
                    if line.strip():
                        freq_end[i] = float(line.strip())

        except Exception as e:
            print(f"  [ERROR] Failed to parse frequency files: {e}")
            raise

        # Calculate metrics
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

            if metrics['cpu_clock'] > 0:
                avg_freq = metrics['cycles'] / (metrics['cpu_clock'] / 1000.0) / 1e9
                perf_summary['avg_frequency_ghz'][str(cpu_id)] = round(avg_freq, 3)
            else:
                perf_summary['avg_frequency_ghz'][str(cpu_id)] = 0.0

            if cpu_id in freq_start:
                start_freq = freq_start[cpu_id] / 1_000_000.0
                perf_summary['start_frequency_ghz'][str(cpu_id)] = round(start_freq, 3)
            else:
                perf_summary['start_frequency_ghz'][str(cpu_id)] = 0.0

            if cpu_id in freq_end:
                end_freq = freq_end[cpu_id] / 1_000_000.0
                perf_summary['end_frequency_ghz'][str(cpu_id)] = round(end_freq, 3)
            else:
                perf_summary['end_frequency_ghz'][str(cpu_id)] = 0.0

            if metrics['cycles'] > 0:
                ipc = metrics['instructions'] / metrics['cycles']
                perf_summary['ipc'][str(cpu_id)] = round(ipc, 2)
            else:
                perf_summary['ipc'][str(cpu_id)] = 0.0

            perf_summary['total_cycles'][str(cpu_id)] = int(metrics['cycles'])
            perf_summary['total_instructions'][str(cpu_id)] = int(metrics['instructions'])

            total_task_clock += metrics['task_clock']
            max_task_clock = max(max_task_clock, metrics['task_clock'])

        if max_task_clock > 0:
            perf_summary['elapsed_time_sec'] = round(max_task_clock / 1000.0, 2)
            utilization = (total_task_clock / max_task_clock / len(cpu_ids)) * 100.0
            perf_summary['cpu_utilization_percent'] = round(utilization, 1)

        print(f"  [OK] Performance metrics calculated")
        return perf_summary

    def run_benchmark(self, num_threads):
        """Run benchmark with specified thread count."""
        print(f"\n{'='*80}")
        print(f">>> Running benchmark with {num_threads} thread(s)")
        print(f"{'='*80}")

        # Create output directory
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        # Build PTS command
        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME=ffmpeg-{num_threads}threads TEST_RESULTS_IDENTIFIER=ffmpeg-{num_threads}threads'

        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        # CRITICAL: Environment variables MUST come BEFORE perf stat
        pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} perf stat -e cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations -A -a -o {perf_stats_file} {pts_base_cmd}'

        print(f"[INFO] {cpu_info}")
        print(f"\n{'>'*80}")
        print(f"[PTS BENCHMARK COMMAND]")
        print(f"  {pts_cmd}")
        print(f"{'<'*80}\n")

        # Record CPU frequency before benchmark
        print(f"[INFO] Recording CPU frequency before benchmark...")
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_start_file)
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)

        # Execute PTS command
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
        print(f"\n[INFO] Recording CPU frequency after benchmark...")
        cmd_template = 'grep "cpu MHz" /proc/cpuinfo | awk \'{{printf "%.0f\\n", $4 * 1000}}\' > {file}'
        command = cmd_template.format(file=freq_end_file)
        subprocess.run(['bash', '-c', command], capture_output=True, text=True)

        if returncode == 0:
            print(f"\n[OK] Benchmark completed successfully")

            try:
                perf_summary = self.parse_perf_stats_and_freq(
                    perf_stats_file,
                    freq_start_file,
                    freq_end_file,
                    cpu_list
                )

                with open(perf_summary_file, 'w') as f:
                    json.dump(perf_summary, f, indent=2)
                print(f"     Perf summary: {perf_summary_file}")

            except Exception as e:
                print(f"  [ERROR] Failed to parse perf stats: {e}")

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
            result_name = f"ffmpeg-{num_threads}threads"
            result_dir = pts_results_dir / result_name
            if not result_dir.exists():
                print(f"[WARN] Result not found for {num_threads} threads")
                continue

            print(f"\n[INFO] Exporting results for {num_threads} thread(s)...")

            # Export to CSV
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                home_json = Path.home() / f"{result_name}.json"
                if home_json.exists():
                    shutil.move(str(home_json), str(json_output))
                    print(f"  [OK] Saved: {json_output}")

        print(f"\n[OK] Export completed")

    def generate_summary(self):
        """Generate summary.log and summary.json from all thread results."""
        print(f"\n{'='*80}")
        print(f">>> Generating summary")
        print(f"{'='*80}")

        summary_log = self.results_dir / "summary.log"
        summary_json_file = self.results_dir / "summary.json"

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

        # Generate summary.log
        with open(summary_log, 'w') as f:
            f.write("="*80 + "\n")
            f.write(f"FFmpeg 7.0.1 Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Threads: {result['threads']}\n")
                f.write(f"  Test: {result['test_name']}\n")

                # Check for None to avoid f-string crash
                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Average: {val_str} {result['unit']}\n")

                f.write("\n")

        print(f"[OK] Summary log saved: {summary_log}")

        # Generate summary.json
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
        print(f"FFmpeg 7.0.1 Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print(f"[INFO] Threads to test: {self.thread_list}")
        print()

        if self.results_dir.exists():
            print(f">>> Cleaning existing results directory...")
            shutil.rmtree(self.results_dir)
            print(f"  [OK] Results directory cleaned\n")

        # Check if already installed
        verify_cmd = f'phoronix-test-suite info {self.benchmark_full}'
        verify_result = subprocess.run(
            ['bash', '-c', verify_cmd],
            capture_output=True,
            text=True
        )

        already_installed = verify_result.returncode == 0 and 'Test Installed: Yes' in verify_result.stdout

        if not already_installed:
            self.clean_pts_cache()
            self.install_benchmark()
        else:
            print(f">>> Benchmark {self.benchmark_full} already installed, skipping installation\n")

        failed = []
        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        self.export_results()
        self.generate_summary()

        print(f"\n{'='*80}")
        print(f"Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests: {len(self.thread_list)}")
        print(f"Successful: {len(self.thread_list) - len(failed)}")
        print(f"Failed: {len(failed)}")
        print(f"{'='*80}")

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description='FFmpeg 7.0.1 Benchmark Runner'
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

    if args.threads is not None and args.threads < 1:
        print(f"[ERROR] Thread count must be >= 1")
        sys.exit(1)

    runner = FFmpegRunner(args.threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
