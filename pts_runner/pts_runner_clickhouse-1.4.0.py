#!/usr/bin/env python3
"""
PTS Runner for clickhouse-1.4.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * None (pre-built binary download)
- Download Size: ~16 GB (hits.tsv.gz dataset)
- Environment Size: ~91,900 MB (after decompression)
- Average Install Time: ~7 Minutes
- Average Run-Time: ~9.7 Minutes per cycle
- Test Type: System (Database / OLAP)
- Supported Platforms: Linux (x86_64, aarch64)

Test Characteristics:
- Multi-threaded: Yes (ClickHouse manages all CPU cores internally)
- Honors CFLAGS/CXXFLAGS: N/A (pre-built binary)
- THFix_in_compile: false - No compilation step
- THChange_at_runtime: false - Thread count NOT user-configurable (CH auto-detects)

Notes:
- Uses ClickBench 100M-row web analytics dataset (hits.tsv.gz, ~16 GB).
- Measures geometric mean of query processing times across 43 SQL queries.
- Three cold-cache runs (drop caches between runs) + warm-cache throughput pass.
- Result unit: queries/minute (higher is better).
- Pre-seed hits.tsv.gz via aria2c before install to avoid slow PTS download.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status, cleanup_pts_artifacts


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

    def download_from_xml(self, benchmark_name, threshold_mb=1):
        """
        Parse downloads.xml for the benchmark and download large files.

        Args:
            benchmark_name: Full benchmark name (e.g., "pts/clickhouse-1.4.0")
            threshold_mb: Size threshold in MB to trigger aria2c (default: 1 MB — always pre-seed)
        """
        if not self.aria2_available:
            print("  [INFO] aria2c not found, skipping pre-seed (will rely on PTS default)")
            return False

        profile_path = Path.home() / ".phoronix-test-suite" / "test-profiles" / benchmark_name / "downloads.xml"

        if not profile_path.exists():
            print(f"  [WARN] downloads.xml not found at {profile_path}")
            print(f"  [INFO] Fetching test profile via phoronix-test-suite info {benchmark_name}...")
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

                if size_bytes <= 0:
                    size_bytes = self.get_remote_file_size(url)

                if size_bytes > 0:
                    size_mb = size_bytes / (1024 * 1024)
                    if size_mb < threshold_mb:
                        continue
                    print(f"  [INFO] {filename} ({size_mb:.1f} MB), pre-seeding with aria2c...")
                    self.ensure_file(url, filename)

        except Exception as e:
            print(f"  [ERROR] Failed to parse downloads.xml: {e}")
            return False

        return True

    def get_remote_file_size(self, url):
        try:
            cmd = ['curl', '-s', '-I', '-L', url]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                return -1
            for line in result.stdout.splitlines():
                if line.lower().startswith('content-length:'):
                    try:
                        return int(line.split(':')[1].strip())
                    except ValueError:
                        pass
        except Exception:
            pass
        return -1

    def ensure_file(self, url, filename, size_bytes=-1):
        target_path = self.cache_dir / filename
        if target_path.exists():
            if size_bytes > 0 and target_path.stat().st_size != size_bytes:
                print(f"  [CACHE] Size mismatch for {filename}, re-downloading...")
            else:
                print(f"  [CACHE] File found: {filename}")
                return True

        print(f"  [ARIA2] Downloading {filename}...")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        _LARGE_FILE_THRESHOLD_BYTES = 10 * 1024 * 1024 * 1024
        num_conn = "4" if size_bytes >= _LARGE_FILE_THRESHOLD_BYTES else "16"
        cmd = [
            "aria2c", f"-x{num_conn}", f"-s{num_conn}",
            "--connect-timeout=30", "--timeout=120",
            "--max-tries=2", "--retry-wait=5", "--continue=true",
            "-d", str(self.cache_dir), "-o", filename, url
        ]
        try:
            subprocess.run(cmd, check=True, timeout=5400)
            print(f"  [aria2c] Download completed: {filename}")
            return True
        except subprocess.TimeoutExpired:
            print(f"  [ERROR] aria2c timed out downloading {filename}")
            if target_path.exists():
                target_path.unlink()
            return False
        except subprocess.CalledProcessError:
            print("  [WARN] aria2c download failed, falling back to PTS default")
            if target_path.exists():
                target_path.unlink()
            return False
class ClickHouseRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize ClickHouse OLAP benchmark runner.

        Args:
            threads_arg: Ignored. ClickHouse manages all CPU cores internally.
            quick_mode: If True, run tests once (FORCE_TIMES_TO_RUN=1) for development.
        """
        self.benchmark = "clickhouse-1.4.0"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "Database"
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # ClickHouse manages its own threading — always single-pass
        if threads_arg is not None and threads_arg != 1:
            print(f"[WARN] ClickHouse manages CPU cores internally. Ignoring threads={threads_arg}, running single-pass.")
        self.thread_list = [self.vcpu_count]

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = (
            self.project_root / "results" / self.machine_name / self.os_name
            / self.test_category_dir / self.benchmark
        )

        self.quick_mode = quick_mode

        # Detect environment
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

        self.ensure_upload_disabled()

        self.perf_paranoid = self.check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()
        if self.perf_events:
            print(f"  [OK] Perf monitoring enabled with events: {self.perf_events}")
        else:
            print("  [INFO] Perf monitoring disabled (command missing or unsupported)")

    def get_os_name(self):
        """Get OS name and version formatted as <Distro>_<Version>."""
        try:
            result = subprocess.run(['lsb_release', '-d', '-s'], capture_output=True, text=True)
            if result.returncode == 0:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1].replace('.', '_')}"
        except Exception:
            pass

        try:
            with open('/etc/os-release', 'r') as f:
                info = {}
                for line in f:
                    if '=' in line:
                        k, v = line.strip().split('=', 1)
                        info[k] = v.strip('"')
            if 'NAME' in info and 'VERSION_ID' in info:
                return f"{info['NAME'].split()[0]}_{info['VERSION_ID'].replace('.', '_')}"
        except Exception:
            pass

        return "Unknown_OS"

    def is_wsl(self):
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
        freqs = []
        # Try /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq
        cpu_count = self.vcpu_count
        for i in range(cpu_count):
            freq_file = Path(f'/sys/devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq')
            if freq_file.exists():
                try:
                    freqs.append(float(freq_file.read_text().strip()))
                except Exception:
                    freqs.append(0.0)
            else:
                freqs.append(0.0)
        return freqs

    def record_cpu_frequency(self, output_file):
        """Record CPU frequencies to file (one per line in kHz)."""
        try:
            freqs = self.get_cpu_frequencies()
            if any(f > 0 for f in freqs):
                with open(output_file, 'w') as f:
                    for freq in freqs:
                        f.write(f"{freq}\n")
                return True
        except Exception:
            pass
        return False

    def ensure_upload_disabled(self):
        """Ensure PTS upload is disabled."""
        batch_setup_path = Path.home() / ".phoronix-test-suite" / "user-config.xml"
        if batch_setup_path.exists():
            content = batch_setup_path.read_text()
            if 'UploadResults' in content and '<UploadResults>TRUE</UploadResults>' in content:
                print("  [WARN] PTS upload may be enabled. Check ~/.phoronix-test-suite/user-config.xml")

    def check_and_setup_perf_permissions(self):
        """Check and setup perf monitoring permissions."""
        paranoid_file = Path('/proc/sys/kernel/perf_event_paranoid')
        try:
            if paranoid_file.exists():
                paranoid = int(paranoid_file.read_text().strip())
                if paranoid > 0:
                    try:
                        subprocess.run(
                            ['sudo', 'sh', '-c', 'echo 0 > /proc/sys/kernel/perf_event_paranoid'],
                            check=True, capture_output=True
                        )
                        return 0
                    except Exception:
                        pass
                return paranoid
        except Exception:
            pass
        return 2

    def get_perf_events(self):
        """Check if perf is available and return supported events."""
        if not shutil.which('perf'):
            return None
        try:
            result = subprocess.run(
                ['perf', 'stat', '-e', 'cycles,instructions,cpu-clock', '--', 'true'],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0 or 'cycles' in result.stderr:
                return 'cycles,instructions,cpu-clock'
        except Exception:
            pass
        return None

    def parse_perf_stats_and_freq(self, perf_stats_file, freq_start_file, freq_end_file, cpu_list):
        """Parse perf stat output and CPU frequency files to generate performance summary."""
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
                    try:
                        freq_khz = float(freq_lines[idx].strip())
                        perf_summary["start_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)
                    except ValueError:
                        pass

        if freq_end_file.exists():
            with open(freq_end_file, 'r') as f:
                freq_lines = f.readlines()
            for idx, cpu_id in enumerate(cpu_ids):
                if idx < len(freq_lines):
                    try:
                        freq_khz = float(freq_lines[idx].strip())
                        perf_summary["end_frequency_ghz"][str(cpu_id)] = round(freq_khz / 1e6, 3)
                    except ValueError:
                        pass

        if perf_stats_file.exists():
            try:
                with open(perf_stats_file, 'r') as f:
                    perf_data = f.read()
            except FileNotFoundError:
                perf_data = ""

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

            elapsed_match = re.search(r'([\d,]+(?:\.\d+)?)\s+seconds time elapsed', perf_data)
            if elapsed_match:
                perf_summary["elapsed_time_sec"] = float(elapsed_match.group(1).replace(',', ''))

        return perf_summary

    def install_benchmark(self):
        """Install benchmark with error detection and verification."""
        # Pre-seed large files (hits.tsv.gz is ~16 GB)
        print("\n>>> Pre-seeding large download files with aria2c...")
        downloader = PreSeedDownloader()
        downloader.download_from_xml(self.benchmark_full, threshold_mb=1)

        print(f"\n>>> Installing {self.benchmark_full}...")

        print("  [INFO] Removing existing installation...")
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # ClickHouse uses pre-built binaries — no CC/CXX flags needed
        install_cmd = f'phoronix-test-suite batch-install {self.benchmark_full}'

        print(f"\n{'>'*80}")
        print("[PTS INSTALL COMMAND]")
        print(f"  {install_cmd}")
        print(f"{'<'*80}\n")

        print("  Running installation...")
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
        self.results_dir.mkdir(parents=True, exist_ok=True)
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
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(install_log)
        if log_f:
            log_f.close()

        full_output = ''.join(install_output)
        install_failed = False

        if returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif 'Checksum Failed' in full_output or 'Downloading of needed test files failed' in full_output:
            install_failed = True
        elif 'ERROR' in full_output or 'FAILED' in full_output:
            install_failed = True

        if install_failed:
            print(f"\n  [ERROR] Installation failed with return code {returncode}")
            print("  [INFO] Check output above for details")
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)

        installed_dir = Path.home() / '.phoronix-test-suite' / 'installed-tests' / 'pts' / self.benchmark
        if not installed_dir.exists():
            print("  [ERROR] Installation verification failed")
            print(f"  [ERROR] Expected directory not found: {installed_dir}")
            print(f"  [INFO] Try manually installing: phoronix-test-suite install {self.benchmark_full}")
            sys.exit(1)

        verify_result = subprocess.run(
            ['phoronix-test-suite', 'test-installed', self.benchmark_full],
            capture_output=True, text=True
        )
        if verify_result.returncode != 0:
            print("  [WARN] test-installed check failed, but directory exists — continuing...")

        print(f"  [OK] Installation completed and verified: {installed_dir}")

    def run_benchmark(self, num_threads):
        """
        Run ClickHouse benchmark.

        Args:
            num_threads: vCPU count (used only for label/perf monitoring; CH uses all cores)
        """
        print(f"\n{'='*80}")
        print(f">>> Running {self.benchmark_full} ({self.vcpu_count} vCPUs available)")
        print(f"{'='*80}")

        self.results_dir.mkdir(parents=True, exist_ok=True)

        label = f"{num_threads}-thread"
        log_file = self.results_dir / f"{label}.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{label}_perf_stats.txt"
        freq_start_file = self.results_dir / f"{label}_freq_start.txt"
        freq_end_file = self.results_dir / f"{label}_freq_end.txt"
        perf_summary_file = self.results_dir / f"{label}_perf_summary.json"

        # Remove existing PTS result to prevent interactive prompts
        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        batch_env = (
            f'{quick_env}BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 '
            f'TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        )

        # ClickHouse auto-detects cores; no taskset to avoid capping throughput
        cpu_list = ','.join([str(i) for i in range(self.vcpu_count)])
        pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'

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

        print(f"[INFO] vCPUs: {self.vcpu_count} (ClickHouse uses all cores internally)")
        print(f"[INFO] Perf monitoring mode: {perf_mode}")
        print(f"\n{'>'*80}")
        print("[PTS BENCHMARK COMMAND]")
        print(f"  {pts_cmd}")
        print(f"  Thread log:  {log_file}")
        print(f"  Stdout log:  {stdout_log}")
        print(f"  Perf stats:  {perf_stats_file}")
        print(f"{'<'*80}\n")

        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        with open(log_file, 'w') as log_f, open(stdout_log, 'a') as stdout_f:
            stdout_f.write(f"\n{'='*80}\n")
            stdout_f.write(f"[PTS BENCHMARK COMMAND]\n{pts_cmd}\n")
            stdout_f.write(f"{'='*80}\n\n")
            stdout_f.flush()

            process = subprocess.Popen(
                ['bash', '-c', pts_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            output_lines = []
            for line in process.stdout:
                print(line, end='')
                log_f.write(line)
                stdout_f.write(line)
                stdout_f.flush()
                output_lines.append(line)

            process.wait()
            returncode = process.returncode

        print("[INFO] Recording CPU frequency after benchmark...")
        if self.record_cpu_frequency(freq_end_file):
            print("  [OK] End frequency recorded")
        else:
            print("  [WARN] CPU frequency not available")

        # Parse perf stats
        if self.perf_events and perf_stats_file.exists():
            try:
                perf_summary = self.parse_perf_stats_and_freq(
                    perf_stats_file, freq_start_file, freq_end_file, cpu_list
                )
                with open(perf_summary_file, 'w') as f:
                    json.dump(perf_summary, f, indent=2)
                print(f"  [OK] Perf summary saved: {perf_summary_file}")
            except Exception as e:
                print(f"  [WARN] Failed to generate perf summary: {e}")

        # Check for PTS failures
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        full_output = ''.join(output_lines)

        if returncode != 0 or pts_test_failed:
            reason = pts_failure_reason or f"exit code {returncode}"
            print(f"  [ERROR] Benchmark failed: {reason}")
            return False

        print(f"  [OK] Benchmark completed successfully")
        return True

    def export_results(self):
        """Export PTS results to CSV and JSON formats."""
        print(f"\n{'='*80}")
        print(">>> Exporting results")
        print(f"{'='*80}")

        for num_threads in self.thread_list:
            result_dir_name = f"{self.benchmark}-{num_threads}threads"
            sanitized = self.benchmark.replace('.', '')
            sanitized_dir_name = f"{sanitized}-{num_threads}threads"

            # Try both names (PTS may sanitize version dots)
            for rname in [result_dir_name, sanitized_dir_name]:
                pts_result_path = Path.home() / ".phoronix-test-suite" / "test-results" / rname
                if pts_result_path.exists():
                    result_dir_name = rname
                    break

            # Export to CSV
            csv_output = self.results_dir / f"{num_threads}-thread.csv"
            print(f"  [EXPORT] CSV: {csv_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_dir_name],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                home_csv = Path.home() / f"{result_dir_name}.csv"
                if home_csv.exists():
                    shutil.move(str(home_csv), str(csv_output))
                    print(f"  [OK] Saved: {csv_output}")
            else:
                print(f"  [WARN] CSV export failed: {result.stderr}")

            # Export to JSON
            json_output = self.results_dir / f"{num_threads}-thread.json"
            print(f"  [EXPORT] JSON: {json_output}")
            result = subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_dir_name],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                home_json = Path.home() / f"{result_dir_name}.json"
                if home_json.exists():
                    shutil.move(str(home_json), str(json_output))
                    print(f"  [OK] Saved: {json_output}")
            else:
                print(f"  [WARN] JSON export failed: {result.stderr}")

        print("\n[OK] Export completed")

    def generate_summary(self):
        """Generate summary.log and summary.json from all thread results."""
        print(f"\n{'='*80}")
        print(">>> Generating summary")
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

        with open(summary_log, 'w') as f:
            f.write("="*80 + "\n")
            f.write("ClickHouse Benchmark Summary\n")
            f.write(f"Machine: {self.machine_name}\n")
            f.write(f"Test Category: {self.test_category}\n")
            f.write(f"vCPUs: {self.vcpu_count}\n")
            f.write("="*80 + "\n\n")

            for result in all_results:
                f.write(f"Test: {result['test_name']}\n")
                f.write(f"  Description: {result['description']}\n")

                val_str = f"{result['value']:.2f}" if result['value'] is not None else "FAILED"
                f.write(f"  Value: {val_str} {result['unit']}\n")

                raw_vals = result.get('raw_values')
                if raw_vals:
                    vals = ', '.join([f'{v:.2f}' for v in raw_vals if v is not None])
                    f.write(f"  Raw values: {vals}\n")
                else:
                    f.write("  Raw values: N/A\n")

                f.write("\n")

            f.write("="*80 + "\n")
            f.write("Summary Table\n")
            f.write("="*80 + "\n")
            f.write(f"{'Test':<40} {'Value':<15} {'Unit':<20}\n")
            f.write("-"*80 + "\n")
            for result in all_results:
                name = (result['test_name'] or '')[:39]
                val_str = f"{result['value']:<15.2f}" if result['value'] is not None else "FAILED         "
                f.write(f"{name:<40} {val_str} {result['unit']:<20}\n")

        print(f"[OK] Summary log saved: {summary_log}")

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
        print("ClickHouse Benchmark Runner")
        print(f"{'='*80}")
        print(f"[INFO] Machine: {self.machine_name}")
        print(f"[INFO] vCPU count: {self.vcpu_count}")
        print(f"[INFO] Test category: {self.test_category}")
        print("[INFO] Thread mode: ClickHouse-internal (auto-detects all cores)")
        print(f"[INFO] Results directory: {self.results_dir}")
        print()

        # Clean existing results for this run
        self.results_dir.mkdir(parents=True, exist_ok=True)
        for num_threads in self.thread_list:
            prefix = f"{num_threads}-thread"
            for f in self.results_dir.glob(f"{prefix}*"):
                if f.is_file():
                    f.unlink()
            print(f"  [INFO] Cleaned existing {prefix} results")

        # Install if needed
        install_status = get_install_status(self.benchmark_full, self.benchmark)
        info_installed = install_status["info_installed"]
        test_installed_ok = install_status["test_installed_ok"]
        installed_dir_exists = install_status["installed_dir_exists"]
        already_installed = install_status["already_installed"]

        print(
            f"[INFO] Install check -> info:{info_installed}, "
            f"test-installed:{test_installed_ok}, dir:{installed_dir_exists}"
        )

        if not already_installed and installed_dir_exists:
            print(
                f"[WARN] Existing install directory found but PTS does not report '{self.benchmark_full}' as installed. "
                "Treating as broken install and reinstalling."
            )

        if not already_installed:
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping installation: {self.benchmark_full}")

        # Run benchmark
        failed = []
        for num_threads in self.thread_list:
            if not self.run_benchmark(num_threads):
                failed.append(num_threads)

        self.export_results()
        self.generate_summary()
        cleanup_pts_artifacts(self.benchmark)

        print(f"\n{'='*80}")
        print("Benchmark Summary")
        print(f"{'='*80}")
        print(f"Total tests: {len(self.thread_list)}")
        print(f"Successful: {len(self.thread_list) - len(failed)}")
        print(f"Failed: {len(failed)}")
        if failed:
            print(f"Failed runs: {failed}")
        print(f"{'='*80}")

        return len(failed) == 0


def main():
    parser = argparse.ArgumentParser(
        description="ClickHouse OLAP Benchmark Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Note: ClickHouse manages CPU cores internally.\n"
            "      Thread arguments are accepted but ignored (single-pass benchmark).\n"
            "\nExample:\n"
            "  python3 pts_runner_clickhouse-1.4.0.py\n"
            "  python3 pts_runner_clickhouse-1.4.0.py --quick\n"
        )
    )

    parser.add_argument(
        'threads_pos',
        nargs='?',
        type=int,
        help='Number of threads (optional; ignored — ClickHouse auto-detects cores)'
    )

    parser.add_argument(
        '--threads',
        type=int,
        help='Thread count (ignored; ClickHouse manages its own threading)'
    )

    parser.add_argument(
        '--quick',
        action='store_true',
        help='Quick mode: Run each test only once (FORCE_TIMES_TO_RUN=1) for development'
    )

    args = parser.parse_args()

    if args.quick:
        print("[INFO] Quick mode enabled: FORCE_TIMES_TO_RUN=1")
        print("[INFO] Tests will run once instead of 3 times")

    threads = args.threads if args.threads is not None else args.threads_pos

    runner = ClickHouseRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
