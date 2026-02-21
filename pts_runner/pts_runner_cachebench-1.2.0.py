#!/usr/bin/env python3
"""
PTS Runner for cachebench-1.2.0

System Dependencies (from phoronix-test-suite info):
- Software Dependencies:
  * Libevent
- Test Type: System
- Supported Platforms: Linux, BSD, MacOSX

Test Characteristics:
- Multi-threaded: Yes
- Honors CFLAGS/CXXFLAGS: Yes
- Notable Instructions: N/A
"""
import os
import sys
import subprocess
import argparse
import shutil
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status


class CachebenchRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize the Cachebench Runner.

        Args:
            threads_arg: Number of threads (optional). If None, will run in scaling mode.
            quick_mode: If True, run in quick mode (FORCE_TIMES_TO_RUN=1).
        """
        self.benchmark = "cachebench-1.2.0"
        self.benchmark_full = "pts/cachebench-1.2.0"
        self.test_category = "Memory Access"
        self.test_category_dir = self.test_category.replace(' ', '_')

        # System info
        self.vcpu_count = os.cpu_count() or 1
        self.machine_name = os.environ.get('MACHINE_NAME', os.uname().nodename)
        self.os_name = self.get_os_name()

        # Project structure
        self.script_dir = Path(__file__).parent.resolve()
        self.project_root = self.script_dir.parent

        # Thread configuration
        self.quick_mode = quick_mode
        if threads_arg is None:
            self.thread_list = list(range(2, self.vcpu_count + 1, 2))
        else:
            n = min(threads_arg, self.vcpu_count)
            if n != threads_arg:
                print(f"  [INFO] Thread count {threads_arg} capped to {n} (nproc)")
            self.thread_list = [n]

        # Results directory
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark

        # Perf configuration
        self.perf_paranoid = self.check_and_setup_perf_permissions()
        self.perf_events = self.get_perf_events()
        self.ensure_upload_disabled()
        self.is_wsl_env = self.is_wsl()
        if self.is_wsl_env:
            print("  [INFO] Running on WSL environment")

    def get_os_name(self):
        """Get OS name and version formatted as <Distro>_<Version>."""
        try:
            result = subprocess.run(
                ["lsb_release", "-d", "-s"],
                capture_output=True,
                text=True
            )
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

    def get_perf_events(self):
        """Determine available perf events by testing command execution."""
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found")
            return None

        hw_events = "cycles,instructions,branches,branch-misses,cache-references,cache-misses"
        test_cmd = f"perf stat -e {hw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)
        if result.returncode == 0:
            if 'not supported' not in (result.stdout + result.stderr):
                return hw_events

        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations,page-faults"
        test_cmd = f"perf stat -e {sw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)
        if result.returncode == 0:
            if 'not supported' not in (result.stdout + result.stderr):
                return sw_events

        print("  [INFO] perf events not available")
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
                print(f"  [ERROR] Failed to adjust (sudo required)")
                print(f"  [WARN] Running in LIMITED mode")
                return current_value
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

    def is_wsl(self):
        """Detect if running in WSL environment (for logging purposes only)."""
        try:
            if not os.path.exists('/proc/version'):
                return False
            with open('/proc/version', 'r') as f:
                content = f.read().lower()
                return 'microsoft' in content or 'wsl' in content
        except Exception:
            return False

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

    def get_cpu_frequencies(self):
        """
        Get current CPU frequencies for all CPUs.
        Tries multiple methods for cross-platform compatibility (x86_64, ARM64, cloud VMs).
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
                    parts = line.split(':')
                    if len(parts) >= 2:
                        mhz = float(parts[1].strip())
                        frequencies.append(int(mhz * 1000))
                if frequencies:
                    return frequencies
        except Exception:
            pass

        # Method 2: /sys/devices/system/cpu/cpufreq (works on ARM64 and some x86)
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
        try:
            with open(output_file, 'w') as f:
                pass
            return False
        except Exception:
            return False

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
        subprocess.run(
            ['bash', '-c', f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        nproc = os.cpu_count() or 1
        install_cmd = f'NUM_CPU_CORES={nproc} phoronix-test-suite batch-install {self.benchmark_full}'

        install_log_env = os.environ.get("PTS_INSTALL_LOG", "").strip().lower()
        install_log_path = os.environ.get("PTS_INSTALL_LOG_PATH", "").strip()
        use_install_log = install_log_env in {"1", "true", "yes"} or bool(install_log_path)
        install_log = Path(install_log_path) if install_log_path else (self.results_dir / "install.log")
        log_file = install_log
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

        output = []
        for line in process.stdout:
            print(line, end='')
            if log_f:
                log_f.write(line)
                log_f.flush()
            output.append(line)
        process.wait()
        if log_f:
            log_f.close()

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        install_failed = False
        full_output = ''.join(output)
        if process.returncode != 0:
            install_failed = True
        elif pts_test_failed:
            install_failed = True
        elif 'FAILED' in full_output:
            install_failed = True

        if install_failed:
            print(f"  [ERROR] Installation failed")
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)

        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        if subprocess.run(['bash', '-c', verify_cmd], capture_output=True).returncode == 0:
            print(f"  [OK] Installation verified")
        else:
            print(f"  [WARN] Installation verification skipped/failed")

    def run_benchmark(self, num_threads):
        """Run benchmark with specified threads."""
        print(f"\n>>> Running {self.benchmark} with {num_threads} threads")

        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''

        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        batch_env = (
            f'{quick_env}NUM_CPU_CORES={num_threads} '
            f'BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 '
            f'TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads '
            f'TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        )

        pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'

        if self.perf_events:
            pts_cmd = f'{batch_env} perf stat -e {self.perf_events} -o {perf_stats_file} {pts_base_cmd}'
        else:
            pts_cmd = f'{batch_env} {pts_base_cmd}'

        print(f"[INFO] Recording CPU frequency before benchmark...")
        if self.record_cpu_frequency(freq_start_file):
            print(f"  [OK] Start frequency recorded")
        else:
            print(f"  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

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

        print(f"\n[INFO] Recording CPU frequency after benchmark...")
        if self.record_cpu_frequency(freq_end_file):
            print(f"  [OK] End frequency recorded")
        else:
            print(f"  [WARN] CPU frequency not available (common on ARM64/cloud VMs)")

        returncode = process.returncode
        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)
        if returncode == 0 and pts_test_failed:
            print(f"\n[ERROR] PTS reported benchmark failure despite zero exit code: {pts_failure_reason}")
            return False
        return returncode == 0

    def export_results(self):
        """Export results to CSV/JSON."""
        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            result_dir_name = result_name.replace('.', '')

            subprocess.run(
                ['phoronix-test-suite', 'result-file-to-csv', result_dir_name],
                capture_output=True
            )
            home_csv = Path.home() / f"{result_dir_name}.csv"
            if home_csv.exists():
                shutil.move(str(home_csv), str(self.results_dir / f"{num_threads}-thread.csv"))

            subprocess.run(
                ['phoronix-test-suite', 'result-file-to-json', result_dir_name],
                capture_output=True
            )
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
            self.clean_pts_cache()
            self.install_benchmark()
        else:
            print(f"[INFO] Benchmark already installed, skipping installation: {self.benchmark_full}")

        for t in self.thread_list:
            self.run_benchmark(t)

        self.export_results()
        self.generate_summary()
        return True


def main():
    parser = argparse.ArgumentParser(description="Cachebench Runner")
    parser.add_argument('threads_pos', nargs='?', type=int, help='Threads (positional)')
    parser.add_argument('--threads', type=int, help='Threads (named)')
    parser.add_argument('--quick', action='store_true', help='Quick mode')
    args = parser.parse_args()

    threads = args.threads if args.threads else args.threads_pos
    runner = CachebenchRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
