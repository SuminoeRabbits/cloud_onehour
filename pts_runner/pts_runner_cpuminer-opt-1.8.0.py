#!/usr/bin/env python3
"""
PTS Runner for cpuminer-opt-1.8.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * C/C++ Compiler Toolchain
  * Libcurl
  * Libjansson
- Estimated Install Time: 5 Seconds
- Environment Size: 10 MB
- Test Type: Processor
- Supported Platforms: Linux

Test Characteristics:
- Multi-threaded: Yes
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: CPU feature dependent
- THFix_in_compile: false - Thread count NOT fixed at compile time
- THChange_at_runtime: true - Runtime thread configuration via NUM_CPU_CORES
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

    def download_from_xml(self, benchmark_name, threshold_mb=96):
        """
        Parse downloads.xml for the benchmark and download large files.
        """
        if not self.aria2_available:
            return False

        profile_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / benchmark_name / "downloads.xml"
        if not profile_path.exists():
            print(f"  [WARN] downloads.xml not found at {profile_path}")
            print(f"  [INFO] Attempting to fetch test profile via phoronix-test-suite info {benchmark_name}...")
            try:
                subprocess.run(
                    ['phoronix-test-suite', 'info', benchmark_name],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception as e:
                print(f"  [WARN] Failed to run phoronix-test-suite info: {e}")
                return False

            if not profile_path.exists():
                print(f"  [WARN] downloads.xml still missing after info: {profile_path}")
                return False

        try:
            import xml.etree.ElementTree as ET
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

                url = url_node.text.strip()
                filename = filename_node.text.strip()

                size_bytes = -1
                if filesize_node is not None and filesize_node.text:
                    try:
                        size_bytes = int(filesize_node.text.strip())
                    except ValueError:
                        pass

                if size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb >= threshold_mb:
                        print(f"  [INFO] {filename} is large ({size_mb:.1f} MB), accelerating with aria2c...")
                        self.ensure_file(url, filename)

        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False

        return True

    def ensure_file(self, url, filename):
        """
        Directly download file using aria2c.
        """
        target_path = self.cache_dir / filename
        if target_path.exists():
            print(f"  [CACHE] File found: {filename}")
            return True

        print(f"  [ARIA2] Downloading {filename} with 16 connections...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "aria2c", "-x", "16", "-s", "16",
            "-d", str(self.cache_dir),
            "-o", filename,
            url
        ]

        try:
            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"  [ERROR] aria2c download failed for {filename}: {e}")
            return False


class CpuminerOptRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize cpuminer-opt runner.

        Args:
            threads_arg: Thread count argument (None for scaling mode, int for fixed mode)
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development
        """
        self.benchmark = "cpuminer-opt-1.8.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Cryptography"
        self.test_category_dir = self.test_category.replace(" ", "_").replace("/", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Thread list setup
        if threads_arg is None:
            self.thread_list = list(range(2, self.vcpu_count + 1, 2))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

        # Detect environment for logging
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        # Enforce safety
        self.ensure_upload_disabled()

        # IMPORTANT: Setup perf permissions BEFORE testing perf availability
        self.perf_paranoid = self.check_and_setup_perf_permissions()

        # Feature Detection: Check if perf is actually functional
        self.perf_events = self.get_perf_events()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

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

    def is_wsl(self):
        """Detect if running in WSL environment."""
        try:
            if not os.path.exists('/proc/version'):
                return False
            with open('/proc/version', 'r') as f:
                content = f.read().lower()
                return 'microsoft' in content or 'wsl' in content
        except Exception:
            return False

    def get_cpu_frequencies(self):
        """Get current CPU frequencies for all CPUs."""
        frequencies = []

        try:
            result = subprocess.run(
                ['bash', '-c', 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    parts = line.split(':')
                    if len(parts) >= 2:
                        mhz = float(parts[1].strip())
                        frequencies.append(int(mhz * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass

        try:
            freq_files = sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_cur_freq'))
            if not freq_files:
                freq_files = sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/cpuinfo_cur_freq'))

            for freq_file in freq_files:
                try:
                    with open(freq_file, 'r') as f:
                        freq_khz = int(f.read().strip())
                        frequencies.append(freq_khz)
                except Exception:
                    frequencies.append(0)

            if frequencies:
                return frequencies
        except Exception:
            pass

        try:
            result = subprocess.run(['lscpu'], capture_output=True, text=True)
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'CPU MHz' in line or 'CPU max MHz' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            mhz = float(parts[1].strip().replace(',', '.'))
                            return [int(mhz * 1000)] * self.vcpu_count
        except Exception:
            pass

        return frequencies

    def record_cpu_frequency(self, output_file):
        """Record current CPU frequencies to a file."""
        frequencies = self.get_cpu_frequencies()
        if frequencies:
            try:
                with open(output_file, 'w') as f:
                    for freq in frequencies:
                        f.write(f"{freq}\n")
                return True
            except Exception as e:
                print(f"  [WARN] Failed to write frequency file: {e}")
                return False
        else:
            try:
                with open(output_file, 'w') as f:
                    pass
                return False
            except Exception:
                return False

    def get_perf_events(self):
        """Determine available perf events by testing actual command execution."""
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found")
            return None

        hw_events = "cycles,instructions,cpu-clock,task-clock,context-switches,cpu-migrations"
        test_cmd = f"perf stat -e {hw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)

        if result.returncode == 0:
            combined_output = result.stderr + result.stdout
            if 'not supported' not in combined_output.lower() and 'not counted' not in combined_output.lower():
                return hw_events

        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations"
        test_cmd = f"perf stat -e {sw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)

        if result.returncode == 0:
            combined_output = result.stderr + result.stdout
            if 'not supported' not in combined_output.lower() and 'not counted' not in combined_output.lower():
                return sw_events

        print("  [WARN] perf events not available")
        return None

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

    def ensure_upload_disabled(self):
        """Ensure that PTS results upload is disabled in user-config.xml."""
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
        print(f"\n>>> Checking for large files to pre-seed...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=96)

        print(f"\n>>> Installing {self.benchmark_full}...")

        print(f"  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        nproc = os.cpu_count() or 1
        install_cmd = (
            f'MAKEFLAGS="-j{nproc}" CC=gcc-14 CXX=g++-14 '
            f'CFLAGS="-O3 -march=native -mtune=native" '
            f'CXXFLAGS="-O3 -march=native -mtune=native" '
            f'phoronix-test-suite batch-install {self.benchmark_full}'
        )

        print(f"\n{'>'*80}")
        print(f"[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        print(f"  Running installation...")
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
        log_f = open(install_log, 'w') if use_install_log else None
        if log_f:
            log_f.write(f"[PTS INSTALL COMMAND]\n{install_cmd}\n\n")
            log_f.flush()

        process = subprocess.Popen(
            ['bash', '-c', install_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )

        install_output = []
        for line in process.stdout:
            print(line, end='')
            if log_f:
                log_f.write(line)
                log_f.flush()
            install_output.append(line)

        process.wait()
        returncode = process.returncode
        if log_f:
            log_f.close()

        install_failed = False
        full_output = ''.join(install_output)

        if returncode != 0:
            install_failed = True
        elif 'Checksum Failed' in full_output or 'Downloading of needed test files failed' in full_output:
            install_failed = True
        elif 'ERROR' in full_output or 'FAILED' in full_output:
            install_failed = True

        if install_failed:
            print(f"\n  [ERROR] Installation failed with return code {returncode}")
            print(f"  [INFO] Check output above for details")
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)

        pts_home = Path.home() / '.phoronix-test-suite'
        installed_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark

        if not installed_dir.exists():
            print(f"  [ERROR] Installation verification failed")
            print(f"  [ERROR] Expected directory not found: {installed_dir}")
            print(f"  [INFO] Installation may have failed silently")
            print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
            sys.exit(1)

        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        verify_result = subprocess.run(['bash', '-c', verify_cmd], capture_output=True, text=True)
        if verify_result.returncode != 0:
            print(f"  [WARN] Test may not be fully installed (test-installed check failed)")
            print(f"  [INFO] But installation directory exists, continuing...")

        print(f"  [OK] Installation completed and verified: {installed_dir}")

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

        if freq_start_file.exists():
            with open(freq_start_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    freq_khz = float(freq_lines[idx].strip())
                    perf_summary["start_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)

        if freq_end_file.exists():
            with open(freq_end_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    freq_khz = float(freq_lines[idx].strip())
                    perf_summary["end_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)

        if perf_stats_file.exists():
            with open(perf_stats_file, 'r') as f:
                perf_data = f.read()

            for cpu_id in cpu_ids:
                cycles_match = re.search(rf'CPU{cpu_id}\s+(\d+(?:,\d+)*)\s+cycles', perf_data)
                if cycles_match:
                    perf_summary["total_cycles"][str(cpu_id)] = int(cycles_match.group(1).replace(',', ''))

                instr_match = re.search(rf'CPU{cpu_id}\s+(\d+(?:,\d+)*)\s+instructions', perf_data)
                if instr_match:
                    perf_summary["total_instructions"][str(cpu_id)] = int(instr_match.group(1).replace(',', ''))

                if cycles_match and instr_match:
                    cycles = int(cycles_match.group(1).replace(',', ''))
                    instructions = int(instr_match.group(1).replace(',', ''))
                    if cycles > 0:
                        perf_summary["ipc"][str(cpu_id)] = round(instructions / cycles, 2)

            elapsed_match = re.search(r'([\d,]+(?:\.\d+)?)\s+seconds time elapsed', perf_data)
            if elapsed_match:
                perf_summary["elapsed_time_sec"] = float(elapsed_match.group(1).replace(',', ''))

        return perf_summary

    def run_benchmark(self, num_threads):
        """Run benchmark with specified threads."""
        print(f"\n{'='*80}")
        print(f">>> Running {self.benchmark_full} with {num_threads} thread(s)")
        print(f"{'='*80}")

        self.results_dir.mkdir(parents=True, exist_ok=True)
        thread_dir = self.results_dir / f"{num_threads}-thread"
        thread_dir.mkdir(parents=True, exist_ok=True)

        perf_stats_file = thread_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = thread_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = thread_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = thread_dir / f"{num_threads}-thread_perf_summary.json"

        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = (
            f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 '
            f'TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        )

        # Prevent interactive prompts by removing prior results
        result_tag = f"{self.benchmark}-{num_threads}threads"
        try:
            subprocess.run(
                ['phoronix-test-suite', 'remove-result', result_tag],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False
            )
        except Exception:
            pass

        if num_threads >= self.vcpu_count:
            cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
            pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"Using all {num_threads} vCPUs (no taskset)"
        else:
            cpu_list = self.get_cpu_affinity_list(num_threads)
            pts_base_cmd = f'taskset -c {cpu_list} phoronix-test-suite batch-run {self.benchmark_full}'
            cpu_info = f"CPU affinity (taskset): {cpu_list}"

        if self.perf_events:
            if self.perf_paranoid <= 0:
                pts_cmd = (
                    f'NUM_CPU_CORES={num_threads} {batch_env} '
                    f'perf stat -e {self.perf_events} -A -a -o {perf_stats_file} {pts_base_cmd}'
                )
                perf_mode = "Full (per-CPU + HW counters)"
            else:
                pts_cmd = (
                    f'NUM_CPU_CORES={num_threads} {batch_env} '
                    f'perf stat -e {self.perf_events} -o {perf_stats_file} {pts_base_cmd}'
                )
                perf_mode = "Limited (aggregated events only)"
        else:
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
            perf_mode = "Disabled (perf unavailable)"

        print(f"[INFO] Perf monitoring mode: {perf_mode}")
        print(f"  [INFO] {cpu_info}")
        print(f"\n{'>'*80}")
        print(f"[PTS RUN COMMAND]")
        print(f"  {pts_cmd}")
        print(f"{'<'*80}\n")

        print(f"[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print(f"  [OK] Start frequency recorded")
        else:
            print(f"  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

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

        print(f"\n[INFO] Recording CPU frequency after benchmark...")
        if self.record_cpu_frequency(freq_end_file):
            print(f"  [OK] End frequency recorded")
        else:
            print(f"  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        if returncode == 0:
            print(f"\n[OK] Benchmark completed successfully")
            if self.perf_events and perf_stats_file.exists():
                try:
                    perf_summary = self.parse_perf_stats_and_freq(
                        perf_stats_file, freq_start_file, freq_end_file, cpu_list
                    )
                    with open(perf_summary_file, 'w') as f:
                        json.dump(perf_summary, f, indent=2)
                    print(f"  [OK] Perf summary saved to {perf_summary_file}")
                except Exception as e:
                    print(f"  [ERROR] Failed to parse perf stats: {e}")
            return True
        else:
            print(f"\n[ERROR] Benchmark failed with return code {returncode}")
            return False

    def export_results(self):
        """Export benchmark results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(f">>> Exporting benchmark results")
        print(f"{'='*80}")

        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"

        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"

            result_dir = pts_results_dir / result_name
            result_dir_nodots = pts_results_dir / result_name.replace('.', '')

            target_result_dir = None
            target_result_name = None

            if result_dir.exists():
                target_result_dir = result_dir
                target_result_name = result_name
            elif result_dir_nodots.exists():
                target_result_dir = result_dir_nodots
                target_result_name = result_name.replace('.', '')

            if not target_result_dir:
                print(f"[WARN] Result not found for {num_threads} threads: {result_name}")
                continue

            print(f"\n[INFO] Exporting results for {num_threads} thread(s)...")

            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', target_result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                home_csv = Path.home() / f"{target_result_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', target_result_name],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                home_json = Path.home() / f"{target_result_name}.json"
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

        all_results = []
        for num_threads in self.thread_list:
            json_file = self.results_dir / f"{num_threads}-thread.json"
            if json_file.exists():
                with open(json_file, 'r') as f:
                    data = json.load(f)
                    for result_id, result in data.get('results', {}).items():
                        for system_id, system_result in result.get('results', {}).items():
                            all_results.append({
                                "threads": num_threads,
                                "result_id": result_id,
                                "system_id": system_id,
                                "value": system_result.get('value', None),
                                "units": system_result.get('units', None)
                            })

        with open(summary_log, 'w') as f:
            f.write(f"Benchmark: {self.benchmark_full}\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"OS: {self.os_name}\n")
            f.write(f"Threads: {self.thread_list}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write("\nResults:\n")
            for r in all_results:
                f.write(f"  Threads={r['threads']}: {r['value']} {r['units']}\n")

        with open(summary_json_file, 'w') as f:
            json.dump({
                "benchmark": self.benchmark_full,
                "machine": self.machine_name,
                "os": self.os_name,
                "threads": self.thread_list,
                "test_category": self.test_category,
                "results": all_results
            }, f, indent=2)

        print(f"  [OK] Summary written to {summary_log} and {summary_json_file}")

    def run(self):
        """Main execution flow."""
        # Clean only thread-specific files (preserve other threads' results)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        for num_threads in self.thread_list:
            prefix = f"{num_threads}-thread"
            thread_dir = self.results_dir / prefix
            if thread_dir.exists():
                shutil.rmtree(thread_dir)
            for f in self.results_dir.glob(f"{prefix}.*"):
                f.unlink()
            print(f"  [INFO] Cleaned existing {prefix} results (other threads preserved)")

        print(f"\n{'#'*80}")
        print(f"# PTS Runner: {self.benchmark_full}")
        print(f"# Machine: {self.machine_name}")
        print(f"# OS: {self.os_name}")
        print(f"# vCPU Count: {self.vcpu_count}")
        print(f"# Thread List: {self.thread_list}")
        print(f"# Quick Mode: {'ENABLED' if self.quick_mode else 'DISABLED'}")
        print(f"{'#'*80}\n")

        self.clean_pts_cache()
        self.install_benchmark()
        for t in self.thread_list:
            if not self.run_benchmark(t):
                return False
        self.export_results()
        self.generate_summary()
        return True


def main():
    parser = argparse.ArgumentParser(description="Run PTS cpuminer-opt benchmark")
    parser.add_argument('threads_pos', nargs='?', type=int, help='Thread count (optional)')
    parser.add_argument('--threads', type=int, help='Thread count (overrides positional)')
    parser.add_argument('--quick', action='store_true', help='Quick mode (FORCE_TIMES_TO_RUN=1)')
    args = parser.parse_args()

    threads = args.threads if args.threads else args.threads_pos
    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3+ times (60-70%% time reduction)")

    runner = CpuminerOptRunner(threads_arg=threads, quick_mode=args.quick)
    ok = runner.run()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
