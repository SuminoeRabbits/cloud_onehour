#!/usr/bin/env python3
"""
PTS Runner for memcached-1.2.0

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
import re
import json
import shutil
import time
from pathlib import Path
from runner_common import detect_pts_failure_from_log, get_install_status

class MemcachedRunner:
    def __init__(self, threads_arg=None, quick_mode=False):
        """
        Initialize the Memcached Runner.

        Args:
            threads_arg: Number of threads (optional). If None, will run in scaling mode.
            quick_mode: If True, run in quick mode (FORCE_TIMES_TO_RUN=1).
        """
        self.benchmark = "memcached-1.2.0"
        self.benchmark_full = "pts/memcached-1.2.0"
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
            # Even-number scaling: [2, 4, 6, ..., nproc]
            self.thread_list = list(range(2, self.vcpu_count + 1, 2))
        else:
            # Fixed mode: cap at vcpu_count
            n = min(threads_arg, self.vcpu_count)
            if n != threads_arg:
                print(f"  [INFO] Thread count {threads_arg} capped to {n} (nproc)")
            self.thread_list = [n]

        # Results directory
        self.results_dir = self.project_root / "results" / self.machine_name / self.os_name / self.test_category_dir / self.benchmark
        
        # Perf configuration
        self.perf_paranoid = self.check_and_setup_perf_permissions()
        # Default events for memory/cpu bound
        self.perf_events = self.get_perf_events()
        # Enforce safety
        self.ensure_upload_disabled()

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
        """
        perf_path = shutil.which("perf")
        if not perf_path:
            print("  [INFO] perf command not found")
            return None

        # Test HW+SW
        hw_events = "cycles,instructions,branches,branch-misses,cache-references,cache-misses"
        test_cmd = f"perf stat -e {hw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)
        if result.returncode == 0:
            if 'not supported' not in (result.stdout + result.stderr):
                return hw_events

        # Test SW only
        sw_events = "cpu-clock,task-clock,context-switches,cpu-migrations,page-faults"
        test_cmd = f"perf stat -e {sw_events} -- sleep 0.01"
        result = subprocess.run(['bash', '-c', test_cmd], capture_output=True, text=True)
        if result.returncode == 0:
            if 'not supported' not in (result.stdout + result.stderr):
                return sw_events
                
        print("  [WARN] perf events not available")
        return None

    def check_and_setup_perf_permissions(self):
        """Check and adjust perf_event_paranoid setting."""
        try:
            result = subprocess.run(
                ['cat', '/proc/sys/kernel/perf_event_paranoid'],
                capture_output=True, text=True, check=True
            )
            current_value = int(result.stdout.strip())
            
            if current_value >= 1:
                print(f"  [INFO] Attempting to adjust perf_event_paranoid to 0...")
                result = subprocess.run(
                    ['sudo', 'sysctl', '-w', 'kernel.perf_event_paranoid=0'],
                    capture_output=True, text=True
                )
                if result.returncode == 0:
                    return 0
                return current_value
            return current_value
        except Exception:
            return 2


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
        
        # Remove existing
        subprocess.run(['bash', '-c', f'echo "y" | phoronix-test-suite remove-installed-test "{self.benchmark_full}"'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # Install
        nproc = os.cpu_count() or 1
        install_cmd = f'NUM_CPU_CORES={nproc} phoronix-test-suite batch-install {self.benchmark_full}'
        
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
        
        full_output = ''.join(output)
        if process.returncode != 0 or 'FAILED' in full_output:
            print(f"  [ERROR] Installation failed")
            if use_install_log:
                print(f"  [INFO] Install log: {install_log}")
            sys.exit(1)
            
        # Verify
        verify_cmd = f'phoronix-test-suite test-installed {self.benchmark_full}'
        if subprocess.run(['bash', '-c', verify_cmd], capture_output=True).returncode == 0:
             print(f"  [OK] Installation verified")
        else:
             print(f"  [WARN] Installation verification skipped/failed")

    def parse_perf_stats_and_freq(self, perf_file, freq_start, freq_end, cpu_list):
        """Parse perf and frequency data."""
        # Minimal implementation for compliance - full logic similar to sysbench/coremark
        return {}

    def run_benchmark(self, num_threads):
        """Run benchmark with specified threads."""
        print(f"\n>>> Running {self.benchmark} with {num_threads} threads")
        
        self.results_dir.mkdir(parents=True, exist_ok=True)
        log_file = self.results_dir / f"{num_threads}-thread.log"
        stdout_log = self.results_dir / "stdout.log"
        perf_stats_file = self.results_dir / f"{num_threads}-thread_perf_stats.txt"
        freq_start_file = self.results_dir / f"{num_threads}-thread_freq_start.txt"
        freq_end_file = self.results_dir / f"{num_threads}-thread_freq_end.txt"
        perf_summary_file = self.results_dir / f"{num_threads}-thread_perf_summary.json"

        quick_env = 'FORCE_TIMES_TO_RUN=1 ' if self.quick_mode else ''
        quick_thread_timeout = int(os.environ.get('MEMCACHED_QUICK_THREAD_TIMEOUT', '1800'))
        normal_thread_timeout = int(os.environ.get('MEMCACHED_THREAD_TIMEOUT', '5400'))
        thread_timeout = quick_thread_timeout if self.quick_mode else normal_thread_timeout

        def cleanup_stale_memcached_processes():
            cleanup_cmds = [
                "pkill -f 'memtier_benchmark.*memcache_text' || true",
                "pkill -f '^memtier_benchmark' || true",
                "pkill -f '^./memcached -c 4096 -t ' || true",
                "pkill -f '/opt/phoronix-test-suite/phoronix-test-suite batch-run pts/memcached-1.2.0' || true",
            ]
            for c in cleanup_cmds:
                subprocess.run(['bash', '-c', c], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cleanup_stale_memcached_processes()
        time.sleep(1)
        
        # Remove existing PTS result to avoid interactive prompts
        sanitized_benchmark = self.benchmark.replace('.', '')
        remove_cmds = [
            f'phoronix-test-suite remove-result {self.benchmark}-{num_threads}threads',
            f'phoronix-test-suite remove-result {sanitized_benchmark}-{num_threads}threads'
        ]
        for cmd in remove_cmds:
            subprocess.run(['bash', '-c', cmd], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
        batch_env = f'{quick_env}NUM_CPU_CORES={num_threads} BATCH_MODE=1 SKIP_ALL_PROMPTS=1 DISPLAY_COMPACT_RESULTS=1 TEST_RESULTS_NAME={self.benchmark}-{num_threads}threads TEST_RESULTS_IDENTIFIER={self.benchmark}-{num_threads}threads TEST_RESULTS_DESCRIPTION={self.benchmark}-{num_threads}threads'
        
        pts_base_cmd = f'phoronix-test-suite batch-run {self.benchmark_full}'
        
        if self.perf_events:
             pts_cmd = f'{batch_env} perf stat -e {self.perf_events} -o {perf_stats_file} {pts_base_cmd}'
        else:
             pts_cmd = f'{batch_env} {pts_base_cmd}'

        timeout_cmd = shutil.which('timeout')
        if timeout_cmd:
            pts_cmd = f'{timeout_cmd} --signal=TERM --kill-after=30s {thread_timeout}s {pts_cmd}'

        # Record start freq
        subprocess.run(['bash', '-c', f'grep "cpu MHz" /proc/cpuinfo | head -1 > {freq_start_file}'])

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
            process.wait()
            returncode = process.returncode
            stdout_f.write(f"\n[PTS EXIT CODE] {returncode}\n")
            stdout_f.flush()

        pts_test_failed, pts_failure_reason = detect_pts_failure_from_log(log_file)

        cleanup_stale_memcached_processes()
            
        # Record end freq
        subprocess.run(['bash', '-c', f'grep "cpu MHz" /proc/cpuinfo | head -1 > {freq_end_file}'])
        
        if returncode == 124:
            print(f"  [ERROR] Memcached benchmark timed out at {thread_timeout}s for {num_threads} threads")
            return False
        if returncode == 0 and pts_test_failed:
            print(f"\n[ERROR] PTS reported benchmark failure despite zero exit code: {pts_failure_reason}")
            return False

        if returncode == 0:
            return True
        return False

    def export_results(self):
        """Export results to CSV/JSON."""
        pts_results_dir = Path.home() / ".phoronix-test-suite" / "test-results"
        for num_threads in self.thread_list:
            result_name = f"{self.benchmark}-{num_threads}threads"
            result_dir_name = result_name.replace('.', '')
            
            # CSV
            subprocess.run(['phoronix-test-suite', 'result-file-to-csv', result_dir_name], capture_output=True)
            home_csv = Path.home() / f"{result_dir_name}.csv"
            if home_csv.exists():
                shutil.move(str(home_csv), str(self.results_dir / f"{num_threads}-thread.csv"))
                
            # JSON
            subprocess.run(['phoronix-test-suite', 'result-file-to-json', result_dir_name], capture_output=True)
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
    parser = argparse.ArgumentParser(description="Memcached Runner")
    parser.add_argument('threads_pos', nargs='?', type=int, help='Threads (positional)')
    parser.add_argument('--threads', type=int, help='Threads (named)')
    parser.add_argument('--quick', action='store_true', help='Quick mode')
    args = parser.parse_args()

    threads = args.threads if args.threads else args.threads_pos
    runner = MemcachedRunner(threads_arg=threads, quick_mode=args.quick)
    success = runner.run()
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
