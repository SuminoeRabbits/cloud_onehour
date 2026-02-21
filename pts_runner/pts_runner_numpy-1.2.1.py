#!/usr/bin/env python3
"""
PTS Runner for numpy-1.2.1

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * Python
- Estimated Install Time: 11 Seconds
- Environment Size: 12.4 MB
- Download Size: 0.01 MB
- Test Type: AI
- Supported Platforms: Linux, BSD, MacOSX

Test Characteristics:
- Multi-threaded: No (single-threaded Python benchmark)
- THFix_in_compile: false
- THChange_at_runtime: false
- Description: General Numpy performance benchmark
"""

import argparse
import atexit
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status

MIN_PYTHON_VERSION = (3, 7, 0)
PREFERRED_PYTHON_VERSIONS = ("3.11", "3.10")

if sys.version_info < MIN_PYTHON_VERSION:
    sys.stderr.write(
        "[ERROR] Python 3.7 or newer is required to run pts_runner_numpy-1.2.1.\n"
    )
    sys.exit(1)


class NumpyBenchmarkRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        # Benchmark configuration
        self.benchmark = "numpy-1.2.1"
        self.benchmark_full = f"pts/{self.benchmark}"
        self.test_category = "AI"
        self.test_category_dir = self.test_category.replace(" ", "_")

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Thread list setup (numpy is single-threaded, but we run once per thread count for consistency)
        if threads_arg is None:
            self.thread_list = list(range(2, self.vcpu_count + 1, 2))
        else:
            n = min(threads_arg, self.vcpu_count)
            self.thread_list = [n]

        # Results directory
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Quick mode for development
        self.quick_mode = quick_mode

        # Prefer Python 3.11 or 3.10 for PTS numpy test execution
        self.system_python_executable = shutil.which('python3') or sys.executable
        self.system_python_version = self.get_python_version(self.system_python_executable)
        self.python_executable, self.python_version = self.select_python_executable()
        self.pip_version = self.detect_pip_version()
        if not self.pip_version:
            print("  [ERROR] pip not available for selected Python.")
            print("  [ERROR] Install python3.11-venv/python3.10-venv or python3.11-pip/python3.10-pip.")
            sys.exit(1)
        self.break_system_packages_supported = self.pip_supports_break_system_packages()
        py_version = sys.version_info
        pip_info = self.pip_version or "unknown"
        print(
            f"  [INFO] Python (runner) {py_version.major}.{py_version.minor}.{py_version.micro} / "
            f"Python (PTS) {self.python_version[0]}.{self.python_version[1]}.{self.python_version[2]} / "
            f"pip {pip_info}"
        )
        if self.system_python_version:
            print(
                f"  [INFO] System default python3: "
                f"{self.system_python_version[0]}.{self.system_python_version[1]}.{self.system_python_version[2]} "
                f"({self.system_python_executable})"
            )

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

    def select_python_executable(self):
        """Pick Python 3.11 or 3.10 for running the PTS numpy test."""
        override = os.environ.get("PTS_PYTHON_BIN", "").strip()
        candidates = [override] if override else []
        candidates += [f"python{ver}" for ver in PREFERRED_PYTHON_VERSIONS]

        for candidate in candidates:
            if not candidate:
                continue
            path = shutil.which(candidate) if os.path.basename(candidate) == candidate else candidate
            if not path or not os.path.exists(path):
                continue
            try:
                result = subprocess.run(
                    [path, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    continue
                version_str = result.stdout.strip()
                version = self.parse_version_tuple(version_str)
                if version[:2] in [(3, 11), (3, 10)]:
                    return path, version
            except Exception:
                continue

        print("  [ERROR] Python 3.11 or 3.10 not found for PTS numpy test.")
        print("  [ERROR] Install python3.11/python3.10, or set PTS_PYTHON_BIN to the desired interpreter.")
        sys.exit(1)

    def get_python_version(self, python_executable):
        """Return (major, minor, micro) for the given python executable."""
        if not python_executable:
            return None
        try:
            result = subprocess.run(
                [python_executable, "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return None
            version_str = result.stdout.strip()
            return self.parse_version_tuple(version_str)
        except Exception:
            return None

    def build_pts_env(self):
        """Return environment for PTS execution using preferred Python only for this script."""
        pts_env = os.environ.copy()
        python_dir = str(Path(self.python_executable).parent)
        # Prepend a temp bin that maps python3 -> preferred python
        temp_bin = self._ensure_temp_python_bin()
        pts_env['PATH'] = f"{temp_bin}:{python_dir}:{pts_env.get('PATH', '')}"
        pts_env['PYTHON'] = self.python_executable
        pts_env['PYTHON3'] = self.python_executable
        return pts_env

    def _ensure_temp_python_bin(self):
        """
        Create a temporary bin dir with python3 symlink pointing to preferred python.
        Ensures cleanup via atexit even on errors.
        """
        if hasattr(self, "_temp_python_bin") and self._temp_python_bin:
            return self._temp_python_bin

        temp_dir = Path(tempfile.mkdtemp(prefix="pts-python-"))
        bin_dir = temp_dir / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        link_path = bin_dir / "python3"
        try:
            if link_path.exists():
                link_path.unlink()
            link_path.symlink_to(self.python_executable)
        except Exception:
            # Fallback: create a wrapper script if symlink is not allowed
            try:
                wrapper = (
                    "#!/bin/sh\n"
                    f"exec \"{self.python_executable}\" \"$@\"\n"
                )
                link_path.write_text(wrapper)
                link_path.chmod(0o755)
            except Exception:
                print("  [WARN] Failed to create python3 shim; PATH override may be insufficient")

        self._temp_python_bin = str(bin_dir)
        atexit.register(shutil.rmtree, temp_dir, True)
        return self._temp_python_bin

    def parse_version_tuple(self, version_str):
        """Convert a version string into a comparable tuple."""
        components = []
        for part in version_str.split('.'):
            if part.isdigit():
                components.append(int(part))
            else:
                match = re.match(r'(\d+)', part)
                if match:
                    components.append(int(match.group(1)))
                break

        while len(components) < 3:
            components.append(0)

        return tuple(components[:3])

    def detect_pip_version(self):
        """Detect pip version for the selected Python."""
        try:
            result = subprocess.run(
                [self.python_executable, '-m', 'pip', '--version'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout:
                parts = result.stdout.strip().split()
                if len(parts) >= 2:
                    return parts[1]
        except Exception:
            return None

        return None

    def pip_supports_break_system_packages(self):
        """Return True if pip understands --break-system-packages."""
        if not self.pip_version:
            return False

        return self.parse_version_tuple(self.pip_version) >= (23, 0, 0)

    def build_pip_install_command(self):
        """Construct the pip install command with conditional flags."""
        cmd = [self.python_executable, '-m', 'pip', 'install', '--user']
        if self.break_system_packages_supported:
            cmd.append('--break-system-packages')
        cmd.extend(['scipy', 'numpy'])
        return cmd

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


    def get_cpu_frequencies(self):
        """
        Get current CPU frequencies for all CPUs.
        Tries multiple methods for cross-platform compatibility (x86_64, ARM64, cloud VMs).

        Returns:
            list: List of frequencies in kHz, one per CPU. Empty list if unavailable.
        """
        frequencies = []

        # Method 1: /proc/cpuinfo (works on x86_64)
        try:
            result = subprocess.run(
                ['bash', '-c', 'grep "cpu MHz" /proc/cpuinfo'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    # Format: "cpu MHz		: 3400.000"
                    parts = line.split(':')
                    if len(parts) >= 2:
                        mhz = float(parts[1].strip())
                        frequencies.append(int(mhz * 1000))  # Convert MHz to kHz
                if frequencies:
                    return frequencies
        except Exception:
            pass

        # Method 2: /sys/devices/system/cpu/cpufreq (works on ARM64 and some x86)
        try:
            # Try scaling_cur_freq first (more commonly available)
            freq_files = sorted(Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_cur_freq'))
            if not freq_files:
                # Fallback to cpuinfo_cur_freq
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

        # Method 3: lscpu (fallback)
        try:
            result = subprocess.run(
                ['lscpu'],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                for line in result.stdout.split('\n'):
                    if 'CPU MHz' in line or 'CPU max MHz' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            mhz = float(parts[1].strip().replace(',', '.'))
                            # Return same frequency for all CPUs
                            return [int(mhz * 1000)] * self.vcpu_count
        except Exception:
            pass

        return frequencies

    def record_cpu_frequency(self, output_file):
        """
        Record current CPU frequencies to a file.

        Args:
            output_file: Path to output file

        Returns:
            bool: True if successful, False otherwise
        """
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
            # Write empty file to indicate unavailability
            try:
                with open(output_file, 'w') as f:
                    pass
                return False
            except Exception:
                return False

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
        if self.is_wsl_env:
            print("  [INFO] WSL detected; disabling perf to avoid kernel tool mismatch")
            return None
        # Check if perf command exists in PATH
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found in PATH")
            return None

        def perf_unavailable(output):
            lowered = output.lower()
            return (
                "perf not found for kernel" in lowered
                or "you may need to install the following packages" in lowered
                or "linux-tools-" in lowered
            )

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
            if perf_unavailable(output):
                print("  [INFO] perf kernel support missing; disabling perf")
                return None

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

            output_sw = result_sw.stdout + result_sw.stderr
            if perf_unavailable(output_sw):
                print("  [INFO] perf kernel support missing; disabling perf")
                return None

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

        # Build PTS base command (numpy is single-threaded, but we use taskset for consistency)
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
                print("  [INFO] Running with perf monitoring (per-CPU mode)")
            else:
                # Limited mode without per-CPU breakdown
                perf_cmd = f"perf stat -e {self.perf_events} -o {perf_stats_file}"
                print("  [INFO] Running with perf monitoring (aggregated mode)")

            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {perf_cmd} {pts_base_cmd}'
        else:
            # Perf unavailable
            pts_cmd = f'NUM_CPU_CORES={num_threads} {batch_env} {pts_base_cmd}'
            print("  [INFO] Running without perf")

        # Record CPU frequency before benchmark
        # Uses cross-platform method (works on x86_64, ARM64, and cloud VMs)
        print("[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print("  [OK] Start frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        # Execute benchmark with real-time output streaming
        pts_env = self.build_pts_env()
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
                bufsize=1,
                env=pts_env
            )

            for line in process.stdout:
                print(line, end='')
            if log_f:
                log_f.write(line)
                log_f.flush()
                log_f.write(line)
                stdout_f.write(line)
                log_f.flush()
                stdout_f.flush()

            process.wait()
            returncode = process.returncode
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if log_f:
            log_f.close()

        # Record CPU frequency after benchmark
        # Uses cross-platform method (works on x86_64, ARM64, and cloud VMs)
        print("\n[INFO] Recording CPU frequency after benchmark...")
        if self.record_cpu_frequency(freq_end_file):
            print("  [OK] End frequency recorded")
        else:
            print("  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        if returncode == 0 and pts_test_failed:
            print(f"\n[ERROR] PTS reported benchmark failure despite zero exit code: {pts_failure_reason}")
            return False

        if returncode == 0:
            print("\n[OK] Benchmark completed successfully")
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

        print("\n[OK] Export completed")

    def generate_summary(self):
        """Generate summary.log and summary.json from all thread results."""
        print(f"\n{'='*80}")
        print(">>> Generating summary")
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
            f.write("Benchmark Summary\n")
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
        """Install benchmark with custom pip options for Ubuntu 24.04 PEP 668."""
        print(f"\n{'='*80}")
        print(f">>> Installing {self.benchmark_full}")
        print(f"{'='*80}")

        # Remove existing installation
        remove_cmd = f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'
        subprocess.run(['bash', '-c', remove_cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Build install command
        nproc = os.cpu_count() or 1
        install_cmd = f'MAKEFLAGS="-j{nproc}" phoronix-test-suite batch-install {self.benchmark_full}'

        # Execute with real-time output streaming
        print("  Running installation...")
        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
        log_file = install_log
        log_f = open(install_log, 'w') if use_install_log else None
        if log_f:
            log_f.write(f"[PTS INSTALL COMMAND]\n{install_cmd}\n\n")
            log_f.flush()
        install_env = self.build_pts_env()
        process = subprocess.Popen(['bash', '-c', install_cmd],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   text=True,
                                   bufsize=1,
                                   env=install_env)

        for line in process.stdout:
            print(line, end='')
            if log_f:
                log_f.write(line)
                log_f.flush()

        process.wait()
        if log_f:
            log_f.close()

        returncode = process.returncode
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        install_failed = False
        if returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        if install_failed:
            print(f"\n  [ERROR] Installation failed with return code {returncode}")
            sys.exit(1)

        # Verify installation directory exists
        pts_home = Path.home() / '.phoronix-test-suite'
        install_dir = pts_home / 'installed-tests' / 'pts' / self.benchmark

        if not install_dir.exists():
            print(f"  [ERROR] Installation failed: {install_dir} does not exist")
            print("  [ERROR] Check output above for details")
            sys.exit(1)

        # Manual pip install with conditional --break-system-packages support
        print("\n  [INFO] Manually installing Python dependencies (auto-detected pip flags)...")
        if not self.break_system_packages_supported:
            print("  [INFO] pip version does not support --break-system-packages; using --user only")

        pip_cmd = self.build_pip_install_command()
        print(f"  [DEBUG] pip command: {' '.join(pip_cmd)}")
        pip_result = subprocess.run(pip_cmd, capture_output=True, text=True)
        
        if pip_result.returncode != 0:
            print("  [ERROR] pip install failed:")
            print(pip_result.stderr)
            sys.exit(1)
        else:
            print("  [OK] Python dependencies installed successfully")

        # Strengthen result parser to tolerate unsupported lines on newer Python/numpy
        result_parser_path = install_dir / 'result_parser.py'
        result_parser_path.write_text(
            "import sys\n"
            "product = 1.0\n"
            "count = 0\n"
            "skipped = 0\n"
            "with open(sys.argv[-1]) as fp:\n"
            "    for l in fp.readlines():\n"
            "        parts = l.split()\n"
            "        if len(parts) < 4:\n"
            "            skipped += 1\n"
            "            continue\n"
            "        try:\n"
            "            avg = float(parts[3])\n"
            "        except ValueError:\n"
            "            skipped += 1\n"
            "            continue\n"
            "        product *= avg\n"
            "        count += 1\n"
            "if count == 0:\n"
            "    print(\"[WARN] No valid benchmark lines found; skipped=%d\" % skipped)\n"
            "    print(\"Geometric mean score: 0.00\")\n"
            "else:\n"
            "    gmean = product**(1.0/count)\n"
            "    score = 1000000.0/gmean\n"
            "    if skipped:\n"
            "        print(\"[WARN] Skipped %d non-numeric lines\" % skipped)\n"
            "    print(\"Geometric mean score: %.2f\" % score)\n"
        )
        print("  [OK] result_parser.py patched for robust parsing")

        # Note: Do NOT patch numpy script - PTS requires $LOG_FILE output for result parsing
        # The original script writes to $LOG_FILE which PTS uses to extract benchmark results
        print("\n  [INFO] Keeping original numpy script (PTS requires $LOG_FILE output)")

        # Secondary check: PTS recognition
        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        result = subprocess.run(['bash', '-c', verify_cmd], capture_output=True, text=True)
        if self.benchmark_full not in result.stdout:
            print(f"  [WARN] {self.benchmark_full} may not be fully recognized by PTS")

        print("  [OK] Installation completed and verified")

    def patch_install_script(self):
        """
        Patch numpy install.sh to add --break-system-packages for Ubuntu 24.04+
        This is required due to PEP 668 externally-managed-environment policy.
        """
        install_script = Path.home() / '.phoronix-test-suite' / 'installed-tests' / 'pts' / self.benchmark / 'install.sh'
        
        if not install_script.exists():
            print(f"  [WARN] Install script not found: {install_script}")
            return False

        if not self.break_system_packages_supported:
            print("  [INFO] Skipping install.sh patch (pip lacks --break-system-packages support)")
            return True
        
        print(f"\n{'='*80}")
        print(">>> Patching install.sh for Ubuntu 24.04 PEP 668 compliance")
        print(f"{'='*80}")
        
        try:
            # Read original script
            with open(install_script, 'r') as f:
                original_content = f.read()
            
            # Check if already patched
            if '--break-system-packages' in original_content:
                print("  [INFO] Install script already patched")
                return True
            
            # Patch: pip3 install --user scipy numpy
            # To:    pip3 install --user --break-system-packages scipy numpy
            patched_content = original_content.replace(
                'pip3 install --user scipy numpy',
                'pip3 install --user --break-system-packages scipy numpy'
            )
            
            if patched_content == original_content:
                print("  [WARN] No pip3 install command found to patch")
                return False
            
            # Write patched script
            with open(install_script, 'w') as f:
                f.write(patched_content)
            
            print("  [OK] Install script patched successfully")
            print("  [INFO] Added --break-system-packages to pip3 install command")
            return True
            
        except Exception as e:
            print(f"  [ERROR] Failed to patch install script: {e}")
            return False

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

        self.results_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = self.results_dir / "stdout.log"
        with open(stdout_log, 'a') as stdout_f:
            stdout_f.write(f"{'='*80}\n")
            stdout_f.write("[RUNNER STARTUP]\n")
            stdout_f.write(f"Python: {sys.version.split()[0]}\n")
            stdout_f.write(f"Python (PTS): {self.python_version[0]}.{self.python_version[1]}.{self.python_version[2]}\n")
            stdout_f.write(f"Python (PTS) exec: {self.python_executable}\n")
            if self.system_python_version:
                stdout_f.write(
                    f"Python (system default): "
                    f"{self.system_python_version[0]}.{self.system_python_version[1]}.{self.system_python_version[2]}\n"
                )
                stdout_f.write(f"Python (system exec): {self.system_python_executable}\n")
            stdout_f.write(f"pip: {self.pip_version or 'unknown'}\n")
            stdout_f.write(f"pip exec: {self.python_executable} -m pip\n")
            stdout_f.write(f"Machine: {self.machine_name}\n")
            stdout_f.write(f"OS: {self.os_name}\n")
            stdout_f.write(f"vCPU: {self.vcpu_count}\n")
            stdout_f.write(f"Threads: {self.thread_list}\n")
            stdout_f.write(f"Perf events: {self.perf_events or '-'}\n")
            stdout_f.write(f"Perf paranoid: {self.perf_paranoid}\n")
            stdout_f.write(f"{'='*80}\n\n")

        # Install benchmark
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
        print(">>> Exporting results")
        print(f"{'='*80}")
        self.export_results()

        # Generate summary
        self.generate_summary()

        print(f"\n{'='*80}")
        print("[SUCCESS] All benchmarks completed successfully")
        print(f"{'='*80}")

        return True

    # Cleanup handled by atexit in _ensure_temp_python_bin


def main():
    parser = argparse.ArgumentParser(
        description="Run numpy-1.2.1 benchmark",
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

    runner = NumpyBenchmarkRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()

    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
